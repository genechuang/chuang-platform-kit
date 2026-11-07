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
    """A fake AuthorizedSession: `store` is secret name -> bytes, `vers` name -> {number: state}; every call recorded.
    A secret named `nodestroy` refuses a destroy (a writer whose role can add but not destroy)."""

    def __init__(self, store, vers=None):
        self.store, self.calls, self.vers = store, [], vers or {}

    def get(self, url, timeout=None):
        self.calls.append(('GET', url))
        name = url.split('/secrets/')[1].split('/')[0].split('?')[0]
        if '/versions?' in url:
            return Resp(200, {'versions': [{'name': f'projects/p/secrets/{name}/versions/{n}', 'state': st}
                                           for n, st in self.vers.get(name, {}).items()]})
        if name not in self.store and name not in self.vers:
            return Resp(404, {'error': 'not found'})
        return Resp(200, {'payload': {'data': base64.b64encode(self.store.get(name, b'')).decode('ascii')}})

    def delete(self, url, timeout=None):
        self.calls.append(('DELETE', url))
        name = url.split('/secrets/')[1]
        if name not in self.store:
            return Resp(404, {})
        del self.store[name]
        return Resp(200, {})

    def post(self, url, json=None, timeout=None):
        self.calls.append(('POST', url, json))
        if url.endswith(':destroy'):
            name, n = url.split('/secrets/')[1].split('/versions/')
            if name == 'nodestroy':
                return Resp(403, {})
            self.vers[name][int(n.split(':')[0])] = 'DESTROYED'
            return Resp(200, {})
        name = url.split('/secrets/')[1].split(':')[0]
        if name == 'refused':
            return Resp(403, {})
        if name in self.vers:
            v = max(self.vers[name], default=0) + 1
            self.vers[name][v] = 'ENABLED'
            self.store[name] = base64.b64decode(json['payload']['data'])
            return Resp(200, {'name': f'projects/p/secrets/{name}/versions/{v}'})
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
check("version", (name, sess.store['TOKEN'], [c[1] for c in sess.calls if c[0] == 'POST'][-1]),
      ('projects/p/secrets/TOKEN/versions/7', b'{"t": 1}', 'https://secretmanager.googleapis.com/v1/projects/p/secrets/TOKEN:addVersion'))
try:
    S.add_version('p', 'refused', b'x', session=sess)
    denied = 'no error'
except RuntimeError as e:
    denied = str(e)
check("403", denied, 'HTTP 403')

print("add_version() destroys the versions it replaced, keeping the newest; a refused destroy never fails the write")
vs = Session({}, {'GMAIL': {1: 'ENABLED', 2: 'DISABLED', 3: 'ENABLED', 4: 'DESTROYED'}, 'nodestroy': {1: 'ENABLED'}})
made = S.add_version('p', 'GMAIL', 'new', session=vs)
check("the positive case: v5 added, v1-v3 destroyed, v4 left destroyed",
      (made, vs.vers['GMAIL']), ('projects/p/secrets/GMAIL/versions/5',
                                 {1: 'DESTROYED', 2: 'DESTROYED', 3: 'DESTROYED', 4: 'DESTROYED', 5: 'ENABLED'}))
import logging  # noqa: E402
warned = []
handler = logging.Handler()
handler.emit = lambda r: warned.append(r.getMessage())
logging.getLogger('chuang_platform_kit.secrets').addHandler(handler)
made = S.add_version('p', 'nodestroy', 'x', session=vs)
check("a refused destroy: the write stands and says so at WARNING", (made, vs.vers['nodestroy'], len(warned)),
      ('projects/p/secrets/nodestroy/versions/2', {1: 'ENABLED', 2: 'ENABLED'}, 1))
before = len(vs.calls)
S.add_version('p', 'GMAIL', 'kept', session=vs, keep=0)
check("keep=0 adds without listing or destroying", ([c[0] for c in vs.calls[before:]], vs.vers['GMAIL'][5]), (['POST'], 'ENABLED'))

