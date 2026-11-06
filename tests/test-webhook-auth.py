#!/usr/bin/env python3
"""chuang_platform_kit.webhook_auth: a Google-signed push and an HMAC-SHA256 signature, checked over fakes.

Pinned (the positive case first): google_push passes a Google-issued token from the pushing account for an address
ending in the path (a trailing slash on the audience allowed) and returns its claims; it refuses a missing or non-Bearer
header, a verifier that raises (a bad signature or an expired token), another issuer, another audience, another email,
an unverified email, and an empty path or email, and google_push_claims names why. hmac_sha256 passes each vendor's
shape (GitHub's sha256=, Apple's hmacsha256=, Sentry's bare hex, GitGuardian's timestamp+token key with or without the
prefix, Slack's v0 string, base64) and refuses a wrong signature, a missing header, a missing secret, a missing
required prefix and a changed body.

Run:  python -B tests/test-webhook-auth.py
"""
import base64
import hashlib
import hmac
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import webhook_auth as W  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


PUSHER = 'pusher@example-project.iam.gserviceaccount.com'
PATH = '/events'


def claims(**over):
    c = {'iss': 'https://accounts.google.com', 'aud': 'https://svc.example.test/events', 'email': PUSHER,
         'email_verified': True, 'exp': 2_000_000_000}
    c.update(over)
    return c


def verifier_for(c, seen=None):
    def verify(token):
        if seen is not None:
            seen.append(token)
        return c
    return verify


def refusing(token):
    raise ValueError('Token expired')


def reason(authorization, path=PATH, email=PUSHER, verifier=None):
    try:
        W.google_push_claims(authorization, path, email, verifier=verifier or verifier_for(claims()))
    except W.WebhookAuthError as e:
        return str(e)
    return None


def sig(secret, body, enc='hex'):
    d = hmac.new(secret.encode() if isinstance(secret, str) else secret, body, hashlib.sha256).digest()
    return d.hex() if enc == 'hex' else base64.b64encode(d).decode()


print('google_push')
seen = []
got = W.google_push('Bearer tok-1', PATH, PUSHER, verifier=verifier_for(claims(), seen))
check('a Google token from the pusher for this path passes with its claims', got, claims())
check('the verifier was handed the bare token', seen, ['tok-1'])
check('an audience with a trailing slash passes',
      W.google_push('Bearer t', PATH, PUSHER, verifier=verifier_for(claims(aud='https://svc.example.test/events/'))) is not None, True)
check('the bare issuer passes', W.google_push('Bearer t', PATH, PUSHER,
                                              verifier=verifier_for(claims(iss='accounts.google.com'))) is not None, True)
check('a missing header is refused', W.google_push('', PATH, PUSHER, verifier=verifier_for(claims())), None)
check('None as the header is refused', W.google_push(None, PATH, PUSHER, verifier=verifier_for(claims())), None)
check('a Basic header is refused', W.google_push('Basic abc', PATH, PUSHER, verifier=verifier_for(claims())), None)
check('an empty Bearer token is refused', W.google_push('Bearer  ', PATH, PUSHER, verifier=verifier_for(claims())), None)
check('a token the verifier refuses (bad signature, expired) is refused',
      W.google_push('Bearer t', PATH, PUSHER, verifier=refusing), None)
check('another issuer is refused',
      W.google_push('Bearer t', PATH, PUSHER, verifier=verifier_for(claims(iss='https://evil.example.test'))), None)
check('a wrong audience is refused',
      W.google_push('Bearer t', PATH, PUSHER, verifier=verifier_for(claims(aud='https://svc.example.test/other'))), None)
check('a missing audience is refused', W.google_push('Bearer t', PATH, PUSHER, verifier=verifier_for(claims(aud=None))), None)
check('a wrong email is refused',
      W.google_push('Bearer t', PATH, PUSHER, verifier=verifier_for(claims(email='other@example.test'))), None)
check('an unverified email is refused',
      W.google_push('Bearer t', PATH, PUSHER, verifier=verifier_for(claims(email_verified='true'))), None)
check('an empty path refuses everything', W.google_push('Bearer t', '', PUSHER, verifier=verifier_for(claims())), None)
check('an empty email refuses everything',
      W.google_push('Bearer t', PATH, '', verifier=verifier_for(claims(email=''))), None)
check('a verifier returning no dict is refused', W.google_push('Bearer t', PATH, PUSHER, verifier=lambda t: None), None)

print('google_push_claims')
check('passes return the claims', W.google_push_claims('Bearer t', PATH, PUSHER, verifier=verifier_for(claims())), claims())
check('missing header names the header', reason(''), 'a Google OIDC token is required: Authorization: Bearer <token>')
check('a refused token names the error type, never the token', reason('Bearer secret-tok', verifier=refusing),
      'invalid Google token: ValueError')
