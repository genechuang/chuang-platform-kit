#!/usr/bin/env python3
"""chuang_platform_kit.app_store_connect: App Store Connect's API, one client for every project (SMAD PickleBot #570).

Pinned (the positive case first): snapshot() reads the builds, the App Store versions with their builds and the open
review's submit time, and the internal testers side by side, the app id a parameter; token() is an ES256 JWT with the
key id, Apple's audience, the issuer and a 20-minute life; call() posts a body, gives {} on an empty answer, and refuses
with Apple's own reason, the status, or the error's name when nothing answers; private_key() reads the environment,
else Secret Manager through the given access, and '' on a refusal. Offline: urllib is a fake.

Run:  python -B tests/test-app-store-connect.py
"""
import io
import json
import os
import sys
import urllib.error
import urllib.parse

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import app_store_connect as A  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


APP = '6819574789'
BUILDS = {'data': [{'id': 'b12', 'attributes': {'version': '12', 'uploadedDate': '2026-10-09T01:00:00Z', 'processingState': 'VALID'},
                    'relationships': {'buildBetaDetail': {'data': {'type': 'buildBetaDetails', 'id': 'd12'}},
                                      'betaAppReviewSubmission': {'data': {'type': 'betaAppReviewSubmissions', 'id': 's12'}},
                                      'preReleaseVersion': {'data': {'type': 'preReleaseVersions', 'id': 'p1'}}}},
                   {'id': 'b11', 'attributes': {'version': '11', 'uploadedDate': '2026-10-08T01:00:00Z', 'processingState': 'VALID'},
                    'relationships': {}}],
          'included': [{'type': 'buildBetaDetails', 'id': 'd12', 'attributes': {'externalBuildState': 'IN_BETA_REVIEW', 'internalBuildState': 'IN_BETA_TESTING'}},
                       {'type': 'betaAppReviewSubmissions', 'id': 's12', 'attributes': {'betaReviewState': 'IN_REVIEW', 'submittedDate': '2026-10-09T02:00:00Z'}},
                       {'type': 'preReleaseVersions', 'id': 'p1', 'attributes': {'version': '0.2.0'}}]}
VERSIONS = {'data': [{'id': 'v1', 'attributes': {'versionString': '1.0.0', 'appVersionState': 'WAITING_FOR_REVIEW'},
                      'relationships': {'build': {'data': {'type': 'builds', 'id': 'b12'}}}},
                     {'id': 'v0', 'attributes': {'versionString': '', 'appStoreState': 'X'}}],
            'included': [{'type': 'builds', 'id': 'b12', 'attributes': {'version': '12'}}]}
SUBMISSIONS = {'data': [{'id': 'r1', 'attributes': {'state': 'WAITING_FOR_REVIEW', 'submittedDate': '2026-10-22T18:00:00Z'}},
                        {'id': 'r0', 'attributes': {'state': 'COMPLETE', 'submittedDate': '2026-10-01T00:00:00Z'}}]}
GROUPS = {'data': [{'id': 'g1', 'attributes': {'isInternalGroup': True}}, {'id': 'g2', 'attributes': {'isInternalGroup': False}}]}
TESTERS = {'data': [{'id': 't-gene', 'attributes': {'firstName': 'Gene', 'lastName': 'Chuang'}}, {'id': 't-x', 'attributes': {}}]}
ANSWERS = {'/builds': BUILDS, f'/apps/{APP}/appStoreVersions': VERSIONS, f'/apps/{APP}/reviewSubmissions': SUBMISSIONS,
           f'/apps/{APP}/betaGroups': GROUPS, '/betaGroups/g1/betaTesters': TESTERS}


def opener_for(answers, calls, fail=None, body=b'{"errors": [{"detail": "The token has expired"}]}'):
    def opener(req, timeout=0):
        path = req.full_url.replace(A.API, '')
        calls.append((req.get_method(), path, req.headers.get('Authorization'), req.data and json.loads(req.data)))
        bare = path.split('?')[0]
        if fail and bare.startswith(fail[0]):
            raise urllib.error.HTTPError(req.full_url, fail[1], 'no', {}, io.BytesIO(body))
        for key, value in answers.items():
            if bare == key:
                return Resp(json.dumps(value).encode() if value is not None else b'')
        return Resp(b'{}')
    return opener


