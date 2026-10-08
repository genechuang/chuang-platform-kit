#!/usr/bin/env python3
"""chuang_platform_kit.neon: a project, its database and a connection URL found or created, idempotently, over a fake opener.

Pinned (the positive case first): provision() on an empty account creates the project (region, major,
database in the body), reads the default branch and its roles, reuses the database the create made, asks
for the DIRECT connection URL and answers ids, host and the URL — the URL registered with redact, never
printed; a second run with the project and database present creates nothing; a database missing on an
existing project is created for the branch's role; a 403 is a PermissionError naming the key; the CLI
prints ids and host only and stores the URL as a new version of a secret it creates when missing.

Run:  python -B tests/test-neon.py
"""
import io
import json
import os
import sys
import urllib.error
import contextlib

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import neon as N  # noqa: E402
from chuang_platform_kit import redact as R  # noqa: E402
from chuang_platform_kit import secrets as S  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class Resp:
    def __init__(self, body):
        self._body = json.dumps(body).encode('utf-8')

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# The URL is assembled from parts: a literal scheme://user:password@host in a test file is a
# credential to a secret scanner whatever the names say (kit CLAUDE.md, Tests).
HOST = 'ep-fake-1.us-west-2.aws.neon.tech'
URI = 'postgresql://' + 'ledger_owner:' + 'FAKE-pw-' + '12345' + '@' + HOST + '/ledger?sslmode=require'
PROJECT = {'id': 'proj-1', 'name': 'chuang-finance', 'region_id': 'aws-us-west-2', 'pg_version': 18}
BRANCH = {'id': 'br-1', 'name': 'main', 'default': True}
ROLE = {'name': 'ledger_owner', 'protected': False}


ORGS = [{'id': 'org-fake-1', 'name': 'Gene'}]


def fake(calls, projects=(), dbs=(), refuse=False, orgs=ORGS, bad_request=None):
    state = {'projects': list(projects), 'dbs': list(dbs)}

    def opener(req, timeout=None):
        path = req.full_url[len(N.API):]
        body = json.loads(req.data.decode('utf-8')) if req.data else None
        calls.append((req.get_method(), path.split('?')[0], body, path))
        if refuse:
            raise urllib.error.HTTPError(req.full_url, 403, 'Forbidden', {}, io.BytesIO(b''))
        if path == '/users/me/organizations':
            return Resp({'organizations': orgs})
        if path.startswith('/projects?'):
            if bad_request:
                raise urllib.error.HTTPError(req.full_url, 400, 'Bad Request', {}, io.BytesIO(json.dumps(bad_request).encode()))
            if 'org_id=' not in path:   # Neon with a personal key: a project call without org_id is a 400
                raise urllib.error.HTTPError(req.full_url, 400, 'Bad Request', {},
                                             io.BytesIO(b'{"code":"","message":"org_id is required"}'))
            return Resp({'projects': state['projects'], 'pagination': {}})
        if path == '/projects' and req.get_method() == 'POST':
            state['projects'].append(PROJECT)
            state['dbs'].append({'name': body['project']['branch']['database_name'], 'owner_name': 'ledger_owner'})
            return Resp({'project': PROJECT, 'branch': BRANCH, 'roles': [ROLE], 'connection_uris': [{'connection_uri': URI}]})
        if path.endswith('/branches'):
            return Resp({'branches': [BRANCH]})
        if path.endswith('/roles'):
            return Resp({'roles': [{'name': 'neon_superuser', 'protected': True}, ROLE]})
        if path.endswith('/databases') and req.get_method() == 'GET':
            return Resp({'databases': state['dbs']})
        if path.endswith('/databases') and req.get_method() == 'POST':
            state['dbs'].append(body['database'])
            return Resp({'database': body['database']})
        if '/connection_uri?' in path:
            return Resp({'uri': URI})
        raise AssertionError('unexpected call ' + path)
    return opener


# 1. The positive case: nothing exists, everything is made, the URL is a secret.
calls = []
made = N.provision('key', 'chuang-finance', database='ledger', opener=fake(calls))
create = next(c for c in calls if c[0] == 'POST' and c[1] == '/projects')
check("provision creates the project with region, major and the database in the body",
      (create[2]['project']['region_id'], create[2]['project']['pg_version'], create[2]['project']['branch']['database_name']),
      ('aws-us-west-2', 18, 'ledger'))
check("the organization is discovered from the key and sent on the list and the create (a personal key needs it)",
      (calls[0][1], 'org_id=org-fake-1' in calls[1][3], create[2]['project'].get('org_id')),
      ('/users/me/organizations', True, 'org-fake-1'))
check("provision answers the ids, the host and the flags; the URL is the direct one",
      (made['project_id'], made['branch_id'], made['database'], made['role'], made['host'],
       made['created_project'], made['created_database'], made['connection_uri'] == URI),
      ('proj-1', 'br-1', 'ledger', 'ledger_owner', HOST, True, False, True))
uri_call = [c for c in calls if '/connection_uri' in c[1]]
check("the URL is asked once, direct (pooled=false), for the database and its owner",
      (len(uri_call), 'pooled=false' in uri_call[0][3], 'database_name=ledger' in uri_call[0][3],
       'role_name=ledger_owner' in uri_call[0][3]), (1, True, True, True))
