#!/usr/bin/env python3
"""chuang_sdlc.triage: a triage pass on a box, on fakes (a fake git, a fake CLI, a fake publish and Popen), never a
network, a real worktree or the CLI.

Pinned (the positive case first): a pass refreshes the worktree, runs the host's command there with the prompt on stdin
(read once the tree is ready), logs its output and says the seconds, the head, the turns and the result; a tree that
cannot be readied runs nothing; a limited account rests and the next runs the same pass; a refused token rests the
host's time and is said in its note; the box's own sign-in never rests; a pass that runs past its cap is killed and
said. The worktree: added when missing, reset and cleaned when present, a failed fetch '' or the sha it has, or
origin/main as the box saw it when newer, the note with `detail`. A request publishes one event and never raises; the
prompt is the file and the note; the lock: one pass at a time, a stale lock ignored, removed however the pass ends; a
detached start runs the host's module.

Run:  python -B tests/test-sdlc-triage.py
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))

from chuang_sdlc import triage as TR  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


NOW = datetime(2026, 11, 8, 8, 0, tzinfo=timezone.utc)   # utc-ok: a fixed instant for a log file's name


class Resting:
    def __init__(self):
        self.marks = {}

    def rest(self, name, until, why):
        self.marks[name] = (until, why)


def answer(result='nothing to triage', code=0, err='', turns=4):
    out = '\n'.join([json.dumps({'type': 'assistant'}), json.dumps({'type': 'result', 'num_turns': turns, 'is_error': False,
                                                                    'result': result})])
    return lambda cmd, **kw: subprocess.CompletedProcess(cmd, code, out if code == 0 else '', err)


print('a pass')
logs, calls, order = tempfile.mkdtemp(), [], []


def run(cmd, **kw):
    calls.append((cmd, kw.get('cwd'), kw.get('input'), kw.get('timeout'), 'CLAUDE_CODE_OAUTH_TOKEN' in kw.get('env', {})))
    return answer()(cmd)


def prompt():
    order.append('prompt')
    return 'the prompt'


def prepare(root, wt, r):
    order.append('prepare')
    return 'abc1234def'


line = TR.run_pass('#7', 'issue', '/repo', '/wt', ['cli', '-p'], prompt, logs, run=run, now=NOW, limit=60, prepare=prepare,
                   attempts=[('login', '')], resting=Resting())
log = os.path.join(logs, '20261108T080000Z-#7.log')
check('the CLI runs in the refreshed tree with the prompt on stdin, its output logged, one line said',
      (calls, order, line.startswith('triage pass for #7: done in 0 s on abc1234, 4 turns (log '), line.endswith(': nothing to triage'),
       os.path.exists(log)),
      ([(['cli', '-p'], '/wt', 'the prompt', 60, False)], ['prepare', 'prompt'], True, True, True))
envs = []
TR.run_pass('#7b', 'issue', '/repo', '/wt', ['cli'], prompt, logs, run=lambda cmd, **kw: envs.append(kw['env']) or answer()(cmd),
            prepare=prepare, attempts=[('login', '')], resting=Resting(), signed_as='Mr Sandman|Claude session')
TR.run_pass('#7c', 'issue', '/repo', '/wt', ['cli'], prompt, logs, run=lambda cmd, **kw: envs.append(kw['env']) or answer()(cmd),
            prepare=prepare, attempts=[('login', '')], resting=Resting())
check("  0.15.0: signed_as is the pass's own CHUANG_SIGNED_AS; none given, the child sets none",
      (envs[0].get('CHUANG_SIGNED_AS'), 'CHUANG_SIGNED_AS' in envs[1] and envs[1]['CHUANG_SIGNED_AS'] == os.environ.get('CHUANG_SIGNED_AS')),
      ('Mr Sandman|Claude session', 'CHUANG_SIGNED_AS' in os.environ))
check('  a tree that cannot be readied runs nothing',
      TR.run_pass('#8', 'issue', '/repo', '/wt', ['cli'], prompt, logs, run=run, prepare=lambda *a: '', attempts=[]),
      'triage pass for #8: the worktree at /wt could not be brought to origin/main; not run')
rest, seen = Resting(), []


def accounts(cmd, **kw):
    tok = kw['env'].get('CLAUDE_CODE_OAUTH_TOKEN', '')
    seen.append(tok)
    if tok == 't1':
        return subprocess.CompletedProcess(cmd, 1, '', "You've hit your usage limit|9999999999")
    if tok == 't2':
        return subprocess.CompletedProcess(cmd, 1, '', 'OAuth token has expired')
    return answer('done')(cmd)


line = TR.run_pass('#9', 'issue', '/repo', '/wt', ['cli'], prompt, logs, run=accounts, prepare=prepare,
                   attempts_of=lambda r: [('a1', 't1'), ('a2', 't2'), ('login', '')], resting_of=lambda: rest,
                   zone_of=lambda: 'America/Los_Angeles', refused_rest_s=100, refused_note='; {accounts} refused')
check('a limited account rests, a refused token rests its time, the box sign-in runs the same pass',
      (seen, sorted(rest.marks), rest.marks['a1'][0], rest.marks['a2'][1].startswith('token refused: '), line.endswith('done; a2 refused')),
      (['t1', 't2', ''], ['a1', 'a2'], 9999999999.0, True, True))


def slow(cmd, **kw):
    raise subprocess.TimeoutExpired(cmd, 5)


check('  a pass past its cap is killed and said', TR.run_pass('#10', 'issue', '/r', '/wt', ['cli'], prompt, logs, run=slow, limit=5,
                                                              prepare=prepare, attempts=[('login', '')], resting=Resting()),
      'triage pass for #10: killed at 5 s; the next pass finds what it left')
check('  no account to run on', TR.run_pass('#11', 'issue', '/r', '/wt', ['cli'], prompt, logs, run=run, prepare=prepare, attempts=[],
                                            resting=Resting()), 'triage pass for #11: no account to run on')
check('  the result record from a streamed answer or one document',
      (TR.result_record('x\n{"type":"result","result":"a"}\n{"type":"x"}'), TR.result_record('{"result":"b"}'), TR.result_record('no')),
      ({'type': 'result', 'result': 'a'}, {'result': 'b'}, {}))

print('the worktree')


def git(answers):
    seen = []

    def run(cmd, cwd=None, **kw):
        seen.append(cmd[1:])
        code, out = answers.get(cmd[1], (0, ''))
        return subprocess.CompletedProcess(cmd, code, out, 'refused' if code else '')
    return run, seen


wt = tempfile.mkdtemp()
r, seen = git({'rev-parse': (0, 'sha1\n')})
check('missing: fetched and added at origin/main, its sha', (TR.prepare_worktree('/repo', os.path.join(wt, 'none'), r), seen[1][:2]),
      ('sha1', ['worktree', 'add']))
os.makedirs(os.path.join(wt, 'there', '.git'))
r, seen = git({'rev-parse': (0, 'sha2\n')})
check('  present: checked out to origin/main and cleaned', (TR.prepare_worktree('/repo', os.path.join(wt, 'there'), r),
                                                           [s[0] for s in seen]), ('sha2', ['fetch', 'checkout', 'clean', 'rev-parse']))
r, seen = git({'fetch': (1, ''), 'rev-parse': (0, 'sha3\n')})
check('  a failed fetch: nothing, or the sha it has kept, with its note',
      (TR.prepare_worktree('/repo', os.path.join(wt, 'there'), r),
       TR.prepare_worktree('/repo', os.path.join(wt, 'there'), r, keep_on_failed_fetch=True, detail=True)),
      ('', ('sha3', 'the fetch of origin/main failed (refused); kept on sha3, the sha it already has')))


def newer(cmd, cwd=None, **kw):
    if cmd[1] == 'fetch':
        return subprocess.CompletedProcess(cmd, 1, '', 'offline')
    if cmd[1:] == ['rev-parse', 'HEAD']:
        return subprocess.CompletedProcess(cmd, 0, 'old11111\n', '')
    if cmd[1:] == ['rev-parse', 'origin/main']:
        return subprocess.CompletedProcess(cmd, 0, 'new22222\n', '')
    return subprocess.CompletedProcess(cmd, 0, '', '')


check('  origin/main as the box saw it, when newer', TR.prepare_worktree('/repo', os.path.join(wt, 'there'), newer, keep_on_failed_fetch=True),
      'new22222')

print('the request, the prompt, the lock, the start')
sent = []
check('one event published, its line', TR.request('#7', 'tasks', number=7, title='t', source='decider', project='p',
                                                  publish=lambda topic, msg, project, attributes: sent.append((topic, project, attributes, msg['data'] if 'data' in msg else msg)) or 'm1'),
      'triage wake: requested for #7 (issue; message m1); the desktop runs the pass')
check('  on the host topic and project, the type as its attribute', sent[0][:3], ('tasks', 'p', {'type': TR.EVENT}))


def down(*a, **k):
    raise OSError('no network')


check('  a refused publish is the line, never a raise', TR.request('#8', 'tasks', publish=down),
      'triage wake: not requested for #8 (OSError: no network); the next pass finds it')
path = os.path.join(tempfile.mkdtemp(), 'prompt.txt')
with open(path, 'w', encoding='utf-8') as f:
    f.write('Investigate.\n\n')
check('the prompt: the file, then the note', (TR.prompt_text(path, 'for #7'), TR.prompt_text(path)), ('Investigate.\n\nfor #7', 'Investigate.'))
lock = os.path.join(tempfile.mkdtemp(), 'run', 'running.json')
held = []
line = TR.locked_pass('#7', 'sweep', lock, lambda why, kind: held.append(TR.running(lock, now=1001.0)) or 'done', now=1000.0)
check('the lock lives for the pass alone, its line kept', (line, held[0].get('why'), os.path.exists(lock),
                                                          open(os.path.join(os.path.dirname(lock), 'passes.log'), encoding='utf-8').read().endswith(' done\n')),
      ('done', '#7', False, True))
with open(lock, 'w', encoding='utf-8') as f:
    json.dump({'why': '#1', 'started': 1000.0}, f)
started = []
popen = lambda cmd, cwd=None, **kw: started.append((cmd[1:], cwd)) or type('P', (), {'pid': 42})()  # noqa: E731
check('a pass running folds a new request into it', TR.start_pass('#2', 'issue', 'host.triage', '/repo', lock, '/logs', popen, now=1010.0),
      'triage pass for #2: folded into the pass running for #1 (since 10 s); it finds this too')
check('  a stale lock is ignored: the host module started detached',
      (TR.start_pass('#3', 'issue', 'host.triage', '/repo', lock, '/logs', popen, now=5000.0), started),
      ('triage pass for #3 (issue): started, pid 42; its log lands in /logs', [(['-m', 'host.triage', 'pass', '#3', 'issue'], '/repo')]))

print(f'\n{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