calls = []
snap = A.snapshot(APP, 'tok', limit=2, opener=opener_for(ANSWERS, calls))
check("the positive case: snapshot reads the builds, the store versions with their builds and the open review's time, and the internal testers",
      (snap['builds']['12'], '11' in snap['builds'], snap['store'], snap['store_builds'], snap['store_submitted'], snap['internal'],
       sorted(c[1].split('?')[0] for c in calls), all(c[2] == 'Bearer tok' for c in calls)),
      ({'version': '0.2.0', 'beta_review': 'IN_REVIEW', 'external': 'IN_BETA_REVIEW', 'uploaded': '2026-10-09T01:00:00Z',
        'processing': 'VALID', 'submitted': '2026-10-09T02:00:00Z'}, True, {'1.0.0': 'WAITING_FOR_REVIEW'}, {'1.0.0': '12'},
       '2026-10-22T18:00:00Z', {'t-gene': 'Gene Chuang', 't-x': 'a tester'},
       ['/apps/6819574789/appStoreVersions', '/apps/6819574789/betaGroups', '/apps/6819574789/reviewSubmissions', '/betaGroups/g1/betaTesters', '/builds'], True))
query = dict(urllib.parse.parse_qsl(next(c[1] for c in calls if c[1].startswith('/builds')).split('?')[1]))
check("  the builds query: the app, newest first, the limit, the three includes",
      (query['filter[app]'], query['sort'], query['limit'], query['include']),
      (APP, '-uploadedDate', '2', 'buildBetaDetail,betaAppReviewSubmission,preReleaseVersion'))
check("  store_versions without builds is the states alone", A.store_versions(APP, 'tok', opener=opener_for(ANSWERS, [])), {'1.0.0': 'WAITING_FOR_REVIEW'})

import jwt  # noqa: E402  (the asc extra)
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
k = ec.generate_private_key(ec.SECP256R1())
pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
t = A.token(pem, ' KEYID ', 'issuer-1', now=1_000_000)
claims = jwt.decode(t, k.public_key(), algorithms=['ES256'], audience='appstoreconnect-v1', options={'verify_exp': False})
head = jwt.get_unverified_header(t)
check("token: ES256, the key id trimmed in the header, the issuer, Apple's audience, 20 minutes",
      (head['alg'], head['kid'], head['typ'], claims['iss'], claims['aud'], claims['iat'], claims['exp'] - claims['iat']),
      ('ES256', 'KEYID', 'JWT', 'issuer-1', 'appstoreconnect-v1', 1_000_000, 1200))

posted = []
made = A.call('POST', '/bundleIdCapabilities', 'tok', body={'data': {'type': 'bundleIdCapabilities'}}, opener=opener_for({'/bundleIdCapabilities': {'data': {'id': 'c1'}}}, posted))
empty = A.call('PATCH', '/betaAppReviewDetails/1', 'tok', body={'x': 1}, opener=opener_for({'/betaAppReviewDetails/1': None}, []))
try:
    A.get('/builds', 'tok', {'limit': '1'}, opener=opener_for(ANSWERS, [], fail=('/builds', 401)))
    refused = ''
except A.Refused as e:
    refused = str(e)
try:
    A.get('/builds', 'tok', opener=opener_for(ANSWERS, [], fail=('/builds', 500), body=b'not json'))
    plain = ''
except A.Refused as e:
    plain = str(e)


def down(req, timeout=0):
    raise urllib.error.URLError('down')


try:
    A.get('/builds', 'tok', opener=down)
    gone = ''
except A.Refused as e:
    gone = str(e)
check("call posts a JSON body and gives the answer, {} on an empty one; a refusal carries Apple's reason, the status alone without one, the error's name when nothing answers",
      (made, posted[0][0], posted[0][3], empty, refused, plain, gone, isinstance(A.Refused('x'), RuntimeError)),
      ({'data': {'id': 'c1'}}, 'POST', {'data': {'type': 'bundleIdCapabilities'}}, {}, 'GET /builds answered 401: The token has expired',
       'GET /builds answered 500', 'GET /builds did not answer (URLError)', True))

asked = []


def access(project, name):
    asked.append((project, name))
    if name == 'REFUSED':
        raise RuntimeError('HTTP 403')
    return b'-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n'


check("private_key: the environment first, else Secret Manager through access, '' on a refusal or with no project",
      (A.private_key('K', {'K': ' pem '}, project='p', access=access), A.private_key('K', {}, project='p', access=access),
       A.private_key('REFUSED', {}, project='p', access=access), A.private_key('K', {}), asked),
      ('pem', '-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----', '', '', [('p', 'K'), ('p', 'REFUSED')]))

print()
print(f"{len(fails)} failure(s)" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
