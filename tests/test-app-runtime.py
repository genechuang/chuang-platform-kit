#!/usr/bin/env python3
"""chuang_app_pipeline.runtime: the parts of an Expo app's native runtime guard that stand on their own (SMADPickleBot
#2244, step 3b of #2161).

Pinned (the positive case first): the guard's four verdicts, and that moved inputs are refused whatever the override
says; a checkout's packages trusted or not, each reason in the folder's own name; the lock's hash and whether a
folder's packages are the lockfile's; a kept answer returned while fresh and dropped once old; a store read tried
again on each kind of failure, the builds a host needs alone returned, and the last cause raised; the row a runtime
file keeps.

Run:  python -B tests/test-app-runtime.py
"""
import hashlib
import json
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'app-pipeline', 'src'))

from chuang_app_pipeline import runtime as R  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


def write(folder, rel, text):
    path = os.path.join(folder, *rel.split('/'))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return path


ROW = {'runtime': 'rt-1', 'inputs': 'in-1'}
check("the positive case: unchanged inputs and the installed store build's runtime is ok, so an update reaches it",
      R.guard_verdict('in-1', ROW, 'rt-1'), 'ok')
check("the other verdicts: moved inputs are refused whatever the override says; a runtime the store build does not run "
      "differs, or is allowed with the host's explicit override; an unread store runtime differs too",
      (R.guard_verdict('in-2', ROW, 'rt-1'), R.guard_verdict('in-2', ROW, 'rt-1', allow=True), R.guard_verdict('in-2', ROW, 'rt-9', allow=True),
       R.guard_verdict('in-1', ROW, 'rt-0'), R.guard_verdict('in-1', ROW, 'rt-0', allow=True), R.guard_verdict('in-1', ROW, None),
       R.guard_verdict('in-1', {}, None)),
      ('inputs_moved', 'inputs_moved', 'inputs_moved', 'runtime_differs', 'allowed', 'runtime_differs', 'inputs_moved'))
check("a store runtime that was not read is never a match, even against a row that records none (0.3.1)",
      [R.guard_verdict('in-1', {'inputs': 'in-1'}, unread) for unread in (None, '')] + [R.guard_verdict('in-1', {'inputs': 'in-1', 'runtime': ''}, '')],
      ['runtime_differs'] * 3)

with tempfile.TemporaryDirectory() as app:
    none = R.install_stale(app)
    os.makedirs(os.path.join(app, 'node_modules'))
    unrecorded = R.install_stale(app, label='mobile')
    lock = write(app, 'package-lock.json', '{"lockfileVersion": 3}')
    marker = write(app, 'node_modules/.package-lock.json', '{}')
    os.utime(marker, (1_000, 1_000))
    os.utime(lock, (2_000, 2_000))
    older = R.install_stale(app)
    os.utime(marker, (3_000, 3_000))
    no_package_json = R.install_stale(app)
    write(app, 'package.json', json.dumps({'dependencies': {'a': '1', '@scope/b': '1'}, 'devDependencies': {'c': '1', 'd': '1', 'e': '1'}}))
    write(app, 'node_modules/a/package.json', '{}')
    lacking = R.install_stale(app)
    for name in ('@scope/b', 'c', 'd', 'e'):
        write(app, f'node_modules/{name}/package.json', '{}')
    check("a checkout's packages: not installed, no install record, older than the lockfile, a dependency missing (three "
          "named and more), then trusted; the folder is named as the host calls it",
          (none, unrecorded, older, no_package_json, lacking, R.install_stale(app)),
          ('app/node_modules is not installed: run npm ci in app/ first',
           'mobile/node_modules has no npm install record (.package-lock.json): run npm ci in mobile/ first',
           'app/node_modules is older than app/package-lock.json: run npm ci in app/ first', None,
           'app/node_modules lacks @scope/b, c, d and more: run npm ci in app/ first', None))
    digest = hashlib.sha256(b'{"lockfileVersion": 3}').hexdigest()
    mark = write(app, 'mark/npm-lock.sha256', digest + '\n')
    stale_mark = write(app, 'mark/old.sha256', 'f' * 64)
    check("the lock's hash, and whether a folder's packages are the lockfile's: the mark holds the hash and node_modules is there",
          (R.lock_hash(app), R.lock_hash(os.path.join(app, 'nowhere')), R.installed(app, mark), R.installed(app, stale_mark),
           R.installed(app, os.path.join(app, 'no-mark')), R.installed(os.path.join(app, 'nowhere'), mark)),
          (digest, '', True, False, False, False))

