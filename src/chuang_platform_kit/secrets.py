"""Secret Manager, the two ways a job uses it: a secret read into the environment, a new version written back.

Lifted from SMAD PickleBot's `jobs/daily/run.py` and `jobs/runner.py` (10/8/26, kit 0.5.0; Gene's
call so ChuangFinance's jobs can move to Cloud Run on the same pattern). REST against
secretmanager.googleapis.com with google-auth's default credentials (the job's service account on
Cloud Run, `gcloud auth application-default login` on a desktop); `session` is the seam — anything
with `.get(url, timeout=)` and `.post(url, json=, timeout=)` answering an object with `.status_code`,
`.json()` and `.raise_for_status()` — so the suites run without google-auth and without a project.
`TokenSession` is the seam's standard-library form over a bearer token (gcloud's own sign-in by default), which
`default_session()` falls back to where google-auth is missing or finds no credentials (0.12.1).

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
import urllib.error
import urllib.request

API = 'https://secretmanager.googleapis.com/v1'
SCOPE = 'https://www.googleapis.com/auth/cloud-platform'
_dumps = json.dumps   # `TokenSession.post` takes `json=` as AuthorizedSession does, which hides the module there


class Answer:
    """What `TokenSession` answers: the three members of requests.Response the readers and writers here use."""

    def __init__(self, status: int, body: bytes):
        self.status_code, self._body = status, body

    def json(self):
        return json.loads(self._body.decode('utf-8') or '{}')

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')


class TokenSession:
    """The session over the standard library and a bearer token, for a box with no application-default credentials and
    no google-auth: `token` is a string or a callable asked at each call, by default `google_auth.access_token` (a
    job's variable, a service-account key, the metadata server, gcloud's own sign-in on a desktop). Lifted from SMAD
    PickleBot's picklebot_core/claude_accounts.py (10/31/26, its #660), so `headless_claude --project` reads a token
    on a desktop with nothing else set up."""

    def __init__(self, token=None, opener=None):
        self.token, self.opener = token, opener or urllib.request.urlopen

    def _bearer(self) -> str:
        if self.token is None:
            from . import google_auth
            return google_auth.access_token()
        return self.token() if callable(self.token) else self.token

    def _send(self, req, timeout):
        req.add_header('Authorization', f'Bearer {self._bearer()}')
        try:
            with self.opener(req, timeout=timeout) as r:
                return Answer(getattr(r, 'status', 200), r.read())
        except urllib.error.HTTPError as e:
            return Answer(e.code, b'')

    def get(self, url: str, timeout: float = 20):
        return self._send(urllib.request.Request(url), timeout)

    def post(self, url: str, json=None, timeout: float = 20):
        req = urllib.request.Request(url, data=_dumps(json or {}).encode('utf-8'), method='POST',
                                     headers={'Content-Type': 'application/json'})
        return self._send(req, timeout)


def default_session():
    """google-auth's AuthorizedSession on the default credentials where google-auth is installed and finds some (a job's
    service account on Cloud Run, `gcloud auth application-default login` on a desktop); else a `TokenSession` over
    `google_auth.access_token`, which ends at gcloud's own sign-in, so a desktop with neither still reads a secret."""
    try:
        import google.auth
        from google.auth.exceptions import DefaultCredentialsError
        from google.auth.transport.requests import AuthorizedSession
    except ImportError:
        return TokenSession()
    try:
        creds, _ = google.auth.default(scopes=[SCOPE])
    except DefaultCredentialsError:
        return TokenSession()
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


def ensure(project: str, name: str, session=None) -> bool:
    """The secret `name` exists in `project` (automatic replication, no versions yet):
    True when this call created it, False when it was there. `add_version()` answers
    404 to a secret that was never created, so a provisioning script calls this first."""
    session = session or default_session()
    resp = session.get(f'{API}/projects/{project}/secrets/{name}', timeout=20)
    if resp.status_code == 200:
        return False
    if resp.status_code != 404:
        resp.raise_for_status()
    q = f'?secretId={name}'
    resp = session.post(f'{API}/projects/{project}/secrets{q}', json={'replication': {'automatic': {}}}, timeout=20)
    resp.raise_for_status()
    return True


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
