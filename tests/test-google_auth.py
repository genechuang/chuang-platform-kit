#!/usr/bin/env python3
"""chuang_platform_kit.google_auth: a Google service from a token in a file, the environment or Secret Manager, refreshed and written back.

Pinned, against fake google-auth and googleapiclient modules (the positive case first): the source
order json > file > env > secret, the first that parses; an expired token is refreshed and the refresh
is written back to the file that exists and to Secret Manager when a secret was named; a token that
cannot be read or refreshed answers None with a WARNING, never raises; service() builds the client
on the credentials and answers None without them; credentials() answers None without google-auth.

Run:  python -B tests/test-google_auth.py
"""
import json
import logging
import os
import sys
import tempfile
import types

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


# -- fake google-auth and googleapiclient, installed before the module is imported
class FakeCreds:
    made = []

    def __init__(self, data, scopes):
        self.data, self.scopes = data, scopes
        self.expired = bool(data.get('expired'))
        self.refresh_token = data.get('refresh_token')
        self.valid = not self.expired or not self.refresh_token and False
        self.valid = not self.expired
        self.refreshed = 0
        FakeCreds.made.append(self)

    @classmethod
    def from_authorized_user_info(cls, data, scopes):
        if 'token' not in data:
            raise ValueError('not an authorized user token')
        return cls(data, scopes)

    @classmethod
    def from_authorized_user_file(cls, path, scopes):
        with open(path, encoding='utf-8') as f:
            return cls.from_authorized_user_info(json.load(f), scopes)

    def refresh(self, request):
        if self.data.get('refresh_fails'):
            raise RuntimeError('invalid_grant')
        self.refreshed += 1
        self.expired, self.valid = False, True
        self.data = {**self.data, 'token': 'fresh', 'expired': False}

    def to_json(self):
        return json.dumps(self.data)


built = []
fake_google = types.ModuleType('google')
fake_auth = types.ModuleType('google.auth')
fake_transport = types.ModuleType('google.auth.transport')
fake_requests = types.ModuleType('google.auth.transport.requests')
fake_requests.Request = lambda: 'request'
fake_oauth2 = types.ModuleType('google.oauth2')
fake_oauth_creds = types.ModuleType('google.oauth2.credentials')
fake_oauth_creds.Credentials = FakeCreds
fake_api = types.ModuleType('googleapiclient')
fake_discovery = types.ModuleType('googleapiclient.discovery')
fake_discovery.build = lambda api, version, credentials=None, cache_discovery=True: built.append((api, version, credentials.data.get('token'))) or f'{api}-{version}'


class FakeSA:
    """google.oauth2.service_account.Credentials: a key file read, no refresh."""

    def __init__(self, path, scopes):
        self.path, self.scopes, self.data = path, scopes, {'token': 'sa'}

    @classmethod
    def from_service_account_file(cls, path, scopes=None):
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        return cls(path, scopes)


fake_sa = types.ModuleType('google.oauth2.service_account')
fake_sa.Credentials = FakeSA
fake_oauth2.service_account = fake_sa
for name, mod in (('google', fake_google), ('google.auth', fake_auth), ('google.auth.transport', fake_transport),
                  ('google.auth.transport.requests', fake_requests), ('google.oauth2', fake_oauth2),
                  ('google.oauth2.credentials', fake_oauth_creds), ('google.oauth2.service_account', fake_sa),
                  ('googleapiclient', fake_api), ('googleapiclient.discovery', fake_discovery)):
    sys.modules[name] = mod

from chuang_platform_kit import google_auth as G  # noqa: E402

logs = []
handler = logging.Handler()
handler.emit = lambda rec: logs.append((rec.levelname, rec.getMessage()))
G.log.addHandler(handler)
SCOPES = ['https://www.googleapis.com/auth/gmail.send']


