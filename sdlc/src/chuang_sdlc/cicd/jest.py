#!/usr/bin/env python3
"""The app's Jest suites that test a pipeline script, run when a push changes that script (SMADPickleBot #525; into
the kit by #1188, phase 2 of #235, 11/9/26).

Some pipeline scripts outside the app's folder are tested by the app's own Jest suites. A push gate that asks for the
app's tested build only when the app's folder changes, and a test mark that runs the Python suites alone, let a change
to one of those scripts through with its Jest tests never run. This module finds and runs them:

    python -m chuang_sdlc.cicd.jest select --before SHA [--after SHA] [--out FILE]   # the suites, one a line
    python -m chuang_sdlc.cicd.jest run --suites FILE                                # jest on them, from the app

The map is read from the suites themselves at run time, never a hand list: every <app>/**/__tests__/**/*.test.ts(x)
(node_modules and dot folders skipped) whose text names a path under the prefix (an import, a require, a path in a
comment), each named path resolved to the file it is (`../../../pipeline/shared/app/store-ready` is store-ready.js) and
followed through that script's own relative requires, so a new suite on a pipeline script is covered with no edit. A
changed file a suite reaches selects it, as does a removed one it still names. A change with no path under the prefix
selects nothing without reading the app, so no node starts for it.

The host sets where things are once (`configure(root=, app=, prefix=, command=, install_hint=, temp_prefix=)`); the
defaults are the working folder, its `app/`, `pipeline/`, `npx jest --ci`. Standard library only, on the kit's
`cicd.proc` runner.
"""
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import time

from . import proc

_SET = {
    'root': None,                      # the repo; None: the working folder
    'app': None,                       # the app folder; None: <root>/app
    'prefix': 'pipeline/',             # the repo folder whose scripts the app's suites test
    'command': ('npx', 'jest', '--ci'),   # jest, before the module's own --json, --outputFile and --runTestsByPath
    'install_hint': '`npm ci` in the app folder',   # what the refusal says to run when the app's packages are missing
    'temp_prefix': 'pipeline-jest-',   # the scratch folder's name, for jest's JSON
}

SUITE = re.compile(r'\.test\.tsx?$')   # jest's default testMatch: **/__tests__/**/*.test.ts(x)
# A script's own relative requires and imports, followed so a suite reaches what the script it tests uses.
RELATIVE = re.compile(r'''(?:\brequire\(\s*|\bfrom\s+|\bimport\s+)['"](\.\.?/[^'"]+)['"]''')
RESOLVE = ('', '.js', '.cjs', '.mjs', '.ts', '.json', '/index.js')   # what Node or Jest tries for a bare specifier
SCRIPT = ('.js', '.cjs', '.mjs', '.ts')
JEST_TIMEOUT = 600   # seconds; a handful of suites take seconds, a cold transform cache under a minute
EXCERPT = 20         # lines of a failing suite's message printed
_ANSI = re.compile(r'\x1b\[[0-9;]*m')


def configure(**settings) -> None:
    """The host's places and words: `root`, `app`, `prefix` (a folder name ending in `/`), `command` (jest's argv
    before the module's own flags), `install_hint`, `temp_prefix`. Unknown names are refused."""
    for name, value in settings.items():
        if name not in _SET:
            raise TypeError(f'jest.configure() takes no {name!r}')
        _SET[name] = tuple(value) if name == 'command' else value


def repo_root() -> str:
    """The repo the paths are relative to."""
    return os.path.abspath(_SET['root'] or os.getcwd())


def app_folder() -> str:
    """The app folder the suites live in and jest runs from."""
    return os.path.abspath(_SET['app'] or os.path.join(repo_root(), 'app'))


def _named_re():
    """A prefix path in a suite's text: the prefix not the tail of a longer name or folder (`../` before it is fine),
    up to its last letter, digit, `_` or `-`, so a sentence's full stop is left off."""
    return re.compile(r'(?<![\w-])(?<![\w-]/)' + re.escape(_SET['prefix']) + r'[\w./-]*[\w-]')


def named_paths(text: str) -> set:
    """The repo paths under the prefix that a suite's text names, as written (`pipeline/shared/app/store-ready`)."""
    return {posixpath.normpath(m.group(0)) for m in _named_re().finditer(text or '')}


