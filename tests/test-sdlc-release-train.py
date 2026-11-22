#!/usr/bin/env python3
"""chuang_sdlc.release_train.Lease: one push at a time to a shared branch (SMADPickleBot #2159, from its dock push).

Pinned (the positive case first): a free lease is taken and freed; a held one is waited for and taken once its holder
frees it; a lease past its `until` is taken over; a holder past `wait` gives up; renew stops when another run holds
it; free never deletes another run's; an unreadable store pushes blind and still frees its own lease; no store answers
True everywhere; the host's collection, id, lease length, tag and branch reach the document and the lines;
parse_instant reads Z, naive and junk.

Run:  python -B tests/test-sdlc-release-train.py
"""
import json
import os
import re
import sys
import time
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

lines = []
check("classify_push: ok, behind (git's words without a hook), refused with the hook's reason from stdout first",
      (R.classify_push(0, '', '', say=lines.append),
       R.classify_push(1, '', ' ! [rejected]        HEAD -> dock (fetch first)', say=lines.append), lines[:],
       R.classify_push(1, 'push gate: refused: no desktop build', 'error: failed to push some refs',
                       say=lines.append, tag='[push]'), lines),
      ('ok', 'behind', [], 'refused',
       ['[push]: push gate: refused: no desktop build', '[push]: the push was refused: error: failed to push some refs']))
check("classify_push: a pre-push hook's [rejected] is the hook's refusal, never 'behind'",
      R.classify_push(1, '', 'error: failed to push (pre-push hook declined)\n ! [rejected] x', say=lambda s: None),
      'refused')


class Held:
    def __init__(self, take=True, renews=()):
        self.took, self.renews, self.calls = take, list(renews), 0

    def take(self, sha):
        return self.took

    def renew(self, sha):
        self.calls += 1
        return self.renews.pop(0) if self.renews else True


def loop(pushes, gate=True, carry=False, lease=None, tries=3, rebases=None):
    seq, got, said = list(pushes), [], []
    heads = iter(f'h{n}' for n in range(100))
    reb = list(rebases or [])
    rc = R.push_loop(lease or Held(), tries, head=lambda: next(heads),
                     rebase=lambda: (got.append('rebase'), reb.pop(0) if reb else True)[1],
                     gate=lambda: (got.append('gate'), gate)[1],
                     push=lambda: (got.append('push'), seq.pop(0))[1],
                     carry=lambda old, new: (got.append('carry'), carry)[1], last=lambda: 'abc1234 subject',
                     say=said.append)
    return rc, got, said


check("push_loop, the positive case: one try gates once and pushes, said with the landed commit",
      loop(['ok']), (0, ['rebase', 'gate', 'push'], ['dock-push: pushed on try 1: abc1234 subject']))
check("push_loop: behind with a mark that carries pushes at once; without one the next try gates the rebased tree",
      (loop(['behind', 'ok'], carry=True), loop(['behind', 'ok'], carry=False)[:2]),
      ((0, ['rebase', 'gate', 'push', 'rebase', 'carry', 'push'],
        ['dock-push: dock moved; the mark carried and pushed on try 1: abc1234 subject']),
       (0, ['rebase', 'gate', 'push', 'rebase', 'carry', 'rebase', 'gate', 'push'])))
check("push_loop: a refused push, a failed gate, a lease not taken, a failed rebase and tries spent each stop with 1",
      (loop(['refused'])[0], loop(['ok'], gate=False)[2], loop(['ok'], lease=Held(take=False))[1],
       loop(['ok'], rebases=[False])[1], loop(['behind', 'behind'], tries=2)[2][-1]),
      (1, ['dock-push: try 1 stopped before the push; nothing pushed'], [], ['rebase'],
       'dock-push: not pushed after 2 tries'))


class Got:
    def __init__(self, returncode=0, stdout=''):
        self.returncode, self.stdout = returncode, stdout


def rows(*items):
    return Got(0, __import__('json').dumps([dict(zip(('databaseId', 'status', 'headBranch', 'event'), r)) for r in items]))


watched, naps = [], []
answers = [rows((1, 'completed', 'main', 'push'), (2, 'in_progress', 'main', 'push'), (3, 'queued', 'main', 'dynamic'),
                (4, 'queued', 'other', 'push'))]
said = R.release_runs('abc', gh=lambda args: answers.pop(0), run=lambda argv, cwd=None, timeout=None: watched.append(argv[3]),
                      at_most=300, sleep=naps.append, now=lambda: 100.0)
check("release_runs, the positive case: the push's own open runs on the branch watched; a dynamic run and another branch's left",
      (said, watched, naps), ("the release's 2 run(s) are done (1 waited for, 0 s)", ['2'], []))
