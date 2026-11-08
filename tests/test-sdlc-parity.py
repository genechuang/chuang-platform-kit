#!/usr/bin/env python3
"""chuang_sdlc.parity: a commit that changes one or two pipelines carries a Pipeline-Parity: trailer.

Pinned (the positive case first): a commit touching one of three pipelines with no trailer is refused, naming the
others, the example with them and the cite on its own line; a trailer of ten characters passes, a shorter one does not;
all three or none needs nothing; a file's pipeline by its globs (subfolders too, a backslash path), '' for a shared
file; a workflow whose diff is only generated paths: lines touches none, any other line does; the default example.

Run:  python -B tests/test-sdlc-parity.py
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))

from chuang_sdlc import parity as P  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


PIPES = {'hosted': ['ci/hosted/*', '.ci/release.yml'], 'cloud': ['ci/cloud/*'], 'local': ['ci/local/*', 'hooks/*']}
EX = 'Pipeline-Parity: {first} has it through run.sh; {last} n/a, <why>'

print('the verdict')
why = P.verdict('Subject\n\nTicket: #1', {'local'}, PIPES, EX, '(the team rule)')
check('the positive case: one pipeline and no trailer is refused, naming the others',
      why, 'pipeline-parity: this commit changes the local pipeline but not cloud or hosted. Make the same change '
           'there, or add a trailer saying how they have it or why they cannot, e.g.\n'
           '  Pipeline-Parity: cloud has it through run.sh; hosted n/a, <why>\n(the team rule)')
check('two pipelines: plural, the one left named',
      P.verdict('S', {'local', 'cloud'}, PIPES, EX).splitlines()[0].startswith(
          'pipeline-parity: this commit changes the cloud and local pipelines but not hosted.'), True)
check('a trailer of ten characters passes', P.verdict('S\n\nPipeline-Parity: cloud n/a.', {'local'}, PIPES), '')
check('a shorter one does not', bool(P.verdict('S\n\nPipeline-Parity: n/a', {'local'}, PIPES)), True)
check('all three, or none, needs nothing', (P.verdict('S', set(PIPES), PIPES), P.verdict('S', set(), PIPES)), ('', ''))
check('a bare trailer never borrows the next line', bool(P.verdict('S\n\nPipeline-Parity:\nSession: Alice Smith', {'local'}, PIPES)), True)
check("a short reason padded by spaces or a CR is still short",
      (bool(P.verdict('S\n\nPipeline-Parity: n/a      \n', {'local'}, PIPES)),
       bool(P.verdict('S\r\n\r\nPipeline-Parity: n/a\r\n\r\n\r\n', {'local'}, PIPES))), (True, True))
check('any one valid line counts', P.verdict('S\n\nPipeline-Parity: n/a\nPipeline-Parity: cloud has it\r\n', {'local'}, PIPES), '')
check("a name outside the pipelines is ignored: no wrong pass, no IndexError",
      (bool(P.verdict('S', {'local', 'cloud', 'elsewhere'}, PIPES)), P.verdict('S', {'elsewhere'}, PIPES)), (True, ''))
check('no cite, no last line', P.verdict('S', {'local'}, PIPES, EX).endswith('<why>'), True)
check('the default example', P.verdict('S', {'local'}, PIPES).splitlines()[-1],
      '  Pipeline-Parity: cloud has it too; hosted n/a, <why>')

print('which and touched')
check('the positive case: a file is its folder\'s pipeline', P.which('ci/local/app/ship.py', PIPES), 'local')
check('a fixed path, a backslash path, a shared file',
      (P.which('.ci/release.yml', PIPES), P.which('ci\\cloud\\x.yaml', PIPES), P.which('ci/shared/run.sh', PIPES)),
      ('hosted', 'cloud', ''))
check('case counts on every platform', (P.which('CI/local/a.py', PIPES), P.which('.CI/release.yml', PIPES)), ('', ''))
diffs = {'.ci/release.yml': "--- a\n+++ b\n-      - 'src/a.py'\n+      - 'src/b.py'\n",
         'ci/hosted/x.yml': '--- a\n+++ b\n+    timeout-minutes: 5\n'}
check('the positive case: the files\' pipelines', P.touched(['ci/local/a.py', 'ci/hosted/x.yml', 'README.md'], PIPES,
                                                            diffs.get), {'local', 'hosted'})
check('a workflow that changes only generated paths: lines touches none',
      P.touched(['.ci/release.yml'], PIPES, diffs.get), set())
check('only_paths: an empty diff is not', P.only_paths(''), False)

print(f'\n{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
