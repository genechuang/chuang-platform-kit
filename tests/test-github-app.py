#!/usr/bin/env python3
"""github_app: a GitHub App's installation tokens, and github.token()'s choice of them (0.56.0; SMAD PickleBot #1280).

Pinned (positive case first): with an App id, a key and an owner, a token is minted through the organization's
installation and kept; a kept token is reused until 5 minutes before GitHub's end and minted again after; an owner that
is a user is found at /users/ when /orgs/ answers 404; a process with no App gets ''; github.token() takes the App's
token when the App and its owner are given and the person's otherwise, and person=True always the person's; an
installation token draws on its own throttle lane, so a spent person's quota never stops the App's calls.

Run:  python -B tests/test-github-app.py
"""
import json
import os
import sys
import urllib.error

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import github as GH, github_app as A  # noqa: E402

FAILS = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f'        got  {got!r}\n        want {want!r}')
        FAILS.append(label)


try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import jwt
    PEM = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
except ImportError:
    PEM = None


class Answer:
    def __init__(self, body, status=200):
        self.body, self.status, self.headers = json.dumps(body).encode(), status, {}

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class GitHub:
    """GitHub's App routes: the org's installation (or a 404 for a user), the token, with the calls kept."""

    def __init__(self, org=True, ends='2026-11-27T15:00:00Z'):
        self.org, self.ends, self.calls, self.n = org, ends, [], 0

    def __call__(self, req, timeout=None):
        path = req.full_url.replace(GH.API, '')
        self.calls.append((req.get_method(), path, req.headers.get('Authorization', '')))
        if path.startswith('/orgs/') and not self.org:
            raise urllib.error.HTTPError(req.full_url, 404, 'Not Found', {}, None)
        if path.endswith('/installation'):
            return Answer({'id': 77})
        if path == '/app/installations/77/access_tokens':
            self.n += 1
            return Answer({'token': f'ghs_minted{self.n}', 'expires_at': self.ends}, 201)
        return Answer({})


ENV = {'GH_APP_ID': '5875015', 'GITHUB_APP_PRIVATE_KEY': PEM or '', 'GH_APP_OWNER': 'SMAD-PickleBot'}
T0 = 1_795_816_800   # 2026-11-27T22:00:00Z, 11/27/26 2:00 PM PT; GitHub's end below is an hour after


def test_mints_and_keeps():
    A.forget()
    gh = GitHub(ends='2026-11-27T23:00:00Z')
    A._now = lambda: T0
    tok = A.token(env=ENV, opener=gh)
    claims = jwt.decode(gh.calls[0][2].split()[1], options={'verify_signature': False})
    check('the positive case: minted through the org installation, the JWT signed as the App, issued 60 s back, 9 minutes long',
          (tok, [c[:2] for c in gh.calls], claims['iss'], claims['iat'], claims['exp'] - claims['iat']),
          ('ghs_minted1', [('GET', '/orgs/SMAD-PickleBot/installation'), ('POST', '/app/installations/77/access_tokens')],
           '5875015', T0 - 60, 600))
    check('  kept: a second ask makes no call', (A.token(env=ENV, opener=gh), len(gh.calls)), ('ghs_minted1', 2))
    ends = 1_795_820_400   # 2026-11-27T23:00:00Z
    A._now = lambda: ends - 301
    check('  still kept 301 s before its end', A.token(env=ENV, opener=gh), 'ghs_minted1')
    A._now = lambda: ends - 299
    check('  minted again inside the last 5 minutes', A.token(env=ENV, opener=gh), 'ghs_minted2')


def test_a_user_owner():
    A.forget()
    gh = GitHub(org=False)
    A._now = lambda: T0
    check('  an owner that is a user: /orgs/ 404, then /users/',
          (A.token('genechuang', env=ENV, opener=gh), [c[1] for c in gh.calls][:2]),
          ('ghs_minted1', ['/orgs/genechuang/installation', '/users/genechuang/installation']))


def test_no_app_no_token():
    A.forget()
    gh = GitHub()
    check('  no App id, no key or no owner: no token and no call',
          (A.token(env={**ENV, 'GH_APP_ID': ''}, opener=gh), A.token(env={**ENV, 'GITHUB_APP_PRIVATE_KEY': ' '}, opener=gh),
           A.token(env={**ENV, 'GH_APP_OWNER': ''}, opener=gh), gh.calls), ('', '', '', []))


def test_github_token_chooses():
    A.forget()
    A._now = lambda: T0
    keep = A.token
    A.token = lambda owner='', env=None, opener=None: 'ghs_app'
    try:
        person = {'GH_TOKEN': 'github_pat_person'}
        check("  github.token(): the App's with an App and its owner; the person's without; person=True the person's",
              (GH.token(env={**ENV, **person}), GH.token(env=person), GH.token(env={**ENV, **person}, person=True),
               GH.token(env={**ENV, 'GH_APP_OWNER': '', **person})),
              ('ghs_app', 'github_pat_person', 'github_pat_person', 'github_pat_person'))
    finally:
        A.token = keep


def test_lanes():
    GH.reset_limits()
    check('  lane_of: an installation token is the App lane, any other the default',
          (GH.lane_of('ghs_x'), GH.lane_of('github_pat_x'), GH.lane_of('gho_x'), GH.lane_of('')), ('app', '', '', ''))
    GH._note('core', {'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': str(T0 + 600)}, 403)
    GH._now = lambda: T0
    try:
        stopped = None
        try:
            GH._check('core', '/x')
        except GH.RateLimited:
            stopped = 'person stopped'
        GH._check('core', '/x', lane='app')   # the App's lane is untouched
        check("  the person's spent quota stops the person's calls, never the App's",
              (stopped, GH.limits()['core']['remaining'], GH.limits('app')), ('person stopped', 0, {}))
    finally:
        GH._now = __import__('time').time
        GH.reset_limits()


if __name__ == '__main__':
    if PEM is None:
        print('  SKIP  PyJWT with cryptography is not installed: the github-app extra')
        print('0 failure(s)')
        sys.exit(0)
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            fn()
    A._now = __import__('time').time
    print(f'{len(FAILS)} failure(s)' + (':' if FAILS else ''))
    for f in FAILS:
        print(f'  - {f}')
    sys.exit(1 if FAILS else 0)
