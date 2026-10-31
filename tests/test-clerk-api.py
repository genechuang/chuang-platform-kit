#!/usr/bin/env python3
"""chuang_platform_kit.clerk_api: Clerk's two APIs and the session check, one client for every project (SMAD PickleBot #571).

Pinned (the positive case first): Backend reads a user by a quoted id with the secret key, the host's user agent and
timeout; a refusal carries the method, the address without its query, the status and Clerk's long_message, and never
the key; nothing answering is Refused with the error's name; the named writes send their bodies; pages() walks both
listing shapes; sign_in_token refuses an answer with no token; Platform reads with its token, sends If-Match and the
dry run; no credential is Refused before any call; verify_session checks a production token, refuses an expired one,
another instance's and a forged one, checks a development instance's token against its own issuer, and is
NotConfigured without a publishable key; frontend_api decodes a publishable key. Offline: urllib is a fake.

Run:  python -B tests/test-clerk-api.py
"""
import base64
import io
import json
import os
import sys
import time
import urllib.error

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import clerk_api as C  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class Resp(io.BytesIO):
    pass


def opener_for(answers, seen, refuse=None):
    """answers: {(method, path under api.clerk.com/v1): body}; refuse: {(method, path): (status, body bytes)}."""
    def opener(req, timeout):
        path = req.full_url.replace(C.API, '')
        seen.append((req.get_method(), path, req.get_header('Authorization'), req.get_header('User-agent'), timeout,
                     req.data and json.loads(req.data), req.get_header('If-match')))
        key = (req.get_method(), path)
        if refuse and key in refuse:
            status, body = refuse[key]
            raise urllib.error.HTTPError(req.full_url, status, 'no', {}, io.BytesIO(body))
        if key in answers:
            a = answers[key]
            return Resp(b'' if a is None else json.dumps(a).encode())
        raise AssertionError(f'unexpected call {key}')
    return opener


seen = []
b = C.Backend('sk_test_secret', opener=opener_for({('GET', '/users/user_2abc%2Fx'): {'id': 'user_2abc'}}, seen), timeout=5, user_agent='smad-api')
got = b.user('user_2abc/x')
check("the positive case: a user by its quoted id, with the secret key, the host's user agent and timeout",
      (got, seen[0][:5]), ({'id': 'user_2abc'}, ('GET', '/users/user_2abc%2Fx', 'Bearer sk_test_secret', 'smad-api', 5)))

refuse = {('POST', '/invitations'): (422, json.dumps({'errors': [{'long_message': 'email address is invalid', 'message': 'short'}]}).encode()),
          ('GET', '/sessions/sess_1'): (404, b'not json'),
          ('GET', '/users?email_address=a%40x.com'): (500, b'{}')}
bb = C.Backend('sk_test_secret', opener=opener_for({}, [], refuse))
outs = []
for call in (lambda: bb.invite('a@x.com', notify=False), lambda: bb.session('sess_1'), lambda: bb.users(email_address='a@x.com')):
    try:
        call()
        outs.append(None)
    except C.Refused as e:
        outs.append((str(e), e.status, e.detail, e.kind, 'sk_test_secret' in str(e)))


def down(req, timeout):
    raise OSError('connection reset')


try:
    C.Backend('sk', opener=down).user('u')
    gone = None
except C.Refused as e:
    gone = (str(e), e.status, e.kind)
check("a refusal: the call, the address, the status and Clerk's long_message (message when none), never the key; nothing answering names the error",
      (outs, gone),
      ([('POST https://api.clerk.com/v1/invitations answered 422: email address is invalid', 422, 'email address is invalid', 'HTTPError', False),
        ('GET https://api.clerk.com/v1/sessions/sess_1 answered 404', 404, '', 'HTTPError', False),
        ('GET https://api.clerk.com/v1/users answered 500', 500, '', 'HTTPError', False)],
       ('GET https://api.clerk.com/v1/users/u did not answer (OSError)', 0, 'OSError')))

