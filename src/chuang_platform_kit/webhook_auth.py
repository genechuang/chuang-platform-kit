"""Every inbound webhook's proof of its sender, in one place: a Google-signed push (a Pub/Sub push subscription, Cloud
Tasks or Cloud Scheduler's OIDC token) and an HMAC-SHA256 signature over the raw body (GitHub, GitGuardian, Sentry,
Apple's App Store Connect, Slack and any vendor of the same shape).

Both refuse by default: no secret, no signature, no token, no audience path or no expected email is a refusal, never a
pass. Comparisons are constant-time (`hmac.compare_digest`).

`google_push` verifies the token's signature and expiry with google-auth (imported lazily, so a host that only checks
HMAC needs nothing); a host that calls it installs `google-auth` and `requests` (the kit's `gmail` extra carries
google-auth). `hmac_sha256` is the standard library alone.
"""
import base64
import hashlib
import hmac

GOOGLE_ISSUERS = ('accounts.google.com', 'https://accounts.google.com')


class WebhookAuthError(Exception):
    """A webhook's credential was refused; the message says why (safe to log: it never carries the token)."""


def _google_verifier(clock_skew_seconds: int):
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token
    request = google_requests.Request()

    def verify(token):
        return id_token.verify_token(token, request, clock_skew_in_seconds=clock_skew_seconds)
    return verify


def google_push_claims(authorization: str, path: str, email: str, *, verifier=None,
                       clock_skew_seconds: int = 0) -> dict:
    """The claims of a Google-signed OIDC push token, or WebhookAuthError naming why not. Checked: the `Authorization:
    Bearer <token>` shape; the token's signature against Google's keys and its expiry (google-auth's verify_token,
    `clock_skew_seconds` of leeway); an issuer of Google's (GOOGLE_ISSUERS); an audience that, without a trailing slash,
    ends in `path` (the push mints the token for the endpoint's own address); and `email` the pushing service
    account's, with email_verified true. An empty `path` or `email` refuses everything. `verifier(token) -> claims` is
    the test seam in place of google-auth, and must raise for a bad signature or an expired token."""
    if not path:
        raise WebhookAuthError('no audience path to check the token against')
    if not email:
        raise WebhookAuthError('no pushing account to check the token against')
    if not (authorization or '').startswith('Bearer '):
        raise WebhookAuthError('a Google OIDC token is required: Authorization: Bearer <token>')
    token = authorization[len('Bearer '):].strip()
    if not token:
        raise WebhookAuthError('a Google OIDC token is required: Authorization: Bearer <token>')
    try:
        claims = (verifier or _google_verifier(clock_skew_seconds))(token)
    except Exception as e:   # noqa: BLE001 - any refusal of the token is a refusal of the request
        raise WebhookAuthError(f'invalid Google token: {type(e).__name__}') from None
    if not isinstance(claims, dict):
        raise WebhookAuthError('invalid Google token: no claims')
    if claims.get('iss') not in GOOGLE_ISSUERS:
        raise WebhookAuthError('not a Google token')
    if not str(claims.get('aud') or '').rstrip('/').endswith(path):
        raise WebhookAuthError('the token is for another address')
    if claims.get('email') != email or claims.get('email_verified') is not True:
        raise WebhookAuthError('the token is not the pushing service account')
    return claims


def google_push(authorization: str, path: str, email: str, *, verifier=None, clock_skew_seconds: int = 0):
    """The claims of a Google-signed OIDC push token from `email` for an address ending in `path`, or None for any
    refusal: google_push_claims' checks, for a caller that answers 401 without saying why."""
    try:
        return google_push_claims(authorization, path, email, verifier=verifier,
                                  clock_skew_seconds=clock_skew_seconds)
    except WebhookAuthError:
        return None


def hmac_sha256(secret, body: bytes, signature: str, *, prefix: str = '', prefix_required: bool = True,
                encoding: str = 'hex', strip_secret: bool = False) -> bool:
    """True when `signature` is the HMAC-SHA256 of `body` (the exact bytes the sender signed: the raw request body, or
    a vendor's own string such as Slack's `v0:<timestamp>:<body>`) under `secret` (str or bytes). `prefix` is the
    scheme the header carries before the digest (`sha256=` for GitHub and GitGuardian, `hmacsha256=` for Apple, `v0=`
    for Slack, none for Sentry); with `prefix_required=False` a bare digest is taken too (GitGuardian). `encoding` is
    'hex' (compared case-insensitively) or 'base64'. `strip_secret` drops whitespace around the secret (a newline
    piped in with it). No secret or no signature: False, never a pass. Constant-time."""
    if isinstance(secret, str):
        secret = secret.encode('utf-8')
    if strip_secret and secret:
        secret = secret.strip()
    if not secret or not signature:
        return False
    if encoding not in ('hex', 'base64'):
        raise ValueError(f"encoding must be 'hex' or 'base64', not {encoding!r}")
    got = str(signature).strip()
    if prefix:
        if got.lower().startswith(prefix.lower()):
            got = got[len(prefix):].strip()
        elif prefix_required:
            return False
    if not got:
        return False
    digest = hmac.new(secret, body or b'', hashlib.sha256).digest()
    if encoding == 'hex':
        return hmac.compare_digest(digest.hex(), got.lower())
    return hmac.compare_digest(base64.b64encode(digest).decode('ascii'), got)
