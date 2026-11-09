#!/usr/bin/env python3
"""The session nanny (plugins/gene-hooks/hooks/nanny.py, SMADPickleBot #1214; Gene, 11/8/26 11:04 PM PT): a background
Bash run is recorded from its start's result; one that prints a wait, prints nothing for its first 30 s, or stops growing
for 120 s is told once as the next tool result's context, and holds the turn's Stop once; a finished run, a quiet-by-design
mail pull and a growing run are left alone.

Run:  python tests/test-hooks-nanny.py      (prints PASS/FAIL per case and `N failure(s)`)
"""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, '..', 'plugins', 'gene-hooks', 'hooks')
BASH = shutil.which('bash') or 'bash'
spec = importlib.util.spec_from_file_location('nanny', os.path.join(HOOKS, 'nanny.py'))
N = importlib.util.module_from_spec(spec)
spec.loader.exec_module(N)
failures = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ('' if ok else f'\n        got  {got!r}\n        want {want!r}'))
    if not ok:
        failures.append(label)


def start(folder, task, command='python build.py', text=''):
    """A background start's event, and its output file holding `text`."""
    out = os.path.join(folder, f'{task}.output')
    with open(out, 'w', encoding='utf-8') as f:
        f.write(text)
    return out, {'session_id': 's1', 'tool_name': 'Bash', 'tool_input': {'command': command, 'run_in_background': True},
                 'tool_response': {'stdout': f'Command running in background with ID: {task}. Output is being written to: {out}. '
                                             'You will be notified when it completes'}}


def other():
    return {'session_id': 's1', 'tool_name': 'Read', 'tool_input': {}, 'tool_response': 'x'}


def main():
    d = tempfile.mkdtemp()
    os.environ['NANNY_DIR'] = os.path.join(d, 'state')
    t0 = 1_000_000.0

    # the positive case: a run that prints a wait is told at the next tool call, once
    _out, ev = start(d, 'b1', 'python android_build.py build', 'android build of abc\nwaiting for another Android build (pid 124028)\n')
    first = N.post(ev, t0)
    again = N.post(other(), t0 + 5)
    check('the positive case: a run printing "waiting for" is told at the next tool call with its id, command, age and last line, once',
          ('background run b1 (python android_build.py build), 0 s old: its output says it is waiting' in first,
           'last line: waiting for another Android build (pid 124028)' in first, again), (True, True, ''))

    # the Stop holds once for it, then lets the turn end
    held = N.stop({'session_id': 's1'}, t0 + 10)
    then = N.stop({'session_id': 's1'}, t0 + 20)
    active = N.stop({'session_id': 's1', 'stop_hook_active': True}, t0 + 30)
    check('  the Stop holds once for a flagged run still running, then ends; never inside a held Stop',
          ('before this turn ends, background run b1' in held, then, active), (True, '', ''))

    # silent for 30 s, stalled for 120 s, a finished run, a quiet mail pull
    _o2, ev2 = start(d, 'b2', 'python slow.py', '')
    N.post(ev2, t0)
    silent_early = N.post(other(), t0 + 10)
    silent = N.post(other(), t0 + 31)
    o3, ev3 = start(d, 'b3', 'python steps.py', 'step 1\n')
    os.utime(o3, (t0, t0))
    N.post(ev3, t0)
    stalled = N.post(other(), t0 + 121)
    _o4, ev4 = start(d, 'b4', 'python done.py', 'all done\n[exited with code 0]\n')
    N.post(ev4, t0)
    done_line = N.post(other(), t0 + 300)
    _o5, ev5 = start(d, 'b5', 'python scripts/session-mail.py wait --for "Snow White"', '')
    N.post(ev5, t0)
    mail = N.post(other(), t0 + 600)
    check('  nothing for 30 s is "printed nothing", 121 s without growing is "not grown"; a finished run and a quiet mail pull are left alone',
          (silent_early.count('b2'), 'b2 (python slow.py), 31 s old: it has printed nothing in its first 30 s' in silent,
           'b3 (python steps.py), 121 s old: its output has not grown for 120 s' in stalled, 'b4' in done_line, 'b5' in mail),
          (0, True, True, False, False))

    # a foreground call is nothing
    fg = {'session_id': 's2', 'tool_name': 'Bash', 'tool_input': {'command': 'ls'}, 'tool_response': 'a b'}
    check('  a foreground call is nothing', (N.post(fg, t0), N.record(fg, {}, t0)), ('', False))

    # through the launcher: the JSON the harness reads
    os.environ['NANNY_DIR'] = os.path.join(d, 'state2')
    _o6, ev6 = start(d, 'b6', 'python lock.py', 'the build lock still holds\n')
    r = subprocess.run([BASH, os.path.join(HOOKS, 'nanny.sh')], input=json.dumps(dict(ev6, hook_event_name='PostToolUse')),
                       capture_output=True, text=True, timeout=30, env=os.environ)
    got = json.loads(r.stdout or '{}')
    check('  the launcher prints additionalContext for PostToolUse and exits 0',
          (r.returncode, 'still holds' in got.get('hookSpecificOutput', {}).get('additionalContext', '')), (0, True))
    r = subprocess.run([BASH, os.path.join(HOOKS, 'nanny.sh')], input=json.dumps({'session_id': 's9', 'hook_event_name': 'Stop'}),
                       capture_output=True, text=True, timeout=30, env=os.environ)
    held_r = subprocess.run([BASH, os.path.join(HOOKS, 'nanny.sh')], input=json.dumps({'session_id': 's1', 'hook_event_name': 'Stop'}),
                            capture_output=True, text=True, timeout=30, env=dict(os.environ, NANNY_DIR=os.path.join(d, 'state2')))
    check('  the event names the hook: a Stop with nothing flagged prints nothing; one with a flagged run prints the block',
          (r.returncode, r.stdout.strip(), json.loads(held_r.stdout or '{}').get('decision')), (0, '', 'block'))
    print(f'{len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
