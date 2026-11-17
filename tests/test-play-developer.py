#!/usr/bin/env python3
"""chuang_platform_kit.play_developer: Google Play's Developer API, one client for every project (0.35.0).

Pinned (the positive case first): tracks() opens an edit, lists its tracks and deletes the edit, the token on every
call and the package in every address; an edit Play says was deleted under the read is opened again once, never a
third time; a refusal is PlayError with Google's status and message and still deletes the edit; each edit call's
method, address and body (open, commit, validate, delete, one track read, a track's releases put, the bundle and the
mapping uploaded as raw bytes); publisher_token mints through IAM Credentials for the account passed in, for the
androidpublisher scope, from the source's token, and refuses with the host's word for the account; the pure readers
(release, version_codes, newest, fact, newest_on). Offline: urllib is a fake.

Run:  python -B tests/test-play-developer.py
"""
import io
import json
import os
import sys
import urllib.error

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import play_developer as P  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


PACKAGE = 'com.example.app'
ACCOUNT = 'publisher@example-project.iam.gserviceaccount.com'
API = P.api(PACKAGE)
UPLOAD = P.upload_api(PACKAGE)
TRACKS = [
    {'track': 'internal', 'releases': [
        {'name': '1.0.0 (3)', 'versionCodes': ['3'], 'status': 'completed'},
        {'name': '1.0.0 (5)', 'versionCodes': ['5'], 'status': 'draft'},
        {'name': 'no code', 'status': 'draft'}]},
    {'track': 'alpha', 'releases': [{'name': '1.0.0 (2)', 'versionCodes': ['2'], 'status': 'completed'}]},
    {'track': 'production', 'releases': [{'name': '1.0.0 (4)', 'versionCodes': ['4'], 'status': 'inProgress', 'userFraction': 0.2}]},
]


def refusal(req, status, message):
    return urllib.error.HTTPError(req.full_url, status, 'Refused', {}, io.BytesIO(
        json.dumps({'error': {'message': message}}).encode()))


class FakePlay:
    """Play and IAM Credentials: every call kept as (method, address, body or bytes, Authorization, Content-Type).
    `deleted` answers that many tracks lists with Play's "edit deleted"; `refuse` is a path ending Play refuses 403."""

    def __init__(self, deleted=0, refuse='', raw_refuse=None):
        self.calls, self.deleted, self.refuse, self.raw_refuse, self.edits = [], deleted, refuse, raw_refuse, 0

    def __call__(self, req, timeout=None):
        url, method = req.full_url, req.get_method()
        raw = req.headers.get('Content-type') == 'application/octet-stream'
        body = req.data if raw else (json.loads(req.data.decode()) if req.data else None)
        self.calls.append((method, url, body, req.headers.get('Authorization'), req.headers.get('Content-type'), timeout))
        if 'iamcredentials' in url:
            return io.BytesIO(json.dumps({'accessToken': 'tok-play'}).encode())
        if raw and self.raw_refuse is not None:
            raise urllib.error.HTTPError(url, 403, 'Forbidden', {}, io.BytesIO(self.raw_refuse))
        if url.endswith('/tracks') and self.deleted:
            self.deleted -= 1
            raise refusal(req, 400, 'This Edit has been deleted.')
        if self.refuse and url.split('?')[0].endswith(self.refuse):
            raise refusal(req, 403, 'The caller does not have permission')
        if url.endswith('/edits'):
            self.edits += 1
            return io.BytesIO(json.dumps({'id': f'e{self.edits}'}).encode())
        if url.endswith('/tracks'):
            return io.BytesIO(json.dumps({'tracks': TRACKS}).encode())
        if url.endswith('/tracks/internal') and method == 'GET':
            return io.BytesIO(json.dumps(TRACKS[0]).encode())
        if '/bundles' in url:
            return io.BytesIO(json.dumps({'versionCode': 9}).encode())
        return io.BytesIO(b'')


def short(play):
    return [m + u.replace(API, '').replace(UPLOAD, 'UPLOAD') for m, u, *_ in play.calls]


