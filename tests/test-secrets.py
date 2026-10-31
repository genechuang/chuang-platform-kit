#!/usr/bin/env python3
"""chuang_platform_kit.secrets: Secret Manager read into the environment and a version written back, over a fake session.

Pinned (the positive case first): load_json() reads a JSON secret and sets the environment without
overwriting a set variable (unless asked); access() decodes the payload and raises on a refusal with
the status, never the data; add_version() posts the base64 payload and answers the version name;
write_file() writes a 0600 file once and never over one that exists; renew_if_changed() answers
missing / unchanged / the new version.

Run:  python -B tests/test-secrets.py
"""
import base64
import json
import os
import sys
import tempfile
import urllib.error

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import secrets as S  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')


class Session:
    """A fake AuthorizedSession: `store` is secret name -> bytes; every call recorded."""

    def __init__(self, store):
        self.store, self.calls = store, []

    def get(self, url, timeout=None):
        self.calls.append(('GET', url))
        name = url.split('/secrets/')[1].split('/')[0]
        if name not in self.store:
            return Resp(404, {'error': 'not found'})
        return Resp(200, {'payload': {'data': base64.b64encode(self.store[name]).decode('ascii')}})

    def post(self, url, json=None, timeout=None):
        self.calls.append(('POST', url, json))
        name = url.split('/secrets/')[1].split(':')[0]
        if name == 'refused':
            return Resp(403, {})
        self.store[name] = base64.b64decode(json['payload']['data'])
        return Resp(200, {'name': f'projects/p/secrets/{name}/versions/7'})


print("the positive case: load_json() reads a JSON secret into the environment, keeping a set variable unless overwrite")
sess = Session({'LOGINS': json.dumps({'ACME_USER': 'u', 'ACME_PASS': 'p', 'NESTED': {'a': 1}}).encode()})
os.environ['ACME_USER'] = 'already'
os.environ.pop('ACME_PASS', None)
data = S.load_json('p', 'LOGINS', into_env=True, session=sess)
check("parsed and set", (data['ACME_PASS'], os.environ['ACME_USER'], os.environ['ACME_PASS'], os.environ['NESTED']),
      ('p', 'already', 'p', '{"a": 1}'))
S.load_json('p', 'LOGINS', into_env=True, overwrite=True, session=sess)
check("overwrite", os.environ['ACME_USER'], 'u')
check("the request", sess.calls[0], ('GET', 'https://secretmanager.googleapis.com/v1/projects/p/secrets/LOGINS/versions/latest:access'))
for k in ('ACME_USER', 'ACME_PASS', 'NESTED'):
    os.environ.pop(k, None)

print("access() decodes the payload; a refusal raises with the status, never the data; a non-object JSON is refused")
check("bytes", S.access('p', 'LOGINS', session=sess)[:12], b'{"ACME_USER"')
try:
    S.access('p', 'MISSING', session=sess)
    refused = 'no error'
except RuntimeError as e:
    refused = str(e)
check("404", refused, 'HTTP 404')
sess.store['LIST'] = b'[1, 2]'
try:
    S.load_json('p', 'LIST', session=sess)
    shape = 'no error'
except ValueError as e:
    shape = str(e)
check("not an object", shape, 'secret LIST is not a JSON object')

print("add_version() posts the base64 payload and answers the version name; a refusal raises")
name = S.add_version('p', 'TOKEN', '{"t": 1}', session=sess)
check("version", (name, sess.store['TOKEN'], sess.calls[-1][1]),
      ('projects/p/secrets/TOKEN/versions/7', b'{"t": 1}', 'https://secretmanager.googleapis.com/v1/projects/p/secrets/TOKEN:addVersion'))
try:
    S.add_version('p', 'refused', b'x', session=sess)
    denied = 'no error'
except RuntimeError as e:
    denied = str(e)
check("403", denied, 'HTTP 403')

print("write_file() writes the secret as a 0600 file once; empty or existing: False, untouched")
with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, 'creds.json')
    first = S.write_file({'CREDS': '{"k": 1}'}, 'CREDS', path)
    again = S.write_file({'CREDS': 'changed'}, 'CREDS', path)
    with open(path, encoding='utf-8') as f:
        content = f.read()
    empty = S.write_file({'CREDS': '  '}, 'CREDS', os.path.join(d, 'none.json'))
    check("file", (first, again, content, empty, os.path.exists(os.path.join(d, 'none.json'))), (True, False, '{"k": 1}', False, False))

    print("renew_if_changed(): missing, unchanged, or the new version")
    check("missing", S.renew_if_changed('p', 'TOKEN', 'x', os.path.join(d, 'gone.json'), session=sess), 'missing')
    check("unchanged", S.renew_if_changed('p', 'TOKEN', '{"k": 1}\n', path, session=sess), 'unchanged')
    check("renewed", (S.renew_if_changed('p', 'TOKEN', 'old', path, session=sess), sess.store['TOKEN']),
          ('projects/p/secrets/TOKEN/versions/7', b'{"k": 1}'))

print("TokenSession: the standard library over a bearer token asked at each call; a refusal keeps its status; default_session() falls back to it without google-auth")
sent = []


class Raw:
    def __init__(self, body):
        self.status, self._body = 200, body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener(req, timeout=0):
    sent.append((req.get_method(), req.full_url, req.headers.get('Authorization'), req.data))
    if 'MISSING' in req.full_url:
        raise urllib.error.HTTPError(req.full_url, 404, 'no', {}, None)
    if req.get_method() == 'POST':
        return Raw(b'{"name": "projects/p/secrets/TOKEN/versions/8"}')
    return Raw(json.dumps({'payload': {'data': base64.b64encode(b'v').decode('ascii')}}).encode())


asked = []
ts = S.TokenSession(token=lambda: asked.append(1) or 'gtok', opener=opener)
check("get", (S.access('p', 'LOGINS', session=ts), sent[0][0], sent[0][2], len(asked)), (b'v', 'GET', 'Bearer gtok', 1))
try:
    S.access('p', 'MISSING', session=ts)
    miss = 'no error'
except RuntimeError as e:
    miss = str(e)
check("404", (miss, len(asked)), ('HTTP 404', 2))
check("post", (S.add_version('p', 'TOKEN', 'x', session=ts), sent[-1][0], json.loads(sent[-1][3])['payload']['data']),
      ('projects/p/secrets/TOKEN/versions/8', 'POST', base64.b64encode(b'x').decode('ascii')))
saved = {k: sys.modules.get(k) for k in ('google', 'google.auth', 'google.auth.exceptions', 'google.auth.transport.requests')}
sys.modules['google'] = None
try:
    fallback = type(S.default_session()).__name__
finally:
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v
check("default_session without google-auth", fallback, 'TokenSession')

print()
print(f"{len(fails)} failure(s)" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