class Session:
    def __init__(self, store):
        self.store, self.posted = store, []

    def get(self, url, timeout=None):
        import base64
        name = url.split('/secrets/')[1].split('/')[0]
        body = {'payload': {'data': base64.b64encode(self.store[name]).decode()}} if name in self.store else {}
        return types.SimpleNamespace(status_code=200 if name in self.store else 404, json=lambda: body,
                                     raise_for_status=lambda: (_ for _ in ()).throw(RuntimeError('404')) if name not in self.store else None)

    def post(self, url, json=None, timeout=None):
        import base64
        self.posted.append((url.split('/secrets/')[1].split(':')[0], base64.b64decode(json['payload']['data']).decode()))
        return types.SimpleNamespace(status_code=200, json=lambda: {'name': 'v2'}, raise_for_status=lambda: None)


print("the positive case: a valid token from a file builds the service; the sources are tried json > file > env > secret")
with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, 'token.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'token': 'from-file', 'refresh_token': 'r'}, f)
    svc = G.service('gmail', 'v1', SCOPES, token_file=path)
    check("built", (svc, built[-1]), ('gmail-v1', ('gmail', 'v1', 'from-file')))
    os.environ['TOK_ENV'] = json.dumps({'token': 'from-env'})
    order = (G.credentials(SCOPES, token_json={'token': 'from-json'}, token_file=path, token_env='TOK_ENV').data['token'],
             G.credentials(SCOPES, token_file=path, token_env='TOK_ENV').data['token'],
             G.credentials(SCOPES, token_file=os.path.join(d, 'none.json'), token_env='TOK_ENV').data['token'],
             G.credentials(SCOPES, token_env='NOPE', secret=('p', 'T'), session=Session({'T': b'{"token": "from-secret"}'})).data['token'])
    check("order", order, ('from-json', 'from-file', 'from-env', 'from-secret'))

    print("an expired token is refreshed and written back to the file that exists and to Secret Manager")
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'token': 'stale', 'refresh_token': 'r', 'expired': True}, f)
    sess = Session({})
    creds = G.credentials(SCOPES, token_file=path, secret=('p', 'GMAIL_TOKEN'), session=sess)
    with open(path, encoding='utf-8') as f:
        on_disk = json.load(f)['token']
    check("refreshed and written back", (creds.refreshed, creds.data['token'], on_disk, sess.posted),
          (1, 'fresh', 'fresh', [('GMAIL_TOKEN', json.dumps({'token': 'fresh', 'refresh_token': 'r', 'expired': False}))]))
    sess2 = Session({})
    G.credentials(SCOPES, token_json={'token': 'stale', 'refresh_token': 'r', 'expired': True}, secret=('p', 'T'), write_back=False, session=sess2)
    check("write_back off", sess2.posted, [])

print("a token that cannot be read or refreshed answers None with a WARNING, never raises")
logs.clear()
os.environ['BAD_ENV'] = 'not json'
none = (G.credentials(SCOPES), G.credentials(SCOPES, token_env='BAD_ENV'), G.credentials(SCOPES, token_json={'nope': 1}),
        G.credentials(SCOPES, token_json={'token': 'x', 'refresh_token': 'r', 'expired': True, 'refresh_fails': True}),
        G.credentials(SCOPES, secret=('p', 'MISSING'), session=Session({})))
check("none", none, (None,) * 5)
check("warned", [m.split(' ')[1] for lvl, m in logs if lvl == 'WARNING'][:5], ['BAD_ENV', 'token_json', 'token', 'credentials', 'the'])
check("service() without a token is None", G.service('drive', 'v3', SCOPES, token_env='NOPE'), None)
os.environ.pop('TOK_ENV', None)
os.environ.pop('BAD_ENV', None)

print("a service account's key is one more source (0.19.0): its Credentials for the scopes, no refresh, no write-back; a missing file is None with a WARNING")
with tempfile.TemporaryDirectory() as d:
    key = os.path.join(d, 'sa.json')
    with open(key, 'w', encoding='utf-8') as f:
        json.dump({'type': 'service_account', 'client_email': 'x@p.iam.gserviceaccount.com'}, f)
    sa = G.credentials(SCOPES, service_account_file=key)
    svc = G.service('sheets', 'v4', SCOPES, service_account_file=key)
    missing = G.credentials(SCOPES, service_account_file=os.path.join(d, 'none.json'))
