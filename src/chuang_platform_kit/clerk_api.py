"""Clerk, one client for every project (SMAD PickleBot #571; Gene, 10/31/26 8:56 AM PT: every third-party API client and
key consolidated in the kit). Clerk has two APIs behind two credentials, and neither takes the other's: the Backend
API on an instance's secret key (users, sessions, sign-in tokens, invitations, email addresses) and the Platform API
on an account's access token (applications and each instance's configuration). So the consolidation is one module
with both, not one credential: `Backend(secret_key)`, `Platform(token)`, and the session check every API needs,
`verify_session`, from the publishable key alone (the Frontend API host is encoded in it; the keys are public, fetched
once per JWKS address and cached).

A refusal is `Refused`, a RuntimeError whose line is `<METHOD> <address> answered <status>: <Clerk's own message>` (its
`long_message`, else `message`), or `<METHOD> <address> did not answer (<error>)`; `status` (0 when nothing answered),
`detail` (Clerk's words) and `kind` (the error's name) are its fields, so a host words its own refusal. Never a key, a
token or an email in it. `opener` is the transport seam in urlopen's shape. Standard library, plus PyJWT with
cryptography for `verify_session` alone (the `clerk` extra).
"""
import base64
import json
import urllib.error
import urllib.parse
import urllib.request

API = 'https://api.clerk.com/v1'
PLATFORM_API = 'https://api.clerk.com/v1/platform'
TIMEOUT = 10
USER_AGENT = 'chuang-platform-kit clerk_api'   # Clerk's edge answers 403 to urllib's default agent (SMAD, 10/5/26)
LEEWAY_SECONDS = 10
PAGE = 500


class Refused(RuntimeError):
    """Clerk refused or did not answer: `status` (0 when nothing answered), `detail` (Clerk's own message, '' when it
    gave none) and `kind` (the error's name)."""

    def __init__(self, message: str, status: int = 0, detail: str = '', kind: str = ''):
        super().__init__(message)
        self.status, self.detail, self.kind = status, detail, kind


class NotConfigured(Exception):
    """No publishable key: sign-in is not set up where this runs."""


class Unauthorized(Exception):
    """The session token is missing, malformed, expired, for another instance, or forged."""


def _clerk_message(raw: bytes) -> str:
    try:
        errs = json.loads(raw.decode()).get('errors') or []
        return (errs[0].get('long_message') or errs[0].get('message') or '') if errs else ''
    except Exception:   # noqa: BLE001 - Clerk's body is a nicety
        return ''


def request(method: str, url: str, token: str, body=None, headers=None, opener=None, timeout: float = TIMEOUT,
            user_agent: str = USER_AGENT):
    """One call with a bearer credential: the parsed JSON, None for an empty answer; Refused otherwise."""
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {'Authorization': f'Bearer {token}', 'Accept': 'application/json', 'User-Agent': user_agent}
    if data is not None:
        hdrs['Content-Type'] = 'application/json'
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    where = url.split('?')[0]
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw.decode()) if raw else None
    except urllib.error.HTTPError as e:
        detail = _clerk_message(e.read() if e.fp is not None else b'')
        raise Refused(f'{method} {where} answered {e.code}' + (f': {detail}' if detail else ''), status=e.code,
                      detail=detail, kind=type(e).__name__)
    except Exception as e:   # noqa: BLE001 - a timeout, a refused connection, a body that is not JSON: one kind to the caller
        raise Refused(f'{method} {where} did not answer ({type(e).__name__})', kind=type(e).__name__)


def _q(value) -> str:
    return urllib.parse.quote(str(value), safe='')