play = FakePlay()
got = P.tracks(PACKAGE, 'tok-play', opener=play)
check('the positive case: tracks() opens an edit, lists its tracks, deletes the edit, the token on each, the package in each address',
      (got, short(play), {c[3] for c in play.calls}, all(f'/applications/{PACKAGE}/' in c[1] for c in play.calls)),
      (TRACKS, ['POST/edits', 'GET/edits/e1/tracks', 'DELETE/edits/e1'], {'Bearer tok-play'}, True))
check('  the addresses are androidpublisher v3, by package',
      (API, UPLOAD),
      ('https://androidpublisher.googleapis.com/androidpublisher/v3/applications/com.example.app',
       'https://androidpublisher.googleapis.com/upload/androidpublisher/v3/applications/com.example.app'))

once = FakePlay(deleted=1)
check('an edit deleted under the read is opened again once, and both edits are deleted',
      (P.tracks(PACKAGE, 't', opener=once) == TRACKS, short(once)),
      (True, ['POST/edits', 'GET/edits/e1/tracks', 'DELETE/edits/e1', 'POST/edits', 'GET/edits/e2/tracks', 'DELETE/edits/e2']))
twice = FakePlay(deleted=2)
try:
    P.tracks(PACKAGE, 't', opener=twice)
    raised = ''
except P.PlayError as e:
    raised = str(e)
check('  deleted twice raises, after two edits and no third',
      (P.EDIT_DELETED in raised, sum(1 for c in twice.calls if c[0] == 'POST')), (True, 2))

refused = FakePlay(refuse='/tracks')
try:
    P.tracks(PACKAGE, 't', opener=refused)
    raised = ''
except P.PlayError as e:
    raised = str(e)
check("a refused list: PlayError with Google's status and message, the edit still deleted, not retried",
      ('HTTP 403 The caller does not have permission' in raised, short(refused)),
      (True, ['POST/edits', 'GET/edits/e1/tracks', 'DELETE/edits/e1']))

# Each edit call: method, address, body.
play = FakePlay()
edit = P.open_edit(PACKAGE, 't', opener=play)
one = P.edit_track(PACKAGE, edit, 'internal', 't', opener=play)
P.set_track(PACKAGE, edit, 'alpha', [P.release(9, 'completed')], 't', opener=play)
P.validate_edit(PACKAGE, edit, 't', opener=play)
P.commit_edit(PACKAGE, edit, 't', opener=play)
gone = P.delete_edit(PACKAGE, edit, 't', opener=play)
check('the edit calls: open (an empty JSON body), one track read, a track put, validate, commit, delete',
      (edit, one, gone, [(m, u.replace(API, ''), b) for m, u, b, *_ in play.calls]),
      ('e1', TRACKS[0], True,
       [('POST', '/edits', {}), ('GET', '/edits/e1/tracks/internal', None),
        ('PUT', '/edits/e1/tracks/alpha', {'track': 'alpha', 'releases': [{'name': '9', 'versionCodes': ['9'], 'status': 'completed'}]}),
        ('POST', '/edits/e1:validate', None), ('POST', '/edits/e1:commit', None), ('DELETE', '/edits/e1', None)]))
check('  delete_edit says False when Play refuses, and never raises',
      P.delete_edit(PACKAGE, 'e1', 't', opener=FakePlay(refuse='/edits/e1')), False)

play = FakePlay()
bundle = P.upload_bundle(PACKAGE, 'e1', b'AAB-BYTES', 't', opener=play)
P.upload_mapping(PACKAGE, 'e1', 9, b'MAPPING', 't', opener=play)
check('the uploads: raw bytes as octet-stream to the upload address, the token on each, their own timeouts',
      (bundle, [(m, u.replace(UPLOAD, ''), b, a, ct, t) for m, u, b, a, ct, t in play.calls]),
      ({'versionCode': 9},
       [('POST', '/edits/e1/bundles?uploadType=media', b'AAB-BYTES', 'Bearer t', 'application/octet-stream', 900),
        ('POST', '/edits/e1/apks/9/deobfuscationFiles/proguard?uploadType=media', b'MAPPING', 'Bearer t', 'application/octet-stream', 300)]))
