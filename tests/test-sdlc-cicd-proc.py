#!/usr/bin/env python3
"""chuang_sdlc.cicd.proc, one command runner for every pipeline script (SMADPickleBot's pipeline_lib.py until #235,
11/8/26; Gene, 10/22/26 11:50 AM PT: "Do pipeline code abide by code reuse, sharing common library").

Pinned: a command's output comes back as UTF-8 text with an arrow intact; the Windows shell is used for gh and gcloud
alone and no window opens; the caller's own folder is the default; `out` is the stripped stdout on success and '' on a
failure, a missing command or a timeout, never an exception; `parse_json`, `json_out` and `gh_json` are None for
anything that is not JSON; `gh` goes through the kit's throttled cli; `primary_root` finds a worktree's checkout;
`is_link` knows a junction. The host's reports-bucket reads (read_report) stay in its own module and suite.
"""
import os
import subprocess
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))

from chuang_sdlc.cicd import proc as lib  # noqa: E402

FAILURES = []


def eq(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ('' if ok else f"\n        got:  {got!r}\n        want: {want!r}"))
    if not ok:
        FAILURES.append(label)


def test_run_returns_utf8_text_from_the_callers_folder():
    """the positive case: a command's output is UTF-8 text (an arrow survives) and it ran in the caller's own folder"""
    done = lib.run([sys.executable, '-c', "import os,sys; sys.stdout.buffer.write(('\\u2192 ' + os.getcwd()).encode())"])
    eq('ran', (done.returncode, done.stdout.startswith('→ '), os.path.normcase(done.stdout[2:]) == os.path.normcase(os.getcwd())),
       (0, True, True))
    sub = os.path.join(ROOT, 'tests')
    done = lib.run([sys.executable, '-c', "import os; print(os.getcwd())"], cwd=sub)
    eq('  a folder given is the one it runs in', os.path.normcase(done.stdout.strip()), os.path.normcase(sub))


def test_primary_root_finds_the_checkout_every_worktree_belongs_to():
    """#1078, the positive case: the common git dir's parent, absolute or relative to the folder asked; when git cannot say, the worktree's own `.git` file's gitdir pointer (a worktree pruned under a running waiter: 17636c7a's dashboard publisher said "not a git repository"), the path before /.git/; no pointer, or no git on the box: the folder itself"""
    import tempfile
    d = tempfile.mkdtemp()
    main = os.path.join(d, 'main')
    os.makedirs(os.path.join(main, '.git'))
    wt = os.path.join(d, 'wt')
    os.makedirs(wt)
    with open(os.path.join(wt, '.git'), 'w', encoding='utf-8') as f:
        f.write(f"gitdir: {main.replace(os.sep, '/')}/.git/worktrees/wt\n")
    bare = os.path.join(d, 'bare')
    os.makedirs(bare)
    ok = lambda out: (lambda args: types.SimpleNamespace(returncode=0, stdout=out))  # noqa: E731
    no = lambda args: types.SimpleNamespace(returncode=128, stdout='')  # noqa: E731

    def gone(args):
        raise OSError('no git')
    eq('the positive case: git says, absolute or relative',
       (lib.primary_root(wt, run=ok(os.path.join(main, '.git') + '\n')), lib.primary_root(main, run=ok('.git\n'))), (main, main))
    eq('  git cannot say, or is not there: the pointer', (lib.primary_root(wt, run=no), lib.primary_root(wt, run=gone)), (main, main))
    eq('  nothing tells: the folder itself', (lib.primary_root(bare, run=no), lib.primary_root(main, run=no)), (bare, main))


