#!/usr/bin/env python3
"""plugins/gene-hooks/hooks/unpushed-commits.sh (1.12.0; SMADPickleBot #642, 10/30/26: the scan left the prompt path).
A fixture of sibling repos under one parent, each with a bare origin, run through bash as the host runs the hook.
Standard library only; the positive case first.

    python tests/test-hooks-unpushed-commits.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, '..', 'plugins', 'gene-hooks', 'hooks', 'unpushed-commits.sh')
BASH = shutil.which('bash') or 'bash'
FAILS = []


def check(label, got, want):
    print(('PASS  ' if got == want else 'FAIL  ') + label + ('' if got == want else f'\n        got  {got!r}\n        want {want!r}'))
    if got != want:
        FAILS.append(label)


def git(*args, cwd):
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, msg):
    with open(os.path.join(repo, 'f.txt'), 'a', encoding='utf-8') as f:
        f.write('x\n')
    git('add', 'f.txt', cwd=repo)
    git('-c', 'user.name=Claude', '-c', 'user.email=noreply@anthropic.com', 'commit', '-q', '-m', msg, cwd=repo)


def repo(parent, name, bare_dir):
    """A clone-like checkout of `name` under `parent` with a bare origin and one pushed commit."""
    bare = os.path.join(bare_dir, f'{name}.git')
    git('init', '-q', '--bare', bare, cwd=bare_dir)
    path = os.path.join(parent, name)
    git('init', '-q', path, cwd=parent)
    git('remote', 'add', 'origin', bare, cwd=path)
    commit(path, 'first')
    branch = git('rev-parse', '--abbrev-ref', 'HEAD', cwd=path)
    git('push', '-q', '-u', 'origin', branch, cwd=path)
    return path, branch


def fire(event, root, tmp, **env):
    e = dict(os.environ, CLAUDE_PROJECT_DIR=root, TMPDIR=tmp.replace('\\', '/'))
    e.pop('CLAUDE_UNPUSHED_HOOK_OFF', None)
    e.update(env)
    r = subprocess.run([BASH, HOOK], input=json.dumps({'hook_event_name': event}), capture_output=True, text=True, env=e, timeout=60)
    return r.stdout.strip()


def said(out):
    return json.loads(out)['systemMessage'] if out else ''


def main():
    d = tempfile.mkdtemp()
    parent, bares, tmp = os.path.join(d, 'code'), os.path.join(d, 'bares'), os.path.join(d, 'tmp')
    for p in (parent, bares, tmp):
        os.makedirs(p)
    a, branch = repo(parent, 'ProjA', bares)
    b, _ = repo(parent, 'ProjB', bares)
    commit(a, 'held here\n\nSession: Snow White')
    cache = os.path.join(tmp, 'claude-unpushed-' + re.sub(r'[^A-Za-z0-9]', '_', a.replace('\\', '/')))

    m = said(fire('SessionStart', a, tmp))
    check('the positive case: SessionStart scans inline and names the project, its branch, the count and the session',
          (f'ProjA ({branch}, 1 unpushed, by Snow White; this session\'s project)' in m, 'ProjB' in m, os.path.isfile(cache)),
          (True, False, True))
    check('  the next prompt says nothing: the list is what it last said', fire('UserPromptSubmit', a, tmp), '')

    # #642: another commit lands; the cache is older than RECHECK. The prompt answers at once from what the last scan
    # found (nothing new to say), starts the scanner detached, and the prompt after the scan speaks.
    commit(a, 'second\n\nSession: Snow White')
    old = time.time() - 700
    os.utime(cache, (old, old))
    before = os.stat(cache).st_mtime
    check('  a stale cache answers at once from the last scan, the scanner detached', fire('UserPromptSubmit', a, tmp), '')
    deadline = time.time() + 15
    while os.stat(cache).st_mtime <= before and time.time() < deadline:
        time.sleep(0.2)
    check('  the detached scan rewrote the cache within 15 s', os.stat(cache).st_mtime > before, True)
    with open(cache, encoding='utf-8') as f:
        found = f.read().strip()
    check('  with the new count', '2 unpushed, by Snow White' in found, True)
    check('  and the next prompt says it', '2 unpushed' in said(fire('UserPromptSubmit', a, tmp)), True)
    check('  once', fire('UserPromptSubmit', a, tmp), '')

    # The scanner mode itself, and the inline switch.
    commit(a, 'third\n\nSession: Snow White')
    check('  scanner mode writes the cache and says nothing', (fire('UserPromptSubmit', a, tmp, CLAUDE_UNPUSHED_SCAN='1'),
                                                               '3 unpushed' in open(cache, encoding='utf-8').read()), ('', True))
    check('  the prompt then speaks the scanner\'s count', '3 unpushed' in said(fire('UserPromptSubmit', a, tmp)), True)
    commit(a, 'fourth\n\nSession: Snow White')
    os.utime(cache, (old, old))
    check('  CLAUDE_UNPUSHED_INLINE looks on the prompt itself', '4 unpushed' in said(fire('UserPromptSubmit', a, tmp, CLAUDE_UNPUSHED_INLINE='1')), True)

    # Another project's held commit, with no Session trailer, is named as its own sessions' to push.
    commit(b, 'theirs')
    os.utime(cache, (old, old))
    m = said(fire('UserPromptSubmit', a, tmp, CLAUDE_UNPUSHED_INLINE='1'))
    check('  another project with an unpushed commit and no trailer',
          ('ProjB' in m and 'a session that left no Session trailer' in m and 'another project: its own sessions push it' in m,
           'message that session' in json.loads(fire('SessionStart', a, tmp))['hookSpecificOutput']['additionalContext']),
          (True, True))
    check('  the kill switch', fire('SessionStart', a, tmp, CLAUDE_UNPUSHED_HOOK_OFF='1'), '')

    shutil.rmtree(d, ignore_errors=True)
    print(f'\n{len(FAILS)} failure(s)')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