def _resolve(path: str, base: str) -> list:
    """The repo files `path` (repo-relative, as an import names it) is: itself, or with what Node adds."""
    return [path + ext for ext in RESOLVE if os.path.isfile(os.path.join(base, path + ext))]


def reach(named, root: str = None) -> frozenset:
    """Every repo path a suite reaches from the prefix paths it names: each as written (so a removed script still
    matches), the file it resolves to, and on through each script's own relative requires inside the prefix."""
    base = root or repo_root()
    files, queue = set(), [f for p in named for f in _resolve(p, base)]
    while queue:
        f = queue.pop()
        if f in files:
            continue
        files.add(f)
        if not f.endswith(SCRIPT):
            continue
        try:
            with open(os.path.join(base, f), encoding='utf-8', errors='replace') as fh:
                text = fh.read()
        except OSError:
            continue
        for rel in RELATIVE.findall(text):
            target = posixpath.normpath(posixpath.join(posixpath.dirname(f), rel))
            if target.startswith(_SET['prefix']):
                queue.extend(_resolve(target, base))
    return frozenset(set(named) | files)


def suite_map(app: str = None, root: str = None) -> dict:
    """{suite (its path from the app, as jest takes it): frozenset of the prefix paths it reaches} for every app suite
    that names a path under the prefix; the suites that name none are left out."""
    app_dir, base = app or app_folder(), root or repo_root()
    out = {}
    for folder, dirs, names in os.walk(app_dir):
        dirs[:] = sorted(d for d in dirs if d != 'node_modules' and not d.startswith('.'))
        rel_folder = os.path.relpath(folder, app_dir).replace('\\', '/')
        if '__tests__' not in rel_folder.split('/'):
            continue
        for name in sorted(names):
            if not SUITE.search(name):
                continue
            try:
                with open(os.path.join(folder, name), encoding='utf-8', errors='replace') as fh:
                    named = named_paths(fh.read())
            except OSError:
                continue
            if named:
                out[posixpath.join(rel_folder, name)] = reach(named, base)
    return out


def select(changed, suites=None, app: str = None, root: str = None) -> list:
    """The suites (paths from the app) a change can break: each one that reaches a changed file, by its path or by its
    path without the extension (a removed script it still requires). `changed` None (no diff to read) is every suite
    on a pipeline script; a change with no path under the prefix is none, answered before the app is read. `suites`
    (suite_map's shape) is the test seam."""
    prefix = _SET['prefix']
    if changed is not None:
        changed = [c.strip().replace('\\', '/') for c in changed if c and c.strip()]
        if not any(c.startswith(prefix) for c in changed):
            return []
    suites = suite_map(app, root) if suites is None else suites
    if changed is None:
        return sorted(suites)
    hit = set()
    for c in changed:
        if c.startswith(prefix):
            keys = {c, posixpath.splitext(c)[0]}
            hit.update(s for s, r in suites.items() if keys & r)
    return sorted(hit)


def changed_files(before: str, after: str = 'HEAD', run=None):
    """The paths that differ between `before` and `after` (git's two-dot name-only diff, run in the repo), or None
    when there is no diff to read: no `before`, an all-zero one, or git refused."""
    if not before or set(before) == {'0'}:
        return None
    got = (run or proc.run)(['git', 'diff', '--name-only', before, after], cwd=repo_root())
    if got.returncode != 0:
        return None
    return [ln.strip() for ln in (got.stdout or '').splitlines() if ln.strip()]


def _rel(name: str, app_dir: str, real=os.path.realpath) -> str:
    """A jest result's absolute test path as a path from the app (the selection's spelling), both sides by their real
    path: jest reports the folder it resolved, and a repo reached through a junction or a symlink (SMADPickleBot's
    D:\\code to the Dev Drive, 10/31/26) made a relpath of the two spellings fail, so every suite read as not run.
    `real` is the test seam."""
    try:
        return os.path.relpath(real(name or ''), real(app_dir)).replace('\\', '/')
    except ValueError:   # another drive on Windows, even resolved
        return (name or '').replace('\\', '/')