def test_shell_only_for_the_windows_shims():
    """on Windows the shell is used for gh and gcloud alone and no window opens; a caller's own setting wins"""
    seen = []
    keep, keep_os = lib.subprocess.run, lib.os.name
    lib.subprocess.run = lambda args, **kw: seen.append((args[0], kw.get('shell'), kw.get('creationflags'), kw.get('cwd'))) or types.SimpleNamespace(returncode=0, stdout='', stderr='')
    keep_which, keep_found = lib.shutil.which, dict(lib._FOUND)
    try:
        lib.os.name = 'nt'
        lib._FOUND.clear()
        lib.shutil.which = lambda c: {'gh': r'C:\bin\gh.cmd', 'gcloud': r'C:\sdk\gcloud.cmd'}.get(c)
        lib.run(['gh', 'api', 'x'])
        lib.run(['git', 'status'])
        lib.run(['gcloud', 'x'], shell=False, cwd=None)
        lib._FOUND.clear()
        lib.shutil.which = lambda c: {'gh': r'C:\Program Files\GitHub CLI\gh.exe'}.get(c)
        lib.run(['gh', 'api', 'x'])
        lib.run(['gcloud', 'x'])
        lib._FOUND.clear()
        lib.shutil.which = lambda c: {'npx': r'C:\Program Files\nodejs\npx.CMD', 'npm': r'C:\Program Files\nodejs\npm.CMD'}.get(c)
        lib.run(['npx', 'jest', '--ci'])
        lib.run(['npm', 'ci'])
    finally:
        lib.subprocess.run, lib.os.name, lib.shutil.which = keep, keep_os, keep_which
        lib._FOUND.clear()
        lib._FOUND.update(keep_found)
    # NO_WINDOW is the platform's own flag, read once at import: Windows' CREATE_NO_WINDOW there, 0 on Linux.
    eq('shims', seen, [('gh', True, lib.NO_WINDOW, None), ('git', False, lib.NO_WINDOW, None),
                       ('gcloud', False, lib.NO_WINDOW, None),
                       # gh.exe runs directly (the desktop's: no cmd.exe to cut its URL); a shim not on PATH keeps the shell
                       ('gh', False, lib.NO_WINDOW, None), ('gcloud', True, lib.NO_WINDOW, None),
                       # Node's npx.CMD and npm.CMD (an upper-case extension on the desktop) go through the shell: without
                       # it `npx jest` could not start (WinError 2, SMADPickleBot #1188)
                       ('npx', True, lib.NO_WINDOW, None), ('npm', True, lib.NO_WINDOW, None)])


def test_a_shell_command_keeps_its_ampersand():
    """cmd_line: an argument with & | < > ^ is double-quoted for cmd.exe (10/22/26: `gh api ...?head_sha=X&per_page=50`
    was cut at the &, exit 1, and 33f8fd39's release profile had no runs); the rest as Windows quotes them; run hands the
    shell the line"""
    eq('quoted', lib.cmd_line(['gh', 'api', 'repos/{owner}/{repo}/actions/runs?head_sha=ab&per_page=50']),
       'gh api "repos/{owner}/{repo}/actions/runs?head_sha=ab&per_page=50"')
    eq('  spaces and plain args as list2cmdline has them, never quoted twice', lib.cmd_line(['gcloud', 'a b', '--x=1', 'a b&c']),
       'gcloud "a b" --x=1 "a b&c"')
    seen = []
    keep, keep_os, keep_which, keep_found = lib.subprocess.run, lib.os.name, lib.shutil.which, dict(lib._FOUND)
    lib.subprocess.run = lambda args, **kw: seen.append(args) or types.SimpleNamespace(returncode=0, stdout='', stderr='')
    try:
        lib.os.name = 'nt'
        lib._FOUND.clear()
        lib.shutil.which = lambda c: r'C:\sdk\gcloud.cmd' if c == 'gcloud' else None
        lib.run(['gcloud', 'x?a=1&b=2'])
        lib.run(['gcloud', 'plain'])
        lib.run(['git', 'log', 'a&b'])
    finally:
        lib.subprocess.run, lib.os.name, lib.shutil.which = keep, keep_os, keep_which
        lib._FOUND.clear()
        lib._FOUND.update(keep_found)
    eq('  run: a shim with an & gets the quoted line; any other command keeps its list', seen,
       ['gcloud "x?a=1&b=2"', ['gcloud', 'plain'], ['git', 'log', 'a&b']])
    if os.name == 'nt':
        got = subprocess.run(lib.cmd_line(['echo', 'a&b']), shell=True, capture_output=True, text=True)
        eq('  a real cmd.exe keeps the & in its argument', (got.returncode, got.stdout.strip(), got.stderr), (0, '"a&b"', ''))