check("service account", (type(sa).__name__, sa.path, sa.scopes, svc, built[-1][:2], missing, logs[-1][0], 'service account key was not read' in logs[-1][1]),
      ('FakeSA', key, SCOPES, 'sheets-v4', ('sheets', 'v4'), None, 'WARNING', True))

print("token_info() reads the stored token itself in the same order with the standard library; refresh_access_token() trades it over REST")
import io  # noqa: E402
import urllib.error  # noqa: E402


class RestResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, 'token.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'refresh_token': 'r-file'}, f)
    os.environ['TOK_ENV'] = json.dumps({'refresh_token': 'r-env'})
    os.environ['BAD_ENV'] = 'not json'
    info_order = (G.token_info(token_json={'refresh_token': 'r-json'}, token_file=path)['refresh_token'],
                  G.token_info(token_file=path, token_env='TOK_ENV')['refresh_token'],
                  G.token_info(token_file=os.path.join(d, 'none.json'), token_env='TOK_ENV')['refresh_token'],
                  G.token_info(token_env='NOPE', secret=('p', 'T'), session=Session({'T': b'{"refresh_token": "r-secret"}'}))['refresh_token'],
                  G.token_info(token_env='BAD_ENV'), G.token_info(secret=('p', 'MISSING'), session=Session({})))
    os.environ.pop('TOK_ENV', None)
    os.environ.pop('BAD_ENV', None)
rest_calls = []


def rest_ok(req, timeout=None):
    rest_calls.append((req.full_url, req.get_method(), req.data, timeout))
    return RestResp(json.dumps({'access_token': 'at', 'expires_in': 3600}).encode())


def refused(exc):
    def o(req, timeout=None):
        raise exc
    return o


def refresh_said(info, opener):
    try:
        return G.refresh_access_token(info, opener=opener)
    except G.RefreshFailed as e:
        return str(e)


got = refresh_said({'refresh_token': 'r', 'client_id': 'c', 'client_secret': 's', 'token_uri': 'https://t.example/token'}, rest_ok)
check("token_info and refresh_access_token",
      (info_order, got, rest_calls[0][:2], b'refresh_token=r' in rest_calls[0][2] and b'client_secret=s' in rest_calls[0][2], rest_calls[0][3],
       refresh_said({'refresh_token': 'r'}, refused(urllib.error.HTTPError('u', 400, 'no', {}, io.BytesIO(b'{}')))),
       refresh_said({'refresh_token': 'r'}, refused(OSError('down'))), refresh_said({}, rest_ok),
       refresh_said({'refresh_token': 'r'}, lambda req, timeout=None: RestResp(b'{}'))),
      (('r-json', 'r-file', 'r-env', 'r-secret', None, None), ('at', 3600), ('https://t.example/token', 'POST'), True, 15,
       'Google answered 400', 'Google could not be reached (OSError)', 'the token has no refresh token', 'Google gave no access token'))

print("access_token() and key_file_token() (0.9.0): the standard library's token, each source in its turn")
import base64  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import urllib.error  # noqa: E402
import urllib.parse  # noqa: E402


class Resp:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


meta = lambda req, timeout=None: Resp({'access_token': 'meta'})  # noqa: E731


def no_meta(req, timeout=None):
    raise OSError('no metadata server')


