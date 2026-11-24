#!/usr/bin/env python3
"""chuang_platform_kit.answer_cache: answers kept in Firestore, rebuilt at the write and pushed.

Pinned (the positive case first):
  - a write followed by the rebuild pass leaves every reader's next read kept: build() is never called again;
  - a first read builds and keeps; the second is kept; a bump of the version, or of a domain the route reads, ends it,
    and a bump of another domain leaves it;
  - between a write and its pass a read builds for itself (never the answer from before the write);
  - the pass rebuilds only what is not usable, newest asked first, skips a document nobody asked for in keep_days,
    counts a rebuild that raises and leaves its document, stops at its budget and says what it left;
  - pushed() is called once per document and version: for one the pass rebuilt, and for one a reader rebuilt itself;
  - one pass at a time: a second caller is busy; a write during a pass makes it go round once more;
  - an answer too large, one its route says not to keep, or one that has ended is served and never kept;
  - a Firestore that cannot be read costs the built answer, never an error;
  - Bumps: several commits in one request bump once or twice with every domain named; wrote_nothing.

Run:  python -B tests/test-answer-cache.py
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import answer_cache as A  # noqa: E402
from chuang_platform_kit import firestore  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class Store:
    """A Firestore of dicts with the six calls the cache makes."""

    def __init__(self):
        self.docs, self.locked, self.down, self.writes = {}, set(), False, 0
        self.queries, self.read, self.unindexed = [], 0, False   # a pass's queries, the documents they read, no index yet

    def _up(self):
        if self.down:
            raise firestore.Unreachable('503 UNAVAILABLE')

    def batch_get(self, refs):
        self._up()
        return [dict(self.docs[r]) if r in self.docs else None for r in refs]

    def get(self, collection, doc_id):
        self._up()
        return dict(self.docs[(collection, doc_id)]) if (collection, doc_id) in self.docs else None

    def set(self, collection, doc_id, data):
        self._up()
        self.writes += 1
        self.docs[(collection, doc_id)] = dict(data)

    def merge(self, collection, doc_id, data=None, increment=None, **kw):
        self._up()
        doc = self.docs.setdefault((collection, doc_id), {})
        doc.update(data or {})
        for field, by in (increment or {}).items():
            doc[field] = (doc.get(field) or 0) + by

    def run_query(self, collection, where=(), select=None, **kw):
        self._up()
        self.queries.append([(f, op) for f, op, _v in where])
        if self.unindexed and any(op == '>=' for _f, op, _v in where):
            raise firestore.Conflict('HTTP 400 FAILED_PRECONDITION')
        out = []
        for (c, doc_id), doc in self.docs.items():
            if c == collection and all(doc.get(f) == v if op == '==' else (doc.get(f) or '') >= v for f, op, v in where):
                out.append((doc_id, {k: v for k, v in doc.items() if not select or k in select}))
                self.read += 1
        return out

    def lock(self, collection, doc_id, ttl, data=None):
        self._up()
        if (collection, doc_id) in self.locked:
            return False
        self.locked.add((collection, doc_id))
        return True

    def release(self, collection, doc_id):
        self.locked.discard((collection, doc_id))
        return True

    def delete(self, collection, doc_id):
        self._up()
        self.docs.pop((collection, doc_id), None)


NOW = datetime(2026, 11, 19, 20, 0, tzinfo=timezone.utc)  # utc-ok: a fixed instant for the cases, noon Pacific


def fresh(**kw):
    store = Store()
    cache = A.AnswerCache(lambda: store, 'smad', revision='r1', **kw)
    return store, cache


def body(answer):
    return json.dumps(answer, ensure_ascii=False, separators=(',', ':'), default=str)


# 1. The positive case: after a write and the pass, every reader's next read is kept and nothing is built.
store, cache = fresh()
data = {'n': 1}
built = []


def home(who, key):
    built.append(who[0])
    return {'who': who[0], 'year': key['year'], 'n': data['n']}


cache.register('home', home, domains=('games',))
for member in ('u1', 'u2', 'u3'):
    cache.answer('home', (member, ''), {'year': 2026}, lambda m=member: home((m, ''), {'year': 2026}), now=NOW)
built.clear()
data['n'] = 2
cache.bump(['games'], now=NOW)
told = []
report = cache.rebuild(now=NOW, pushed=lambda route, who: told.append((route, who[0])))
after_pass = sorted(built)
built.clear()
reads = [cache.answer('home', (m, ''), {'year': 2026}, lambda: built.append('READ') or {}, now=NOW) for m in ('u1', 'u2', 'u3')]
check("the positive case: a write, then the pass, rebuilds every reader's answer once",
      (after_pass, report['rebuilt'], report['failed'], report['left']), (['u1', 'u2', 'u3'], 3, 0, 0))
check("  and every reader's next read is kept, with the new data, and nothing is built",
      ([how for _b, how in reads], [json.loads(b)['n'] for b, _h in reads], built), (['kept'] * 3, [2, 2, 2], []))
check("  pushed() once per document", sorted(told), [('home', 'u1'), ('home', 'u2'), ('home', 'u3')])
told.clear()
again = cache.rebuild(now=NOW, pushed=lambda route, who: told.append((route, who[0])))
check("  a second pass with nothing written rebuilds and pushes nothing", (again['rebuilt'], again['pushed'], told), (0, 0, []))

# 2. Read: built and kept, then kept; a bump of the version or of its domain ends it, another domain's leaves it.
store, cache = fresh()
calls = []


def build():
    calls.append(1)
    return {'v': len(calls)}


first = cache.answer('roster', ('u1', ''), {}, build, now=NOW, domains=('roster',))
second = cache.answer('roster', ('u1', ''), {}, build, now=NOW, domains=('roster',))
cache.bump(['games'], now=NOW)
other_domain = cache.answer('roster', ('u1', ''), {}, build, now=NOW, domains=('roster',))
cache.bump(['roster'], now=NOW)
own_domain = cache.answer('roster', ('u1', ''), {}, build, now=NOW, domains=('roster',))
cache.bump([A.UNKNOWN], now=NOW)
unknown = cache.answer('roster', ('u1', ''), {}, build, now=NOW, domains=('roster',))
check("a first read builds and keeps, the second is kept; another domain's write leaves it; its own, or an unknown table's, ends it",
      (first[1], second[1], other_domain[1], own_domain[1], unknown[1], len(calls)), ('built', 'kept', 'kept', 'built', 'built', 3))
whole = [cache.answer('me', ('u1', ''), {}, build, now=NOW)[1], cache.answer('me', ('u1', ''), {}, build, now=NOW)[1]]
cache.bump(['games'], now=NOW)
whole.append(cache.answer('me', ('u1', ''), {}, build, now=NOW)[1])
check("  a route naming no domains is kept under the whole version: any write ends it", whole, ['built', 'kept', 'built'])
check("  another caller, or another key, has its own answer",
      (cache.answer('me', ('u2', ''), {}, build, now=NOW)[1], cache.answer('me', ('u1', 'viewed'), {}, build, now=NOW)[1],
       cache.answer('me', ('u1', ''), {'k': 1}, build, now=NOW)[1]), ('built', 'built', 'built'))
check("  version_of: the whole version, or the domains' counters with unknown's; 'other' is refused",
      (A.version_of({'version': 7}), A.version_of({'d_games': 2, 'd_unknown': 1}, ('roster', 'games'))),
      ('7', 'd:games=2,roster=0,unknown=1'))
try:
    A.version_of({}, (A.OTHER,))
    refused = False
except ValueError:
    refused = True
check("  a route naming 'other' is refused", refused, True)

# 3. An end by the clock, another revision, other inputs.
store, cache = fresh()
calls.clear()
cache.answer('games', ('u1', ''), {}, build, now=NOW, until=lambda answer, now: now + timedelta(minutes=5))
check("kept until its `until`, built after it",
      (cache.answer('games', ('u1', ''), {}, build, now=NOW + timedelta(minutes=4))[1],
       cache.answer('games', ('u1', ''), {}, build, now=NOW + timedelta(minutes=6), until=lambda a, n: n + timedelta(minutes=5))[1]),
      ('kept', 'built'))
cache.revision = 'r2'
check("  an answer kept by other code is never served", cache.answer('games', ('u1', ''), {}, build, now=NOW + timedelta(minutes=7))[1], 'built')
stamp = ['a']
cache.answer('home', ('u1', ''), {}, build, now=NOW, inputs=lambda: stamp[0])
same = cache.answer('home', ('u1', ''), {}, build, now=NOW, inputs=lambda: stamp[0])[1]
stamp[0] = 'b'
check("  the same inputs are kept; other inputs build", (same, cache.answer('home', ('u1', ''), {}, build, now=NOW, inputs=lambda: stamp[0])[1]),
      ('kept', 'built'))
check("  no day's end and no age ends it: eleven hours and two days on it is still kept (0.52.0)",
      (cache.answer('home', ('u1', ''), {}, build, now=NOW + timedelta(hours=11), inputs=lambda: stamp[0])[1],
       cache.answer('home', ('u1', ''), {}, build, now=NOW + timedelta(days=2), inputs=lambda: stamp[0])[1]), ('kept', 'kept'))

# 4. Never kept: too large, the route's keep, ended already.
store, cache = fresh(max_bytes=50)
big = cache.answer('big', ('u1', ''), {}, lambda: {'x': 'y' * 100}, now=NOW)
check("an answer over the size is served and never kept",
      (json.loads(big[0])['x'][:2], cache.answer('big', ('u1', ''), {}, lambda: {'x': 'y' * 100}, now=NOW)[1]), ('yy', 'built'))
store, cache = fresh()
cache.answer('me', ('u1', ''), {}, lambda: {'linked': False}, now=NOW, keep=lambda answer: answer['linked'])
check("  an answer its route says not to keep", cache.answer('me', ('u1', ''), {}, lambda: {'linked': False}, now=NOW,
                                                           keep=lambda answer: answer['linked'])[1], 'built')
cache.answer('now', ('u1', ''), {}, lambda: {}, now=NOW, until=lambda answer, now: now)
check("  an answer that has ended already", cache.answer('now', ('u1', ''), {}, lambda: {}, now=NOW, until=lambda a, n: n)[1], 'built')

# 5. Between a write and its pass a read builds for itself; the pass then pushes that document without rebuilding it.
store, cache = fresh()
data = {'n': 1}
built.clear()
cache.register('home', home, domains=('games',))
for member in ('u1', 'u2'):
    cache.answer('home', (member, ''), {'year': 2026}, lambda m=member: home((m, ''), {'year': 2026}), now=NOW)
data['n'] = 2
cache.bump(['games'], now=NOW)
own = cache.answer('home', ('u1', ''), {'year': 2026}, lambda: home(('u1', ''), {'year': 2026}), now=NOW)
built.clear()
told.clear()
report = cache.rebuild(now=NOW, pushed=lambda route, who: told.append(who[0]))
check("between a write and its pass a read builds for itself, with the new data", (own[1], json.loads(own[0])['n']), ('built', 2))
check("  the pass rebuilds only the other reader's, and pushes both once", (built, report['rebuilt'], sorted(told)), (['u2'], 1, ['u1', 'u2']))

# 6. The pass: nobody asked lately, a rebuild that raises, the budget, newest asked first.
store, cache = fresh()
data = {'n': 1}
built.clear()


def touchy(who, key):
    if who[0] == 'bad':
        raise RuntimeError('this caller is gone')
    return home(who, key)


cache.register('home', touchy, domains=('games',))
for i, member in enumerate(('old', 'bad', 'a', 'b', 'c')):
    at = NOW - timedelta(days=9) if member == 'old' else NOW - timedelta(minutes=10 * (5 - i))
    cache.answer('home', (member, ''), {'year': 2026}, lambda m=member: home((m, ''), {'year': 2026}), now=at,
                 until=lambda answer, now: now + timedelta(days=30))
cache.bump(['games'], now=NOW)
built.clear()
report = cache.rebuild(now=NOW)
check("the pass skips a document nobody asked for in keep_days, counts a rebuild that raises, newest asked first",
      (built, report['rebuilt'], report['failed']), (['c', 'b', 'a'], 3, 1))
check("  0.55.0: in one query a round, and the document nobody asked for lately is never read (SMADPickleBot #2336)",
      (store.queries, store.read), ([[('scope', '=='), ('asked', '>=')]], 4))
cache.bump(['games'], now=NOW)
built.clear()
store.unindexed, store.queries = True, []
report = cache.rebuild(now=NOW)
check("  with no index yet the pass asks route by route, as before, and rebuilds the same",
      (built, store.queries), (['c', 'b', 'a'], [[('scope', '=='), ('asked', '>=')], [('scope', '=='), ('route', '==')]]))
check("  a pass is no reader: a rebuilt answer keeps when it was last asked for, so one nobody reads ages out while writes keep coming",
      store.docs[(cache.collection, cache.doc_id('home', ('a', ''), {'year': 2026}))]['asked'], (NOW - timedelta(minutes=30)).isoformat())
store.unindexed = False
bad_id = cache.doc_id('home', ('bad', ''), {'year': 2026})
check("  the failed caller's document is left as it was, for its next reader",
      json.loads(store.docs[(cache.collection, bad_id)]['json'])['n'], 1)
cache.bump(['games'], now=NOW)
built.clear()
ticks = iter([0, 0] + [50] * 20)   # the pass's start, one document within the budget, then the budget is spent
report = cache.rebuild(now=NOW, budget=45, clock=lambda: next(ticks))
check("  a pass stops at its budget and says what it left", (len(built), report['left'] > 0, report['rounds']), (1, True, 1))

# 6b. 0.45.0 (SMADPickleBot #2029): a rebuild refused for its caller is dropped, never tried every pass.
store, cache = fresh()
tries = []


class Gone(Exception):
    pass


def picky(who, key):
    tries.append(who[0])
    if who[0] == 'gone':
        raise Gone('this sign-in is linked to nobody')
    if who[0] == 'flaky':
        raise RuntimeError('the database blinked')
    return home(who, key)


cache.refused = lambda e: isinstance(e, Gone)
cache.register('home', picky, domains=('games',))
for i, member in enumerate(('gone', 'flaky', 'a')):
    cache.answer('home', (member, ''), {'year': 2026}, lambda m=member: home((m, ''), {'year': 2026}),
                 now=NOW - timedelta(minutes=10 * (3 - i)), until=lambda answer, now: now + timedelta(days=30))
cache.bump(['games'], now=NOW)
tries.clear()
first = cache.rebuild(now=NOW)
gone_id = cache.doc_id('home', ('gone', ''), {'year': 2026})
gone_left = (cache.collection, gone_id) in store.docs
cache.bump(['games'], now=NOW)
tries.clear()
second = cache.rebuild(now=NOW)
check("  0.45.0: the refused caller's answer is dropped (counted apart), never built again; another error stays and is tried again",
      (first.get('dropped'), first['failed'], gone_left, 'gone' in tries, 'flaky' in tries, second.get('dropped')),
      (1, 1, False, False, True, None))

# 7. One pass at a time; a write during a pass makes it go round once more.
store, cache = fresh()
store.locked.add((cache.locks, 'smad__rebuild'))
check("a second pass while one runs is busy", cache.rebuild(now=NOW), {'busy': True})
store, cache = fresh()
data = {'n': 1}
built.clear()
wrote = []


def writes_once(who, key):
    if not wrote:
        wrote.append(1)
        data['n'] = 3
        cache.bump(['games'], now=NOW)   # a write lands while the pass runs
    return home(who, key)


cache.register('home', writes_once, domains=('games',))
cache.answer('home', ('u1', ''), {'year': 2026}, lambda: home(('u1', ''), {'year': 2026}), now=NOW)
cache.bump(['games'], now=NOW)
report = cache.rebuild(now=NOW)
kept = cache.answer('home', ('u1', ''), {'year': 2026}, lambda: {}, now=NOW)
check("a write during a pass makes it go round once more, and the lease is given back",
      (report['rounds'], kept[1], json.loads(kept[0])['n'], store.locked), (3, 'kept', 3, set()))

# 8. Firestore cannot be read: the built answer, never an error.
store, cache = fresh()
store.down = True
down = cache.answer('home', ('u1', ''), {}, lambda: {'ok': True}, now=NOW)
check("a Firestore that cannot be read costs the built answer", (down[1], json.loads(down[0]), cache.counts['unread']), ('built', {'ok': True}, 1))
check("  bump answers False and a pass says unread, neither raising", (cache.bump(['games']), 'unread' in cache.rebuild(now=NOW)), (False, True))
store, cache = fresh(enabled=False)
check("  a cache switched off builds every time and writes nothing",
      (cache.answer('home', ('u1', ''), {}, lambda: {}, now=NOW)[1], cache.answer('home', ('u1', ''), {}, lambda: {}, now=NOW)[1], store.writes),
      ('built', 'built', 0))

# 9. `asked` is refreshed at most once per ASKED_EVERY by a served read.
store, cache = fresh()
cache.answer('home', ('u1', ''), {}, lambda: {}, now=NOW)
name = cache.doc_id('home', ('u1', ''), {})
cache.answer('home', ('u1', ''), {}, lambda: {}, now=NOW + timedelta(hours=1))
soon = store.docs[(cache.collection, name)]['asked']
cache.answer('home', ('u1', ''), {}, lambda: {}, now=NOW + timedelta(hours=9))
check("a served read refreshes `asked` once it is six hours old, not before",
      (soon, store.docs[(cache.collection, name)]['asked']), (NOW.isoformat(), (NOW + timedelta(hours=9)).isoformat()))
store, cache = fresh(asked_every=timedelta(minutes=5))
cache.answer('home', ('u1', ''), {}, lambda: {}, now=NOW)
cache.answer('home', ('u1', ''), {}, lambda: {}, now=NOW + timedelta(minutes=2))
soon = store.docs[(cache.collection, name)]['asked']
cache.answer('home', ('u1', ''), {}, lambda: {}, now=NOW + timedelta(minutes=6))
check("  with asked_every five minutes (0.46.0, SMADPickleBot #2109) it is refreshed once five minutes old, not before",
      (soon, store.docs[(cache.collection, name)]['asked']), (NOW.isoformat(), (NOW + timedelta(minutes=6)).isoformat()))

# 10. Bumps: one request's commits.
store, cache = fresh()
bumps = A.Bumps(cache)
check("no commit: nothing pending", (bumps.pending, bumps.wrote_nothing), (False, False))
for domains in (['games'], ['roster'], [], ['games']):
    bumps.committed(domains)
bumps.finish()
signal = store.docs[(cache.versions, 'smad')]
check("several commits bump once or more, never more than the commits, with every domain named",
      (1 <= signal['version'] <= 4, signal['d_games'] >= 1, signal['d_roster'] >= 1, bumps.pending, bumps.wrote_nothing),
      (True, True, True, True, False))
nameless = A.Bumps(cache)
nameless.committed()
nameless.finish()
check("  a request that named no table it wrote says so", nameless.wrote_nothing, True)

# 0.52.0 (SMADPickleBot #2175; Gene, 11/22/26: "It should dirty only when data changes, not based on time expiry!",
# "when time based data expires, throw an event that cache listens to to expire"): no answer ends by its age or by a
# day's end; a figure that changes with the clock is a domain the host bumps; a route's shape number, not the
# revision, decides whether a deploy builds it again.
store, cache = fresh()
made = []


def bind():
    made.append(1)
    return {'bound': True}


def ask(c, at, **kw):
    return c.answer('bind', ('u1', 's1'), 'k', bind, now=at, domains=('devices',), **kw)[1]


lasting = [ask(cache, NOW), ask(cache, NOW + timedelta(hours=13)), ask(cache, NOW + timedelta(days=3))]
check("the positive case: nothing written, so it is kept thirteen hours on and three days on; its age is never asked",
      lasting, ['built', 'kept', 'kept'])
cache.bump(['devices'], now=NOW + timedelta(days=4))
check("  its version moving ends it", ask(cache, NOW + timedelta(days=4)), 'built')
check("  LASTING is only when its document is dropped, nothing having changed",
      ask(cache, NOW + timedelta(days=4) + A.LASTING + timedelta(hours=1)), 'built')

# A clock figure is a domain: the host's midnight event bumps `day`, and only the routes that name it end.
store, cache = fresh()
day_reads = [cache.answer('home', ('u1', ''), {}, bind, now=NOW, domains=('games', 'day'))[1],
             cache.answer('bind', ('u1', 's1'), 'k', bind, now=NOW, domains=('devices',))[1]]
cache.bump(['day'], now=NOW + timedelta(hours=10), what='clock')   # the host's midnight event
day_reads += [cache.answer('home', ('u1', ''), {}, bind, now=NOW + timedelta(hours=10), domains=('games', 'day'))[1],
              cache.answer('bind', ('u1', 's1'), 'k', bind, now=NOW + timedelta(hours=10), domains=('devices',))[1]]
check("  the day's bump ends the route that names `day` and leaves the one that does not",
      day_reads, ['built', 'built', 'built', 'kept'])

# A route's shape decides the deploy: with one, another revision serves it until the number moves; with none, every
# deploy builds it again (the safe rule for a route nobody has pinned).
store, cache = fresh()
other_code = A.AnswerCache(lambda: store, 'smad', revision='r2')
shaped = [ask(cache, NOW, shape=1), ask(other_code, NOW, shape=1), ask(other_code, NOW, shape=2), ask(other_code, NOW, shape=2)]
check("  a shaped answer is served by another revision, and built once when its shape number moves",
      shaped, ['built', 'kept', 'built', 'kept'])
store, cache = fresh()
other_code = A.AnswerCache(lambda: store, 'smad', revision='r2')
check("  an answer with no shape is built again by another revision",
      (ask(cache, NOW), ask(cache, NOW), ask(other_code, NOW)), ('built', 'kept', 'built'))
store, cache = fresh()
check("  an answer kept before it had a shape is built once when the route gets one",
      (ask(cache, NOW), ask(cache, NOW, shape=1), ask(cache, NOW, shape=1)), ('built', 'built', 'kept'))

store, cache = fresh()
own_end = [cache.answer('bind', ('u1', 's1'), 'k', bind, now=NOW, until=lambda a, now: now + timedelta(hours=2))[1],
           cache.answer('bind', ('u1', 's1'), 'k', bind, now=NOW + timedelta(hours=3),
                        until=lambda a, now: now + timedelta(hours=2))[1]]
check("  an end the answer itself names still ends it", own_end, ['built', 'built'])
store, cache = fresh()
cache.register('queue', lambda who, key: {'n': 1}, domains=('roster',), shape=3)
other_code = A.AnswerCache(lambda: store, 'smad', revision='r2')
other_code.register('queue', lambda who, key: {'n': 1}, domains=('roster',), shape=3)
reg_reads = [cache.answer('queue', ('u1', ''), {}, lambda: {'n': 1}, now=NOW)[1],
             other_code.answer('queue', ('u1', ''), {}, lambda: {'n': 1}, now=NOW + timedelta(days=2))[1]]
check("  a registered route's shape is used when the call gives none, and another revision's pass leaves it alone",
      (reg_reads, other_code.rebuild(now=NOW + timedelta(days=2))['rebuilt']), (['built', 'kept'], 0))

print(f"\n{len(fails)} failure(s)" + (': ' + '; '.join(fails) if fails else ''))
sys.exit(1 if fails else 0)