print("prune() keeps the newest enabled versions; a secret with none enabled is left alone; versions() is newest first")
vs = Session({}, {'A': {1: 'ENABLED', 2: 'ENABLED', 3: 'ENABLED'}, 'RETIRED': {1: 'DISABLED', 2: 'DISABLED'}})
check("versions", S.versions('p', 'A', session=vs), [(3, 'ENABLED'), (2, 'ENABLED'), (1, 'ENABLED')])
check("keep 2", (S.prune('p', 'A', keep=2, session=vs), vs.vers['A']), ([1], {1: 'DESTROYED', 2: 'ENABLED', 3: 'ENABLED'}))
check("all disabled: nothing destroyed", (S.prune('p', 'RETIRED', session=vs), vs.vers['RETIRED']),
      ([], {1: 'DISABLED', 2: 'DISABLED'}))

print("delete(): True when deleted, False when not there; TokenSession has delete")
ds = Session({'OLD': b'x'})
check("delete", (S.delete('p', 'OLD', session=ds), S.delete('p', 'OLD', session=ds), ds.calls[0]),
      (True, False, ('DELETE', 'https://secretmanager.googleapis.com/v1/projects/p/secrets/OLD')))
check("TokenSession.delete", callable(getattr(S.TokenSession, 'delete', None)), True)

print("main(): add reads stdin, creates a missing secret, adds and prunes; prune; delete; usage; nothing on stdin")
import io  # noqa: E402
import contextlib  # noqa: E402


class Created(Session):
    def post(self, url, json=None, timeout=None):
        if '?secretId=' in url:
            name = url.split('secretId=')[1]
            self.vers[name] = {}
            self.calls.append(('CREATE', name))
            return Resp(200, {})
        return Session.post(self, url, json, timeout)


cs = Created({}, {'KEPT': {1: 'ENABLED'}})
out = io.StringIO()
with contextlib.redirect_stdout(out):
    rc_new = S.main(['add', 'p', 'NEW'], stdin=io.BytesIO(b'v1-secret\r\n'), session=cs)
    rc_kept = S.main(['add', 'p', 'KEPT'], stdin=io.BytesIO(b'v2'), session=cs)
check("add: created, stored without the line end, the older version destroyed, the value never printed",
      (rc_new, rc_kept, cs.store['NEW'], cs.vers['KEPT'], 'v1-secret' in out.getvalue(), 'CREATE' in [c[0] for c in cs.calls]),
      (0, 0, b'v1-secret', {1: 'DESTROYED', 2: 'ENABLED'}, False, True))
err = io.StringIO()
with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
    usage = S.main(['put', 'p', 'X'], session=cs)
    empty = S.main(['add', 'p', 'KEPT'], stdin=io.BytesIO(b'  \n'), session=cs)
    refused = S.main(['add', 'p', 'refused'], stdin=io.BytesIO(b'x'), session=Created({}, {}))
check("usage 2, an empty stdin 2 and nothing written, a refusal 1", (usage, empty, refused, cs.vers['KEPT']),
      (2, 2, 1, {1: 'DESTROYED', 2: 'ENABLED'}))
with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, 'value.txt')
    with open(path, 'wb') as fh:
        fh.write(b'from-file\r\n')
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        rc_file = S.main(['add', 'p', 'KEPT', '--data-file', path], session=cs)
        rc_bad = S.main(['prune', 'p', 'KEPT', '--data-file', path], session=cs)
        rc_none = S.main(['add', 'p', 'KEPT', '--data-file'], session=cs)
check("--data-file: read from the file; only with add; a path is required", (rc_file, cs.store['KEPT'], cs.vers['KEPT'], rc_bad, rc_none),
      (0, b'from-file', {1: 'DESTROYED', 2: 'DESTROYED', 3: 'ENABLED'}, 2, 2))