class Backend:
    """Clerk's Backend API on one instance's secret key."""

    def __init__(self, secret_key: str, opener=None, timeout: float = TIMEOUT, user_agent: str = USER_AGENT, api: str = API):
        self.secret_key, self.opener, self.timeout, self.user_agent, self.api = secret_key, opener, timeout, user_agent, api

    def call(self, path: str, method: str = 'GET', body=None, query=None):
        """One call under the API root (`path` may carry its own query); the parsed JSON, None for an empty answer."""
        if not self.secret_key:
            raise Refused('no Clerk secret key')
        url = self.api + path + (('&' if '?' in path else '?') + urllib.parse.urlencode(query, doseq=True) if query else '')
        return request(method, url, self.secret_key, body, opener=self.opener, timeout=self.timeout, user_agent=self.user_agent)

    def pages(self, path: str, page: int = PAGE):
        """Every row of a paged listing, a bare list or {'data': [...]}, `page` at a time."""
        offset = 0
        while True:
            sep = '&' if '?' in path else '?'
            body = self.call(f'{path}{sep}limit={page}&offset={offset}')
            rows = body.get('data', []) if isinstance(body, dict) else (body or [])
            yield from rows
            if len(rows) < page:
                return
            offset += page

    def user(self, user_id: str):
        return self.call(f'/users/{_q(user_id)}')

    def users(self, **query):
        return self.call('/users', query=query or None)

    def delete_user(self, user_id: str):
        return self.call(f'/users/{_q(user_id)}', 'DELETE')

    def ban(self, user_id: str):
        return self.call(f'/users/{_q(user_id)}/ban', 'POST')

    def unban(self, user_id: str):
        return self.call(f'/users/{_q(user_id)}/unban', 'POST')

    def session(self, session_id: str):
        return self.call(f'/sessions/{_q(session_id)}')

    def sessions(self, user_id: str, status: str = 'active', limit: int = 100):
        return self.call(f'/sessions?user_id={_q(user_id)}&status={_q(status)}&limit={int(limit)}')

    def revoke_session(self, session_id: str):
        return self.call(f'/sessions/{_q(session_id)}/revoke', 'POST', {})

    def sign_in_token(self, user_id: str, seconds: int = 300) -> str:
        """A one-time sign-in token for the account (POST /sign_in_tokens), used at once as a sign-in ticket."""
        made = self.call('/sign_in_tokens', 'POST', {'user_id': user_id, 'expires_in_seconds': seconds}) or {}
        token = made.get('token') if isinstance(made, dict) else None
        if not token:
            raise Refused('POST /sign_in_tokens answered no token')
        return token

    def invite(self, email: str, notify: bool = True, redirect_url: str = '', ignore_existing: bool = True, **extra):
        body = {'email_address': email, 'notify': notify, 'ignore_existing': ignore_existing, **extra}
        if redirect_url:
            body['redirect_url'] = redirect_url
        return self.call('/invitations', 'POST', body)

    def revoke_invitation(self, invitation_id: str):
        return self.call(f'/invitations/{_q(invitation_id)}/revoke', 'POST', {})

    def add_email(self, user_id: str, email: str, verified: bool = True, primary: bool = True):
        return self.call('/email_addresses', 'POST', {'user_id': user_id, 'email_address': email, 'verified': verified,
                                                     'primary': primary})

    def update_email(self, email_id: str, **fields):
        return self.call(f'/email_addresses/{_q(email_id)}', 'PATCH', fields)

    def delete_email(self, email_id: str):
        return self.call(f'/email_addresses/{_q(email_id)}', 'DELETE')

    def delete_external_account(self, user_id: str, account_id: str):
        return self.call(f'/users/{_q(user_id)}/external_accounts/{_q(account_id)}', 'DELETE')


class Platform:
    """Clerk's Platform API on an account's access token: applications and each instance's configuration."""

    def __init__(self, token: str, opener=None, timeout: float = TIMEOUT, user_agent: str = USER_AGENT, api: str = PLATFORM_API):
        self.token, self.opener, self.timeout, self.user_agent, self.api = token, opener, timeout, user_agent, api

    def call(self, path: str, method: str = 'GET', body=None, query=None, headers=None):
        if not self.token:
            raise Refused('no Clerk Platform API token')
        url = self.api + path + ('?' + urllib.parse.urlencode(query, doseq=True) if query else '')
        return request(method, url, self.token, body, headers, opener=self.opener, timeout=self.timeout, user_agent=self.user_agent)

    def applications(self):
        return self.call('/applications')

    def application(self, app_id: str, include_secret_keys: bool = False):
        return self.call(f'/applications/{_q(app_id)}', query={'include_secret_keys': 'true'} if include_secret_keys else None)

    @staticmethod
    def config_path(app_id: str, env: str) -> str:
        return f'/applications/{app_id}/instances/{_q(env)}/config'

    def config(self, app_id: str, env: str, keys=None, schema: bool = False):
        path = self.config_path(app_id, env) + ('/schema' if schema else '')
        return self.call(path, query={'keys': list(keys)} if keys else None)

    def patch_config(self, app_id: str, env: str, body: dict, if_match: str = '', dry_run: bool = False):
        path = self.config_path(app_id, env) + ('?dry_run=true' if dry_run else '')
        return self.call(path, 'PATCH', body, headers={'If-Match': if_match} if if_match else None)


