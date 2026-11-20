#!/usr/bin/env python3
"""session-role.py (Gene, 10/25/26: a session's role comes from sessions.md through code, never from its memory).
Pinned, the positive case first: Snow White's folder gets her role, the profiler's duty in it; the V: drive and
Black Widow's worktree; the Mac by folder name on macOS only; a subfolder (a worktree, a spawned session) and an
unknown folder get nothing; the hook's JSON is SessionStart's additionalContext; a broken event says nothing.

In the plugin since SMADPickleBot #1810: the manifest is the one the project's `.claude/gene-hooks.toml` names
(`[sessions] manifest`); a temp project proves a named one is read and that none, or a missing file, says nothing. The
two cases on the real sessions.md run where a claude-shared checkout sits beside this repository, and say SKIP where
none does (a runner).

Run:  python tests/test-hooks-session-role.py      (PASS/FAIL per case, then `N failure(s)`)
"""
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, '..', 'plugins', 'gene-hooks', 'hooks')
spec = importlib.util.spec_from_file_location('session_role', os.path.join(HOOKS, 'session-role.py'))
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)
REAL = os.path.normpath(os.path.join(HERE, '..', '..', 'claude-shared', 'sessions.md'))
failures = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ('' if ok else f'\n        got  {got!r}\n        want {want!r}'))
    if not ok:
        failures.append(label)


TEXT = """**Roles: what each session owns** (intro)

- **Snow White** (SMADPickleBot): CI/CD; **the release profiler and optimizer**: after every release, the profile.
  A second line of hers.
- **Black Widow** (SMADPickleBot-app): the app and UI.
- **Tim Apple** (the Mac mini): store builds.

**Where each session works** (intro)
- `D:\\code\\SMADPickleBot` = Snow White
- `V:\\SMADPickleBot` = Snow White
- `D:\\code\\SMADPickleBot-app` = Black Widow
- `mac:SMADPickleBot` = Tim Apple

**The rules, by direction:**
- **local → local:** not a role.
"""