seen2 = []
answers = {('POST', '/invitations'): {'id': 'inv_1', 'url': 'https://x/accept?ticket=t'},
           ('POST', '/sign_in_tokens'): {'token': 'tok123'},
           ('POST', '/sessions/sess_a/revoke'): {'status': 'revoked'},
           ('POST', '/email_addresses'): {'id': 'idn_2'},
           ('PATCH', '/email_addresses/idn_1'): {'id': 'idn_1'},
           ('DELETE', '/email_addresses/idn_1'): None,
           ('DELETE', '/users/u1/external_accounts/eac_1'): None,
           ('POST', '/users/u1/ban'): {}, ('POST', '/users/u1/unban'): {}, ('DELETE', '/users/u1'): None,
           ('POST', '/invitations/inv_1/revoke'): {},
           ('GET', '/sessions?user_id=u1&status=active&limit=100'): [{'id': 'sess_a'}]}
w = C.Backend('sk', opener=opener_for(answers, seen2))
made = (w.invite('a@x.com', notify=False, redirect_url='https://app/sign-up'), w.sign_in_token('u1', seconds=300),
        w.revoke_session('sess_a'), w.add_email('u1', 'r@x.com'), w.update_email('idn_1', primary=True), w.delete_email('idn_1'),
        w.delete_external_account('u1', 'eac_1'), w.ban('u1'), w.unban('u1'), w.delete_user('u1'), w.revoke_invitation('inv_1'),
        w.sessions('u1'))
bodies = {f'{m} {p}': body for m, p, _a, _u, _t, body, _i in seen2}
check("the named calls send their bodies to their addresses",
      (made[1], made[5], made[11], bodies['POST /invitations'], bodies['POST /sign_in_tokens'], bodies['POST /email_addresses'],
       bodies['PATCH /email_addresses/idn_1'], bodies['POST /sessions/sess_a/revoke'], len(seen2)),
      ('tok123', None, [{'id': 'sess_a'}], {'email_address': 'a@x.com', 'notify': False, 'ignore_existing': True, 'redirect_url': 'https://app/sign-up'},
       {'user_id': 'u1', 'expires_in_seconds': 300}, {'user_id': 'u1', 'email_address': 'r@x.com', 'verified': True, 'primary': True},
       {'primary': True}, {}, 12))

paged = []
rows = [{'i': n} for n in range(3)]
p = C.Backend('sk', opener=opener_for({('GET', '/users?limit=2&offset=0'): rows[:2], ('GET', '/users?limit=2&offset=2'): rows[2:],
                                       ('GET', '/invitations?status=pending&limit=2&offset=0'): {'data': rows[:1]}}, paged))
try:
    C.Backend('sk', opener=opener_for({('POST', '/sign_in_tokens'): {'object': 'sign_in_token'}}, [])).sign_in_token('u')
    no_token = ''
except C.Refused as e:
    no_token = str(e)
try:
    C.Backend('', opener=opener_for({}, paged)).user('u')
    no_key = ''
except C.Refused as e:
    no_key = str(e)
check("pages() walks a bare list and {'data': ...}; no token in the answer is Refused; no key is Refused before any call",
      (list(p.pages('/users', page=2)), list(p.pages('/invitations?status=pending', page=2)), no_token, no_key, len(paged)),
      (rows, rows[:1], 'POST /sign_in_tokens answered no token', 'no Clerk secret key', 3))

seenp = []
plat = {('GET', '/platform/applications'): [{'id': 'app_1'}],
        ('GET', '/platform/applications/app_1?include_secret_keys=true'): {'instances': []},
        ('GET', '/platform/applications/app_1/instances/development/config?keys=sign_up&keys=social'): {'config_version': 'v7'},
        ('PATCH', '/platform/applications/app_1/instances/development/config?dry_run=true'): {'config_version': 'v8'},
        ('PATCH', '/platform/applications/app_1/instances/development/config'): {}}
