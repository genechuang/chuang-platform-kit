#!/usr/bin/env python3
"""chuang_app_pipeline.artifact: an Expo app's build artifact, the parts any app's pipeline needs (SMADPickleBot
#2237, step 3a of #2161).

Pinned (the positive case first): the key is the tree's first 12 and the config's hash, moving with either; an
export's sizes by kind and its line; a release export names the API and no host a release must not; an export carries
its build's stamp; the installed packages are the lockfile's; two platforms' exports join into one bundle, the JSON
halves laid together, a disagreement refused; what a manifest says was tested and which checks a build then runs;
the runtime file, Expo's own error words, an app.json's local files, and forward slashes in the metadata.

Run:  python -B tests/test-app-artifact.py
"""
import json
import os
import re
import sys
import tarfile
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'app-pipeline', 'src'))

from chuang_app_pipeline import artifact as A  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


def write(folder, rel, data):
    path = os.path.join(folder, *rel.split('/'))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb' if isinstance(data, bytes) else 'w', **({} if isinstance(data, bytes) else {'encoding': 'utf-8'})) as f:
        f.write(data)
    return path


TREE = '5aa7bc4f0e1d2c3b4a5968778695a4b3c2d1e0f9'
VALUES = ['pk_live_x', 'firebase-key', 'app-id', 'dsn']
check("the positive case: the key is the app tree's first 12 and the config's 8-digit hash, and moves with either",
      (A.key(TREE, VALUES), A.config_hash(VALUES), A.key(TREE, VALUES) == A.key('f' + TREE[1:], VALUES),
       A.key(TREE, VALUES) == A.key(TREE, VALUES[:-1] + ['other']), A.key(TREE, VALUES) == A.key(TREE, list(VALUES))),
      # 32927451: `printf 'pk_live_x|firebase-key|app-id|dsn' | sha256sum`, its first 8, computed apart from the function
      ('5aa7bc4f0e1d-32927451', '32927451', False, False, True))

with tempfile.TemporaryDirectory() as d:
    out = os.path.join(d, 'dist')
    write(out, 'a/app.js', b'j' * 3_940_000)
    write(out, 'a/app.js.map', b'm' * 9_000_000)
    write(out, 'fonts/x.TTF', b'f' * 850_000)
    write(out, 'img/y.png', b'i' * 84_000)
    write(out, 'index.html', b'h' * 5_000)
    got = A.sizes(out)
    check("an export's sizes by kind, source maps left out, and its line beside the last archive",
          (got, A.size_line(got, 5_970_000, 10_400_000), A.size_line({'javascript': 1}, 2, None).endswith('(last not on record)'), A.mb(None)),
          ({'javascript': 3_940_000, 'fonts': 850_000, 'images': 84_000, 'other': 5_000},
           'ships 4.88 MB (javascript 3.94 MB, fonts 0.85 MB, images 0.08 MB, other 0.01 MB), archive 5.97 MB (last 10.40 MB)', True, '?'))
    packed = os.path.join(d, 'web.tgz')
    A.pack(out, packed)
    with tarfile.open(packed) as t:
        names = sorted(n for n in t.getnames() if n.endswith(('.js', '.html')))
    check('pack: the folder under its own name', (names, len(A.sha256(packed))), (['dist/a/app.js', 'dist/index.html'], 64))

NON_PROD = re.compile(r'\b(?:dev|stg)\.example\.test\b')
with tempfile.TemporaryDirectory() as d:
    good, bad, none = (os.path.join(d, n) for n in ('good', 'bad', 'none'))
    write(good, 'a.js', 'fetch("https://api.example.test/v1"); var s="20261122-2316"')
    write(good, 'a.js.map', 'https://dev.example.test in a map is never read')
    write(bad, 'a.js', 'fetch("https://api.example.test"); fetch("https://dev.example.test/api")')
    write(none, 'a.hbc', 'no api at all')
    check("a release export names the API and no host a release must not; each fault is said",
          (A.release_check(good, 'https://api.example.test', NON_PROD), A.release_check(bad, 'https://api.example.test', NON_PROD),
           A.release_check(none, 'https://api.example.test', NON_PROD)),
          ([], ['it names the non-production host dev.example.test'], ['it never names https://api.example.test']))
    check("an export carries its build's stamp as a quoted string, or is a stale cache's",
          (A.stamp_check(good, '20261122-2316'), A.stamp_check(good, '20261122-9999')),
          ([], ["it does not carry this build's stamp 20261122-9999 (a stale cache?)"]))

with tempfile.TemporaryDirectory() as app:
    lock = {'packages': {'': {'name': 'app'}, 'node_modules/a': {'version': '1.0.0'}, 'node_modules/b': {'version': '2.0.0'},
                         'node_modules/opt': {'version': '9', 'optional': True}, 'node_modules/ln': {'link': True}}}
    write(app, 'package-lock.json', json.dumps(lock))
    missing = A.stale_packages(app)
    write(app, 'node_modules/.package-lock.json', json.dumps({'packages': {'node_modules/a': {'version': '1.0.0'}, 'node_modules/b': {'version': '1.9.0'}}}))
    stale = A.stale_packages(app)
    write(app, 'node_modules/.package-lock.json', json.dumps({'packages': {'node_modules/a': {'version': '1.0.0'}, 'node_modules/b': {'version': '2.0.0'}}}))
    check("the installed packages are the lockfile's: none installed, one behind, then equal; optional ones and links skipped",
          (missing, stale, A.stale_packages(app)), (['node_modules is not installed'], ['b: lock 2.0.0, installed 1.9.0'], []))

