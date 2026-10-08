#!/usr/bin/env python3
"""chuang_platform_kit.monitoring: errors and traces to Sentry, scrubbed, and nothing at all without a DSN.

Pinned (the positive case first): init() with a DSN starts the SDK with PII off, the scrubbers wired and
the service tag set; with no DSN it is a no-op and capture/span/transaction do nothing; before_send masks
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

    def start_span(self, op, name):
        self.spans.append((op, name))
        return FakeScope(self)

    def start_transaction(self, op, name):
        self.spans.append(('tx:' + op, name))
        return FakeScope(self)

    def flush(self, timeout=None):
        self.spans.append(('flush', timeout))


print("\n-- init: on with a DSN, off without")
sdk = FakeSdk()
on = M.init('https://k@o1.ingest.sentry.io/2', 'production', release='abc1234', service='api', sdk=sdk)
kw = sdk.inits[0]
check("the positive case: a DSN starts the SDK, PII off, the scrubbers wired, the service tagged",
      (on, M.enabled(), kw['send_default_pii'], kw['before_send'] is M.before_send, kw['before_breadcrumb'] is M.before_breadcrumb,
       kw['before_send_transaction'] is M.before_send, kw['release'], kw['environment'], sdk.tags),
      (True, True, False, True, True, True, 'abc1234', 'production', {'service': 'api'}))
with M.span('sheet write', sdk=sdk):
    pass
with M.transaction('vote message', op='queue', sdk=sdk):
    pass
M.flush(sdk=sdk)
check("span and transaction open on the SDK; flush sends the queue (Cloud Run throttles CPU between requests)",
      sdk.spans, [('function', 'sheet write'), ('tx:queue', 'vote message'), ('flush', 2.0)])
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

print("\n-- the real SDK, with an in-memory transport: what leaves is scrubbed")
try:
    import sentry_sdk
    from sentry_sdk.transport import Transport

    sent = []

    class Memory(Transport):
        def __init__(self, options=None):
            super().__init__(options)

        def capture_envelope(self, envelope):
            for item in envelope.items:
                if item.payload.json is not None:
                    sent.append(item.payload.json)

    real = sentry_sdk.init  # init() passes its own kwargs; wrap to add the in-memory transport

    class WithTransport:
        def __getattr__(self, name):
            return getattr(sentry_sdk, name)

        def init(self, **kw):
            real(transport=Memory, **kw)

    M.init('https://k@o1.ingest.sentry.io/2', 'test', service='kit-test', sdk=WithTransport())
    import logging
    logging.getLogger('kit-test').error('could not reach carl@example.com at 626.555.0104')
    sentry_sdk.flush()
    msgs = [e.get('logentry', {}).get('message') or e.get('message') for e in sent if e.get('type') != 'transaction']
    check("an ERROR log line becomes one event, with the email and phone masked, PII off",
          (len(msgs), msgs[0] if msgs else None, any('carl@' in repr(e) for e in sent), sent[0].get('user') if sent else None),
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
