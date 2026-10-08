"""Secret Manager, the two ways a job uses it: a secret read into the environment, a new version written back.

Lifted from SMAD PickleBot's `jobs/daily/run.py` and `jobs/runner.py` (10/8/26, kit 0.5.0; Gene's
call so ChuangFinance's jobs can move to Cloud Run on the same pattern). REST against
secretmanager.googleapis.com with google-auth's default credentials (the job's service account on
Cloud Run, `gcloud auth application-default login` on a desktop); `session` is the seam — anything
with `.get(url, timeout=)` and `.post(url, json=, timeout=)` answering an object with `.status_code`,
`.json()` and `.raise_for_status()` — so the suites run without google-auth and without a project.

    from chuang_platform_kit import secrets
    secrets.load_json('my-project', 'COLLECTOR_LOGINS', into_env=True)   # {"USER": "...", "PASS": "..."} -> os.environ
    secrets.add_version('my-project', 'GMAIL_OAUTH_TOKEN_JSON', creds.to_json())
    secrets.write_file(os.environ, 'GOOGLE_CREDENTIALS_JSON', 'credentials.json')   # for a script that reads a file
    secrets.renew_if_changed('my-project', 'GMAIL_OAUTH_TOKEN_JSON', before, 'token.json')

Nothing here logs, prints or returns a secret's value except to its caller; a refusal raises with
the HTTP status and never the payload. Names are the caller's: the kit knows no project.
"""
import base64
import json
import os

API = 'https://secretmanager.googleapis.com/v1'
SCOPE = 'https://www.googleapis.com/auth/cloud-platform'


def default_session():
    """An AuthorizedSession on google-auth's default credentials (needs the `gmail`
    extra's google-auth; a host without it gets an ImportError that says so)."""
    try:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession
    except ImportError as e:   # pragma: no cover - the extra is not installed
        raise ImportError("chuang_platform_kit.secrets needs google-auth: pip install chuang-platform-kit[gmail]") from e
    creds, _ = google.auth.default(scopes=[SCOPE])
    return AuthorizedSession(creds)


def access(project: str, name: str, version: str = 'latest', session=None) -> bytes:
    """The bytes of one secret version. Raises on a refusal (the status, never the data)."""
    session = session or default_session()
    url = f'{API}/projects/{project}/secrets/{name}/versions/{version}:access'
    resp = session.get(url, timeout=20)
    resp.raise_for_status()
    data = (resp.json().get('payload') or {}).get('data', '')
    return base64.b64decode(data) if data else b''


def load_json(project: str, name: str, into_env: bool = False, overwrite: bool = False,
              version: str = 'latest', session=None) -> dict:
    """A secret holding a JSON object, parsed; with `into_env`, each key set in
    os.environ (an existing variable kept unless `overwrite`), so a job's logins
    live in one secret and reach the code as the environment it already reads."""
    raw = access(project, name, version, session)
    data = json.loads(raw.decode('utf-8') or '{}')
    if not isinstance(data, dict):
        raise ValueError(f'secret {name} is not a JSON object')
    if into_env:
        for key, value in data.items():
            if overwrite or not os.environ.get(key):
                os.environ[key] = value if isinstance(value, str) else json.dumps(value)
    return data


def add_version(project: str, name: str, data, session=None) -> str:
    """A new version of the secret holding `data` (bytes or str); the version's
    resource name. Raises on a refusal: the caller reports its kind, never the data."""
    session = session or default_session()
    payload = data.encode('utf-8') if isinstance(data, str) else bytes(data)
    url = f'{API}/projects/{project}/secrets/{name}:addVersion'
    resp = session.post(url, json={'payload': {'data': base64.b64encode(payload).decode('ascii')}}, timeout=20)
    resp.raise_for_status()
    return resp.json().get('name', '')


def write_file(env, var: str, path: str, mode: int = 0o600) -> bool:
    """The secret in env[var] written as the file at `path`, readable by this user
    alone, so a script that reads a file (a credentials JSON, an OAuth token) finds
    it. False, and the file untouched, when the variable is empty or a file is
    already there."""
    value = (env or {}).get(var, '')
    if not str(value).strip() or os.path.exists(path):
        return False
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(value)
    return True


def renew_if_changed(project: str, name: str, before: str, path: str, session=None) -> str:
    """After a step that may refresh a token file: when the file's content differs
    from `before` (the value the step started with), store it as a new version of
    the secret. 'missing' (no file), 'unchanged', or the new version's name."""
    if not os.path.exists(path):
        return 'missing'
    with open(path, encoding='utf-8') as f:
        after = f.read()
    if after.strip() == (before or '').strip():
        return 'unchanged'
    return add_version(project, name, after, session=session)
