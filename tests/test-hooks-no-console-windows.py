#!/usr/bin/env python3
"""The SessionStart hook plugins/gene-hooks/hooks/no-console-windows.sh (Gene, 10/20/26: "no-console check at session
start"): claude-shared's no-console-windows.ps1 over the box's tasks and the session's repo, a finding told, never a
block; nothing at all where the check or PowerShell is missing (the Mac, a cloud session).

Run:  python tests/test-hooks-no-console-windows.py      (prints PASS/FAIL per case and `N failure(s)`)
"""
import json
import os
import shutil
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, '..', 'plugins', 'gene-hooks', 'hooks', 'no-console-windows.sh')
HOOKS_JSON = os.path.join(HERE, '..', 'plugins', 'gene-hooks', 'hooks', 'hooks.json')
BASH = shutil.which('bash') or 'bash'
failures = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ('' if ok else f'\n        got  {got!r}\n        want {want!r}'))
    if not ok:
        failures.append(label)


def run(env_extra, cwd='x'):
    r = subprocess.run([BASH, HOOK], input=json.dumps({'hook_event_name': 'SessionStart', 'cwd': cwd}),
                       capture_output=True, text=True, timeout=60, env=dict(os.environ, **env_extra))
    return r.returncode, r.stdout


def main():
    with open(HOOKS_JSON, encoding='utf-8') as f:
        hooks = json.load(f)['hooks']['SessionStart'][0]['hooks']
    check('registered at SessionStart through boot.sh, with room for PowerShell to start',
          [(h['timeout']) for h in hooks if h['command'].endswith('no-console-windows.sh')], [15])
    with tempfile.TemporaryDirectory() as d:
        check('no check script on this box: nothing, exit 0', run({'NO_CONSOLE_CHECK': os.path.join(d, 'none.ps1')}), (0, ''))
    if shutil.which('powershell.exe') and os.path.exists('D:/code/claude-shared/hooks/no-console-windows.ps1'):
        repo = tempfile.mkdtemp()
        subprocess.run(['git', 'init', '-q', repo], check=True)
        with open(os.path.join(repo, 'a.py'), 'w', encoding='utf-8') as f:
            f.write('import subprocess\nsubprocess.Popen(["git"], creationflags=subprocess.DETACHED_PROCESS)\n')
        subprocess.run(['git', '-C', repo, 'add', '.'], check=True)
        code, out = run({}, cwd=repo)
        told = json.loads(out) if out else {}
        check('a console-less launch in the repo: told as one line and listed in context, exit 0',
              (code, 'No console windows: ' in told.get('systemMessage', ''),
               'DETACHED_PROCESS' in told.get('hookSpecificOutput', {}).get('additionalContext', '')), (0, True, True))
        shutil.rmtree(repo, ignore_errors=True)
    else:
        print('  (no PowerShell or claude-shared here: the finding case runs on the Windows desktop only)')
    print(f'\n{len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
