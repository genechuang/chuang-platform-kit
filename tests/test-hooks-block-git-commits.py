#!/usr/bin/env python3
"""The commit-rules hook (plugins/gene-hooks/hooks/block-git-commits.sh; in the plugin since 1.35.0, SMADPickleBot
#1810): the git invocations that have produced wrong commits are refused, everything else passes, and it stands down
while a project's own settings still wire its copy. Through the real script, as Claude Code runs it.

Run:  python tests/test-hooks-block-git-commits.py      (prints PASS/FAIL per case and `N failure(s)`)
"""
import json
import os
import shutil
import tempfile

import bounded

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, '..', 'plugins', 'gene-hooks', 'hooks', 'block-git-commits.sh')
BASH = shutil.which('bash') or 'bash'
AS_CLAUDE = 'git -c user.name=Claude -c user.email=noreply@anthropic.com commit -q -F msg.txt'
failures = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ('' if ok else f'\n        got  {got!r}\n        want {want!r}'))
    if not ok:
        failures.append(label)


def run(command, project, env=None):
    """(exit code, the first line said) for one Bash call's command in `project`."""
    e = {k: v for k, v in os.environ.items() if k != 'CLAUDE_GIT_GUARD_OFF'}
    e.update(CLAUDE_PROJECT_DIR=project.replace('\\', '/'), **(env or {}))
    r = bounded.run([BASH, HOOK], input=json.dumps({'tool_name': 'Bash', 'tool_input': {'command': command}}),
                    capture_output=True, text=True, timeout=30, env=e)
    return r.returncode, (r.stderr.strip().splitlines() or [''])[0][:40]


def main():
    project = tempfile.mkdtemp()
    check('the positive case: a commit not authored as Claude is refused, with the line to run',
          run('git commit -m "x"', project), (2, 'BLOCKED: a Claude session commits as Cla'))
    for label, command in (('git add -A', 'git add -A'), ('git add .', 'git add .'), ('git commit -a', f'{AS_CLAUDE.replace("commit", "commit -a")}'),
                           ('--no-verify', f'{AS_CLAUDE} --no-verify'), ('a force push', 'git push --force origin main'),
                           ('the short force push, last in a compound command', f'{AS_CLAUDE} && git push -f')):
        check(f'  refused: {label}', run(command, project)[0], 2)
    for label, command in (('a commit as Claude', AS_CLAUDE), ('a named file', 'git add .gitignore'), ('a path under .', 'git add ./a/b.py'),
                           ('a force push with a lease', 'git push --force-with-lease origin main'), ('no git at all', 'ls -la'),
                           ('git status', 'git status --short')):
        check(f'  passes: {label}', run(command, project)[0], 0)
    check('  CLAUDE_GIT_GUARD_OFF turns it off', run('git add -A', project, {'CLAUDE_GIT_GUARD_OFF': '1'})[0], 0)
    os.makedirs(os.path.join(project, '.claude', 'hooks'))
    off = os.path.join(project, '.claude', 'hooks', '.disabled')
    open(off, 'w').close()
    check("  the project's .claude/hooks/.disabled turns it off", run('git add -A', project)[0], 0)
    os.remove(off)
    with open(os.path.join(project, '.claude', 'settings.json'), 'w', encoding='utf-8') as f:
        json.dump({'hooks': {'PreToolUse': [{'matcher': 'Bash', 'hooks': [
            {'command': 'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/block-git-commits.sh"'}]}]}}, f)
    check("  while the project's own settings wire its copy, the plugin's stands down (the project's decides)",
          run('git add -A', project)[0], 0)
    with open(os.path.join(project, '.claude', 'settings.json'), 'w', encoding='utf-8') as f:
        json.dump({'hooks': {'PreToolUse': [{'hooks': [{'command': 'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/other.sh"'}]}]}}, f)
    check('  project settings naming other hooks alone change nothing', run('git add -A', project)[0], 2)
    print(f'{len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
