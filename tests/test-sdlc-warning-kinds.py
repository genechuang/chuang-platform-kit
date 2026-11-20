#!/usr/bin/env python3
"""chuang_sdlc.warning_kinds: WARNING lines folded into kinds, and which kind a person should look at.

Lifted from SMAD PickleBot's picklebot_core/warning_digest.py (2026-11-19 PT, chuang-sdlc 0.39.0, SMADPickleBot #1850).
Pinned, positive case first:
  - lines that differ only in numbers, ids and times are one kind; a kind never seen fires `new`, then waits;
  - a decided kind fires again only when it grows (four times its mean, at least 10, after 6 hours) or, kept, repeats
    (6 of the last 24 hours, once a day); a promoted kind never repeats; a bad verdict or empty reason is refused;
  - the counts keep the last 24 hours; hour_label names the UTC hour;
  - a record round-trips through plain fields; the wake line names the count, the cases and how many more;
  - the store reads and writes the project and collection it is given, and a refused read is a URLError carrying
    Google's status and never the token.

Run:  python -B tests/test-sdlc-warning-kinds.py
"""
import datetime as dt
import io
import json
import os
import sys
import urllib.error

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))

from chuang_sdlc import warning_kinds as W  # noqa: E402

fails = []
H0 = '2026-10-27T22:00Z'


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


def entry(service, message):
    return {'resource': {'type': 'cloud_run_revision', 'labels': {'service_name': service}}, 'jsonPayload': {'message': message}}


# the positive case
lines = [entry('alerts', 'APP-12: the screenshot was not put under the post (not_in_channel)'),
         entry('alerts', 'APP-1C: the screenshot was not put under the post (not_in_channel)'),
         entry('decider', 'request 4f9a1c2e at 2026-10-27T15:02:11Z took 812 ms')]
counts = W.fold(lines)
fired, recs = W.decide(counts, {}, H0, 1000.0)
fired2, recs2 = W.decide(counts, recs, '2026-10-27T23:00Z', 4600.0)
check("lines differing only in numbers, ids and times are one kind; a new kind fires once, then waits",
      (len(counts), sorted(f['case'] for f in fired), [f['count'] for f in fired if f['service'] == 'alerts'], fired2,
       [r['state'] for r in recs2.values()],
       W.kind('decider', 'request 9a9a9a9a at 2026-10-28T01:00:00Z took 9 ms') in counts),
      (2, ['new', 'new'], [2], [], ['fired', 'fired'], True))
check("an empty message and the id's length", (W.kind('svc', ''), len(W.kind_id('x'))), ('svc: (no message)', 20))
check("the service of a job's and a function's entry, and a text payload",
      (W.service_of({'resource': {'labels': {'job_name': 'j'}}}), W.service_of({'resource': {'type': 'gce'}}),
       W.service_of({}), W.message_of({'textPayload': 't'})), ('j', 'gce', 'unknown', 't'))

# decided kinds
k = 'svc: a thing failed'
kid = W.kind_id(k)
base = {'kind': k, 'service': 'svc', 'sample': 's', 'first_seen': '2026-10-26T00:00Z', 'state': 'fired', 'reason': '',
        'fired_at': 0, 'case': 'new', 'hours': {f'2026-10-27T{h:02d}:00Z': 2 for h in range(14, 21)}}


def seen(n):
    return {k: {'count': n, 'service': 'svc', 'sample': 's'}}


kept = W.decision(base, 'keep', 'a transient the next run passed')
quiet, _ = W.decide(seen(2), {kid: dict(kept, fired_at=10 ** 10)}, '2026-10-27T21:00Z', 10 ** 10)
grow, _ = W.decide(seen(12), {kid: dict(kept, fired_at=10 ** 10)}, '2026-10-27T21:00Z', 10 ** 10)
rep, rrec = W.decide(seen(2), {kid: kept}, '2026-10-27T21:00Z', 10 ** 10)
again, _ = W.decide(seen(2), rrec, '2026-10-27T22:00Z', 10 ** 10 + 60)
prom, _ = W.decide(seen(2), {kid: W.decision(base, 'promote', 'made an ERROR')}, '2026-10-27T21:00Z', 10 ** 10)
refused = []
for args in (('ignore', 'why'), ('keep', '  ')):
    try:
        W.decision(base, *args)
    except ValueError:
        refused.append(args[0])