def test_out_never_raises():
    """out: the stripped stdout on success; '' on a non-zero exit, a command that does not exist, or a timeout"""
    eq('ok', lib.out([sys.executable, '-c', "print('  hi  ')"]), 'hi')
    eq('  failed', lib.out([sys.executable, '-c', "import sys; print('x'); sys.exit(3)"]), '')
    eq('  missing', lib.out(['no-such-command-cicd-12345']), '')
    eq('  timeout', lib.out([sys.executable, '-c', 'import time; time.sleep(5)'], timeout=0.5), '')


def test_gh_goes_through_the_kits_throttled_cli():
    """gh (#364): the positive case, `gh <args>` through the kit's cli on the caller's runner with the caller's keywords
    alone; a quota the kit refuses is 429 and a gh that cannot start 127, never an exception; check raises; no kit runs
    the runner straight"""
    seen, cli_calls = [], []

    def runner(args, **kw):
        seen.append((args, kw))
        return types.SimpleNamespace(returncode=0, stdout='ok\n', stderr='')

    class Kit:
        class GitHubError(RuntimeError):
            pass

        @staticmethod
        def cli(args, run=None, **kw):
            cli_calls.append(list(args))
            return run(['gh', *args], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)

    def limited(args, run=None, **kw):
        raise Kit.GitHubError('core rate limit: spent, 0 left, back in 30 s')

    def missing(args, **kw):
        raise FileNotFoundError('gh')
    keep = lib._github
    try:
        lib._github = Kit
        done = lib.gh(['run', 'list', '--limit', 1], run=runner, cwd='x')
        positive = (done.stdout, list(cli_calls), list(seen))
        lone = lib.gh(['api', 'x'], run=lambda args: types.SimpleNamespace(returncode=0, stdout='[]', stderr='')).stdout
        Kit.cli = staticmethod(limited)
        refused = lib.gh(['api', 'x'], run=runner)
        Kit.cli = staticmethod(lambda args, run=None, **kw: run(['gh', *args]))
        gone = lib.gh(['api', 'x'], run=missing)
        try:
            lib.gh(['issue', 'close', '7'], run=lambda args: types.SimpleNamespace(returncode=1, stdout='', stderr='no'), check=True)
            checked = 'returned'
        except subprocess.CalledProcessError as e:
            checked = (e.returncode, e.cmd[:3])
        lib._github = None
        seen.clear()
        plain = lib.gh(['variable', 'get', 'X'], run=runner, timeout=5)
    finally:
        lib._github = keep
    eq('through the kit', (positive, lone, refused.returncode, 'rate limit' in refused.stderr, gone.returncode, checked,
                           plain.stdout, seen),
       (('ok\n', [['run', 'list', '--limit', '1']], [(['gh', 'run', 'list', '--limit', '1'], {'cwd': 'x'})]), '[]', 429, True,
        127, (1, ['gh', 'issue', 'close']), 'ok\n', [(['gh', 'variable', 'get', 'X'], {'timeout': 5})]))


def test_json_helpers():
    """parse_json, json_out and gh_json: the parsed object, None for a failure, nothing printed, or text that is not JSON"""
    ok = types.SimpleNamespace(returncode=0, stdout='{"a": 1}\n')
    eq('parse', (lib.parse_json(ok), lib.parse_json(types.SimpleNamespace(returncode=1, stdout='{"a": 1}')),
                 lib.parse_json(types.SimpleNamespace(returncode=0, stdout='  ')), lib.parse_json(types.SimpleNamespace(returncode=0, stdout='nope'))),
       ({'a': 1}, None, None, None))
    eq('  json_out', lib.json_out([sys.executable, '-c', "print('[1, 2]')"]), [1, 2])
    eq('  gh_json of a command gh refuses (or no gh at all)', lib.gh_json('no-such-subcommand-cicd', cwd=None), None)


