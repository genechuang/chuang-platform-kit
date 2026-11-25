#!/usr/bin/env python3
"""chuang_app_pipeline.expo_channel: what an Expo update channel serves a phone, judged against a release's notice
(SMADPickleBot #1891, moved into chuang-app-pipeline by #2236).

Pinned (the positive case first): a delivered update passes with what was checked, asked as a phone asks; each wrong
delivery (branch, runtime, publish time, other files, no download, other bytes) is a failure with its reason and an
ERROR line; what cannot be judged (a lower channel moved on or unread) is said and not failed; a release that
published no update is not checked; an unreachable server or no manifest is a failure; the channels' names are the
host's; same_tree never passes an unread tree.

Run:  python -B tests/test-app-expo-channel.py
"""
import base64
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'app-pipeline', 'src'))

from chuang_app_pipeline import expo_channel as C  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


SHA = '1b78fe1d61c19ebfdff659c419280dadce1ed64a'
RUNTIME = '83736e6e4ef01839a02799eaa5b066f4378bbd7b'
BUNDLE = b'hermes bytecode of the release'
HASH = base64.urlsafe_b64encode(hashlib.sha256(BUNDLE).digest()).decode().rstrip('=')
NOTICE = {'sha': '1b78fe1', 'label': '20261119-1006', 'runtime': RUNTIME, 'platform': 'ios', 'published_at': '2026-11-19T18:11:39.575Z'}
URL = 'https://u.example.invalid/project'


def manifest(branch='production', created='2026-11-19T18:11:39.173Z', runtime=RUNTIME, bundle_hash=HASH, group='g-prod'):
    return {'id': 'u-1', 'createdAt': created, 'runtimeVersion': runtime,
            'launchAsset': {'hash': bundle_hash, 'key': 'k1', 'contentType': 'application/javascript', 'url': 'https://cdn.example.invalid/k1?x'},
            'assets': [], 'metadata': {'updateGroup': group, 'branchName': branch}}


def multipart(m):
    b = '-----ExpoManifestBoundary-abc'
    ext = json.dumps({'assetRequestHeaders': {'k1': {'authorization': 'EAS-HMAC synthetic'}}})
    return (f'--{b}\r\nContent-Disposition: form-data; name="manifest"\r\nContent-Type: application/json\r\n\r\n{json.dumps(m)}\r\n'
            f'--{b}\r\nContent-Disposition: form-data; name="extensions"\r\nContent-Type: application/json\r\n\r\n{ext}\r\n--{b}--\r\n').encode()


class Curl:
    """A stand-in curl: each channel's manifest and the bundle's download, by what the arguments ask for."""

    def __init__(self, prod=None, dev=None, bundle=BUNDLE, code='200', down=False, lower='dev'):
        self.prod, self.dev, self.bundle, self.code, self.down, self.lower, self.calls = prod, dev, bundle, code, down, lower, []

    def __call__(self, args, **kw):
        self.calls.append(args)
        text = ' '.join(args)
        done = lambda out, rc=0: subprocess.CompletedProcess(args, rc, out, b'could not resolve host' if rc else b'')   # noqa: E731
        if '-o' in args:   # the bundle
            with open(args[args.index('-o') + 1], 'wb') as f:
                f.write(self.bundle)
            return done(self.code.encode())
        if self.down:
            return done(b'', 6)
        m = self.dev if f'expo-channel-name: {self.lower}' in text else self.prod
        return done(multipart(m) if m else b'{"error":"nothing"}')


dev = manifest(group='g-dev', created='2026-11-19T18:09:30.000Z')
curl = Curl(prod=manifest(), dev=dev)
v = C.check(SHA, 'ios', NOTICE, URL, run=curl, by='host check')
asked = ' '.join(curl.calls[0])
check("the positive case: the released channel serves this release's update, the same bundle the lower channel serves, "
      "and the bundle downloads with the manifest's hash; asked as a phone asks, the download carrying the asset header",
      (v['passed'], v['checked'], v['update'], v['group'], v['label'], v['by'], v['detail'],
       all(h in asked for h in ('expo-platform: ios', f'expo-runtime-version: {RUNTIME}', 'expo-channel-name: production', URL)),
       'authorization: EAS-HMAC synthetic' in ' '.join(curl.calls[-1]),
       C.line(v).startswith('channel check: iPhone: production serves 20261119-1006')),
      (True, True, 'u-1', 'g-prod', '20261119-1006', 'host check',
       ['the bundle is the one the dev channel serves, the files the device ran', "the bundle downloads and its sha256 is the manifest's"],
       True, True, True))
cases = {
    'branch': (Curl(prod=manifest(branch='preview'), dev=dev), 'the channel serves branch preview, not production'),
    'runtime': (Curl(prod=manifest(runtime='f' * 40), dev=dev), "the update's runtime is ffffffffff, the release's is 83736e6e4e"),
    'stale': (Curl(prod=manifest(created='2026-11-19T17:31:40.000Z'), dev=dev), 'production serves an update published 2026-11-19T17:31:40.000Z'),
    'other files': (Curl(prod=manifest(bundle_hash='x' * 43), dev=dev), "production's bundle is not the one the dev channel serves: not the files the device ran"),
    'no download': (Curl(prod=manifest(), dev=dev, code='403'), 'the bundle did not download (HTTP 403)'),
    'other bytes': (Curl(prod=manifest(), dev=dev, bundle=b'tampered'), "the downloaded bundle's sha256 is not the manifest's hash"),
}
got = {}
for name, (c, want) in cases.items():
    w = C.check(SHA, 'android', NOTICE, URL, run=c)
    got[name] = (w['passed'], any(want in d for d in w['detail']), C.line(w).startswith('ERROR: channel check: Android update of 1b78fe1'))