check("a kept kind is quiet at its rate, grows at four times it, repeats once a day; a promoted one never repeats",
      ((kept['state'], kept['reason']), quiet, [f['case'] for f in grow], [f['case'] for f in rep], again, prom, refused,
       W.decision(base, 'demote', 'expected')['state']),
      (('kept', 'a transient the next run passed'), [], ['growing'], ['repeating'], [], [], ['ignore', 'keep'], 'demoted'))

# the window
old = {'kind': 'svc: x', 'state': 'kept', 'hours': {'2026-10-25T10:00Z': 5, '2026-10-27T10:00Z': 1}, 'fired_at': 10 ** 10}
_, recs = W.decide({'svc: x': {'count': 3, 'service': 'svc', 'sample': 's'}}, {W.kind_id('svc: x'): old}, H0, 10 ** 10)
check("the counts keep the last 24 hours, and hour_label names the UTC hour",
      (recs[W.kind_id('svc: x')]['hours'],
       W.hour_label(dt.datetime(2026, 10, 27, 22, 41, tzinfo=dt.timezone.utc).timestamp())),   # utc-ok: the key's own zone
      ({'2026-10-27T10:00Z': 1, H0: 3}, H0))

# fields and the wake line
rec = {'kind': 'svc: x', 'state': 'fired', 'fired_at': 12.5, 'count': 3, 'hours': {H0: 3}, 'reason': None}
many = [{'case': 'new', 'kind': f'svc: kind {i}', 'count': i} for i in range(7)]
check("a record round-trips through plain fields; the wake line names the count and how many more past five",
      (W.from_plain(W.to_plain(rec)), W.from_plain({'hours': 'not json'})['hours'],
       W.wake_text(many).startswith('warnings: 7 kind(s) to decide: new svc: kind 0 (0 this hour)'),
       W.wake_text(many).endswith('and 2 more')),
      (dict(rec, reason=''), {}, True, True))


# the store
class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


asked = []


def store(req, timeout=None):
    asked.append((req.get_method(), req.full_url, json.loads(req.data) if req.data else None))
    if req.full_url.endswith(':runQuery'):
        doc = {'name': 'projects/p1/databases/(default)/documents/kinds_here/abc',
               'fields': {'kind': {'stringValue': 'svc: x'}, 'hours': {'stringValue': '{"' + H0 + '": 3}'}}}
        return Answer(json.dumps([{'document': doc}]).encode())
    return Answer(b'{}')


loaded = W.load('p1', 'kinds_here', 'tok', opener=store)
W.save('p1', 'kinds_here', 'tok', 'abc', {'kind': 'svc: x', 'hours': {H0: 4}}, opener=store)
check("the store reads and writes the project and collection it is given",
      (loaded, all('/projects/p1/' in u for _, u, _ in asked), json.dumps(asked[0][2]).count('kinds_here'),
       any('kinds_here/abc' in json.dumps(b or {}) for _, _, b in asked[1:])),
      ({'abc': {'kind': 'svc: x', 'hours': {H0: 3}}}, True, 1, True))


def refusing(req, timeout=None):
    raise urllib.error.HTTPError(req.full_url, 500, 'x', {}, io.BytesIO(b'{"error": {"status": "INTERNAL"}}'))


try:
    W.load('p1', 'kinds_here', 'secret-tok', opener=refusing)
    got = None
except urllib.error.URLError as e:
    got = ('INTERNAL' in str(e), 'secret-tok' in str(e))
check("a refused read is a URLError with Google's status, never the token", got, (True, False))
check("an empty collection loads as nothing",
      W.load('p1', 'k', 'tok', opener=lambda req, timeout=None: Answer(b'[{"readTime": "x"}]')), {})

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
