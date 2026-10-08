#!/usr/bin/env python3
"""chuang_platform_kit.config: a versioned configuration document, cached, with a change signal.

Pinned (the positive case first): one fetch serves every read inside the ttl, a dotted key and the
code's defaults; after the ttl the source is asked with the held version and an unchanged answer
keeps the document; mark_dirty() reads again at once and on_change hears a new version; a source
that raises keeps the last document, marks it stale and waits retry_after; the fallback answers before
any document and disagrees at WARNING once; typed() coerces; etag()/unchanged() answer a conditional
GET; the OpenFeature provider resolves typed flags with the version as the variant (when the SDK is
installed).

Run:  python -B tests/test-config.py
"""
import logging
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import config as C  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Source:
    """A fake authority: `docs` is what it would answer; `calls` the versions it was asked with."""

    def __init__(self, version, data):
        self.version, self.data, self.calls, self.down = version, data, [], False

    def __call__(self, held):
        self.calls.append(held)
        if self.down:
            raise ConnectionError('source unreachable')
        return None if held == self.version else C.Document(self.version, self.data)


logs = []
handler = logging.Handler()
handler.emit = lambda rec: logs.append((rec.levelname, rec.getMessage()))
C.log.addHandler(handler)

print("the positive case: one fetch serves every read inside the ttl, a dotted key, the code's defaults")
clock, src = Clock(), Source('v1', {'booking': {'target_time': '00:01:00'}, 'hourly_rate': 5, 'flag': 'true'})
cfg = C.Config(src, defaults={'session_hours': 2, 'hourly_rate': 0}, ttl=300, clock=clock)
reads = (cfg.get('booking.target_time'), cfg.get('hourly_rate'), cfg.get('session_hours'), cfg.get('nothing', 'dflt'))
check("reads", reads, ('00:01:00', 5, 2, 'dflt'))
check("one fetch, with no version held", (src.calls, cfg.version, cfg.stale), ([''], 'v1', False))
try:
    cfg.get('nothing')
    missing = 'no error'
except KeyError as e:
    missing = str(e)
check("a key nobody has is a KeyError", missing, "'nothing'")
check("snapshot() is the defaults under the document", cfg.snapshot(),
      {'session_hours': 2, 'hourly_rate': 5, 'booking': {'target_time': '00:01:00'}, 'flag': 'true'})

print("after the ttl the source is asked with the held version; unchanged keeps the document")
clock.t += 301
check("asked with v1, unchanged", (cfg.get('hourly_rate'), src.calls[-1], cfg.version), (5, 'v1', 'v1'))
src.version, src.data = 'v2', {'hourly_rate': 6}
check("no read before the next ttl", (cfg.get('hourly_rate'), len(src.calls)), (5, 2))

print("mark_dirty() reads again at once; on_change hears the new version")
heard = []
cfg.on_change(lambda old, new: heard.append((old, new)))
cfg.mark_dirty()
check("dirty: the new document", (cfg.get('hourly_rate'), cfg.version, heard), (6, 'v2', [('v1', 'v2')]))
check("refresh(force=True) answers whether a new document was taken", (cfg.refresh(force=True), cfg.refresh()), (False, False))

print("a source that raises keeps the last document, marks it stale, waits retry_after")
src.down = True
clock.t += 301
n = len(src.calls)
check("stale, the document stands", (cfg.get('hourly_rate'), cfg.stale, len(src.calls) - n), (6, True, 1))
check("no retry inside retry_after", (cfg.get('hourly_rate'), len(src.calls) - n), (6, 1))
clock.t += 31
check("a retry after it", (cfg.get('hourly_rate'), len(src.calls) - n), (6, 2))
check("one WARNING for the outage", [m for lvl, m in logs if lvl == 'WARNING' and 'not read' in m],
      ["config: the configuration was not read (ConnectionError: source unreachable); version v2 stands"])