print("writers(): a gcloud version add, a list-form destroy, an addVersion URL and a secret delete are found; docs, tests and the kit's module are not")
import subprocess  # noqa: E402
with tempfile.TemporaryDirectory() as repo:
    files = {
        'scripts/store.ps1': 'gcloud secrets versions add TOKEN --data-file=$tmp\n',
        'tools/env.py': "x = 1\ngcloud('secrets', 'versions', 'destroy', '3')\n",
        'svc/rest.py': "url = f'{API}/secrets/{n}:addVersion'\n",
        'ops/clean.sh': 'gcloud secrets delete OLD --quiet\n',
        'ops/read.sh': ('gcloud secrets versions access latest --secret=TOKEN\npython -m chuang_platform_kit.secrets add p TOKEN\n'
                        'python -m chuang_platform_kit.secrets delete p OLD\npython -m chuang_platform_kit.secrets prune p TOKEN\n'
                        "secrets.delete('p', 'OLD')\n"),
        'README.md': 'gcloud secrets versions add TOKEN\n',
        'tests/test-x.py': 'gcloud secrets versions add TOKEN\n',
        'src/chuang_platform_kit/secrets.py': "url = ':addVersion'\n",
    }
    for path, text in files.items():
        os.makedirs(os.path.join(repo, os.path.dirname(path)), exist_ok=True)
        with open(os.path.join(repo, path), 'w', encoding='utf-8') as fh:
            fh.write(text)
    subprocess.run(['git', 'init', '-q'], cwd=repo)
    subprocess.run(['git', 'add', '.'], cwd=repo)
    check("found", sorted(S.writers(repo)),
          [('ops/clean.sh', 1), ('scripts/store.ps1', 1), ('svc/rest.py', 1), ('tools/env.py', 2)])

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
made = S.add_version('p', 'TOKEN', 'x', session=ts)
posts = [s for s in sent if s[0] == 'POST']
check("post, then the version list the prune reads", (made, json.loads(posts[-1][3])['payload']['data'], sent[-1][0], '/versions?' in sent[-1][1]),
      ('projects/p/secrets/TOKEN/versions/8', base64.b64encode(b'x').decode('ascii'), 'GET', True))
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

print("resolve_env: a Cloud Run container's env, each literal as it is and each secret reference read at its version")
reads = []


def fake_read(url):
    reads.append(url.replace(S.API, ''))
    name = url.split('/secrets/')[1].split('/')[0]
    return {'payload': {'data': base64.b64encode({'TOKEN': b'tok', 'OTHER': b'o'}[name]).decode()}}


ENV = [{'name': 'MODE', 'value': 'live'},
       {'name': 'API_TOKEN', 'valueSource': {'secretKeyRef': {'secret': 'TOKEN', 'version': '3'}}},
       {'name': 'X', 'valueSource': {'secretKeyRef': {'secret': 'projects/other/secrets/OTHER'}}},
       {'name': 'EMPTY'}]
check("the positive case: literals, a pinned version, latest by default, a full resource name's own project",
      (S.resolve_env(ENV, 'p', get_json=fake_read), reads),
      ({'MODE': 'live', 'API_TOKEN': 'tok', 'X': 'o', 'EMPTY': ''},
       ['/projects/p/secrets/TOKEN/versions/3:access', '/projects/other/secrets/OTHER/versions/latest:access']))
check("no entries: an empty dict, nothing read", (S.resolve_env(None, 'p', get_json=lambda u: 1 / 0)), {})


def refusing(url):
    raise RuntimeError(f"403 reading {url.split('/secrets/')[1].split('/')[0]}")


try:
    S.resolve_env(ENV, 'p', get_json=refusing)
    refused = None
except RuntimeError as e:
    refused = str(e)
check("a refused secret raises, naming the secret", refused, '403 reading TOKEN')

print()
print(f"{len(fails)} failure(s)" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
