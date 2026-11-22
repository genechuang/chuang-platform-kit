#!/usr/bin/env python3
"""chuang_platform_kit.standby: a desktop worker with a cloud standby on one lease document (SMADPickleBot #2158).

Pinned (the positive case first): claim creates once; beat writes while the desktop holds and names the holder
otherwise; decide reads take / book / stand down / wait; take writes the cloud's document; gate stands by on a fresh
beat, takes a stale one, stands down on a good end, grabs on an unreadable store after stale_s and with no store.

Run:  python -B tests/test-standby.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import standby as S  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class Store:
    def __init__(self, docs=None, broken=False):
        self.docs, self.broken = dict(docs or {}), broken

    def create(self, c, k, data):
        if (c, k) in self.docs:
            return False
        self.docs[(c, k)] = dict(data)
        return True

    def get(self, c, k):
        if self.broken:
            raise OSError('down')
        return self.docs.get((c, k))

    def merge(self, c, k, data):
        self.docs.setdefault((c, k), {}).update(data)

    def set(self, c, k, data):
        self.docs[(c, k)] = dict(data)


T0 = datetime(2026, 11, 21, 7, 58, tzinfo=timezone.utc)   # utc-ok: a fixed instant for the lease's clock
L = S.Lease('jobs', what='the night', tag='[STANDBY]')
store = Store()
first, again = L.claim(store, 'n1', T0), L.claim(store, 'n1', T0)
check("the positive case: claim creates the document once, the desktop its holder, and beat writes while it holds",
      (first, again, store.docs[('jobs', 'n1')]['holder'], L.beat(store, 'n1', T0 + timedelta(seconds=10)),
       store.docs[('jobs', 'n1')]['beat_at'], L.beat(store, 'none', T0)),
      (True, False, 'desktop', '', T0 + timedelta(seconds=10), 'none'))
fresh = {'holder': 'desktop', 'beat_at': (T0 - timedelta(seconds=5)).isoformat()}
check("decide: no document, a cloud holder, a good end, a bad end, a stale beat, a fresh beat, a clock far ahead",
      [L.decide(d, T0)[0] for d in (None, {'holder': 'cloud'}, {'holder': 'desktop', 'done_at': T0, 'code': 0},
                                    {'holder': 'desktop', 'done_at': T0, 'code': 2},
                                    {'holder': 'desktop', 'beat_at': T0 - timedelta(seconds=31)}, fresh,
                                    {'holder': 'desktop', 'beat_at': T0 + timedelta(seconds=31)})],
      ['take', 'book', 'stand down', 'take', 'take', 'wait', 'take'])
lines, t = [], [T0]


def clock():
    t[0] = t[0] + timedelta(seconds=5)
    return t[0]


s2 = Store({('jobs', 'k'): {'holder': 'desktop', 'beat_at': T0}})
ran = L.gate(s2, 'k', 'book', T0 + timedelta(minutes=5), now=clock, sleep=lambda s: None, log=lines.append)
check("gate: a desktop that stops beating is taken once its beat is stale_s old, and the cloud's document written",
      (ran, s2.docs[('jobs', 'k')]['holder'], s2.docs[('jobs', 'k')]['was'], lines[0].endswith('standing by'),
       'the cloud took the night, grabbing' in lines[-1]),
      (True, 'cloud', 'desktop', True, True))
lines.clear()
good = Store({('jobs', 'k'): {'holder': 'desktop', 'beat_at': T0, 'done_at': T0, 'code': 0}})
broken, t = Store(broken=True), [T0]
check("gate: a good end stands down; an unreadable store grabs after stale_s; no store grabs at once",
      (L.gate(good, 'k', 'book', T0, now=lambda: T0, sleep=lambda s: None, log=lines.append),
       L.gate(broken, 'k', 'book', T0 + timedelta(minutes=5), now=clock, sleep=lambda s: None, log=lines.append),
       L.gate(None, 'k', 'book', T0, log=lines.append), lines[-1]),
      (False, True, True, '[STANDBY] book: no Firestore client; grabbing'))
check("aware: a naive datetime is UTC, an ISO Z string reads, junk is None",
      (S.aware(datetime(2026, 1, 1)).tzinfo, S.aware('2026-11-21T07:58:00Z'), S.aware('x'), S.aware(3)),
      (timezone.utc, T0, None, None))   # utc-ok: the zone a naive instant is read in

print()
print(f"{len(fails)} failure(s)" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
