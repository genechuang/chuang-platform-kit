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
for name, mod in (('google', fake_google), ('google.auth', fake_auth), ('google.auth.transport', fake_transport),
                  ('google.auth.transport.requests', fake_requests), ('google.oauth2', fake_oauth2),
                  ('google.oauth2.credentials', fake_oauth_creds), ('googleapiclient', fake_api), ('googleapiclient.discovery', fake_discovery)):
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
