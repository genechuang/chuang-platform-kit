#!/usr/bin/env python3
"""The foreground guard (plugins/gene-hooks/hooks/foreground-guard.py, run from stamp-gate.sh on every Bash and
PowerShell call; Gene, 10/21/26 12:05 AM PT: "run long running processes in the background ... add it to a hook"): a
known long runner, or a call whose timeout is two minutes or more, is refused in the foreground and allowed with
run_in_background; short commands pass.

Run:  python tests/test-hooks-foreground-guard.py      (prints PASS/FAIL per case and `N failure(s)`)
"""
import json
import os
import shutil
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, '..', 'plugins', 'gene-hooks', 'hooks')
BASH = shutil.which('bash') or 'bash'
failures = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ('' if ok else f'\n        got  {got!r}\n        want {want!r}'))
    if not ok:
        failures.append(label)


def guard(command, tool='Bash', **extra):
    event = {'tool_name': tool, 'tool_input': dict({'command': command}, **extra)}
    r = subprocess.run([BASH, os.path.join(HOOKS, 'foreground-guard.sh')], input=json.dumps(event), capture_output=True,
                       text=True, timeout=30)
    return r.returncode


def main():
    check('the positive case: a foreground ship.py is refused', guard('python app/scripts/ship.py --phone --push'), 2)
    check('  the same in the background is allowed', guard('python app/scripts/ship.py --phone --push', run_in_background=True), 0)
    check('  a foreground git push is refused (the pre-push gate), in a chain too',
          [guard('git push'), guard('git add a && git commit -m x && git push 2>&1 | tail -3'), guard('git -C /d/x push -q')], [2, 2, 2])
    check('  a push to hopper runs no gate: allowed', guard('git push -q origin HEAD:hopper'), 0)
    check('  the other long runners, foreground',
          [guard(c) for c in ('python scripts/desktop-tests.py', 'python scripts/build-images.py', 'python scripts/run-tests.py',
                              'python -m pytest -n auto', 'gh run watch 123 --exit-status', 'gcloud builds submit .',
                              'terraform -chdir=infra/terraform plan', 'eas build -p ios', 'npx expo export', 'npx jest --ci',
                              'python app/scripts/artifact.py build --full', 'python app/scripts/sim_smoke.py abc')], [2] * 12)
    check('  a two-minute timeout is long whatever the command', guard('python tests/test-x.py', timeout=120000), 2)
    check('  PowerShell is guarded too', guard('python scripts/desktop-tests.py', tool='PowerShell'), 2)
    check('short ones pass: status, a run view, one test file, one jest file, pytest on one file',
          [guard(c) for c in ('git status -s', 'gh run view 1 --json jobs', 'python tests/test-x.py',
                              'npx jest src/lib/__tests__/a.test.ts', 'python -m pytest tests/test_a.py')], [0] * 5)
    check('  run by path, after cd, env or from PowerShell: still refused',
          [guard(c) for c in ('cd /d/code/x && python scripts/desktop-tests.py', 'PYTHONIOENCODING=utf-8 python scripts/build-images.py',
                              'python -u /d/code/x/app/scripts/ship.py', '& python "D:\\code\\x\\scripts\\run-tests.py"',
                              './scripts/build-images.py')], [2] * 5)
    check('naming a runner is not running it: grep, cat, sed, git log on its path pass (10/21/26: grep was refused)',
          [guard(c) for c in ('grep -n "def " scripts/desktop-tests.py', 'cat app/scripts/ship.py | head',
                              'docker images | grep test; grep -n IMAGE /d/code/x/scripts/desktop-tests.py',
                              'git log --oneline -- scripts/build-images.py', 'sed -n 1,20p scripts/run-tests.py')], [0] * 5)
    check('  one suite by name through run-tests.py --only passes', guard('python scripts/run-tests.py --only push-decider'), 0)
    check('  not a shell tool: never looked at', guard('python app/scripts/ship.py', tool='Read'), 0)
    r = subprocess.run([BASH, os.path.join(HOOKS, 'foreground-guard.sh')], input='not json', capture_output=True, text=True, timeout=30)
    check('  an unreadable event: allowed, never a trap', r.returncode, 0)
    # The guard rides stamp-gate.sh, which every open session runs from the newest install on every call.
    d = tempfile.mkdtemp()
    path = os.path.join(d, 't.jsonl')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(json.dumps({'type': 'user', 'message': {'content': 'go'}}) + '\n')
    ev = {'tool_name': 'Bash', 'transcript_path': path, 'session_id': 's1', 'tool_input': {'command': 'git push'}}
    r = subprocess.run([BASH, os.path.join(HOOKS, 'stamp-gate.sh')], input=json.dumps(ev), capture_output=True, text=True,
                       timeout=30, env=dict(os.environ, TMPDIR=d, TEMP=d, STAMP_GATE_WAIT=''))
    check('stamp-gate.sh refuses a foreground push through the guard, its reason on stderr',
          (r.returncode, 'run_in_background' in r.stderr), (2, True))
    shutil.rmtree(d, ignore_errors=True)
    print(f'\n{len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
