#!/usr/bin/env python3
"""A hook that moved into the plugin stands down while a box still wires its own copy (plugins/gene-hooks/hooks/
moved.lib.sh; SMADPickleBot #1810), so no hook runs twice during the move, and runs once the box's settings no longer
name it. Through the real wrappers, as Claude Code runs them.

Run:  python tests/test-hooks-moved.py      (prints PASS/FAIL per case and `N failure(s)`)
"""
import json
import os
import shutil
import tempfile

import bounded

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, '..', 'plugins', 'gene-hooks', 'hooks')
BASH = shutil.which('bash') or 'bash'
failures = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ('' if ok else f'\n        got  {got!r}\n        want {want!r}'))
    if not ok:
        failures.append(label)


def settings(tmp, text):
    path = os.path.join(tmp, 'settings.json')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return path.replace('\\', '/')


def run(script, event, user_settings):
    env = dict(os.environ, GENE_HOOKS_USER_SETTINGS=user_settings)
    r = bounded.run([BASH, os.path.join(HOOKS, script)], input=json.dumps(event), capture_output=True, text=True,
                    timeout=30, env=env)
    return r.returncode


def main():
    tmp = tempfile.mkdtemp()
    write = {'tool_name': 'Bash', 'tool_input': {'command': 'echo x > a.txt'}}
    none = os.path.join(tmp, 'missing.json').replace('\\', '/')
    clean = settings(tmp, json.dumps({'hooks': {'Stop': [{'hooks': [{'command': 'bash "D:/x/hooks/other.sh"'}]}]}}))
    check('the positive case: with no copy of its own on the box, the plugin refuses a shell write',
          run('no-shell-writes.sh', write, none), 2)
    check('  settings that name other hooks alone change nothing', run('no-shell-writes.sh', write, clean), 2)
    own = settings(tmp, json.dumps({'hooks': {'PreToolUse': [{'hooks': [
        {'command': 'bash "D:/code/claude-shared/hooks/no-shell-writes.sh"'}]}]}}))
    check("  while the box's settings still name its own copy, the plugin's stands down (the box's copy decides)",
          run('no-shell-writes.sh', write, own), 0)
    check('  the stand-down is per hook: another moved hook still runs',
          run('reminder-gate.sh', {'stop_hook_active': False}, own), 0)
    check('  a plain command passes either way', [run('no-shell-writes.sh', {'tool_name': 'Bash', 'tool_input': {'command': 'git status'}}, s)
                                                 for s in (none, own)], [0, 0])
    print(f'{len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