def run_suites(suites, app: str = None, run=None, timeout: int = JEST_TIMEOUT) -> tuple:
    """Jest on `suites` (paths from the app), from the app, by path (--runTestsByPath; each jest project takes the
    suites its testMatch holds). (passed, summary, details). Passed is read from jest's own JSON (success, no failed
    suite, every selected suite run), never the exit code alone; a failure's summary names each failing suite and its
    details are the start of each one's message. Nothing to run passes with no node started; no node_modules fails,
    naming the install hint. `run` (proc.run's shape) is the test seam."""
    app_dir = app or app_folder()
    suites = list(suites or [])
    if not suites:
        return True, 'nothing to run: no app suite tests a changed pipeline script', []
    if not os.path.isdir(os.path.join(app_dir, 'node_modules')):
        return False, (f'the app\'s packages are not installed ({app_dir}/node_modules is missing): run '
                       f'{_SET["install_hint"]}, then again; not run: {", ".join(suites)}'), []
    work = tempfile.mkdtemp(prefix=_SET['temp_prefix'])
    out = os.path.join(work, 'jest.json')
    t0 = time.time()
    try:
        try:
            done = (run or proc.run)([*_SET['command'], '--json', f'--outputFile={out}', '--runTestsByPath', *suites],
                                     cwd=app_dir, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, f'jest did not finish within {timeout} s: {", ".join(suites)}', []
        except OSError as e:
            return False, f'jest could not start ({e}): {", ".join(suites)}', []
        seconds = round(time.time() - t0, 1)
        try:
            with open(out, encoding='utf-8') as fh:   # jest writes UTF-8
                got = json.load(fh)
        except (OSError, ValueError):
            tail = ((done.stderr or '') + (done.stdout or '')).strip().splitlines()[-15:]
            return False, f'jest wrote no results (exit {done.returncode}): {", ".join(suites)}', tail
    finally:
        shutil.rmtree(work, ignore_errors=True)
    results = got.get('testResults') or []
    ran = {_rel(r.get('name'), app_dir): r for r in results}
    failed = [s for s, r in ran.items() if r.get('status') != 'passed']
    missing = [s for s in suites if s not in ran]
    if done.returncode == 0 and got.get('success') is True and not failed and not missing and not got.get('numFailedTestSuites'):
        return True, f'{len(suites)} app suite(s), {got.get("numTotalTests", 0)} test(s) passed in {seconds} s: {", ".join(suites)}', []
    details = []
    for s in failed:
        msg = [ln for ln in _ANSI.sub('', ran[s].get('message') or '').splitlines() if ln.strip()]
        details += [f'--- {s} ---'] + msg[:EXCERPT]
    why = []
    if failed:
        why.append('failed: ' + ', '.join(failed))
    if missing:
        why.append('not run by jest (no project\'s testMatch holds it): ' + ', '.join(missing))
    if not why:
        why.append(f'jest exited {done.returncode}')
    return False, f'FAILED in {seconds} s, ' + '; '.join(why), details


def main(argv=None) -> int:
    """`select` (the suites a push can break, written one a line) or `run` (jest on a selection); exit 1 on a failure."""
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    pick = sub.add_parser('select', help='the app suites a push can break through the pipeline scripts it changes')
    pick.add_argument('--before', default='', help="the push's previous head; none means every such suite")
    pick.add_argument('--after', default='HEAD')
    pick.add_argument('--out', help='write the suites here, one a line (an empty file for none)')
    go = sub.add_parser('run', help='jest on the suites a `select` wrote, from the app')
    go.add_argument('--suites', required=True, help="a `select --out` file")
    a = ap.parse_args(argv)
    if a.cmd == 'select':
        changed = changed_files(a.before, a.after)
        picked = select(changed)
        said = 'no diff to read: every app suite on a pipeline script' if changed is None else f'{len(changed)} changed file(s)'
        print(f'pipeline-jest: {len(picked)} app suite(s) test the pipeline scripts this push changed ({said})'
              + (': ' + ', '.join(picked) if picked else ''))
        if a.out:
            with open(a.out, 'w', encoding='utf-8', newline='\n') as fh:
                fh.write(''.join(f'{s}\n' for s in picked))
        return 0
    try:
        with open(a.suites, encoding='utf-8') as fh:
            suites = [ln.strip() for ln in fh if ln.strip()]
    except OSError as e:
        print(f'pipeline-jest: ERROR: cannot read the selection {a.suites}: {e}')
        return 1
    ok, summary, details = run_suites(suites)
    for line in details:
        print(line)
    print(f'pipeline-jest: {summary}' if ok else f'pipeline-jest: ERROR: {summary}')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
