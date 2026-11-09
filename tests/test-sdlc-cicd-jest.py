#!/usr/bin/env python3
"""chuang_sdlc.cicd.jest (#1188, phase 2 of #235): the app's Jest suites a pipeline change reaches, with the host's
places and words configured. SMADPickleBot's tests/test-pipeline-jest.py pins the selection on its real tree and every
run path through its shim; this suite pins what the host sets.

Pinned (the positive case first): another host's prefix and app folder select the suite that requires its script; its
jest command runs with the module's own flags after it, from its app folder; the diff runs in the configured repo; the
install hint is the host's words; an unknown setting is refused. Every jest run is a fake: no node starts.

Run:  python -B tests/test-sdlc-cicd-jest.py
"""
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))

from chuang_sdlc.cicd import jest as J  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


@contextlib.contextmanager
def tree(files):
    base = tempfile.mkdtemp(prefix='kit-jest-t-')
    try:
        for rel, text in files.items():
            path = os.path.join(base, *rel.split('/'))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(text)
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


keep = dict(J._SET)
with tree({'web/src/__tests__/deploy.test.ts': "const d = require('../../../tools/ci/deploy');\n",
           'tools/ci/deploy.js': "module.exports = require('./shared');\n", 'tools/ci/shared.js': '',
           'web/node_modules/.keep': ''}) as base:
    J.configure(root=base, app=os.path.join(base, 'web'), prefix='tools/', command=['yarn', 'jest'],
                install_hint='`yarn` in web/', temp_prefix='host-jest-')
    print('a host of its own')
    check('the positive case: the host prefix selects the suite reaching its script, through its relative require',
          (J.select(['tools/ci/shared.js']), J.select(['pipeline/x.js']), J.app_folder(), J.repo_root()),
          (['src/__tests__/deploy.test.ts'], [], os.path.join(base, 'web'), base))
    calls = []

    def run(args, **kw):
        calls.append((args, kw))
        if args[0] == 'git':
            return subprocess.CompletedProcess(args, 0, 'tools/ci/deploy.js\n', '')
        out = next(a for a in args if a.startswith('--outputFile=')).split('=', 1)[1]
        with open(out, 'w', encoding='utf-8') as f:
            json.dump({'success': True, 'numTotalTests': 2, 'numFailedTestSuites': 0, 'testResults': [
                {'name': os.path.join(base, 'web', 'src', '__tests__', 'deploy.test.ts'), 'status': 'passed'}]}, f)
        return subprocess.CompletedProcess(args, 0, '', '')
    changed = J.changed_files('a' * 40, run=run)
    ok, summary, _ = J.run_suites(['src/__tests__/deploy.test.ts'], run=run)
    check("  the diff runs in the repo; jest is the host's command with the module's flags after it, from the app folder",
          (changed, calls[0][1].get('cwd'), calls[1][0][:3], calls[1][0][-2:], calls[1][1].get('cwd'), ok),
          (['tools/ci/deploy.js'], base, ['yarn', 'jest', '--json'], ['--runTestsByPath', 'src/__tests__/deploy.test.ts'],
           os.path.join(base, 'web'), True))
with tree({'web/package.json': '{}'}) as base:
    J.configure(root=base, app=os.path.join(base, 'web'))
    ok, summary, _ = J.run_suites(['src/__tests__/deploy.test.ts'], run=run)
    check("  no packages: the host's own install words", (ok, 'run `yarn` in web/, then again' in summary), (False, True))
try:
    J.configure(nope=1)
    check('an unknown setting is refused', 'accepted', 'refused')
except TypeError:
    check('an unknown setting is refused', True, True)
J._SET.clear()
J._SET.update(keep)

print(f'\n{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
