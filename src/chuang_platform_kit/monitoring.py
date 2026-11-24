"""Errors and traces to Sentry, behind functions of our own: the one module that imports sentry_sdk.

A host calls `init()` once at start-up with its DSN, environment, release and service name; without a
DSN (local runs, tests, a host that has not set one up) it is a no-op and nothing is ever sent. After
that the logging integration turns every ERROR record into an issue and INFO/WARNING into breadcrumbs,
and the web-framework and database integrations Sentry enables when it finds them (FastAPI, SQLAlchemy)
trace requests and queries. Callers use `capture()`, `span()`, `transaction()` and `flush()` (at the end
of each request, message or job, since Cloud Run throttles CPU between requests), never sentry_sdk,
so the vendor can be swapped by changing this file alone.

Nothing private leaves: every event, span and breadcrumb passes `scrub()` first, which is
`redact.redact()` (tokens, keys, the process's secret values) plus email addresses and phone numbers;
`DATA_COLLECTION` keeps the SDK from attaching who a request came from, what it carried, or any local
variable.

sentry_sdk 3 (0.54.0): its defaults collect more than 2.x's did, create no event from a log line, and
send every INFO line as a Sentry log, so each of those is set here by name; 2.x is not supported.

sentry_sdk is an optional dependency (`pip install chuang-platform-kit[monitoring]`): a host without it
gets the no-op, with one WARNING from init() if a DSN was given.
"""
import contextlib
import logging
import re

from .redact import redact

log = logging.getLogger(__name__)

EMAIL_RE = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
# A WhatsApp chat id (16265550104@c.us, a group's 1203...@g.us): its digits are a phone number, and the
# email pattern would otherwise take the whole id. Masked first, keeping the suffix.
WHATSAPP_ID_RE = re.compile(r'(?<![\w.])\+?\d{6,20}(?:-\d+)?(?=@[cg]\.us\b)')
# A phone number: an optional + and opening parenthesis, then 10 to 15 digits in groups split by
# spaces, dots, dashes or parentheses (626-555-0104, (626) 555 0104, +1 626 555 0104, 16265550104).
PHONE_RE = re.compile(r'(?<![\w.(])\+?\(?(?:\d[\s().-]{0,2}){9,14}\d(?![\w.])')
EMAIL_MASK = '[email]'
PHONE_MASK = '[phone]'

# What the SDK may attach by itself. 3.0's own defaults collect user identity, request bodies, query data and more headers;
# this is stricter than its migration guide's restrictive form: no body, no cookie, no query string, the few request headers
# named here, and no stack frame's local variables. The last was found reading one failed request whole, as 2.x sent it too:
# the web framework's frames hold the request, so a variable carried every header (a device credential among them, which no
# scrub knows by its shape) and the body with a person's name. The stack itself, with its source lines, is kept. A new
# category of a later release would come in at the SDK's default: test_monitoring fails when one appears not named here.
DATA_COLLECTION = {
    'user_info': False,
    'http_bodies': [],
    'http_headers': {'request': {'mode': 'allowlist', 'terms': ['user-agent', 'content-type', 'content-length', 'x-app-']}},
    'cookies': {'mode': 'off'},
    'url_query_params': {'mode': 'off'},
    'graphql': {'document': False, 'variables': False},
    'gen_ai': {'inputs': False, 'outputs': False},
    'mcp': {'inputs': False, 'outputs': False},
    'database_query_data': False,
    'queues': False,
    'stack_frame_variables': False,
    'frame_context_lines': 5,
}

_enabled = False


def scrub(text):
    """`text` with secrets masked (redact), then every email address and phone number; anything that
    is not a string is returned as it is."""
    if not isinstance(text, str) or not text:
        return text
    text = redact(text)
    text = WHATSAPP_ID_RE.sub(PHONE_MASK, text)
    text = EMAIL_RE.sub(lambda m: m.group(0) if m.group(0).startswith(PHONE_MASK) else EMAIL_MASK, text)
    return PHONE_RE.sub(PHONE_MASK, text)