def test_is_link_knows_a_junction_or_a_symlink_and_never_a_real_folder():
    """#1094 review, the positive case: a junction (Windows) or a symbolic link is a link, a real folder or a missing path is not; a Windows Python without os.path.isjunction reads the reparse-point attribute instead"""
    import shutil
    import tempfile
    d = tempfile.mkdtemp()
    target, link, real = (os.path.join(d, n) for n in ('target', 'link', 'real'))
    os.makedirs(target)
    os.makedirs(real)
    if os.name == 'nt':
        import _winapi
        _winapi.CreateJunction(target, link)
    else:
        os.symlink(target, link, target_is_directory=True)
    eq('a link', (lib.is_link(link), lib.is_link(real), lib.is_link(os.path.join(d, 'none'))), (True, False, False))
    if os.name == 'nt':
        keep = os.path.isjunction
        del os.path.isjunction
        try:
            eq('  no isjunction: the attribute', (lib.is_link(link), lib.is_link(real)), (True, False))
        finally:
            os.path.isjunction = keep
    os.rmdir(link) if os.name == 'nt' else os.unlink(link)
    shutil.rmtree(d, ignore_errors=True)


def _checkout(root, name, lock=b'lock-1', packages=True):
    """A checkout with an app/package-lock.json and, when asked, an installed app/node_modules holding one file."""
    c = os.path.join(root, name)
    os.makedirs(os.path.join(c, 'app'))
    if lock is not None:
        with open(os.path.join(c, 'app', 'package-lock.json'), 'wb') as f:
            f.write(lock)
    if packages:
        os.makedirs(os.path.join(c, 'app', 'node_modules', 'jest'))
        with open(os.path.join(c, 'app', 'node_modules', 'jest', 'index.js'), 'w') as f:
            f.write('x')
    return c


def test_lend_dir_lends_the_first_matching_checkouts_folder_and_unlink_lent_takes_only_the_link():
    """SMADPickleBot #1283, the positive case: a worktree with no app/node_modules gets a link to the first source whose package-lock.json matches its own, a source with another lock or none is passed over and named; unlink_lent removes the link and keeps every file it pointed at"""
    import shutil
    import tempfile
    d = tempfile.mkdtemp()
    work = _checkout(d, 'work', packages=False)
    other = _checkout(d, 'other', lock=b'lock-2')
    bare = _checkout(d, 'bare', packages=False)
    main = _checkout(d, 'main')
    said = lib.lend_dir(work, (work, other, bare, main))
    link = os.path.join(work, 'app', 'node_modules')
    eq('lent from the matching source', (said, lib.is_link(link), os.path.exists(os.path.join(link, 'jest', 'index.js'))),
       (f'app/node_modules lent from {main}', True, True))
    eq('  a second lend leaves it', lib.lend_dir(work, (main,)), 'its own app/node_modules already there')
    eq('  unlinked: the link gone, the source untouched',
       (lib.unlink_lent(work), os.path.lexists(link), os.path.exists(os.path.join(main, 'app', 'node_modules', 'jest', 'index.js'))),
       ('', False, True))
    eq('  no source would do: each named',
       lib.lend_dir(work, (other, bare)),
       f"no app/node_modules lent ({other}'s app/package-lock.json differs; {bare} has none)")
    eq('  no sources at all', lib.lend_dir(work, ()), 'no app/node_modules lent (no source)')
    shutil.rmtree(d, ignore_errors=True)


def test_lend_dir_never_touches_a_real_folder():
    """  a work with its own real folder, or with no lock file, is left as it is; unlink_lent never empties a real folder"""
    import shutil
    import tempfile
    d = tempfile.mkdtemp()
    own = _checkout(d, 'own')
    nolock = _checkout(d, 'nolock', lock=None, packages=False)
    main = _checkout(d, 'main')
    eq('its own folder kept', (lib.lend_dir(own, (main,)), lib.unlink_lent(own),
                               os.path.exists(os.path.join(own, 'app', 'node_modules', 'jest', 'index.js'))),
       ('its own app/node_modules already there', '', True))
    eq('  no lock file: nothing lent', (lib.lend_dir(nolock, (main,)).startswith('no app/package-lock.json in '),
                                       os.path.lexists(os.path.join(nolock, 'app', 'node_modules'))), (True, False))
    eq('  another folder and lock by name', lib.lend_dir(nolock, (main,), folder='web/node_modules', lock='web/yarn.lock'),
       f'no web/yarn.lock in {nolock}, none lent')
    shutil.rmtree(d, ignore_errors=True)


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            print(fn.__doc__.strip().splitlines()[0] if fn.__doc__ else name)
            fn()
    print(f'{len(FAILURES)} failure(s)')
    sys.exit(1 if FAILURES else 0)