check("the URL is registered with redact the moment it is read", 'FAKE-pw-12345' in R.redact('x ' + URI + ' y'), False)

# 2. Idempotent: the project and the database exist, nothing is created.
calls = []
again = N.provision('key', 'Chuang-Finance', database='ledger',
                    opener=fake(calls, projects=[PROJECT], dbs=[{'name': 'ledger', 'owner_name': 'ledger_owner'}]))
check("a second run reuses the project (case-insensitive name) and the database, creating nothing",
      (again['created_project'], again['created_database'], [c for c in calls if c[0] == 'POST']), (False, False, []))

# 3. The project exists without the database: it is created for the branch's role.
calls = []
third = N.provision('key', 'chuang-finance', database='ledger', opener=fake(calls, projects=[PROJECT], dbs=[]))
db_post = [c for c in calls if c[0] == 'POST' and c[1].endswith('/databases')]
check("a missing database is created for the branch's first non-protected role",
      (third['created_project'], third['created_database'], db_post[0][2] if db_post else None),
      (False, True, {'database': {'name': 'ledger', 'owner_name': 'ledger_owner'}}))

# 4. A refusal names the key, never the data.
try:
    N.provision('key', 'chuang-finance', opener=fake([], refuse=True))
    got = 'no error'
except PermissionError as e:
    got = 'API key' in str(e) and 'organization' in str(e)
check("a 403 is a PermissionError naming the key and the organization scope", got, True)

# 4b. The organization: given, discovered, ambiguous, or none; and a 400 says why.
calls = []
N.provision('key', 'chuang-finance', org_id='org-given', opener=fake(calls, projects=[PROJECT], dbs=[{'name': 'neondb', 'owner_name': 'ledger_owner'}]))
check("an org_id given is used as it is, nothing is asked", (calls[0][1], 'org_id=org-given' in calls[0][3]), ('/projects', True))
try:
    N.provision('key', 'x', opener=fake([], orgs=ORGS + [{'id': 'org-fake-2', 'name': 'Other'}]))
    got = 'no error'
except RuntimeError as e:
    got = '2 organizations' in str(e) and 'org-fake-2' in str(e) and 'org_id' in str(e)
check("two organizations in reach is a RuntimeError naming them, never a guess", got, True)
check("no organization listed (an organization key) means none is sent", N.organization_id('key', opener=fake([], orgs=[])), None)
try:
    N.provision('key', 'x', org_id='org-given', opener=fake([], bad_request={'code': '', 'message': 'limit must be <= 400'}))
    got = 'no error'
except RuntimeError as e:
    got = ('400' in str(e), 'limit must be <= 400' in str(e))
check("a 400 is a RuntimeError carrying Neon's own message", got, (True, True))

# 5. host_of is the printable part.
check("host_of answers the host alone", N.host_of(URI), HOST)
check("host_of on a non-URL answers empty, never raises", N.host_of('nonsense'), '')

# 6. The CLI: prints ids and host only, stores the URL as a secret version, creating the secret when missing.
class Session:
    def __init__(self, exists):
        self.exists, self.posts = exists, []

    class R:
        def __init__(self, status, body):
            self.status_code, self._body = status, body

        def json(self):
            return self._body

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(self.status_code)

    def get(self, url, timeout=None):
        return self.R(200 if self.exists else 404, {})

    def post(self, url, json=None, timeout=None):
        self.posts.append((url.split('/v1/')[-1], json))
        return self.R(200, {'name': 'projects/chuang-finance/secrets/DATABASE_URL/versions/1'})


keep = (S.default_session, N.provision)
sess = Session(exists=False)
S.default_session = lambda: sess
N.provision = lambda key, name, **kw: dict(made, connection_uri=URI)
out = io.StringIO()
os.environ['NEON_API_KEY'] = 'k-env'
try:
    with contextlib.redirect_stdout(out):
        code = N.main(['--name', 'chuang-finance', '--database', 'ledger', '--store', 'chuang-finance/DATABASE_URL'])
finally:
    S.default_session, N.provision = keep
    os.environ.pop('NEON_API_KEY', None)
text = out.getvalue()
stored = [p for p in sess.posts if p[0].endswith(':addVersion')]
check("the CLI stores the URL as a new version of a secret it created first",
      (code, [p[0] for p in sess.posts][0], len(stored), 'stored as projects/chuang-finance/secrets/DATABASE_URL/versions/1 (secret created)' in text),
      (0, 'projects/chuang-finance/secrets?secretId=DATABASE_URL', 1, True))
check("the CLI prints the host and the ids, never the URL or its password",
      (HOST in text, 'proj-1' in text, 'FAKE-pw' in text, URI in text), (True, True, False, False))

# 7. secrets.ensure(): exists -> False, no POST; missing -> created.
sess2 = Session(exists=True)
check("secrets.ensure answers False and posts nothing when the secret exists",
      (S.ensure('p', 'N', session=sess2), sess2.posts), (False, []))

print(f"\n{len(fails)} failure(s)" + (': ' + '; '.join(fails) if fails else ''))
sys.exit(1 if fails else 0)
