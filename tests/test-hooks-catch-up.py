#!/usr/bin/env python3
"""The catch-up hook (plugins/gene-hooks/hooks/catch-up-hook.sh over catch-up.sh; in the plugin since 1.35.0,
SMADPickleBot #1810): a checkout another session pushed under is fast-forwarded on a prompt and at a session's start
and the session is told what moved; one that cannot safely move is told why; and the hook stands down where the box
or the project still runs its own. Every case on a bare origin and two clones in a temp folder.

Run:  python tests/test-hooks-catch-up.py      (prints PASS/FAIL per case and `N failure(s)`)
"""
import json
import os
import shutil
import tempfile

import bounded

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, '..', 'plugins', 'gene-hooks', 'hooks', 'catch-up-hook.sh')
BASH = shutil.which('bash') or 'bash'
failures = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ('' if ok else f'\n        got  {got!r}\n        want {want!r}'))
    if not ok:
        failures.append(label)


def git(repo, *args):
    return bounded.run(['git', '-C', repo, '-c', 'user.name=T', '-c', 'user.email=t@example.invalid',
                        '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', *args],
                       capture_output=True, text=True, timeout=60).stdout.strip()


def commit(repo, name, text, msg):
    with open(os.path.join(repo, name), 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    git(repo, 'add', name)
    git(repo, 'commit', '-q', '-m', msg)


def world():
    """(mine, theirs): two clones of one bare origin, `theirs` one pushed commit ahead of `mine`."""
    root = tempfile.mkdtemp()
    origin, mine, theirs = (os.path.join(root, n) for n in ('origin.git', 'mine', 'theirs'))
    bounded.run(['git', 'init', '-q', '--bare', '-b', 'main', origin], capture_output=True, timeout=60)
    bounded.run(['git', 'clone', '-q', origin, theirs], capture_output=True, timeout=60)
    commit(theirs, 'a.txt', 'one\n', 'first')
    git(theirs, 'push', '-q', 'origin', 'HEAD:main')
    bounded.run(['git', 'clone', '-q', origin, mine], capture_output=True, timeout=60)
    commit(theirs, 'b.txt', 'two\n', 'second')
    git(theirs, 'push', '-q', 'origin', 'HEAD:main')
    return mine, theirs


def run(project, event='UserPromptSubmit', env=None, settings=''):
    e = {k: v for k, v in os.environ.items() if k != 'CLAUDE_NO_CATCH_UP'}
    e.update(CLAUDE_PROJECT_DIR=project.replace('\\', '/'),
             GENE_HOOKS_USER_SETTINGS=settings or os.path.join(project, 'no-settings.json').replace('\\', '/'), **(env or {}))
    r = bounded.run([BASH, HOOK], input=json.dumps({'hook_event_name': event}), capture_output=True, text=True, timeout=60, env=e)
    try:
        out = json.loads(r.stdout) if r.stdout.strip() else {}
    except ValueError:
        out = {'unparsed': r.stdout[:200]}
    return r.returncode, out


def main():
    mine, _ = world()
    code, out = run(mine)
    said = out.get('hookSpecificOutput', {})
    check('the positive case: on a prompt the checkout is fast-forwarded and the session told what moved',
          (code, git(mine, 'log', '--format=%s', '-1'), said.get('hookEventName'), 'b.txt' in said.get('additionalContext', ''),
           out.get('systemMessage', '')[:36]),
          (0, 'second', 'UserPromptSubmit', True, 'git: caught up 1 commit(s) on main -'))
    check('  a second prompt inside two minutes fetches nothing and says nothing', run(mine), (0, {}))

    mine, _ = world()
    code, out = run(mine, 'SessionStart')
    check("  at a session's start too, under that event's name",
          (code, git(mine, 'log', '--format=%s', '-1'), out.get('hookSpecificOutput', {}).get('hookEventName')), (0, 'second', 'SessionStart'))

    mine, _ = world()
    commit(mine, 'c.txt', 'mine\n', 'my own')
    code, out = run(mine)
    check('  a branch with its own unpushed commit is not moved, and the session is told why',
          (code, git(mine, 'log', '--format=%s', '-1'), 'unpushed commit' in out.get('systemMessage', '')), (0, 'my own', True))

    mine, _ = world()
    check('  CLAUDE_NO_CATCH_UP turns it off', (run(mine, env={'CLAUDE_NO_CATCH_UP': '1'}), git(mine, 'log', '--format=%s', '-1')),
          ((0, {}), 'first'))
    own = os.path.join(mine, 'user-settings.json')
    with open(own, 'w', encoding='utf-8') as f:
        json.dump({'hooks': {'UserPromptSubmit': [{'hooks': [{'command': 'bash "D:/x/claude-shared/hooks/catch-up-hook.sh"'}]}]}}, f)
    check("  while the box's settings still wire its own copy, the plugin's stands down",
          (run(mine, settings=own.replace('\\', '/')), git(mine, 'log', '--format=%s', '-1')), ((0, {}), 'first'))
    os.remove(own)
    os.makedirs(os.path.join(mine, '.claude', 'hooks'))
    with open(os.path.join(mine, '.claude', 'hooks', 'fetch-origin.sh'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('#!/usr/bin/env bash\nbash catch-up.sh "$PWD"\n')
    check("  in a project whose own hook calls catch-up.sh, it is left to that hook",
          (run(mine), git(mine, 'log', '--format=%s', '-1')), ((0, {}), 'first'))
    check('  any other event is ignored', run(mine, 'Stop'), (0, {}))
    check('  a folder that is no git checkout is silent', run(tempfile.mkdtemp()), (0, {}))
    print(f'{len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
