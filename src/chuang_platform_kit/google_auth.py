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
"""
import json
import logging
import os

log = logging.getLogger(__name__)


def _from_sources(Credentials, scopes, token_json, token_file, token_env, secret, session, logger):
    """(source, Credentials) for the first token source that yields one, or (None, None)."""
    scopes = list(scopes)
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