check('each wrong delivery is a failure with its reason and an ERROR line', got, {name: (False, True, True) for name in cases})
moved = C.check(SHA, 'ios', NOTICE, URL, run=Curl(prod=manifest(), dev=manifest(bundle_hash='n' * 43, created='2026-11-19T18:40:00.000Z')))
blind = C.check(SHA, 'ios', NOTICE, URL, run=Curl(prod=manifest(), dev=None))
check('a lower channel that moved on, or cannot be read, is said and the delivery still passes on the rest',
      (moved['passed'], moved['detail'][0][:36], blind['passed'], any('could not be read' in d for d in blind['detail'])),
      (True, 'the dev channel has moved on to a ne', True, True))
# 0.5.0 (SMADPickleBot #2391): a newer build reached the lower channel between the device's run and the ship, so its
# update was made before the released one's
early = manifest(bundle_hash='n' * 43, created='2026-11-19T18:00:00.000Z')
at = lambda text: __import__('datetime').datetime.fromisoformat(text)   # noqa: E731
newer = C.check(SHA, 'ios', NOTICE, URL, run=Curl(prod=manifest(), dev=early), built=at('2026-11-19T17:00:00+00:00'))
own = C.check(SHA, 'ios', NOTICE, URL, run=Curl(prod=manifest(), dev=early), built=at('2026-11-19T09:55:00-08:00'))
unsaid = C.check(SHA, 'ios', NOTICE, URL, run=Curl(prod=manifest(), dev=early))
check("a newer build on the lower channel by the release's own build time is said, not failed; within that build's own "
      "time, or with no build time, other files are still a failure",
      (newer['passed'], newer['detail'][0][:36], own['passed'], unsaid['passed']),
      (True, 'the dev channel has moved on to a ne', False, False))
other_note = {**NOTICE, 'sha': 'abc1234'}
asked = []
check("a release that published no update is not checked: no notice, a notice naming another commit with no tree reader "
      "or a different tree; the same tree at an earlier commit is this release's update",
      (C.check(SHA, 'ios', None, URL, run=Curl(prod=manifest()))['checked'],
       C.check(SHA, 'ios', {}, URL, run=Curl(prod=manifest()))['detail'],
       C.check(SHA, 'ios', other_note, URL, run=Curl(prod=manifest()))['checked'],
       C.check(SHA, 'ios', other_note, URL, run=Curl(prod=manifest()), same=lambda a, b: False)['detail'][0],
       C.check(SHA, 'ios', other_note, URL, run=Curl(prod=manifest(), dev=dev), same=lambda a, b: asked.append((a, b)) or True)['checked'],
       asked),
      (False, ['no ios update was published for 1b78fe1 (no notice); nothing to check'], False,
       "no ios update was published for 1b78fe1 (the notice names abc1234, whose app tree is not this release's); nothing to check",
       True, [(SHA, 'abc1234')]))
down = C.check(SHA, 'ios', NOTICE, URL, run=Curl(prod=manifest(), down=True))
empty = C.check(SHA, 'ios', NOTICE, URL, run=Curl(prod=None))
check('an unreachable server or no manifest is a failure, never a pass',
      ((down['passed'], down['detail'][0][:38]), (empty['passed'], empty['detail'])),
      ((False, 'the update server could not be reached'), (False, ['the channel answered no manifest'])))
staged = Curl(prod=manifest(branch='live'), dev=manifest(group='g-dev', created='2026-11-19T18:09:30.000Z'), lower='staging')
named = C.check(SHA, 'ios', NOTICE, URL, run=staged, channel='live', lower='staging')
check("the channels' names are the host's: the request, the branch check and the words follow them",
      (named['passed'], named['detail'][0], 'expo-channel-name: live' in ' '.join(staged.calls[0]),
       'expo-channel-name: staging' in ' '.join(staged.calls[1]), C.line(named, 'live')[:38]),
      (True, 'the bundle is the one the staging channel serves, the files the device ran', True, True,
       'channel check: iPhone: live serves 202'))
trees = {'r1': 'tree-a', 'r2': 'tree-a', 'r3': 'tree-b'}
read = lambda r: trees.get(r, '')   # noqa: E731
check('same_tree: equal app trees, never an unread one',
      (C.same_tree('r1', 'r2', read), C.same_tree('r1', 'r3', read), C.same_tree('r1', 'nope', read), C.same_tree('nope', 'gone', read)),
      (True, False, False, False))
try:
    C.parse(b'<html>error</html>')
    refused = False
except ValueError:
    refused = True
check('parse: the manifest and its asset headers; an answer with no manifest is a ValueError',
      (C.parse(multipart(manifest()))['headers'], C.parse(multipart(manifest()))['manifest']['id'], refused),
      ({'k1': {'authorization': 'EAS-HMAC synthetic'}}, 'u-1', True))

print()
print(f"{len(fails)} failure(s)" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