def _scrub_all(value, depth: int = 0):
    """Every string inside an event (dicts, lists and tuples, at any depth) scrubbed, keys included."""
    if depth > 40:
        return value
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {scrub(k) if isinstance(k, str) else k: _scrub_all(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_scrub_all(v, depth + 1) for v in value)
    return value


# What the host itself named when it called init(): never a person's, and Sentry finds a release's commits and deploys by
# it, so it goes out as it was. A release named by its date and time ('20261123-2351+6a18bb6') is twelve digits and a dash,
# which the phone pattern took for a number, so every event named a release no deploy had marked (SMADPickleBot #2313).
OWN_FIELDS = ('release', 'dist', 'environment')
OWN_ATTRIBUTES = ('sentry.release', 'sentry.dist', 'sentry.environment')


def _scrub_event(event):
    """The event scrubbed, but for the fields of OWN_FIELDS (a trace's in `contexts` and the tags are scrubbed as ever)."""
    out = _scrub_all(event)
    if isinstance(event, dict):
        out.update({k: event[k] for k in OWN_FIELDS if k in event})
    return out


_drops = []


def drop_events_when(predicate) -> None:
    """Register `predicate(event, hint) -> bool`: an error event for which any registered predicate is true is not
    sent (0.31.0). For a host that knows an error is not news, which the SDK cannot: a read that failed on a dropped
    database connection and is about to be run once more (SMADPickleBot #1454: the retry answered 200, and Sentry
    still filed the first attempt as an issue and an alert). A span is never dropped, and a predicate that
    raises drops nothing: a broken filter must not hide errors. Registering the same function twice keeps one."""
    if predicate not in _drops:
        _drops.append(predicate)


UNSCRUBBED = 'monitoring: this could not be scrubbed, so its contents were left out'


def before_send(event, hint=None):
    """Sentry's before_send: the event with every string scrubbed, or None (not sent) for an error event a host's
    drop_events_when predicate names. An event the scrub itself fails on is sent as its id, time, level, release,
    environment and tags alone: the error is still heard of, and nothing unscrubbed leaves."""
    if isinstance(event, dict):
        for predicate in list(_drops):
            try:
                if predicate(event, hint):
                    return None
            except Exception:   # noqa: BLE001 - a filter that fails hides nothing
                log.warning('monitoring: a drop_events_when predicate raised; the event is sent')
    try:
        return _scrub_event(event)
    except Exception:   # noqa: BLE001 - 3.0 drops the event of a hook that raises
        log.warning('monitoring: an event could not be scrubbed; it is sent without its contents')
        kept = ('event_id', 'timestamp', 'level', 'platform', 'release', 'environment', 'tags')
        return {**{k: event[k] for k in kept if k in event}, 'message': UNSCRUBBED}


def before_send_span(span, hint=None):
    """Sentry's before_send_span: the span's name and attributes scrubbed, the two parts the SDK lets a hook change. One
    the scrub fails on goes out with neither: the SDK sends a span whose hook raised as it was."""
    try:
        out = _scrub_all(span)
        was = span.get('attributes') if isinstance(span, dict) else None
        if was:
            out['attributes'].update({k: was[k] for k in OWN_ATTRIBUTES if k in was})
        return out
    except Exception:   # noqa: BLE001 - never as it was
        return {'name': UNSCRUBBED, 'attributes': {}}


def before_breadcrumb(crumb, hint=None):
    """Sentry's before_breadcrumb: the breadcrumb with every string scrubbed; one the scrub fails on is left out."""
    try:
        return _scrub_all(crumb)
    except Exception:   # noqa: BLE001
        return None


def init(dsn: str, environment: str, release: str = '', service: str = '',
         traces_sample_rate: float = 1.0, sdk=None) -> bool:
    """Start sending errors and traces to Sentry; True when it did. A no-op (False) without a DSN, or
    without sentry_sdk installed (one WARNING). `service` is a tag on every event, so one Sentry project
    can hold several processes (each Cloud Function and Job). `sdk` is the test seam."""
    global _enabled
    dsn = (dsn or '').strip()
    if not dsn:
        _enabled = False
        return False
    if sdk is None:
        try:
            import sentry_sdk as sdk
        except ImportError:
            log.warning('monitoring: a Sentry DSN is set but sentry_sdk is not installed: sending nothing')
            _enabled = False
            return False
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.integrations.unraisablehook import UnraisablehookIntegration
    sdk.init(dsn=dsn, environment=environment or 'production', release=release or None,
             traces_sample_rate=traces_sample_rate, data_collection=DATA_COLLECTION, max_request_body_size='never',
             # A logged message's issue is grouped by its words, as it was: with a stack attached (3.0's default) two
             # different messages from one line of code would be one issue.
             attach_stacktrace=False,
             before_send=before_send, before_send_span=before_send_span, before_breadcrumb=before_breadcrumb,
             # `level` None: no line is sent as a Sentry log (3.0's default sends INFO up); `event_level` by name, since
             # 3.0's default makes no event of an ERROR line and every alert rests on it.
             integrations=[LoggingIntegration(level=None, event_level=logging.ERROR, breadcrumb_level=logging.INFO)],
             disabled_integrations=[UnraisablehookIntegration()])
    if service:
        sdk.set_tag('service', service)
        # 3's spans take no tag of a scope's, so a slow trace and the performance hint made of it said no service (0.54.2).
        sdk.get_global_scope().set_attribute('service', service)
    # The transport's own retries are not the host's news: on Cloud Run, where CPU is throttled between
    # requests, they logged ~636 SSL-retry WARNINGs a day (SMADPickleBot, Feb 2026). Hosts call flush().
    for name in ('sentry_sdk', 'sentry_sdk.errors', 'urllib3.connectionpool'):
        logging.getLogger(name).setLevel(logging.ERROR)
    _enabled = True
    return True


def flush(timeout: float = 2.0, sdk=None) -> None:
    """Send what is queued before the request, message or job ends. On Cloud Run the CPU is throttled
    once a response is returned, so an event left in the queue is sent late, or retried, or lost;
    call this at the end of each unit of work. A no-op when monitoring is off."""
    if not _enabled:
        return
    if sdk is None:
        import sentry_sdk as sdk
    sdk.flush(timeout=timeout)


def enabled() -> bool:
    """Whether init() turned sending on in this process."""
    return _enabled


def capture(exc: BaseException, sdk=None, **context):
    """Report an exception the caller handled (one that never reaches an ERROR log line), with
    `context` as scrubbed extra data; the event id, or None when monitoring is off."""
    if not _enabled:
        return None
    if sdk is None:
        import sentry_sdk as sdk
    with sdk.new_scope() as scope:
        for k, v in context.items():
            scope.set_extra(k, _scrub_all(v))
        return sdk.capture_exception(exc)


@contextlib.contextmanager
def span(name: str, op: str = 'function', sdk=None):
    """Time a block as a child of the current trace (a query batch, a sheet write, a send); yields the
    span, or None when monitoring is off. An exception still propagates."""
    if not _enabled:
        yield None
        return
    if sdk is None:
        import sentry_sdk as sdk
    with sdk.start_span(name=name, attributes={'sentry.op': op}) as s:
        yield s


@contextlib.contextmanager
def transaction(name: str, op: str = 'task', sdk=None):
    """A unit of work of its own, outside any web request (a queue message, a scheduled job's step):
    its own trace, with the spans inside it; yields it, or None when monitoring is off."""
    if not _enabled:
        yield None
        return
    if sdk is None:
        import sentry_sdk as sdk
    # Its own scope, so the new trace it starts is not left on the thread for the next unit of work to inherit.
    with sdk.isolation_scope():
        sdk.new_trace()
        with sdk.start_span(name=name, attributes={'sentry.op': op}, parent_span=None) as t:
            yield t
