#!/usr/bin/env python3
"""chuang_platform_kit.monitoring: errors and traces to Sentry, scrubbed, and nothing at all without a DSN.

Pinned (the positive case first): init() with a DSN starts the SDK (3.x, 0.54.0) with its data collection restricted,
an ERROR line an event and no line a Sentry log, the scrubbers wired and the service tag set; with no DSN it is a no-op and capture/span/transaction do nothing; before_send masks
a token, an email and a phone number anywhere in an event; ordinary numbers and text pass; capture()
attaches scrubbed context; a real sentry_sdk with an in-memory transport receives the scrubbed event.

Run:  python -B tests/test-monitoring.py
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import monitoring as M  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class FakeScope:
    def __init__(self, sdk):
        self.sdk = sdk

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def set_extra(self, k, v):
        self.sdk.extras[k] = v


class FakeSdk:
    def __init__(self):
        self.inits, self.tags, self.extras, self.captured, self.spans = [], {}, {}, [], []

    def init(self, **kw):
        self.inits.append(kw)

    def set_tag(self, k, v):
        self.tags[k] = v

    def new_scope(self):
        return FakeScope(self)

    def capture_exception(self, exc):
        self.captured.append(exc)
        return 'evt-1'

    def start_span(self, name, attributes, parent_span='current'):
        self.spans.append(((('tx:' if parent_span is None else '') + attributes['sentry.op']), name))
        return FakeScope(self)

    def isolation_scope(self):
        self.spans.append('isolated')
        return FakeScope(self)

    def new_trace(self):
        self.spans.append('new trace')

    def flush(self, timeout=None):
        self.spans.append(('flush', timeout))


print("\n-- init: on with a DSN, off without")
sdk = FakeSdk()
on = M.init('https://k@o1.ingest.sentry.io/2', 'production', release='abc1234', service='api', sdk=sdk)
kw = sdk.inits[0]
check("the positive case: a DSN starts the SDK, what it may collect restricted, the scrubbers wired, the service tagged",
      (on, M.enabled(), kw['data_collection'] is M.DATA_COLLECTION, kw['before_send'] is M.before_send, kw['before_breadcrumb'] is M.before_breadcrumb,
       kw['before_send_span'] is M.before_send_span, kw['release'], kw['environment'], sdk.tags),
      (True, True, True, True, True, True, 'abc1234', 'production', {'service': 'api'}))
check("  nothing of 2.x's is passed (3.x refuses an option it does not know), no body is read, a message's issue keeps its words",
      ([k for k in ('send_default_pii', 'before_send_transaction', 'enable_logs') if k in kw], kw['max_request_body_size'], kw['attach_stacktrace']),
      ([], 'never', False))
with M.span('sheet write', sdk=sdk):
    pass
with M.transaction('vote message', op='queue', sdk=sdk):
    pass
M.flush(sdk=sdk)
check("span and transaction open on the SDK; flush sends the queue (Cloud Run throttles CPU between requests)",
      sdk.spans, [('function', 'sheet write'), 'isolated', 'new trace', ('tx:queue', 'vote message'), ('flush', 2.0)])
import logging  # noqa: E402
check("the transport's loggers are quieted to ERROR (its SSL retries were ~636 WARNINGs a day on Cloud Run, Feb 2026)",
      [logging.getLogger(n).level for n in ('sentry_sdk', 'sentry_sdk.errors', 'urllib3.connectionpool')],
      [logging.ERROR] * 3)
eid = M.capture(ValueError('boom'), sdk=sdk, member='ann@example.com', note='call 626-555-0104')
check("capture reports the exception with scrubbed context", (eid, type(sdk.captured[0]).__name__, sdk.extras),
      ('evt-1', 'ValueError', {'member': '[email]', 'note': 'call [phone]'}))

sdk2 = FakeSdk()
off = M.init('', 'production', sdk=sdk2)
with M.span('x', sdk=sdk2) as s, M.transaction('y', sdk=sdk2) as t:
    pass
check("no DSN: a no-op; nothing starts, capture/span/transaction do nothing",
      (off, M.enabled(), sdk2.inits, M.capture(ValueError('x'), sdk=sdk2), s, t, M.flush(sdk=sdk2) or sdk2.spans, sdk2.captured),
      (False, False, [], None, None, None, [], []))
check("a blank or spaces-only DSN is no DSN", M.init('   ', 'production', sdk=FakeSdk()), False)

print("\n-- scrub: secrets, emails and phones, anywhere in an event")
os.environ['MONITORING_TEST_TOKEN'] = 'tok_live_9f8e7d6c5b4a39281706'
event = {'message': 'login for ann.lee@gmail.com failed',
         'exception': {'values': [{'value': 'GREEN-API https://api.green-api.com/waInstance1/sendMessage/tok_live_9f8e7d6c5b4a39281706'}]},
         'breadcrumbs': {'values': [{'message': 'Processing message from 16265550104@c.us'},
                                    {'message': 'texted (626) 555-0104 and +1 626 555 0104'}]},
         'extra': {'ann.lee@gmail.com': 'key as email', 'count': 42, 'score': '11-9', 'year': '2026', 'amount': '$38.50'}}
out = M.before_send(event)
text = repr(out)
check("an email is masked", ('ann.lee@gmail.com' in text, out['message']), (False, 'login for [email] failed'))
check("a secret value is masked (redact)", 'tok_live_9f8e7d6c5b4a39281706' in text, False)
check("phone numbers in any shape are masked, a WhatsApp id's digits too",
      ([b['message'] for b in out['breadcrumbs']['values']]),
      (['Processing message from [phone]@c.us', 'texted [phone] and [phone]']))
check("ordinary numbers pass: a count, a score, a year, an amount; keys are scrubbed too",
      out['extra'], {'[email]': 'key as email', 'count': 42, 'score': '11-9', 'year': '2026', 'amount': '$38.50'})
check("a breadcrumb goes through the same scrub", M.before_breadcrumb({'message': 'to bob@x.org'})['message'], 'to [email]')
check("non-strings and empties pass untouched", (M.scrub(None), M.scrub(''), M.scrub(5)), (None, '', 5))
del os.environ['MONITORING_TEST_TOKEN']

print("\n-- drop_events_when (0.31.0): a host says which errors are not news")
retried = {'on': True}
drop = lambda event, hint=None: retried['on']   # noqa: E731
M.drop_events_when(drop)
M.drop_events_when(drop)   # the same function twice is one
check("the positive case: an error event the host's predicate names is not sent",
      (M.before_send({'message': 'the games could not be read'}), len(M._drops)), (None, 1))
retried['on'] = False
check("an event it does not name is sent, scrubbed as ever",
      M.before_send({'message': 'failed for ann.lee@gmail.com'}), {'message': 'failed for [email]'})
retried['on'] = True
check("a span is never dropped, and is scrubbed", M.before_send_span({'name': 'GET /v1/games', 'attributes': {'who': 'bob@x.org', 'n': 3}}),
      {'name': 'GET /v1/games', 'attributes': {'who': '[email]', 'n': 3}})
M._drops[:] = [lambda event, hint=None: 1 / 0]
check("a predicate that raises drops nothing: a broken filter hides no error",
      M.before_send({'message': 'still sent'}), {'message': 'still sent'})
M._drops[:] = []

print("\n-- a scrub that fails sends the fact, never the contents (0.54.0: 3.0 drops the event of a hook that raises, and sends a span as it was)")
whole = M._scrub_all
M._scrub_all = lambda *a, **k: 1 / 0
failed = M.before_send({'event_id': 'e1', 'level': 'error', 'tags': {'service': 'api'}, 'message': 'call 626-555-0104', 'extra': {'x': 'bob@x.org'}})
span, crumb = M.before_send_span({'name': 'bob@x.org', 'attributes': {'x': 'bob@x.org'}}), M.before_breadcrumb({'message': 'bob@x.org'})
M._scrub_all = whole
check("the event as its id, level and tags; the span as neither name nor attributes; the breadcrumb left out", (failed, span, crumb),
      ({'event_id': 'e1', 'level': 'error', 'tags': {'service': 'api'}, 'message': M.UNSCRUBBED}, {'name': M.UNSCRUBBED, 'attributes': {}}, None))

print("\n-- the real SDK, with an in-memory transport: what leaves is scrubbed")
try:
    import sentry_sdk
    from sentry_sdk.transport import Transport

    sent, kinds, whole = [], [], []

    class Memory(Transport):
        def __init__(self, options=None):
            super().__init__(options)

        def capture_envelope(self, envelope):
            for item in envelope.items:
                kinds.append(item.headers.get('type'))
                if item.payload.json is not None:
                    sent.append(item.payload.json)
                    whole.append(item.payload.json)

    real = sentry_sdk.init  # init() passes its own kwargs; wrap to add the in-memory transport

    class WithTransport:
        def __getattr__(self, name):
            return getattr(sentry_sdk, name)

        def init(self, **kw):
            real(transport=Memory, **kw)

    M.init('https://k@o1.ingest.sentry.io/2', 'test', service='kit-test', sdk=WithTransport())
    import logging
    logging.getLogger('kit-test').info('an ordinary line')
    logging.getLogger('kit-test').error('could not reach carl@example.com at 626.555.0104')
    with M.transaction('job for carl@example.com', op='task'):
        with M.span('step', op='db'):
            pass
    def lookup():
        password, plain = 'hunter2-hunter2', 'a plain value'
        raise ValueError(f'no such row {len(password) + len(plain)}')
    try:
        lookup()
    except ValueError as e:
        M.capture(e)
    sentry_sdk.flush()
    spans = [i for p in sent for i in (p.get('items') or [])]
    sent = [p for p in sent if 'items' not in p]
    msgs = [e.get('logentry', {}).get('message') or e.get('message') for e in sent if not e.get('exception')]
    failures = [e for e in sent if e.get('exception')]
    frames = [f for e in failures for x in e['exception']['values'] for f in x['stacktrace']['frames']]
    check("an INFO line is sent as nothing: no Sentry log, no event", ([k for k in kinds if k == 'log'], len(msgs)), ([], 1))
    check("a unit of work and its step are two spans of one trace, the work's name scrubbed",
          (sorted(s['name'] for s in spans), len({s['trace_id'] for s in spans}), any('carl@' in repr(s) for s in spans)),
          (['job for [email]', 'step'], 1, False))
    check("a captured exception carries its stack with its source line, and no local variable",
          (len(failures), any(f.get('function') == 'lookup' and 'raise ValueError' in (f.get('context_line') or '') for f in frames),
           [f for f in frames if f.get('vars')]), (1, True, []))
    from sentry_sdk import data_collection as DC
    check("every category the SDK can collect is named in DATA_COLLECTION: a new one fails here, never arrives at its default",
          sorted(DC._resolve({})), sorted(M.DATA_COLLECTION))
    got = DC._resolve(M.DATA_COLLECTION)
    check("  and each is off or filtered",
          (got['user_info'], got['http_bodies'], got['cookies']['mode'], got['database_query_data'], got['queues'],
           got['http_headers']['request']['mode'], got['url_query_params']['mode'], got['stack_frame_variables']),
          (False, [], 'off', False, False, 'allowlist', 'off', False))
    try:
        from fastapi import Body, FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()

        @app.post('/v1/games/{id}/vote')
        def vote(id: int, body: dict = Body(...)):
            raise RuntimeError(f'the vote of game {id} failed')
        before = len(whole)
        TestClient(app, raise_server_exceptions=False).post(
            '/v1/games/7/vote?email=ann@example.com', json={'name': 'Ann Alpha'},
            headers={'Authorization': 'Device dc_' + 'x' * 43, 'X-Forwarded-For': '1.2.3.4', 'Cookie': '__Host-device=dc_' + 'y' * 43,
                     'X-Act-As': 'bob-beta', 'User-Agent': 'SMAD/35'})
        sentry_sdk.flush()
        for e in whole[before:]:   # the source lines around a frame are this file's own, the request's words among them
            for x in (e.get('exception') or {}).get('values') or []:
                for f in (x.get('stacktrace') or {}).get('frames') or []:
                    for k in ('pre_context', 'context_line', 'post_context'):
                        f.pop(k, None)
        all_of_it = repr(whole[before:])
        check("a failed web request read whole: no credential, address, body, query string or person leaves; its route and app do",
              ([w for w in ('dc_', '1.2.3.4', 'Ann Alpha', 'example.com', 'bob-beta') if w in all_of_it],
               [w for w in ('/v1/games/{id}/vote', 'RuntimeError', 'SMAD/35') if w not in all_of_it]), ([], []))
    except ImportError:
        print("  (fastapi not installed: the web request case is skipped)")
    check("an ERROR log line becomes one event, with the email and phone masked, PII off",
          (len(msgs), msgs[0] if msgs else None, any('carl@' in repr(e) for e in sent), (sent[0].get('user') or None) if sent else None),
          (1, 'could not reach [email] at [phone]', False, None))
    check("the event carries the service tag", (sent[0].get('tags') or {}).get('service') if sent else None, 'kit-test')
    M.init('', 'test')
except ImportError:
    print("  (sentry_sdk not installed: the real-SDK case is skipped)")

print()
print(f"{len(fails)} failure(s)" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