empty = [Got(0, '[]'), Got(0, '[{"databaseId": 5, "status": "completed", "headBranch": "main"}]')]   # no event key
check("release_runs: an empty list asked once more after 5 s; only_open with none open is ''; unlistable said",
      (R.release_runs('abc', gh=lambda a: empty.pop(0), run=None, at_most=300, sleep=naps.append, now=lambda: 0.0),
       naps, R.release_runs('abc', gh=lambda a: rows((6, 'completed', 'main', 'push')), run=None, at_most=300,
                            only_open=True),
       R.release_runs('abc', gh=lambda a: Got(1), run=None, at_most=300, retry_empty=False),
       R.release_runs('abc', gh=lambda a: Got(1), run=None, at_most=300, only_open=True)),
      ("the release's 1 run(s) are done (0 waited for, 0 s)", [5], '', "the release's runs could not be listed; gating now",
       ''))
clock = iter([0.0, 400.0])
check("release_runs: a bound already spent says how many still ran",
      R.release_runs('abc', gh=lambda a: rows((7, 'queued', 'main', 'push')), run=None, at_most=300,
                     now=lambda: next(clock)),
      "1 of the release's 1 run(s) still ran after 300 s; gating beside them")

import tempfile  # noqa: E402

folder = tempfile.mkdtemp(prefix='release-train-')
for i, (name, body) in enumerate((('warm-a.log', '100'), ('warm-b.log', 'x'), ('warm-c.log', '300'), ('stages-d.json', '200'),
                                  ('other.txt', '999'))):
    path = os.path.join(folder, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(body)
    os.utime(path, (1000 + i, 1000 + i))


def seconds(path):
    with open(path, encoding='utf-8') as f:
        text = f.read()
    return int(text) if text.isdigit() else None


warms = R.newest(folder, 'warm-', '.log')
check("newest, the positive case: the prefix…suffix files newest first; a folder that cannot be read is []",
      ([os.path.basename(p) for p in warms], R.newest(os.path.join(folder, 'gone'), 'warm-', '.log')),
      (['warm-c.log', 'warm-b.log', 'warm-a.log'], []))
bound = R.history_bound((('warms', warms, seconds), ('ships', R.newest(folder, 'stages-', '.json'), seconds)),
                        1.5, 600, last=20)
check("history_bound: factor times the slowest readable run of every source, an unreadable one skipped",
      bound, (450, {'warms': [300, 100], 'ships': [200]}, 300))
check("history_bound: only the last `last` readable runs of a source count; none on record is the default",
      (R.history_bound((('warms', warms, seconds),), 1.5, 600, last=1),
       R.history_bound((('warms', [], seconds),), 1.5, 600, last=20)),
      ((450, {'warms': [300]}, 300), (600, {'warms': []}, None)))

class Gh:
    """A scripted `gh`: each call's args recorded, the answers given in order by the first word after 'run'."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, args, timeout=None):
        self.calls.append(args[:2] + [a for a in args if a.startswith('.')])
        key = args[1] if args[1] != 'view' else args[args.index('-q') + 1]
        return self.answers[key].pop(0)


naps = []
done = Gh({'watch': [Got(1)], '.conclusion': [Got(0, 'success\n')]})
check("watch_run, the positive case: one watch, the conclusion read",
      (R.watch_run(done, 'o/r', 7, 3600, sleep=naps.append, now=lambda: 0.0), len(done.calls), naps), ('success', 2, []))
again = Gh({'watch': [Got(1), Got(0)], '.conclusion': [Got(0, ''), Got(0, 'failure')], '.status': [Got(0, 'in_progress')]})
check("watch_run: a watch that returned while the run was still going is watched again after again_s",
      (R.watch_run(again, 'o/r', 7, 3600, again_s=30, sleep=naps.append, now=lambda: 0.0), naps), ('failure', [30]))
odd = Gh({'watch': [Got(0)], '.conclusion': [Got(0, '')], '.status': [Got(0, 'completed')]})
late = Gh({'watch': [Got(1)], '.conclusion': [Got(0, '')], '.status': [Got(0, 'queued')]})
check("watch_run: a completed run with no conclusion, or one still going at the bound, is 'unknown'",
      (R.watch_run(odd, 'o/r', 7, 3600, now=lambda: 0.0), R.watch_run(late, 'o/r', 7, 20, again_s=30, now=lambda: 0.0)),
      ('unknown', 'unknown'))
check("run_head: the run's headSha, '' when GitHub refuses",
      (R.run_head(Gh({'.headSha': [Got(0, 'abc123\n')]}), 'o/r', 7), R.run_head(Gh({'.headSha': [Got(1)]}), 'o/r', 7)),
      ('abc123', ''))
listing = Got(0, '[{"databaseId": 9, "createdAt": "2026-11-22T01:00:10Z"}, {"databaseId": 8, "createdAt": "2026-11-22T01:00:30Z"},'
                 ' {"databaseId": 1, "createdAt": "2026-11-22T00:00:00Z"}]')
since = datetime(2026, 11, 22, 1, 0, 5, tzinfo=timezone.utc).timestamp()   # utc-ok: GitHub's createdAt is UTC
naps = []
check("dispatched_run: the oldest dispatch run made since the dispatch; asked again while none has appeared; '' after",
      (R.dispatched_run(Gh({'list': [Got(0, '[]'), listing]}), 'o/r', 'app.yml', since, 3, 4, sleep=naps.append), naps,
       R.dispatched_run(Gh({'list': [Got(0, '[]'), Got(0, 'x')]}), 'o/r', 'app.yml', since, 2, 4, sleep=lambda s: None)),
      ('9', [4], ''))

def jobs_reader(*reads):
    seq = list(reads)

    def read():
        got = seq.pop(0) if seq else seq_last[0]
        if isinstance(got, Exception):
            raise got
        return got
    seq_last = [reads[-1]]
    return read


done_all = lambda jobs: bool(jobs) and all(j.get('status') == 'completed' for j in jobs)   # noqa: E731
ok_one = lambda j: j.get('conclusion') in ('success', 'skipped')   # noqa: E731
green = {'jobs': [{'status': 'completed', 'conclusion': 'success'}, {'status': 'completed', 'conclusion': 'skipped'}]}
naps = []
check("watch_jobs, the positive case: every job done on two reads in a row, then whether each succeeded",
      (R.watch_jobs(jobs_reader({'jobs': []}, green, green), done_all, ok_one, 60, pause=naps.append), naps),
      (True, [5, 5]))
red = {'jobs': [{'status': 'completed', 'conclusion': 'failure'}]}
going = {'jobs': [{'status': 'in_progress'}]}
check("watch_jobs: done once then a job still going starts the count again; a failed job is False; a wait that runs "
      "out is False; an unreadable read counts as no jobs",
      (R.watch_jobs(jobs_reader(green, going, green, green), done_all, ok_one, 60, pause=lambda s: None),
       R.watch_jobs(jobs_reader(red, red), done_all, ok_one, 60, pause=lambda s: None),
       R.watch_jobs(jobs_reader(going), done_all, ok_one, 15, pause=lambda s: None),
       R.watch_jobs(jobs_reader(ValueError('x'), green, green), done_all, ok_one, 60, pause=lambda s: None)),
      (True, False, False, True))

check("gh_seconds: whole seconds between two GitHub stamps; None for a missing one",
      (R.gh_seconds('2026-11-22T01:00:00Z', '2026-11-22T01:01:05Z'), R.gh_seconds(None, '2026-11-22T01:00:00Z')),
      (65, None))
RUNS = {'workflow_runs': [
    {'id': 1, 'name': 'Deploy', 'event': 'push', 'head_branch': 'main', 'status': 'completed', 'conclusion': 'success',
     'run_started_at': '2026-11-22T01:00:00Z', 'updated_at': '2026-11-22T01:02:00Z'},
    {'id': 2, 'name': 'Graph', 'event': 'dynamic', 'head_branch': 'main', 'status': 'queued'},
    {'id': 3, 'name': 'Deploy', 'event': 'workflow_dispatch', 'head_branch': 'dock', 'status': 'in_progress'}]}
JOBS = {'jobs': [
    {'name': 'api', 'conclusion': 'success', 'started_at': '2026-11-22T01:00:10Z', 'completed_at': '2026-11-22T01:01:40Z',
     'steps': [{'name': 'Smoke', 'started_at': '2026-11-22T01:01:00Z', 'completed_at': '2026-11-22T01:01:30Z'},
               {'name': 'Setup', 'started_at': '2026-11-22T01:00:10Z', 'completed_at': '2026-11-22T01:00:15Z'},
               {'name': 'Deploy', 'started_at': '2026-11-22T01:00:15Z', 'completed_at': '2026-11-22T01:01:00Z'}]},
    {'name': 'site', 'conclusion': 'skipped'}]}
watched = []
runs = R.run_profiles(lambda path: JOBS if '/jobs' in path else RUNS, 'abc', watched.append, skip_branch='dock')
check("run_profiles, the positive case: the release's own runs (no dynamic run, no dock run), a skipped job left "
      "out, each job's seconds, wait to start and two slowest steps; only an open own run is watched",
      (runs, watched),
      ([{'name': 'Deploy', 'id': 1, 'conclusion': 'success', 'seconds': 120,
         'jobs': [{'name': 'api', 'seconds': 90, 'waited': 10, 'slowest': [('Deploy', 45), ('Smoke', 30)]}]}], []))
history = os.path.join(folder, 'history.jsonl')
first_lines = R.profile_lines('a' * 40, 'web', {'tests': 20}, runs, history, now=lambda: 1.0)
second = R.profile_lines('b' * 40, 'web', {'tests': 15}, runs, history, now=lambda: 2.0)
check("profile_lines: the stages, each run and its slowest jobs; the first of a kind says so, the next sets each "
      "beside it and the history grows",
      (first_lines[:3], first_lines[-1], second[-1], len(open(history, encoding='utf-8').read().splitlines())),
      (['release profile (aaaaaaaa, web):', '  stages: tests 20 s', '  Deploy 120 s (success)'],
       '  no earlier web release on record to compare',
       '  vs the last web release (aaaaaaaa): tests 20 → 15 s; Deploy 120 → 120 s', 2))
old_log = os.path.join(folder, 'tests.log')
with open(old_log, 'w', encoding='utf-8') as f:
    f.write('x')
os.utime(old_log, (500, 500))
with open(os.path.join(folder, 'push.log'), 'w', encoding='utf-8') as f:
    f.write('y')
check("keep_logs: the logs written since the release began copied to <sha7>/; an older one and a missing one left out",
      (R.keep_logs('c' * 40, folder, ('tests.log', 'push.log', 'build.log'), since=1000),
       os.path.exists(os.path.join(folder, 'c' * 7, 'push.log'))), (['push.log'], True))

asked, naps = [], []
answers = iter(['', '11', '11 12'])
got = R.dispatched_runs(lambda argv, timeout=None: (asked.append(argv), next(answers))[1], 'deploy.yml',
                        '2026-11-22T01:00:00Z', 2, sleep=naps.append)
check("dispatched_runs, the positive case: asked until `want` are listed, `wait` s apart, the dispatch's own since",
      (got, naps, asked[0][4], asked[0][-1].endswith('select(.createdAt >= "2026-11-22T01:00:00Z") | .databaseId')),
      (['11', '12'], [2, 2], 'deploy.yml', True))
check("dispatched_runs: none wanted reads nothing; a list that never fills gives what it has after `tries`",
      (R.dispatched_runs(lambda a, timeout=None: 1 / 0, 'x', 's', 0),
       R.dispatched_runs(lambda a, timeout=None: '7', 'x', 's', 3, tries=2, sleep=lambda s: None)),
      ([], ['7']))

STEPS_JOBS = Got(0, json.dumps({'jobs': [{'steps': [
    {'name': 'Build with Expo (ios)', 'started_at': '2026-11-22T01:00:00Z', 'completed_at': '2026-11-22T01:10:00Z'},
    {'name': 'Wait for TestFlight', 'started_at': '2026-11-22T01:10:00Z', 'completed_at': '2026-11-22T01:15:30Z'},
    {'name': 'Upload', 'started_at': '2026-11-22T01:15:30Z'}]}]}))
check("step_seconds, the positive case: each named step's seconds; a step not finished or not there is None",
      R.step_seconds(lambda a, timeout=None: STEPS_JOBS, 'o/r', 5,
                     {'expo': lambda n: n.startswith('Build with Expo'), 'apple': lambda n: n == 'Wait for TestFlight',
                      'upload': lambda n: n == 'Upload', 'gone': lambda n: n == 'x'}),
      {'expo': 600, 'apple': 330, 'upload': None, 'gone': None})
check("step_seconds: an unreadable answer names nothing",
      R.step_seconds(lambda a, timeout=None: Got(0, 'x'), 'o/r', 5, {'expo': lambda n: True}), {'expo': None})
log = Got(0, 'a\nTestFlight: build 30 uploaded\nb\nTestFlight: build 31 submitted for external review\r\nApp Store: ok\n')
check("log_lines: the last line of each pattern; absent or an unread log is ''",
      (R.log_lines(lambda a, timeout=None: log, 'o/r', 5,
                   {'tf': re.compile(r'(TestFlight: build \d+ [^\r\n]+)'), 'st': re.compile(r'(App Store: [^\r\n]+)'),
                    'none': re.compile(r'(Nope: [^\n]+)')}),
       R.log_lines(lambda a, timeout=None: Got(1), 'o/r', 5, {'tf': re.compile(r'(x)')})),
      ({'tf': 'TestFlight: build 31 submitted for external review', 'st': 'App Store: ok', 'none': ''}, {'tf': ''}))

class Page:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


def status(state):
    return lambda url, timeout=None: Page(json.dumps({'components': [{'name': 'Git', 'status': 'major_outage'},
                                                                     {'name': 'Actions', 'status': state}]}).encode())


def down(url, timeout=None):
    raise OSError('no route')


check("actions_operational, the positive case: operational and degraded are up; an outage is down; an unread page or "
      "one with no Actions component is None",
      (R.actions_operational(status('operational')), R.actions_operational(status('degraded_performance')),
       R.actions_operational(status('major_outage')), R.actions_operational(down),
       R.actions_operational(lambda url, timeout=None: Page(b'{"components": []}'))),
      (True, True, False, None, None))

stage_dir = tempfile.mkdtemp(prefix='stage-')
stage_log = os.path.join(stage_dir, 'tests.log')
said = []


def fake_run(code, body):
    def run(args, cwd=None, stdout=None, stderr=None, **kw):
        stdout.write(body)

        class Done:
            returncode = code
        return Done()
    return run


ticks = iter([100.0, 117.4])
ok = R.run_stage('tests', ['x'], stage_log, say=said.append, run=fake_run(0, 'all green\n'), now=lambda: next(ticks))
check("run_stage, the positive case: (True, seconds, the output), the output in the log, one ok line",
      (ok, open(stage_log, encoding='utf-8').read(), said), ((True, 17, 'all green\n'), 'all green\n', ['ok  tests: 17 s']))
said.clear()
body = ''.join(f'line {n}\n' for n in range(20))
bad = R.run_stage('tests', ['x'], stage_log, say=said.append, run=fake_run(1, body), now=lambda: 0.0, tail=3,
                  stamp=lambda: '174500')
failed_log = os.path.join(stage_dir, 'tests-failed-174500.log')
check("run_stage: a failed stage keeps its log under a failed name and says its last lines; the run before stays -prev",
      (bad[0], os.path.exists(failed_log), said[0].splitlines(),
       open(os.path.join(stage_dir, 'tests-prev.log'), encoding='utf-8').read()),
      (False, True, [f'FAILED tests: 0 s (its log: {failed_log})', 'line 17', 'line 18', 'line 19'], 'all green\n'))

sha_dir = tempfile.mkdtemp(prefix='short-sha-')
for name in ('warm-abcdef1.log', 'warm-abcdef12.log', 'warm-abcdef9.log', 'stages-abcdef1.json'):
    open(os.path.join(sha_dir, name), 'w').close()
check("short_sha_path, the positive case: the longest short-sha name the full sha starts with",
      os.path.basename(R.short_sha_path(sha_dir, 'abcdef12345', 'warm-', '.log')), 'warm-abcdef12.log')
check("short_sha_path: a seven-character name when that is the only match; '' for none or a missing folder",
      (os.path.basename(R.short_sha_path(sha_dir, 'abcdef1999', 'warm-', '.log')),
       R.short_sha_path(sha_dir, '1234567890', 'warm-', '.log'),
       R.short_sha_path(os.path.join(sha_dir, 'gone'), 'abcdef12345', 'warm-', '.log')),
      ('warm-abcdef1.log', '', ''))

T0 = 1_800_000_000   # 2027-01-15T08:00:00Z


def stamp(at, text):
    return f"{time.strftime(R.STAMP, time.gmtime(at))} {text}"


run_log = [stamp(T0 - 600, 'warm: a stage failed (tests 3 s)'), 'no stamp at all', stamp(T0 + 5, 'tests: started'),
           stamp(T0 + 90, 'warm: every stage passed (tests 12 s; 90 s in all)')]
check("stamped_verdict, the positive case: this run's own pass line is 0",
      R.stamped_verdict(run_log, T0, 'warm: ', 'warm: every stage passed'), 0)
check("stamped_verdict: this run's failure is 1; an earlier run's line is never read (None); unknown since is None",
      (R.stamped_verdict(run_log[:3] + [stamp(T0 + 40, 'warm: a stage failed (images)')], T0, 'warm: ',
                         'warm: every stage passed'),
       R.stamped_verdict(run_log[:3], T0, 'warm: ', 'warm: every stage passed'),
       R.stamped_verdict(run_log, None, 'warm: ', 'warm: every stage passed'),
       R.stamped_verdict(run_log, str(T0), 'warm: ', 'warm: every stage passed')),
      (1, None, None, 0))

print()
print(f"{len(fails)} failure(s)" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