# -- the session check ---------------------------------------------------------------------------------------------

def frontend_api(publishable_key: str) -> str:
    """The instance's Frontend API host from its publishable key: pk_test_<base64 of "host$"> -> host; '' otherwise."""
    pk = (publishable_key or '').strip()
    if not pk.startswith(('pk_test_', 'pk_live_')):
        return ''
    body = pk.split('_', 2)[2]
    try:
        host = base64.b64decode(body + '=' * (-len(body) % 4)).decode('utf-8')
    except Exception:   # noqa: BLE001
        return ''
    return host.rstrip('$').strip()


def issuer(publishable_key: str) -> str:
    """The instance's issuer, https://<its Frontend API host>; '' for a key that names none."""
    host = frontend_api(publishable_key)
    return f'https://{host}' if host else ''


def jwks_url(publishable_key: str) -> str:
    """Where the instance publishes its signing keys; '' for a key that names none."""
    iss = issuer(publishable_key)
    return f'{iss}/.well-known/jwks.json' if iss else ''


JWK_CLIENTS = {}


def jwk_client(url: str):
    """One PyJWKClient per JWKS address: it caches the keys an hour and refetches on an unknown key id, which is how
    Clerk rotates."""
    import jwt
    if url not in JWK_CLIENTS:
        JWK_CLIENTS[url] = jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)
    return JWK_CLIENTS[url]


def _claimed_issuer(token: str) -> str:
    """The `iss` a token says it has, read without checking anything: only to pick which instance's keys verify it."""
    import jwt
    try:
        return str(jwt.decode(token, options={'verify_signature': False}).get('iss') or '')
    except (jwt.PyJWTError, ValueError):
        return ''


def verify_session(authorization: str, publishable_key: str, test_publishable_key: str = '', signing_key=None,
                   leeway: int = LEEWAY_SECONDS, clients=None) -> tuple:
    """(the claims, whether the second instance signed them) of a valid Clerk session token from an Authorization
    header; Unauthorized or NotConfigured otherwise. The signature against the instance's JWKS by the token's key id,
    exp, nbf and the issuer. A token whose claimed issuer is `test_publishable_key`'s instance (a development
    instance for test devices) is checked against that instance's keys and issuer instead: the claimed issuer only
    picks the keys, the signature and the issuer are still checked. `signing_key` is the test seam; `clients` the
    JWKS clients by address (JWK_CLIENTS)."""
    import jwt
    iss = issuer(publishable_key)
    if not iss:
        raise NotConfigured('no Clerk publishable key')
    if not authorization or not authorization.startswith('Bearer '):
        raise Unauthorized('a Clerk session token is required: Authorization: Bearer <token>')
    token = authorization[len('Bearer '):].strip()
    if not token:
        raise Unauthorized('empty token')
    test_iss = issuer(test_publishable_key) if test_publishable_key else ''
    test = bool(test_iss) and test_iss != iss and _claimed_issuer(token) == test_iss
    if test:
        iss = test_iss
    clients = JWK_CLIENTS if clients is None else clients
    try:
        if signing_key is not None:
            key = signing_key
        else:
            url = jwks_url(test_publishable_key) if test else jwks_url(publishable_key)
            client = clients.get(url) or jwk_client(url)
            key = client.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=['RS256'], issuer=iss, leeway=leeway,
                            options={'require': ['exp', 'sub', 'iss']})
    except jwt.ExpiredSignatureError:
        raise Unauthorized('the session token has expired')
    except jwt.ImmatureSignatureError:
        raise Unauthorized('the session token is not valid yet')
    except jwt.InvalidIssuerError:
        raise Unauthorized('the token is for another Clerk instance')
    except (jwt.PyJWTError, ValueError) as e:
        raise Unauthorized(f'invalid session token: {type(e).__name__}')
    return claims, test