src.down = False
clock.t += 31
check("back: fresh again", (cfg.get('hourly_rate'), cfg.stale), (6, False))

print("the fallback answers before any document, and disagrees at WARNING once")
logs.clear()
env = {'HOURLY_RATE': '5', 'hourly_rate': '5'}
src2 = Source('r1', {'hourly_rate': 6})
src2.down = True
cfg2 = C.Config(src2, defaults={'hourly_rate': 0}, fallback=env, ttl=300, retry_after=30, clock=clock, name='league')
check("the fallback, no document", (cfg2.get('hourly_rate'), cfg2.version, cfg2.stale), ('5', '', True))
src2.down = False
clock.t += 31
check("the document, then a WARNING once", (cfg2.get('hourly_rate'), cfg2.get('hourly_rate'),
                                            [m for lvl, m in logs if lvl == 'WARNING' and 'wins' in m]),
      (6, 6, ["league: hourly_rate is 6 in the document and '5' in the fallback; the document wins"]))
check("a callable fallback", C.Config(lambda held: None, fallback=lambda: {'a': 1}).get('a'), 1)

print("typed() coerces")
cfg3 = C.Config(Source('t', {'flag': 'yes', 'n': '7', 'bad': 'x', 'f': '2.5'}), clock=clock)
check("typed", (cfg3.typed('flag', bool), cfg3.typed('n', int), cfg3.typed('f', float), cfg3.typed('bad', int, 0),
                cfg3.typed('missing', bool, False)), (True, 7, 2.5, 0, False))
try:
    cfg3.typed('bad', int)
    bad = 'no error'
except ValueError as e:
    bad = str(e)
check("typed without a default raises", bad, "config: bad is 'x', not int")

print("etag() and unchanged() answer a conditional GET")
check("etag", C.etag('2026-10-08T10:00:00Z'), '"2026-10-08T10:00:00Z"')
check("unchanged", (C.unchanged('"v2"', 'v2'), C.unchanged('W/"v2"', 'v2'), C.unchanged('"v1", "v2"', 'v2'), C.unchanged('*', 'v2'),
                    C.unchanged('"v1"', 'v2'), C.unchanged('', 'v2'), C.unchanged('"v2"', '')),
      (True, True, True, True, False, False, False))

print("lookup() reads a dotted path and a flat key alike")
check("lookup", (C.lookup({'a': {'b': 1}}, 'a.b'), C.lookup({'a.b': 2}, 'a.b'), C.lookup({'a': 1}, 'a.b', 'd'), C.lookup({}, 'x', None)),
      (1, 2, 'd', None))

print("the OpenFeature provider resolves typed flags with the version as the variant")
try:
    from openfeature import api as of_api
    from openfeature.flag_evaluation import Reason
    cfg4 = C.Config(Source('p9', {'splash': 'peek', 'seconds': 4, 'on': 'true', 'ratio': '0.5', 'bad': 'x'}), clock=clock)
    of_api.set_provider(C.provider(cfg4, name='test-document'))
    client = of_api.get_client()
    details = client.get_string_details('splash', 'serve')
    check("a string flag", (details.value, details.reason, details.variant), ('peek', Reason.STATIC, 'p9'))
    check("typed flags", (client.get_boolean_value('on', False), client.get_integer_value('seconds', 1), client.get_float_value('ratio', 0.0)),
          (True, 4, 0.5))
    check("a missing flag is the default", (client.get_string_details('nope', 'dflt').value, client.get_string_details('nope', 'dflt').reason),
          ('dflt', Reason.DEFAULT))
    check("a type mismatch is the default with the error", (client.get_integer_details('bad', 1).value, client.get_integer_details('bad', 1).reason),
          (1, Reason.ERROR))
except ImportError:
    print("  (openfeature-sdk not installed: the provider case is skipped)")

C.log.removeHandler(handler)
print()
print(f"{len(fails)} failure(s)" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