def main():
    ctx = R.context('D:\\code\\SMADPickleBot', TEXT, 'win32')
    check('the positive case: Snow White\'s folder gets her role, the profiler\'s duty and her second line in it',
          (ctx.startswith('You are Snow White'), 'the release profiler and optimizer' in ctx, 'A second line of hers.' in ctx,
           'Black Widow' in ctx), (True, True, True, False))
    check('  case and slashes and a trailing separator do not matter on Windows; V: is the same folder',
          [R.who(p, TEXT, 'win32') for p in ('d:/code/smadpicklebot/', 'V:\\SMADPickleBot', 'D:\\code\\SMADPickleBot-app')],
          ['Snow White', 'Snow White', 'Black Widow'])
    check('  the Mac by folder name, on macOS alone',
          (R.who('/Users/gene/code/SMADPickleBot', TEXT, 'darwin'), R.who('/Users/gene/code/SMADPickleBot', TEXT, 'linux')),
          ('Tim Apple', ''))
    both = R.SHELL_WRITES + ' ' + R.DIRECT
    check('  a worktree or spawned session in a subfolder, and an unknown folder, get no role, only the Edit and Write and the direct-message rules',
          (R.context('D:\\code\\SMADPickleBot\\.claude\\worktrees\\x', TEXT, 'win32'), R.context('C:\\tmp', TEXT, 'win32')),
          (both, both))
    check('  every session is told file content goes through Edit and Write, and that auto mode\'s shell-edit advice does not hold here',
          (ctx.endswith(both), 'Edit tool' in R.SHELL_WRITES, 'sed -i' in R.SHELL_WRITES, 'auto-mode' in R.SHELL_WRITES),
          (True, True, True, True))
    check('  #472: every session is told to talk to peers by direct message, mail the fallback',
          ('SendMessage' in R.DIRECT, 'send_message' in R.DIRECT, 'only when' in R.DIRECT), (True, True, True))
    check('  a session with a folder and no role line still learns who it is', R.role('Nobody', TEXT), '')
    if os.path.isfile(REAL):
        real = open(REAL, encoding='utf-8').read()
        check('  #472: the real manifest holds the direct-message map',
              ('**Direct messages are the rule**' in real, '| From → To | Direct path | Fallback |' in real), (True, True))
        check("  the real sessions.md: Snow White's folder names her, and her line carries the profile duty",
              (R.who('D:\\code\\SMADPickleBot', real, 'win32'), 'profiler' in R.role('Snow White', real)), ('Snow White', True))
    else:
        print('  SKIP  the real sessions.md (no claude-shared checkout beside this repository)')

    def start(event):
        out = io.StringIO()
        sys.stdin = io.StringIO(event if isinstance(event, str) else json.dumps(event))
        with contextlib.redirect_stdout(out):
            rc = R.main(['start'])
        return rc, out.getvalue()

    with tempfile.TemporaryDirectory() as d:
        own = os.path.join(d, 'own.md')
        with open(own, 'w', encoding='utf-8') as f:
            f.write(TEXT)
        R.SESSIONS = own   # the suite's own manifest, whatever the event's folder
        try:
            rc, said = start({'cwd': 'D:\\code\\SMADPickleBot', 'source': 'compact'})
            got = json.loads(said or '{}').get('hookSpecificOutput', {})
            check('  start prints SessionStart additionalContext, a compaction too',
                  (got.get('hookEventName'), got.get('additionalContext', '').startswith('You are Snow White')),
                  ('SessionStart', True))
            check('  a broken event exits 0 and prints nothing', start('not json'), (0, ''))
        finally:
            R.SESSIONS = ''
        # SMADPickleBot #1810: the manifest is the one the project's .claude/gene-hooks.toml names
        shared = os.path.join(d, 'shared')
        os.makedirs(shared)
        proj = os.path.join(d, 'proj')
        with open(os.path.join(shared, 'sessions.md'), 'w', encoding='utf-8') as f:
            f.write(TEXT.replace('`mac:SMADPickleBot` = Tim Apple', f'`{proj}` = Tim Apple'))

        def project(name, config):
            top = os.path.join(d, name)
            os.makedirs(os.path.join(top, '.claude', 'deep'))
            if config is not None:
                with open(os.path.join(top, '.claude', 'gene-hooks.toml'), 'w', encoding='utf-8') as f:
                    f.write(config)
            return top

        named = project('proj', '[sessions]\nmanifest = "../shared/sessions.md"\n')
        rc, said = start({'cwd': named})
        ctx = json.loads(said or '{}').get('hookSpecificOutput', {}).get('additionalContext', '')
        check('the configured manifest is the one read: the folder it lists gets its session and role',
              (rc, ctx.startswith('You are Tim Apple'), 'store builds.' in ctx, R.manifest(named)),
              (0, True, True, os.path.normpath(os.path.join(shared, 'sessions.md'))))
        rc, said = start({'cwd': os.path.join(named, '.claude', 'deep')})
        both = R.SHELL_WRITES + ' ' + R.DIRECT
        check('  a subfolder of that project finds the same manifest and, owning no role, gets the two rules alone',
              (rc, json.loads(said or '{}').get('hookSpecificOutput', {}).get('additionalContext')), (0, both))
        check('no [sessions] table, a manifest that is not there, and no config at all: exit 0, nothing said',
              [start({'cwd': project(*p)}) for p in (('mailonly', '[mail]\nscript = "scripts/mail.py"\n'),
                                                    ('gone', '[sessions]\nmanifest = "../no-such/sessions.md"\n'),
                                                    ('bare', None))],
              [(0, '')] * 3)
    print(f'{len(failures)} failure(s)' + (':' if failures else ''))
    for f in failures:
        print(f'  {f}')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