for label, raw, want in (("  a refused upload: PlayError with the status and Google's message", b'{"error": {"message": "APK too large"}}', 'answered HTTP 403: APK too large'),
                         ('  a refused upload with no JSON: the status and its reason', b'not json', 'answered HTTP 403: Forbidden')):
    try:
        P.upload_bundle(PACKAGE, 'e1', b'x', 't', opener=FakePlay(raw_refuse=raw))
        raised = ''
    except P.PlayError as e:
        raised = str(e)
    check(label, (want in raised, 't' == raised), (True, False))


class Mine(Exception):
    pass


try:
    P.call('GET', f'{API}/edits/e1/tracks', 't', opener=FakePlay(refuse='/tracks'), error=Mine)
    kind = ''
except Mine as e:
    kind = type(e).__name__
check("  call(error=) raises the host's own class", kind, 'Mine')

# The token.
play = FakePlay()
tok = P.publisher_token(ACCOUNT, source=lambda: 'user-tok', opener=play)
method, url, body, auth, *_ = play.calls[0]
check("publisher_token: IAM Credentials' generateAccessToken for the account passed in, the androidpublisher scope, on the source's token",
      (tok, method, url, url == P.iam_token_url(ACCOUNT), body['scope'], auth),
      ('tok-play', 'POST',
       'https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/publisher@example-project.iam.gserviceaccount.com:generateAccessToken',
       True, ['https://www.googleapis.com/auth/androidpublisher'], 'Bearer user-tok'))
none = FakePlay()
try:
    P.publisher_token(ACCOUNT, source=lambda: '', opener=none, what='release-bot')
    raised = ''
except P.PlayError as e:
    raised = str(e)
check("  no source token: PlayError in the host's word for the account, no call made, no token in the text",
      (raised.startswith('release-bot token not minted: '), none.calls), (True, []))

# The pure readers.
check('release: the code as its name and a string, the status; notes in a language, cut to Play\'s limit',
      (P.release(7), P.release(7, 'completed', '1.2 (7)', 'n' * 600, 'fr-FR')['releaseNotes'][0]['language'],
       len(P.release(7, notes='n' * 600)['releaseNotes'][0]['text']), P.release(7, 'completed', '1.2 (7)')),
      ({'name': '7', 'versionCodes': ['7'], 'status': 'draft'}, 'fr-FR', P.NOTES_MAX,
       {'name': '1.2 (7)', 'versionCodes': ['7'], 'status': 'completed'}))
check('version_codes: every numeric code as an int', P.version_codes(TRACKS[0]['releases'] + [{'versionCodes': ['x', 12]}]), [3, 5, 12])
check('newest: the highest version code; a release with no code is skipped; none is None',
      (P.newest(TRACKS[0]['releases'])['name'], P.newest([{'name': 'no code'}]), P.newest(None)), ('1.0.0 (5)', None, None))
check('fact: track, version, codes as strings, status, fraction',
      (P.fact('production', TRACKS[2]['releases'][0]), P.fact('internal', {'status': P.NONE})),
      ({'track': 'production', 'version': '1.0.0 (4)', 'codes': ['4'], 'status': 'inProgress', 'fraction': 0.2},
       {'track': 'internal', 'version': '', 'codes': [], 'status': 'none', 'fraction': None}))
beta = [{'track': 'beta', 'releases': [{'name': 'b', 'versionCodes': ['7'], 'status': 'completed'}]},
        {'track': 'alpha', 'releases': [{'name': 'a', 'versionCodes': ['8'], 'status': 'completed'}]},
        {'track': 'production', 'releases': []}]
order = ('production', 'beta', 'alpha')
check('newest_on: the first track in the order given that holds a release; an empty track falls through; none says NONE',
      (P.newest_on(TRACKS, order)['track'], P.newest_on(beta, order)['track'], P.newest_on(beta[1:], order)['track'],
       P.newest_on([], order), P.newest_on([], 'internal'), P.newest_on(TRACKS, 'internal')['codes']),
      ('production', 'beta', 'alpha', {'track': '', 'version': '', 'codes': [], 'status': 'none', 'fraction': None},
       {'track': 'internal', 'version': '', 'codes': [], 'status': 'none', 'fraction': None}, ['5']))

print()
print(f"{len(fails)} failure(s)" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
