#!/usr/bin/env python3
"""chuang_sdlc.cicd.smoke_rest, the backend smoke's reads in one process over REST (SMADPickleBot #1128, the CO #1124
profile: about 60 gcloud starts at once on the desktop), on a fake transport: no call leaves this process.

Pinned (positive case first): a function smoke's function, service, revision, bus subscription and identity token
read side by side on one token into the files the host's smoke-test.sh gcloud reads write; a Cloud Run service's URL; a
function that was never a Cloud Function takes the service's URL; any other refusal, or no token, is Refused with
Google's message and never a token; no identity token says why; the key's account; main writes LF files and exits 0,
or 3 with the reason and nothing written.

Run:  python -B tests/test-sdlc-cicd-smoke-rest.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
import types
import urllib.error

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))

from chuang_sdlc.cicd import smoke_rest as SR  # noqa: E402
from chuang_platform_kit import google_auth, google_rest  # noqa: E402

fails = []


def eq(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


# The kit as installed, never kit_loader's download, in every case.
SR.kit = lambda: (google_auth, google_rest)

FN_URL = 'https://cloudfunctions.googleapis.com/v2/projects/proj/locations/us-west1/functions/app-store-watch'
SVC_URL = 'https://us-west1-run.googleapis.com/apis/serving.knative.dev/v1/namespaces/proj/services/app-store-watch'
REV_URL = 'https://us-west1-run.googleapis.com/apis/serving.knative.dev/v1/namespaces/proj/revisions/rev-2'
SUB_URL = 'https://pubsub.googleapis.com/v1/projects/proj/subscriptions/bot-reactions'
IAM = 'https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/'
METADATA = 'http://metadata.google.internal/'
SERVICE = {'status': {'url': 'https://app-store-watch-abc.a.run.app', 'latestCreatedRevisionName': 'rev-2',
                      'latestReadyRevisionName': 'rev-2',
                      'conditions': [{'type': 'ConfigurationsReady', 'status': 'True'}, {'type': 'Ready', 'status': 'True'}]}}
FUNCTION = {'state': 'ACTIVE', 'serviceConfig': {'uri': 'https://app-store-watch-xyz.a.run.app'}}


class _Answer:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


def transport(answers, seen):
    """An urlopen stand-in: each request's (method, url, Authorization, body) to `seen`, the answer the first key of
    `answers` the URL starts with: a dict (200 with its JSON) or an int (that HTTP error, Google's JSON message)."""
    def opener(req, timeout=None):
        body = json.loads(req.data.decode()) if req.data else None
        seen.append((req.get_method(), req.full_url, req.headers.get('Authorization'), body))
        for prefix, answer in answers.items():
            if req.full_url.startswith(prefix):
                if isinstance(answer, int):
                    msg = json.dumps({'error': {'message': 'Permission denied' if answer == 403 else 'not found'}}).encode()
                    raise urllib.error.HTTPError(req.full_url, answer, 'refused', {}, io.BytesIO(msg))
                return _Answer(answer if isinstance(answer, bytes) else json.dumps(answer).encode())   # bytes: plain text
        raise urllib.error.URLError('no route')
    return opener


def test_a_function_smoke_reads_everything_in_one_go():
    """the positive case: the function, the service, the dev rung's revision and the bus subscription are read on the one token given, and the identity token is minted as the impersonated account for the function's own URL; the files are smoke-test.sh's gcloud reads' shape"""
    seen = []
    op = transport({FN_URL: FUNCTION, SVC_URL: SERVICE, REV_URL: {'status': {'conditions': [{'type': 'Ready', 'status': 'True'}]}},
                    SUB_URL: {'pushConfig': {'pushEndpoint': 'https://dev---vote-writer-abc.a.run.app'}},
                    IAM: {'token': 'ID-TOKEN-1'}}, seen)
    files = SR.reads('app-store-watch', 'proj', 'us-west1', 'function', revision='rev-2', bus=True, id_token=True,
                     token='TOK', impersonate='deploy@proj.iam.gserviceaccount.com', env={}, opener=op)
    mint = next(s for s in seen if s[1].startswith(IAM))
    eq("the positive case: the function, the service, the dev rung's revision and the bus subscription are read on the one token given, and the identity token is minted as the impersonated account for the function's own URL; the files are smoke-test.sh's gcloud reads' shape",
       (files, sorted(s[1] for s in seen if s[0] == 'GET'), {s[2] for s in seen}, mint[1], mint[3]),
       ({'one': ('ACTIVE\thttps://app-store-watch-xyz.a.run.app', 0), 'svc_ready': ('True', 0), 'two': ('rev-2\trev-2', 0),
         'cond': ('True', 0), 'endpoint': ('https://dev---vote-writer-abc.a.run.app', 0), 'token': ('TOK', 0),
         'idtoken': ('ID-TOKEN-1', 0)},
        sorted([FN_URL, SVC_URL, REV_URL, SUB_URL]), {'Bearer TOK'},
        IAM + 'deploy@proj.iam.gserviceaccount.com:generateIdToken',
        {'audience': 'https://app-store-watch-xyz.a.run.app', 'includeEmail': True}))


def test_a_cloud_run_service_and_a_function_that_never_was_one():
    """  KIND run: the service's URL alone, no function read and no Ready file; a function's 404 is no refusal: its URL is the service's (svc_url, the Claude Runner, #294) and the identity token's audience too"""
    seen = []
    run = SR.reads('the-api', 'proj', 'us-west1', 'run', token='TOK', env={},
                   opener=transport({SVC_URL.replace('app-store-watch', 'the-api'): SERVICE}, seen))
    eq("  run", (run, [s[1] for s in seen]),
       ({'one': ('https://app-store-watch-abc.a.run.app', 0), 'two': ('rev-2\trev-2', 0)}, [SVC_URL.replace('app-store-watch', 'the-api')]))
    seen2 = []
    gone = SR.reads('app-store-watch', 'proj', 'us-west1', 'function', id_token=True, token='TOK',
                    impersonate='deploy@proj.iam.gserviceaccount.com', env={},
                    opener=transport({FN_URL: 404, SVC_URL: SERVICE, IAM: {'token': 'ID-2'}}, seen2))
    eq("  never a Cloud Function", (gone['one'], gone['svc_url'], gone['idtoken'], next(s[3] for s in seen2 if s[1].startswith(IAM))['audience']),
       (('function app-store-watch was not found (NOT_FOUND, status=[404])', 1), ('https://app-store-watch-abc.a.run.app', 0),
        ('ID-2', 0), 'https://app-store-watch-abc.a.run.app'))


def test_a_refusal_or_no_token_is_refused_without_the_token():
    """  any other refusal (the service's 403, the revision's 404, no answer at all) is Refused with Google's call and message and never the token, and so is no token from any source"""
    def refused(answers, **kw):
        try:
            SR.reads('app-store-watch', 'proj', 'us-west1', 'function', token='SECRET-TOK', env={}, opener=transport(answers, []), **kw)
        except SR.Refused as e:
            return str(e)
        return 'not refused'
    deny = refused({FN_URL: FUNCTION, SVC_URL: 403})
    rev = refused({FN_URL: FUNCTION, SVC_URL: SERVICE, REV_URL: 404}, revision='rev-2')
    lost = refused({FN_URL: FUNCTION})
    keep = SR.kit
    SR.kit = lambda: (types.SimpleNamespace(access_token=lambda env=None: ''), google_rest)
    try:
        try:
            SR.reads('app-store-watch', 'proj', 'us-west1', env={}, opener=transport({}, []))
            none = 'not refused'
        except SR.Refused as e:
            none = str(e)
    finally:
        SR.kit = keep
    eq("  refused, never the token",
       ('HTTP 403 Permission denied' in deny, 'namespaces/proj/services/app-store-watch' in deny,
        'HTTP 404 not found' in rev, 'could not be reached (URLError)' in lost,
        any('SECRET-TOK' in t for t in (deny, rev, lost)), none),
       (True, True, True, True, False, 'no Google token (CI_ACCESS_TOKEN, a key, the metadata server and gcloud gave none)'))


def test_no_identity_token_says_why():
    """  a refused generateIdToken is no idtoken file but idtoken.why naming the account and the URL; with no account the metadata server is asked for its account and then for a token, and its silence is said too"""
    iam = SR.reads('app-store-watch', 'proj', 'us-west1', 'function', id_token=True, token='TOK',
                   impersonate='deploy@proj.iam.gserviceaccount.com', env={},
                   opener=transport({FN_URL: FUNCTION, SVC_URL: SERVICE, IAM: 403}, []))
    seen = []
    meta = SR.reads('app-store-watch', 'proj', 'us-west1', 'function', id_token=True, token='TOK', env={},
                    opener=transport({FN_URL: FUNCTION, SVC_URL: SERVICE}, seen))
    eq("  why", ('idtoken' in iam, iam['idtoken.why'], 'idtoken' in meta, meta['idtoken.why'], any(s[1].startswith(METADATA) for s in seen)),
       (False, ('IAM Credentials gave none as deploy@proj.iam.gserviceaccount.com for https://app-store-watch-xyz.a.run.app', 0),
        False, ('the metadata server named no account; the metadata server gave none for https://app-store-watch-xyz.a.run.app', 0), True))


def test_cloud_build_mints_as_its_own_account():
    """  no impersonation and no key (a Cloud Build step, whose metadata identity endpoint answers nothing, iam.tf): the metadata server names its account and the token is minted as that account through generateIdToken, the REST twin of smoke-test.sh's id_token_self, with the metadata identity endpoint never asked; that refused, the metadata identity endpoint last; a name that is no service account's is no account"""
    acct = b'github-deploy@proj.iam.gserviceaccount.com\n'
    seen = []
    own = SR.reads('app-store-watch', 'proj', 'us-west1', 'function', id_token=True, token='TOK', env={},
                   opener=transport({FN_URL: FUNCTION, SVC_URL: SERVICE, SR.METADATA_EMAIL: acct, IAM: {'token': 'ID-SELF'}}, seen))
    mint = next(s for s in seen if s[1].startswith(IAM))
    seen2 = []
    last = SR.reads('app-store-watch', 'proj', 'us-west1', 'function', id_token=True, token='TOK', env={},
                    opener=transport({FN_URL: FUNCTION, SVC_URL: SERVICE, SR.METADATA_EMAIL: acct, IAM: 403,
                                      google_auth.METADATA_IDENTITY: b'ID-META'}, seen2))
    seen3 = []
    neither = SR.reads('app-store-watch', 'proj', 'us-west1', 'function', id_token=True, token='TOK', env={},
                       opener=transport({FN_URL: FUNCTION, SVC_URL: SERVICE, SR.METADATA_EMAIL: acct, IAM: 403}, seen3))
    eq("  Cloud Build",
       (own['idtoken'], mint[1], mint[2], mint[3]['audience'], any(s[1].startswith(google_auth.METADATA_IDENTITY) for s in seen),
        last['idtoken'], neither['idtoken.why'][0],
        SR.metadata_account(transport({SR.METADATA_EMAIL: b'default'}, [])), SR.metadata_account(transport({}, []))),
       (('ID-SELF', 0), IAM + 'github-deploy@proj.iam.gserviceaccount.com:generateIdToken', 'Bearer TOK',
        'https://app-store-watch-xyz.a.run.app', False,
        ('ID-META', 0),
        'IAM Credentials gave none as github-deploy@proj.iam.gserviceaccount.com; the metadata server gave none for https://app-store-watch-xyz.a.run.app',
        '', ''))


def test_the_runners_own_account_from_its_key():
    """  key_account: a service account's key under GOOGLE_APPLICATION_CREDENTIALS (a path or the JSON itself) names the account a runner mints as; a user's credential, a broken file or no variable names none"""
    d = tempfile.mkdtemp()
    key = os.path.join(d, 'key.json')
    with open(key, 'w', encoding='utf-8') as f:
        json.dump({'type': 'service_account', 'client_email': 'deploy@proj.iam.gserviceaccount.com'}, f)
    bad = os.path.join(d, 'bad.json')
    with open(bad, 'w', encoding='utf-8') as f:
        f.write('{not json')
    got = (SR.key_account({SR.KEY_ENV: key}), SR.key_account({SR.KEY_ENV: json.dumps({'type': 'service_account', 'client_email': 'b@p'})}),
           SR.key_account({SR.KEY_ENV: json.dumps({'type': 'authorized_user'})}), SR.key_account({SR.KEY_ENV: bad}), SR.key_account({}))
    shutil.rmtree(d, ignore_errors=True)
    eq("  the key's account", got, ('deploy@proj.iam.gserviceaccount.com', 'b@p', '', '', ''))


def test_ready_and_the_dev_rung_read():
    """  ready: the Ready condition's status wherever it sits, else the first condition's (gcloud's conditions[0]), '' with none; service(): the v1 service with its traffic, Refused on Google's refusal"""
    eq("  ready", (SR.ready(SERVICE['status']), SR.ready({'conditions': [{'type': 'X', 'status': 'False'}]}), SR.ready({})),
       ('True', 'False', ''))
    svc = SR.service('proj', 'us-west1', 'app-store-watch', 'TOK', opener=transport({SVC_URL: SERVICE}, []))
    try:
        SR.service('proj', 'us-west1', 'app-store-watch', 'TOK', opener=transport({SVC_URL: 403}, []))
        why = 'not refused'
    except SR.Refused as e:
        why = str(e)
    eq("  service", (svc, 'HTTP 403 Permission denied' in why), (SERVICE, True))


def test_main_writes_lf_files_or_refuses_writing_nothing():
    """  main: every file and its .rc written LF-ended (a Windows interpreter's CRLF would break the script's comparisons) and one line saying what was read, exit 0; a refusal exits 3 with its reason and writes nothing"""
    d = tempfile.mkdtemp()
    keep = SR.reads
    asked = []
    SR.reads = lambda *a, **kw: asked.append((a, kw)) or {'one': ('ACTIVE\thttps://fn', 0), 'two': ('r\tr', 0), 'idtoken': ('ID', 0)}
    said = []
    try:
        code = SR.main(['reads', d, 'app-store-watch', 'proj', 'us-west1', 'function', '--id-token'], out=said.append)
        raw = {n: open(os.path.join(d, n), 'rb').read() for n in sorted(os.listdir(d))}
        empty = tempfile.mkdtemp()

        def refuse(*a, **kw):
            raise SR.Refused('GET v2/projects/proj/locations/us-west1/functions/x: HTTP 403 Permission denied')
        SR.reads = refuse
        said2 = []
        code2 = SR.main(['reads', empty, 'x', 'proj', 'us-west1', 'function'], out=said2.append)
        left = os.listdir(empty)
        shutil.rmtree(empty, ignore_errors=True)
    finally:
        SR.reads = keep
        shutil.rmtree(d, ignore_errors=True)
    eq("  main", (code, raw, asked[0][0][:7], said[0].startswith('smoke: read over REST in '),
                  said[0].endswith('one process (the function, the service, an identity token)'), 'ID' in said[0],
                  code2, said2, left),
       (0, {'idtoken': b'ID\n', 'idtoken.rc': b'0\n', 'one': b'ACTIVE\thttps://fn\n', 'one.rc': b'0\n', 'two': b'r\tr\n', 'two.rc': b'0\n'},
        ('app-store-watch', 'proj', 'us-west1', 'function', '', False, True), True, True, False,
        3, ['GET v2/projects/proj/locations/us-west1/functions/x: HTTP 403 Permission denied'], []))


if __name__ == '__main__':
    for _name, _fn in list(globals().items()):
        if _name.startswith('test_') and callable(_fn):
            _fn()
    print(f'\n{len(fails)} failure(s)')
    sys.exit(1 if fails else 0)