pl = C.Platform('ak_platform', opener=opener_for(plat, seenp))
gotp = (pl.applications(), pl.application('app_1', include_secret_keys=True), pl.config('app_1', 'development', keys=['sign_up', 'social']),
        pl.patch_config('app_1', 'development', {'sign_up': {}}, dry_run=True), pl.patch_config('app_1', 'development', {'sign_up': {}}, if_match='v8'))
try:
    C.Platform('ak', opener=opener_for({}, [], {('GET', '/platform/applications'): (403, json.dumps({'errors': [{'message': 'missing scope applications:manage'}]}).encode())})).applications()
    plat_refused = ''
except C.Refused as e:
    plat_refused = str(e)
check("Platform: the token, the queries, the dry run and If-Match; a refusal in the same words",
      (gotp[0], gotp[3], seenp[0][2], seenp[-1][6], seenp[-2][6], plat_refused),
      ([{'id': 'app_1'}], {'config_version': 'v8'}, 'Bearer ak_platform', 'v8', None,
       'GET https://api.clerk.com/v1/platform/applications answered 403: missing scope applications:manage'))

# -- the session check
import jwt  # noqa: E402  (the clerk extra)
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PK = 'pk_live_' + base64.b64encode(b'clerk.example.com$').decode().rstrip('=')
DEV = 'pk_test_' + base64.b64encode(b'dev-1.clerk.accounts.dev$').decode().rstrip('=')
now = int(time.time())


def tok(iss, sub='user_1', exp=now + 60, k=key):
    return 'Bearer ' + jwt.encode({'iss': iss, 'sub': sub, 'exp': exp, 'iat': now, 'sid': 'sess_1'}, k, algorithm='RS256')


class Keys:
    def __init__(self, k):
        self.k = k

    def get_signing_key_from_jwt(self, token):
        class S:
            pass
        s = S()
        s.key = self.k.public_key()
        return s


clients = {C.jwks_url(PK): Keys(key), C.jwks_url(DEV): Keys(key)}
claims, test = C.verify_session(tok('https://clerk.example.com'), PK, DEV, clients=clients)
dev_claims, dev_test = C.verify_session(tok('https://dev-1.clerk.accounts.dev', sub='user_d'), PK, DEV, clients=clients)
errs = []
for header in (tok('https://clerk.example.com', exp=now - 120), tok('https://elsewhere.example'), tok('https://clerk.example.com', k=other),
               'Basic x', 'Bearer ', ''):
    try:
        C.verify_session(header, PK, DEV, clients=clients)
        errs.append('passed')
    except C.Unauthorized as e:
        errs.append(str(e))
try:
    C.verify_session(tok('https://clerk.example.com'), '', clients=clients)
    unconfigured = ''
except C.NotConfigured as e:
    unconfigured = str(e)
check("verify_session: a production token, a development one against its own issuer; expired, another instance's, forged and missing refused; no key NotConfigured",
      ((claims['sub'], test), (dev_claims['sub'], dev_test), errs, unconfigured),
      (('user_1', False), ('user_d', True),
       ['the session token has expired', 'the token is for another Clerk instance', 'invalid session token: InvalidSignatureError',
        'a Clerk session token is required: Authorization: Bearer <token>', 'empty token',
        'a Clerk session token is required: Authorization: Bearer <token>'], 'no Clerk publishable key'))
check("frontend_api / issuer / jwks_url from a publishable key; anything else is ''",
      (C.frontend_api(PK), C.issuer(DEV), C.jwks_url(PK), C.frontend_api('sk_live_x'), C.issuer('')),
      ('clerk.example.com', 'https://dev-1.clerk.accounts.dev', 'https://clerk.example.com/.well-known/jwks.json', '', ''))
made = C.jwk_client('https://other.example/.well-known/jwks.json')
check("jwk_client: one PyJWKClient per address, kept", (type(made).__name__, C.jwk_client('https://other.example/.well-known/jwks.json') is made),
      ('PyJWKClient', True))

print()
print(f"{len(fails)} failure(s)" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
