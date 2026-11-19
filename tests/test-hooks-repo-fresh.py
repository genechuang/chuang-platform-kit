#!/usr/bin/env python3
"""The freshness hook (plugins/gene-hooks/hooks/repo-fresh.sh over catch-up.sh; in the plugin since 1.36.0,
SMADPickleBot #1810; one project's fetch-origin.sh until then): on a prompt and at a session's start a checkout
another session pushed under is fast-forwarded and the session told what moved, one that cannot safely move is told
why and how far behind it is, the session channel beside the project (the sibling `<Project>-sessions`) rings when a
note waits, a project with no channel is told nothing of one, and the hook stands down where the box or the project
still runs its own. Every case on bare origins and clones in a temp folder, every fetch inline.

Run:  python tests/test-hooks-repo-fresh.py      (prints PASS/FAIL per case and `N failure(s)`)
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


def git(repo, *args):
    return bounded.run(['git', '-C', repo, '-c', 'user.name=T', '-c', 'user.email=t@example.invalid',
                        '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', *args],
                       capture_output=True, text=True, timeout=60).stdout.strip()


def commit(repo, name, text, msg):
    with open(os.path.join(repo, name), 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    git(repo, 'add', name)
    git(repo, 'commit', '-q', '-m', msg)


def pair(root, name, ahead=True):
    """(mine, theirs): two clones of one bare origin under `root`, mine named `name`; with `ahead`, theirs has pushed
    one commit mine lacks."""
    origin, mine, theirs = (os.path.join(root, n) for n in (f'{name}.git', name, f'{name}-theirs'))
    bounded.run(['git', 'init', '-q', '--bare', '-b', 'main', origin], capture_output=True, timeout=60)
    bounded.run(['git', 'clone', '-q', origin, theirs], capture_output=True, timeout=60)
    commit(theirs, 'a.txt', 'one\n', 'first')
    git(theirs, 'push', '-q', 'origin', 'HEAD:main')
    bounded.run(['git', 'clone', '-q', origin, mine], capture_output=True, timeout=60)
    if ahead:
        commit(theirs, 'b.txt', 'two\n', 'second')
        git(theirs, 'push', '-q', 'origin', 'HEAD:main')
    return mine, theirs


def world(ahead=True):
    return pair(tempfile.mkdtemp(), 'proj', ahead)


def run(project, event='UserPromptSubmit', env=None, settings='', script='repo-fresh.sh'):
    drop = ('CLAUDE_NO_CATCH_UP', 'CLAUDE_ORIGIN_HOOK_OFF', 'CLAUDE_SESSIONS_DIR', 'CLAUDE_SHARED_DIR')
    e = {k: v for k, v in os.environ.items() if k not in drop}
    e.update(CLAUDE_PROJECT_DIR=project.replace('\\', '/'), CLAUDE_FETCH_INLINE='1',
             CLAUDE_SHARED_DIR=os.path.join(project, 'no-shared').replace('\\', '/'),
             GENE_HOOKS_USER_SETTINGS=settings or os.path.join(project, 'no-settings.json').replace('\\', '/'), **(env or {}))
    r = bounded.run([BASH, os.path.join(HOOKS, script)], input=json.dumps({'hook_event_name': event}), capture_output=True,
                    text=True, timeout=90, env=e)
    try:
        out = json.loads(r.stdout) if r.stdout.strip() else {}
    except ValueError:
        out = {'unparsed': r.stdout[:300]}
    return r.returncode, out


def head(repo):
    return git(repo, 'log', '--format=%s', '-1')


def main():
    mine, _ = world()
    code, out = run(mine)
    said = out.get('hookSpecificOutput', {})
    check('the positive case: on a prompt the checkout is fast-forwarded and the session told what moved',
          (code, head(mine), said.get('hookEventName'), 'b.txt' in said.get('additionalContext', ''), out.get('systemMessage', '')[:36]),
          (0, 'second', 'UserPromptSubmit', True, 'git: caught up 1 commit(s) on main -'))
    check('  a second prompt inside two minutes fetches nothing and says nothing', run(mine), (0, {}))

    mine, _ = world()
    code, out = run(mine, 'SessionStart')
    check("  at a session's start too, under that event's name",
          (code, head(mine), out.get('hookSpecificOutput', {}).get('hookEventName')), (0, 'second', 'SessionStart'))

    mine, _ = world()
    commit(mine, 'c.txt', 'mine\n', 'my own')
    code, out = run(mine)
    check('  a branch with its own unpushed commit is not moved: told how far behind and why it was not caught up',
          (code, head(mine), 'is 1 commit(s) behind origin/main' in out.get('systemMessage', ''),
           'auto catch-up skipped' in out.get('systemMessage', '')), (0, 'my own', True, True))

    mine, _ = world()
    code, out = run(mine, env={'CLAUDE_NO_CATCH_UP': '1'})
    check('  CLAUDE_NO_CATCH_UP moves nothing and still says it is behind',
          (code, head(mine), 'is 1 commit(s) behind' in out.get('systemMessage', '')), (0, 'first', True))
    mine, _ = world()
    check('  CLAUDE_ORIGIN_HOOK_OFF turns the whole hook off', (run(mine, env={'CLAUDE_ORIGIN_HOOK_OFF': '1'}), head(mine)), ((0, {}), 'first'))
    os.makedirs(os.path.join(mine, '.claude', 'hooks'))
    off = os.path.join(mine, '.claude', 'hooks', '.disabled')
    open(off, 'w').close()
    check("  so does the project's .claude/hooks/.disabled", (run(mine), head(mine)), ((0, {}), 'first'))
    os.remove(off)

    own = os.path.join(mine, 'user-settings.json')
    with open(own, 'w', encoding='utf-8') as f:
        json.dump({'hooks': {'UserPromptSubmit': [{'hooks': [{'command': 'bash "D:/x/claude-shared/hooks/catch-up-hook.sh"'}]}]}}, f)
    check("  while the box's settings still wire its own catch-up hook, the plugin's stands down",
          (run(mine, settings=own.replace('\\', '/')), head(mine)), ((0, {}), 'first'))
    os.remove(own)
    with open(os.path.join(mine, '.claude', 'settings.json'), 'w', encoding='utf-8') as f:
        json.dump({'hooks': {'UserPromptSubmit': [{'hooks': [{'command': 'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/fetch-origin.sh"'}]}]}}, f)
    check("  while the project's own settings wire its fetch-origin hook, the plugin's stands down",
          (run(mine), head(mine)), ((0, {}), 'first'))
    os.remove(os.path.join(mine, '.claude', 'settings.json'))
    code, out = run(mine, script='catch-up-hook.sh')
    check("  1.35.0's name still answers, as the one hook", (code, head(mine), out.get('systemMessage', '')[:20]), (0, 'second', 'git: caught up 1 com'))

    # the session channel beside the project: the sibling <Project>-sessions
    root = tempfile.mkdtemp()
    proj, _ = pair(root, 'proj', ahead=False)
    check('  a current checkout with no session channel beside it is told nothing of one', run(proj), (0, {}))
    proj2root = tempfile.mkdtemp()
    proj2, _ = pair(proj2root, 'proj', ahead=False)
    os.makedirs(os.path.join(proj2, '.claude'))
    with open(os.path.join(proj2, '.claude', 'gene-hooks.toml'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('# this project keeps a session channel\nsessions = true\n')
    code, out = run(proj2)
    check("  a project whose config says it keeps one, with no clone, is told so",
          (code, out.get('systemMessage', '')[:45]), (0, 'session channel: sessions repo NOT CLONED at '))
    root3 = tempfile.mkdtemp()
    proj3, _ = pair(root3, 'proj', ahead=False)
    notes, _ = pair(root3, 'proj-sessions', ahead=True)
    code, out = run(proj3)
    msg = out.get('systemMessage', '')
    check('  a note waiting in the sibling sessions repo rings, naming that folder and no other',
          (code, 'session-notes.md in proj-sessions (1 new' in msg or 'caught up beside the project' in msg, 'SMADPickleBot' in json.dumps(out)),
          (0, True, False))
    check('  a folder that is no git checkout is silent', run(tempfile.mkdtemp()), (0, {}))
    print(f'{len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