with tempfile.TemporaryDirectory() as d:
    ios, android, out = (os.path.join(d, n) for n in ('ios', 'android', 'out'))
    write(ios, 'metadata.json', json.dumps({'version': 0, 'fileMetadata': {'ios': {'bundle': 'b\\ios.hbc', 'assets': [{'path': 'assets\\0a', 'ext': 'png'}]}}}))
    write(android, 'metadata.json', json.dumps({'version': 0, 'fileMetadata': {'android': {'bundle': 'b/android.hbc', 'assets': []}}}))
    write(ios, 'assetmap.json', json.dumps({'a': 1}))
    write(android, 'assetmap.json', json.dumps({'b': 2}))
    write(ios, 'b/ios.hbc', b'ios')
    write(android, 'b/android.hbc', b'android')
    write(ios, 'assets/0a', b'same')
    write(android, 'assets/0a', b'same')
    joined = A.join_exports([ios, android], out)
    meta = json.load(open(os.path.join(out, 'metadata.json'), encoding='utf-8'))
    A.posix_metadata(out)
    fixed = json.load(open(os.path.join(out, 'metadata.json'), encoding='utf-8'))['fileMetadata']['ios']
    check("two platforms' exports join into one bundle: each one's files, the JSON halves laid together, the second folder gone; "
          "the metadata then carries forward slashes",
          (joined == out, sorted(meta['fileMetadata']), json.load(open(os.path.join(out, 'assetmap.json'), encoding='utf-8')),
           sorted(os.listdir(os.path.join(out, 'b'))), os.path.exists(android), A.bundle_has(out, 'ios'), A.bundle_has(out, 'android'),
           A.bundle_has(out, 'web'), A.bundle_has(d, 'ios'), fixed['bundle'], fixed['assets'][0]['path']),
          (True, ['android', 'ios'], {'a': 1, 'b': 2}, ['android.hbc', 'ios.hbc'], False, True, True, False, False, 'b/ios.hbc', 'assets/0a'))
    one, two, out2 = (os.path.join(d, n) for n in ('one', 'two', 'out2'))
    write(one, 'assets/x', b'one')
    write(two, 'assets/x', b'two')
    try:
        A.join_exports([one, two], out2)
        said = ''
    except A.JoinError as e:
        said = str(e)
    check('a file two exports disagree on is refused, never one silently winning', said, 'the phone exports disagree on assets/x; nothing joined')

full = {'checks': {'passed': True, 'scope': 'full'}, 'files': {'web.tgz': 'x', 'app-test-results.json': 'y'}}
related = {'checks': {'passed': True, 'scope': 'related'}, 'files': {'web.tgz': 'x', 'app-test-results.json': 'y'}}
check("what a manifest says was tested: every suite passed and the results file is there; a related-only pass, no results "
      "file, no checks and an empty manifest are not; and which checks a build then runs",
      ([A.tested_here(m) for m in (full, {'checks': {'passed': True}, 'files': {'web.tgz': 'x', 'app-test-results.json': 'y'}}, related,
                                   {'checks': {'passed': True}, 'files': {'web.tgz': 'x'}}, {'files': {'app-test-results.json': 'y'}}, {})],
       A.checks_to_run(True, ['web'], None), A.checks_to_run(True, ['ios'], related), A.checks_to_run(True, ['ios'], full),
       A.checks_to_run(False, ['web'], None), A.checks_to_run(False, ['ios'], full)),
      ([True, True, False, False, False, False], 'full', 'full', None, 'related', None))

with tempfile.TemporaryDirectory() as d:
    path = write(d, 'runtime-versions.json', json.dumps({'ios': {'runtime': 'r1', 'inputs': 'i', 'since': 's'}}))
    check("the runtime file, Expo's own error words, and the local files an app.json names",
          (A.runtime_versions(path)['ios']['runtime'],
           A.expo_error('x\n  PluginError: Failed to resolve plugin for module "m"\ny'), A.expo_error('npx expo config failed\nwith code 1'),
           A.expo_error('nothing useful'), A.expo_error(None),
           A.config_files({'expo': {'icon': './assets/icon.png', 'name': 'App', 'ios': {'googleServicesFile': './G.plist'},
                                    'plugins': [['p', {'image': './assets/splash.png'}], 'q']}})),
          ('r1', 'PluginError: Failed to resolve plugin for module "m"', 'npx expo config failed with code 1', '', '',
           ['G.plist', 'assets/icon.png', 'assets/splash.png']))

print()
print(f"{len(fails)} failure(s)" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
