"""Errors and traces to Sentry, behind functions of our own: the one module that imports sentry_sdk.

A host calls `init()` once at start-up with its DSN, environment, release and service name; without a
DSN (local runs, tests, a host that has not set one up) it is a no-op and nothing is ever sent. After
that the logging integration turns every ERROR record into an issue and INFO/WARNING into breadcrumbs,
and the web-framework and database integrations Sentry enables when it finds them (FastAPI, SQLAlchemy)
trace requests and queries. Callers use `capture()`, `span()`, `transaction()` and `flush()` (at the end
of each request, message or job, since Cloud Run throttles CPU between requests), never sentry_sdk,
so the vendor can be swapped by changing this file alone.

Nothing private leaves: every event, transaction and breadcrumb passes `scrub()` first, which is
`redact.redact()` (tokens, keys, the process's secret values) plus email addresses and phone numbers;
`send_default_pii` stays off, so no IP, cookie or user header is attached either.

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


def before_send(event, hint=None):
    """Sentry's before_send (and before_send_transaction): the event with every string scrubbed."""
    return _scrub_all(event)


def before_breadcrumb(crumb, hint=None):
    """Sentry's before_breadcrumb: the breadcrumb with every string scrubbed."""
    return _scrub_all(crumb)


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
    sdk.init(dsn=dsn, environment=environment or 'production', release=release or None,
             traces_sample_rate=traces_sample_rate, send_default_pii=False,
             before_send=before_send, before_send_transaction=before_send, before_breadcrumb=before_breadcrumb,
             integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)])
    if service:
        sdk.set_tag('service', service)
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
    with sdk.start_span(op=op, name=name) as s:
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
    with sdk.start_transaction(op=op, name=name) as t:
        yield t