ok = lambda *a, **k: type('P', (), {'returncode': 0, 'stdout': 'gc\n'})()  # noqa: E731
bad = lambda *a, **k: type('P', (), {'returncode': 1, 'stdout': ''})()  # noqa: E731
check("the positive case: the environment's token first, stripped", G.access_token({'CI_ACCESS_TOKEN': ' tok '}), 'tok')
check("  another name for it", G.access_token({'JOB_TOKEN': 't2', 'CI_ACCESS_TOKEN': 'no'}, env_name='JOB_TOKEN'), 't2')
check("  the metadata server next", G.access_token({}, opener=meta), 'meta')
check("  then gcloud, then nothing", (G.access_token({}, opener=no_meta, run=ok), G.access_token({}, opener=no_meta, run=bad)), ('gc', ''))
with tempfile.TemporaryDirectory() as d:
    junk = os.path.join(d, 'key.json')
    with open(junk, 'w', encoding='utf-8') as f:
        f.write('{"type": "nothing"}')
    check("  a key that cannot mint, or a missing file, is the next source",
          (G.access_token({'GOOGLE_APPLICATION_CREDENTIALS': junk}, opener=meta),
           G.access_token({'GOOGLE_APPLICATION_CREDENTIALS': os.path.join(d, 'none.json')}, opener=meta)), ('meta', 'meta'))
    if shutil.which('openssl'):
        pem, pub = os.path.join(d, 'k.pem'), os.path.join(d, 'k.pub')
        subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048', '-out', pem], check=True, capture_output=True)
        subprocess.run(['openssl', 'pkey', '-in', pem, '-pubout', '-out', pub], check=True, capture_output=True)
        keyfile = os.path.join(d, 'sa.json')
        with open(keyfile, 'w', encoding='utf-8') as f:
            json.dump({'type': 'service_account', 'client_email': 'ci@example.iam.gserviceaccount.com',
                       'private_key': open(pem, encoding='utf-8').read(), 'token_uri': 'https://oauth2.example/token'}, f)
        sent = []

        def opener(req, timeout=None):
            sent.append((req.full_url, urllib.parse.parse_qs(req.data.decode())))
            return Resp({'access_token': 'minted'})
        check("  a service-account key file: the JWT grant, minted", G.key_file_token(keyfile, opener=opener, now=1_800_000_000), 'minted')
        url, form = sent[0]
        head, body, sig = form['assertion'][0].split('.')
        pad = lambda s: base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))  # noqa: E731
        claims = json.loads(pad(body))
        sigfile, msgfile = os.path.join(d, 's'), os.path.join(d, 'm')
        open(sigfile, 'wb').write(pad(sig))
        open(msgfile, 'wb').write(f'{head}.{body}'.encode())
        verified = subprocess.run(['openssl', 'dgst', '-sha256', '-verify', pub, '-signature', sigfile, msgfile], capture_output=True)
        check("  RS256 over the key's account for its token endpoint, an hour, verifiable with the public key",
              (url, form['grant_type'][0], json.loads(pad(head))['alg'], claims['iss'], claims['aud'], claims['scope'],
               claims['exp'] - claims['iat'], verified.returncode),
              ('https://oauth2.example/token', 'urn:ietf:params:oauth:grant-type:jwt-bearer', 'RS256',
               'ci@example.iam.gserviceaccount.com', 'https://oauth2.example/token', G.CLOUD_PLATFORM, 3600, 0))

        def refused(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 400, 'invalid_grant', {}, None)
        check("  not a service account, or a refused exchange: ''",
              (G.key_file_token(junk, opener=opener), G.key_file_token(keyfile, opener=refused)), ('', ''))
        inline = open(keyfile, encoding='utf-8').read()
        check("  the key's JSON itself, and behind a pasted NAME=",
              (G.key_file_token(inline, opener=opener, now=1_800_000_000), G.key_file_token(' {"type": "authorized_user"}', opener=opener),
               G.key_file_token('GOOGLE_APPLICATION_CREDENTIALS=' + inline, opener=opener, now=1_800_000_000)), ('minted', '', 'minted'))
        check("  access_token() takes the key from the environment, file or JSON, before the metadata server",
              (G.access_token({'GOOGLE_APPLICATION_CREDENTIALS': keyfile}, opener=opener),
               G.access_token({'GOOGLE_APPLICATION_CREDENTIALS': inline}, opener=opener)), ('minted', 'minted'))
    else:
        print("  (no openssl here: the key cases are not run)")

print("credentials() answers None without google-auth installed")
for name in ('google.auth.transport.requests', 'google.oauth2.credentials'):
    sys.modules[name] = None   # importing it raises ImportError
check("no library", G.credentials(SCOPES, token_json={'token': 'x'}), None)

G.log.removeHandler(handler)
print()
print(f"{len(fails)} failure(s)" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
