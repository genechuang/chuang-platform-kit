"""A Google API service (Gmail, Drive, Sheets, People) from an OAuth token held in a file, the environment or Secret Manager — refreshed, and the refreshed token written back.

Lifted from SMAD PickleBot's `email_notify._get_gmail_service` chain and its daily job's
token write-back (10/8/26, kit 0.5.0): one place that turns "where is the token" into a
`googleapiclient` service, so a host names its sources and never repeats the refresh dance.

    from chuang_platform_kit import google_auth
    svc = google_auth.service('gmail', 'v1', SCOPES, token_file='gmail-token.json',
                              token_env='GMAIL_OAUTH_TOKEN_JSON',
                              secret=('my-project', 'GMAIL_OAUTH_TOKEN_JSON'))
    drive = google_auth.service('drive', 'v3', SCOPES, token_env='GOOGLE_OAUTH_TOKEN_JSON')

The token is looked for in this order and the first that parses wins: `token_json` (a dict or
JSON text the caller already holds), `token_file` (read by google-auth itself, so a corrupt file
falls through), `token_env` (the variable's JSON), then the Secret Manager secret
`(project, name)` through `secrets.access`. An expired token with a refresh token is refreshed;
after a refresh the new token is written back to the file when the file exists, and to Secret
Manager as a new version when `secret` was given and `write_back` is on — so a job's token
outlives the job and a desktop's outlives the session. A token that cannot be read or refreshed
answers None with a WARNING on `logger` (the caller's, so its log says it; this module's by
default); nothing raises on the way to "no service", as the callers (a notification email, a
watch renewal) go on without one.

Needs the `gmail` extra (google-auth, google-api-python-client). `credentials()` is the half
without the client library, for a host that builds its own service or wants the Credentials
for a different transport.

An access token for Google's REST APIs, with the standard library alone (0.9.0, 10/27/26; lifted from SMAD
PickleBot's `ci_claim.access_token`, the one token reader its functions, jobs, runners and desktop shared):

    from chuang_platform_kit import google_auth
    token = google_auth.access_token()                 # '' when no source answers; never raises

`access_token()` takes the first source that answers: the environment variable `env_name` (CI_ACCESS_TOKEN: a
pipeline job minting one token for every step), a service-account key under GOOGLE_APPLICATION_CREDENTIALS (a
path, or the key's JSON itself, as a cloud environment's variable holds it) minted in-process by google-auth when
it is installed and else by `key_file_token()` (the key's RS256 JWT signed with the openssl CLI and exchanged at
Google's token endpoint, a fraction of a second where gcloud's first start is 5 to 37 s), the metadata server
(Cloud Run, Cloud Functions, Cloud Build), then `gcloud auth print-access-token` (a desktop, a runner after the
auth action), bounded by `gcloud_timeout`.
"""
import json
import logging
import os
import subprocess
import time
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)
METADATA_TOKEN = 'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token'
CLOUD_PLATFORM = 'https://www.googleapis.com/auth/cloud-platform'
KEY_ENV = 'GOOGLE_APPLICATION_CREDENTIALS'