with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, 'memo.json')
    first = R.memo_get(path, 'ios:tree', 1800, now=1_000)
    R.memo_put(path, 'ios:tree', {'runtime': 'rt-1', 'number': '31'}, 1800, now=1_000)
    R.memo_put(path, 'android:tree', {'runtime': 'rt-2'}, 1800, now=2_000)
    fresh = R.memo_get(path, 'ios:tree', 1800, now=2_700)
    old = R.memo_get(path, 'ios:tree', 1800, now=2_801)
    R.memo_put(path, 'web:tree', {'runtime': 'rt-3'}, 1800, now=3_000)   # ios:tree is past its time now and is dropped
    kept = sorted(json.load(open(path, encoding='utf-8')))
    write(d, 'bad.json', 'not json')
    check("a kept answer: none before, returned with its time while fresh, None once old, the expired dropped at the next "
          "write; an unreadable file is None, and a write that cannot land is no error",
          (first, fresh, old, kept, R.memo_get(os.path.join(d, 'bad.json'), 'k', 1800),
           R.memo_put(os.path.join(d, 'no', 'such', 'folder', 'm.json'), 'k', {}, 1800)),
          (None, {'runtime': 'rt-1', 'number': '31', 'at': 1_000}, None, ['android:tree', 'web:tree'], None, None))

BUILDS = json.dumps([{'id': 1, 'buildProfile': 'preview'}, {'id': 2, 'buildProfile': 'production'}])
answers = [SystemExit('command failed: npx eas build:list exited 1'), '<html>error page</html>', 'warning: something\n' + BUILDS]
told, slept = [], []


def ask():
    got = answers.pop(0)
    if isinstance(got, BaseException):
        raise got
    return got


got = R.read_builds(ask, 3, (3, 8), need=lambda b: b['buildProfile'] == 'production', sleep=slept.append,
                    on_retry=lambda attempt, why, pause: told.append((attempt, why, pause)))
check("a store read tried again: a failed command, then an answer with no list, then the list after a preamble; the "
      "builds the host needs alone, each retry told with its cause and its wait",
      (got, told, slept),
      ([{'id': 2, 'buildProfile': 'production'}],
       [(1, 'command failed: npx eas build:list exited 1', 3), (2, 'ValueError: substring not found', 8)], [3, 8]))
try:
    R.read_builds(lambda: BUILDS, 2, (0,), need=lambda b: b['buildProfile'] == 'simulator', sleep=lambda s: None)
    last = ''
except R.ReadFailed as e:
    last = str(e)
check("no build of the kind asked on every try raises the last cause; an answer that is not a list is a failure; with "
      "no need every build is returned on the first try",
      (last, R.read_builds(lambda: BUILDS, 1, (0,)) == json.loads(BUILDS)),
      ('IndexError: no build of the kind asked in the answer', True))
try:
    R.read_builds(lambda: '{"not": "a list"} [', 1, (0,))
    odd = ''
except R.ReadFailed as e:
    odd = str(e)[:10]
check("an answer that never parses raises ReadFailed after the last try, never the parser's own error", odd, 'JSONDecode')

data = {'ios': {'runtime': 'rt-1', 'inputs': 'in-1', 'since': 'old'}}
before, row = R.record_inputs(data, 'ios', 'in-2', since='inputs changed 11/22/26')
kept_before, kept_row = R.record_inputs(data, 'ios', 'in-3', keep=True, since='ignored')
new_before, new_row = R.record_inputs(data, 'android', 'in-a', since='first set')
check("the row a runtime file keeps: new inputs become the runtime with its stamp; with keep only the inputs move; a "
      "platform with no row gets one",
      (before, dict(row, inputs='in-2'), kept_before, kept_row, new_before, data['android']),
      ('rt-1', {'runtime': 'in-2', 'inputs': 'in-2', 'since': 'inputs changed 11/22/26'}, 'in-2',
       {'runtime': 'in-2', 'inputs': 'in-3', 'since': 'inputs changed 11/22/26'}, None,
       {'runtime': 'in-a', 'inputs': 'in-a', 'since': 'first set'}))

print()
print(f"{len(fails)} failure(s)" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