check('another issuer is named', reason('Bearer t', verifier=verifier_for(claims(iss='x'))), 'not a Google token')
check('a wrong audience is named', reason('Bearer t', verifier=verifier_for(claims(aud='https://a.test/b'))),
      'the token is for another address')
check('a wrong email is named', reason('Bearer t', verifier=verifier_for(claims(email='x@y.test'))),
      'the token is not the pushing service account')
check('an empty path is named', reason('Bearer t', path=''), 'no audience path to check the token against')

print('hmac_sha256')
body = b'{"action":"opened"}'
gh = 'sha256=' + sig('s3cret', body)
check("GitHub's sha256=<hex> passes", W.hmac_sha256('s3cret', body, gh, prefix='sha256='), True)
check('upper-case hex passes', W.hmac_sha256('s3cret', body, gh.upper().replace('SHA256=', 'sha256='), prefix='sha256='), True)
check('surrounding whitespace on the header passes', W.hmac_sha256('s3cret', body, '  ' + gh + '\n', prefix='sha256='), True)
check('a bytes secret passes', W.hmac_sha256(b's3cret', body, gh, prefix='sha256='), True)
check('a wrong signature is refused', W.hmac_sha256('s3cret', body, 'sha256=' + '0' * 64, prefix='sha256='), False)
check('a signature under another secret is refused',
      W.hmac_sha256('s3cret', body, 'sha256=' + sig('other', body), prefix='sha256='), False)
check('a changed body is refused', W.hmac_sha256('s3cret', body + b' ', gh, prefix='sha256='), False)
check('a missing header is refused', W.hmac_sha256('s3cret', body, '', prefix='sha256='), False)
check('None as the header is refused', W.hmac_sha256('s3cret', body, None, prefix='sha256='), False)
check('no secret is refused, never a pass', W.hmac_sha256('', body, gh, prefix='sha256='), False)
check('a None secret is refused', W.hmac_sha256(None, body, gh, prefix='sha256='), False)
check('a bare digest without the required prefix is refused',
      W.hmac_sha256('s3cret', body, sig('s3cret', body), prefix='sha256='), False)
check('the prefix alone is refused', W.hmac_sha256('s3cret', body, 'sha256=', prefix='sha256='), False)

apple = 'hmacsha256=' + sig('apple-secret', body)
check("Apple's hmacsha256=<hex> passes", W.hmac_sha256('apple-secret', body, apple, prefix='hmacsha256='), True)
check("Apple's header under GitHub's prefix is refused", W.hmac_sha256('apple-secret', body, apple, prefix='sha256='), False)

sentry = sig('client-secret', body)
check("Sentry's bare hex passes", W.hmac_sha256('client-secret', body, sentry), True)
check('a secret with a piped newline passes with strip_secret',
      W.hmac_sha256('client-secret\r\n', body, sentry, strip_secret=True), True)
check('a secret with a piped newline is refused without strip_secret', W.hmac_sha256('client-secret\n', body, sentry), False)

stamp, token = '1730000000', 'gg-token'
gg = sig(stamp + token, body)
check("GitGuardian's sha256=<hex> under timestamp+token passes",
      W.hmac_sha256(stamp + token, body, 'sha256=' + gg, prefix='sha256=', prefix_required=False), True)
check("GitGuardian's bare digest passes when the prefix is optional",
      W.hmac_sha256(stamp + token, body, gg, prefix='sha256=', prefix_required=False), True)
check('GitGuardian signed under another timestamp is refused',
      W.hmac_sha256('1730000001' + token, body, gg, prefix='sha256=', prefix_required=False), False)

slack_base = b'v0:' + stamp.encode() + b':' + body
slack = 'v0=' + sig('signing', slack_base)
check("Slack's v0=<hex> over v0:<timestamp>:<body> passes", W.hmac_sha256('signing', slack_base, slack, prefix='v0='), True)
check("Slack's signature over the body alone is refused", W.hmac_sha256('signing', body, slack, prefix='v0='), False)

b64 = sig('k', body, 'base64')
check('a base64 digest passes', W.hmac_sha256('k', body, b64, encoding='base64'), True)
check('a base64 digest checked as hex is refused', W.hmac_sha256('k', body, b64), False)
check('an empty body signs as empty bytes', W.hmac_sha256('k', b'', sig('k', b'')), True)
try:
    W.hmac_sha256('k', body, b64, encoding='base32')
    check('an unknown encoding raises', 'no error', 'ValueError')
except ValueError:
    check('an unknown encoding raises', 'ValueError', 'ValueError')

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