def key_file_token(path: str, opener=None, run=subprocess.run, now=None, scope: str = CLOUD_PLATFORM) -> str:
    """An access token from a service-account key (`path`: the key file's path, the key's JSON itself, or either
    behind a pasted `GOOGLE_APPLICATION_CREDENTIALS=`) with the standard library and the openssl CLI, or '' for
    anything else: the key's RS256-signed JWT exchanged at its token endpoint. Never raises."""
    import base64
    import tempfile

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()
    try:
        path = (path or '').strip()
        if path.startswith(KEY_ENV + '='):   # an editor kept the whole `NAME=value` line as the value
            path = path[len(KEY_ENV) + 1:].strip()
        if path.startswith('{'):
            key = json.loads(path)
        else:
            with open(path, encoding='utf-8') as f:
                key = json.load(f)
        if key.get('type') != 'service_account':
            return ''
        t = int(now if now is not None else time.time())
        uri = key.get('token_uri') or 'https://oauth2.googleapis.com/token'
        claims = {'iss': key['client_email'], 'scope': scope, 'aud': uri, 'iat': t, 'exp': t + 3600}
        unsigned = b64(json.dumps({'alg': 'RS256', 'typ': 'JWT'}).encode()) + '.' + b64(json.dumps(claims).encode())
        with tempfile.NamedTemporaryFile('w', suffix='.pem', delete=False) as pem:
            pem.write(key['private_key'])
        try:
            signed = run(['openssl', 'dgst', '-sha256', '-sign', pem.name], input=unsigned.encode(), capture_output=True,
                         timeout=10)
        finally:
            os.remove(pem.name)
        if signed.returncode != 0 or not signed.stdout:
            return ''
        body = urllib.parse.urlencode({'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
                                       'assertion': unsigned + '.' + b64(signed.stdout)}).encode()
        req = urllib.request.Request(uri, data=body, headers={'Content-Type': 'application/x-www-form-urlencoded'})
        with (opener or urllib.request.urlopen)(req, timeout=15) as r:
            return json.loads(r.read().decode() or '{}').get('access_token') or ''
    except Exception:   # noqa: BLE001 - no openssl, a bad key, a refused exchange: the caller's next source
        return ''


def access_token(env=None, opener=None, run=subprocess.run, gcloud_timeout=30, env_name: str = 'CI_ACCESS_TOKEN',
                 scope: str = CLOUD_PLATFORM) -> str:
    """An OAuth access token for Google's REST APIs, or '' when no source answers (module docstring): `env_name`,
    the key under GOOGLE_APPLICATION_CREDENTIALS (google-auth in-process when installed, else key_file_token), the
    metadata server, then gcloud bounded by `gcloud_timeout`. Never raises."""
    env = os.environ if env is None else env
    if env.get(env_name):
        return env[env_name].strip()
    key = (env.get(KEY_ENV) or '').strip()
    if key:
        if os.path.exists(key):
            try:
                from google.auth.transport.requests import Request
                from google.oauth2 import service_account
                creds = service_account.Credentials.from_service_account_file(key, scopes=[scope])
                creds.refresh(Request())
                if creds.token:
                    return creds.token
            except Exception:   # noqa: BLE001 - the library missing, or not a service account's key: the next source
                pass
        if os.path.exists(key) or key.startswith('{') or key.startswith(KEY_ENV + '='):
            tok = key_file_token(key, opener=opener, run=run, scope=scope)
            if tok:
                return tok
    opener = opener or urllib.request.urlopen
    try:
        req = urllib.request.Request(METADATA_TOKEN, headers={'Metadata-Flavor': 'Google'})
        with opener(req, timeout=min(3, gcloud_timeout)) as r:
            tok = json.loads(r.read().decode() or '{}').get('access_token')
            if tok:
                return tok
    except Exception:   # noqa: BLE001 - not on Google Cloud: no metadata server
        pass
    try:
        # Fixed argv, no external input; the shell only so Windows finds the gcloud .cmd shim.
        p = run(['gcloud', 'auth', 'print-access-token'], capture_output=True, text=True, timeout=gcloud_timeout,  # nosec B602
                shell=(os.name == 'nt'))
        return (p.stdout or '').strip() if p.returncode == 0 else ''
    except Exception:   # noqa: BLE001 - no gcloud, or none signed in
        return ''


