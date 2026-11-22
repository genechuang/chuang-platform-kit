#!/usr/bin/env python3
"""chuang_sdlc.release_train.Lease: one push at a time to a shared branch (SMADPickleBot #2159, from its dock push).

Pinned (the positive case first): a free lease is taken and freed; a held one is waited for and taken once its holder
frees it; a lease past its `until` is taken over; a holder past `wait` gives up; renew stops when another run holds
it; free never deletes another run's; an unreadable store pushes blind and still frees its own lease; no store answers
True everywhere; the host's collection, id, lease length, tag and branch reach the document and the lines;
parse_instant reads Z, naive and junk.

Run:  python -B tests/test-sdlc-release-train.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))

from chuang_sdlc import release_train as R  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


NOW = datetime(2026, 11, 21, 23, 0, tzinfo=timezone.utc)   # utc-ok: a fixed instant for the lease's clock
KEY = ('push_leases', 'host')


class Store:
    def __init__(self, doc=None, broken=False):
        self.docs = {KEY: dict(doc)} if doc else {}
        self.broken = broken

    def create(self, c, k, data):
        if self.broken:
            raise OSError('down')
        if (c, k) in self.docs:
            return False
        self.docs[(c, k)] = dict(data)
        return True

    def get(self, c, k):
        return self.docs.get((c, k))

    def set(self, c, k, data):
        self.docs[(c, k)] = dict(data)

    def delete(self, c, k):
        self.docs.pop((c, k), None)


def lease(store, now=NOW, **kw):
    return R.Lease(store, 'Snow White', 'push_leases', 'host', 600, now=lambda: now, **kw)


def other(minutes=5, session='Black Widow'):
    return {'session': session, 'run': 'theirs', 'sha': 'abcdef1234', 'until': (NOW + timedelta(minutes=minutes)).isoformat()}


free = lease(Store())
took = free.take('a1b2c3d4')
doc = dict(free.store.get(*KEY))
free.free()
check("the positive case: a free lease is taken with this run's token, its until lease_s on, and freed at the end",
      (took, doc['session'], doc['run'] == free.run, doc['until'], free.store.get(*KEY)),
      (True, 'Snow White', True, (NOW + timedelta(seconds=600)).isoformat(), None))

held, lines = Store(other()), []


def nap(s):
    held.delete(*KEY)   # the holder lands while this run waits


waiter = lease(held, sleep=nap, say=lines.append, tag='[push]', branch='staging')
check("a held lease is waited for (one line naming the holder, its sha and the host's branch) and taken once freed",
      (waiter.take('b'), held.get(*KEY)['run'] == waiter.run, lines),
      (True, True, ['[push]: Black Widow is pushing abcdef12 to staging; waiting for it to land']))

stale = Store(other(-1))
check("a lease past its until (a killed run's) is deleted and taken by this run",
      (lease(stale).take('c'), stale.get(*KEY)['session']), (True, 'Snow White'))

lines = []
check("a holder past wait gives up, said once with the bound",
      (lease(Store(other(60)), wait=20, poll=10, sleep=lambda s: None, say=lines.append).take('d'), lines[-1]),
      (False, "dock-push: Black Widow has held dock's push lease past 20 s; giving up the wait"))

stolen, lines = Store(), []
thief = lease(stolen, say=lines.append)
thief.take('e')
stolen.set(*KEY, other())
check("renew stops when another run holds the lease now; free leaves that run's lease alone",
      (thief.renew('e'), lines[-1], (thief.free(), stolen.get(*KEY)['run'])[1]),
      (False, "dock-push: the lease is Black Widow's now; this run stops", 'theirs'))

mine = lease(Store())
mine.take('f')
before = mine.store.get(*KEY)['until']
mine.now = lambda: NOW + timedelta(minutes=8)
check("renew pushes until on while the lease is this run's",
      (mine.renew('f'), before != mine.store.get(*KEY)['until']), (True, True))

flaky, lines = Store(broken=True), []
blind = lease(flaky, say=lines.append)
blind_took = blind.take('g')
flaky.docs[KEY] = {'run': blind.run}   # the timed-out create was written after all
blind.free()
check("an unreadable store pushes blind: no renew write, and free still deletes a lease naming this run",
      (blind_took, blind.blind, blind.renew('g'), flaky.get(*KEY), lines[0]),
      (True, True, True, None, 'dock-push: the lease could not be read (OSError); pushing without one'))

none = lease(None)
check("no store: take and renew answer True and free writes nothing",
      (none.take('h'), none.renew('h'), none.free()), (True, True, None))

check("parse_instant: a Z string, a naive string read as UTC, junk and empty",
      (R.parse_instant('2026-11-21T23:00:00Z'), R.parse_instant('2026-11-21T23:00:00'), R.parse_instant('x'),
       R.parse_instant('')),
      (NOW, NOW, None, None))

print()
print(f"{len(fails)} failure(s)" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
