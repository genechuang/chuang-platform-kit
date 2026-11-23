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

# ---------------------------------------------------------------------------------------------------------- Handover
import io  # noqa: E402
import json  # noqa: E402
import urllib.error  # noqa: E402


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener(status=200, body=b'{}'):
    calls = []

    def op(req, timeout=None):
        calls.append((req.get_method(), req.full_url, json.loads(req.data) if req.data else None))
        if status != 200:
            raise urllib.error.HTTPError(req.full_url, status, 'x', {},
                                         io.BytesIO(json.dumps({'error': {'message': 'denied'}}).encode()))
        return Resp(body)
    op.calls = calls
    return op


class Mine(Exception):
    pass


SUB, Q = 'projects/p/subscriptions/work', 'projects/p/locations/l/queues/deadman'
H = S.Handover(SUB, 'push@p.iam', Q, 'https://worker.example', 'owners', 'one', what='work', cloud_what='the cloud worker',
               signal=('signals', 'runner'), signal_extra={'what': 'runner'}, error=Mine)
hs, op = Store(), opener()
line = H.take(hs, 't', 'started', now=T0.replace(microsecond=5), opener=op)
check("the positive case: the desktop takes it, the owner written first and to the second, the subscription made a "
      "pull one, the signal sent",
      (line, hs.docs[('owners', 'one')], op.calls, hs.docs[('signals', 'runner')]),
      ('worker owner: the desktop holds work (started)',
       {'holder': 'desktop', 'beat_at': T0, 'reason': 'started', 'since': T0},
       [('POST', f'https://pubsub.googleapis.com/v1/{SUB}:modifyPushConfig', {'pushConfig': {}})],
       {'holder': 'desktop', 'since': T0, 'at': T0, 'what': 'runner'}))
H.write(hs, 'desktop', 'beat', now=T0 + timedelta(seconds=60), since=T0)
check("a beat by the same holder leaves the signal as it was",
      (hs.docs[('signals', 'runner')]['at'], hs.docs[('owners', 'one')]['beat_at']), (T0, T0 + timedelta(seconds=60)))
op = opener()
line = H.give_back(hs, 't', 'by hand', now=T0 + timedelta(seconds=90), opener=op)
check("give back: the push restored with the pusher's token for the worker's own address, then the cloud recorded",
      (line, op.calls[0][2]['pushConfig'], hs.docs[('owners', 'one')]['holder'], hs.docs[('signals', 'runner')]['holder']),
      ('worker owner: the cloud holds work (by hand)',
       {'pushEndpoint': 'https://worker.example',
        'oidcToken': {'serviceAccountEmail': 'push@p.iam', 'audience': 'https://worker.example'}}, 'cloud', 'cloud'))
late = Store({('owners', 'one'): {'holder': 'desktop', 'beat_at': (T0 - timedelta(seconds=75)).isoformat()}})
op = opener()
changed, line = H.failover(late, 't', now=T0, opener=op)
stray_op = opener()
stray = H.failover(Store({('owners', 'one'): {'holder': 'desktop', 'beat_at': T0 - timedelta(seconds=12)}}), 't', now=T0,
                   opener=stray_op)
check("failover: a beat past deadman_s restores the push and is one ERROR line; a fresh beat is a stray task; the "
      "cloud's own hold or no record is nothing to do; an unreadable beat is a stale one",
      (changed, line, late.docs[('owners', 'one')]['holder'], '75 s ago' in late.docs[('owners', 'one')]['reason'],
       len(op.calls), stray, stray_op.calls,
       [H.failover(Store(d), 't', now=T0, opener=opener())[0]
        for d in ({('owners', 'one'): {'holder': 'cloud'}}, {}, {('owners', 'one'): {'holder': 'desktop', 'beat_at': 'x'}})]),
      (True, 'ERROR: worker owner: the desktop stopped answering; work failed over to the cloud worker', 'cloud', True,
       1, (False, 'worker owner: the dead man fired but the desktop beat 12 s ago: a stray task; the one armed since '
                  'covers it'), [], [False, False, True]))
op = opener(body=json.dumps({'name': f'{Q}/tasks/abc'}).encode())
name = H.deadman_task('t', 'https://host.example/', 7, now=T0, opener=op)
task = op.calls[0][2]['task']
gone = opener(404)
H.disarm('t', f'{Q}/tasks/abc', gone)
H.disarm('t', '', None)
try:
    H.disarm('t', f'{Q}/tasks/abc', opener(500))
    refused = False
except Mine:
    refused = True
check("the dead man's task is due deadman_s ahead at the host's failover route as the pusher; disarm takes a task "
      "already gone and raises the host's error for any other refusal",
      (name, op.calls[0][1], task['scheduleTime'], task['httpRequest']['url'], task['httpRequest']['oidcToken'],
       len(gone.calls), refused),
      (f'{Q}/tasks/abc', f'https://cloudtasks.googleapis.com/v2/{Q}/tasks', '2026-11-21T07:59:10Z',
       'https://host.example/worker/failover',
       {'serviceAccountEmail': 'push@p.iam', 'audience': 'https://host.example/worker/failover'}, 1, True))
steady = T0 - timedelta(seconds=300)
check("may_resume and may_take_back: a fresh desktop record resumes; the take-back waits for the holder, the host's "
      "busy reason and the steady time, in that order",
      (H.may_resume({'holder': 'desktop', 'beat_at': T0 - timedelta(seconds=12)}, T0),
       H.may_resume({'holder': 'desktop', 'beat_at': T0 - timedelta(seconds=70)}, T0),
       H.may_resume({'holder': 'cloud', 'beat_at': T0}, T0), H.may_resume({'holder': 'desktop'}, T0),
       H.may_take_back({'holder': 'cloud'}, steady, T0), H.may_take_back({'holder': 'desktop'}, steady, T0),
       H.may_take_back({'holder': 'cloud'}, steady, T0, busy='a job is on'),
       H.may_take_back({'holder': 'cloud'}, T0 - timedelta(seconds=60), T0), H.may_take_back({'holder': 'cloud'}, None, T0)),
      (True, False, False, False, '', 'the desktop already holds it', 'a job is on',
       'the heartbeat has not been steady 300 s', 'the heartbeat has not been steady 300 s'))
check("the subscription says who holds it, and the queue's tasks are listed by name",
      (H.subscription_holder('t', opener(body=json.dumps({'pushConfig': {'pushEndpoint': 'u'}}).encode())),
       H.subscription_holder('t', opener(body=b'{"pushConfig": {}}')),
       H.pending_tasks('t', opener(body=json.dumps({'tasks': [{'name': 'a'}, {}, {'name': 'b'}]}).encode()))),
      ('cloud', 'desktop', ['a', 'b']))
plain = S.Handover(SUB, 'push@p.iam', Q, 'https://worker.example', 'owners', 'one')
ps = Store()
plain.write(ps, 'desktop', 'r', now=T0)
try:
    plain.set_push('t', None, opener(403))
    said = ''
except S.SwitchFailed as e:
    said = str(e)
check("with no signal only the owner record is written, and a refused switch raises SwitchFailed with Google's reason",
      (list(ps.docs), '403' in said and 'denied' in said), ([('owners', 'one')], True))

print()
print(f"{len(fails)} failure(s)" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