def _from_sources(Credentials, scopes, token_json, token_file, token_env, secret, session, logger):
    """(source, Credentials) for the first token source that yields one, or (None, None). `scopes` None keeps the
    token's own granted scopes (0.15.1): a refresh that asks for a scope the token was never granted is refused."""
    scopes = list(scopes) if scopes is not None else None
    if token_json:
        try:
            data = token_json if isinstance(token_json, dict) else json.loads(token_json)
            return 'json', Credentials.from_authorized_user_info(data, scopes)
        except Exception as e:   # noqa: BLE001 - not JSON, or not a token
            logger.warning('google_auth: token_json is not an authorized-user token (%s)', type(e).__name__)
    if token_file and os.path.exists(token_file):
        try:
            return 'file', Credentials.from_authorized_user_file(token_file, scopes)
        except Exception as e:   # noqa: BLE001 - a corrupt file falls through to the next source
            logger.warning('google_auth: %s not read (%s)', token_file, type(e).__name__)
    if token_env and os.environ.get(token_env, '').strip():
        try:
            return 'env', Credentials.from_authorized_user_info(json.loads(os.environ[token_env]), scopes)
        except Exception as e:   # noqa: BLE001
            logger.warning('google_auth: %s is not an authorized-user token (%s)', token_env, type(e).__name__)
    if secret:
        try:
            from . import secrets as _secrets
            project, name = secret
            raw = _secrets.access(project, name, session=session)
            return 'secret', Credentials.from_authorized_user_info(json.loads(raw.decode('utf-8')), scopes)
        except Exception as e:   # noqa: BLE001 - a missing secret is "no token", logged
            logger.warning('google_auth: the token secret was not read (%s)', type(e).__name__)
    return None, None


def credentials(scopes, token_json=None, token_file=None, token_env=None, secret=None,
                write_back=True, session=None, request=None, logger=None):
    """google.oauth2 Credentials for `scopes` from the first token source that parses,
    refreshed when expired, the refresh written back (module docstring); None when
    no source yields a usable token. Warnings go to `logger` (this module's by default)."""
    logger = logger or log
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        logger.debug('google_auth: google-auth is not installed (pip install chuang-platform-kit[gmail])')
        return None
    source, creds = _from_sources(Credentials, scopes, token_json, token_file, token_env, secret, session, logger)
    if creds is None:
        return None
    try:
        if creds.expired and creds.refresh_token:
            creds.refresh(request or Request())
            if write_back:
                _write_back(creds.to_json(), token_file, secret, session, logger)
    except Exception as e:   # noqa: BLE001
        logger.warning('google_auth: token refresh failed (%s: %s)', type(e).__name__, e)
    if not creds.valid:
        logger.warning('google_auth: credentials not valid after refresh')
        return None
    return creds


def _write_back(token_text: str, token_file, secret, session, logger) -> list:
    """The refreshed token stored where it came from: the file when it exists, Secret
    Manager when a secret was named. The places written, for the caller's log."""
    done = []
    if token_file and os.path.exists(token_file):
        try:
            with open(token_file, 'w', encoding='utf-8') as f:
                f.write(token_text)
            done.append('file')
        except OSError as e:
            logger.warning('google_auth: refreshed token not written to %s (%s)', token_file, type(e).__name__)
    if secret:
        try:
            from . import secrets as _secrets
            project, name = secret
            _secrets.add_version(project, name, token_text, session=session)
            done.append('secret')
        except Exception as e:   # noqa: BLE001
            logger.warning('google_auth: refreshed token not stored in Secret Manager (%s)', type(e).__name__)
    return done


def service(api: str, version: str, scopes, logger=None, **sources):
    """A `googleapiclient.discovery` service for `api`/`version` on credentials()
    from the same sources (`token_json`, `token_file`, `token_env`, `secret`,
    `write_back`, `session`); None without a usable token or the client library."""
    logger = logger or log
    creds = credentials(scopes, logger=logger, **sources)
    if creds is None:
        return None
    try:
        from googleapiclient.discovery import build
    except ImportError:
        logger.debug('google_auth: google-api-python-client is not installed (pip install chuang-platform-kit[gmail])')
        return None
    try:
        return build(api, version, credentials=creds)
    except Exception as e:   # noqa: BLE001
        logger.warning('google_auth: %s %s service not built (%s: %s)', api, version, type(e).__name__, e)
        return None
