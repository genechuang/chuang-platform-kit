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

The one place a secret is written (0.29.0; Gene, 11/7/26: "secrets write and management should be done in ONE place
in chuang-kit"): every write goes through `add_version`, which destroys the versions it replaced, since Secret Manager
bills each version until it is destroyed and every reader takes `latest`; `prune` and `delete` beside it; a script or
a person writes with `python -m chuang_platform_kit.secrets add PROJECT NAME` (the value on stdin); and a project's
suite refuses any other write in its tree with `writers(root)`.

Nothing here logs, prints or returns a secret's value except to its caller; a refusal raises with
the HTTP status and never the payload. Names are the caller's: the kit knows no project.
"""
import base64
import json
import logging
import os
import re
import subprocess
import sys
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

    def delete(self, url: str, timeout: float = 20):
        return self._send(urllib.request.Request(url, method='DELETE'), timeout)


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
    resp = session.get(access_url(project, name, version), timeout=20)
    resp.raise_for_status()
    data = (resp.json().get('payload') or {}).get('data', '')
    return base64.b64decode(data) if data else b''


def access_url(project: str, name: str, version: str = 'latest') -> str:
    """Secret Manager's address for one secret version's `:access` read."""
    return f'{API}/projects/{project}/secrets/{name}/versions/{version}:access'


def resolve_env(entries, project: str, get_json=None, session=None) -> dict:
    """{name: value} of a Cloud Run container's `env` entries (a service's or a job's, as the v2 API lists them):
    each literal value as it is, each `valueSource.secretKeyRef` read from Secret Manager at its version ('latest'
    when none), so a desktop run gets the configuration the cloud deployed. A secret named by its full resource name
    (`projects/<p>/secrets/<name>`) is read from that project. `get_json(url) -> dict` is the caller's own reader of
    Google's REST answers (the one it read the service or job with, which its tests already fake); without it,
    `access` on `session`. A refusal raises, naming the secret, never a value (0.27.0)."""
    def read(p, n, v):
        if get_json is None:
            return access(p, n, v, session)
        data = ((get_json(access_url(p, n, v)) or {}).get('payload') or {}).get('data', '')
        return base64.b64decode(data) if data else b''
    out = {}
    for e in entries or []:
        ref = (e.get('valueSource') or {}).get('secretKeyRef')
        if not ref:
            out[e['name']] = e.get('value', '')
            continue
        name, where = ref['secret'], project
        if name.startswith('projects/'):
            parts = name.split('/')
            where, name = parts[1], parts[3]
        out[e['name']] = read(where, name, ref.get('version') or 'latest').decode('utf-8')
    return out


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


def add_version(project: str, name: str, data, session=None, keep: int = 1) -> str:
    """A new version of the secret holding `data` (bytes or str); the version's
    resource name. Raises on a refusal: the caller reports its kind, never the data.
    Then the versions it replaced are destroyed (`prune`, the newest `keep` kept; 0.29.0): Secret Manager bills every
    version not destroyed, and every reader takes `latest` (Gene, 11/7/26: "we don't use older versions period").
    A failed prune is a WARNING and never the write's failure: the new version is in, and the next write prunes again.
    `keep=0` adds without pruning."""
    session = session or default_session()
    payload = data.encode('utf-8') if isinstance(data, str) else bytes(data)
    url = f'{API}/projects/{project}/secrets/{name}:addVersion'
    resp = session.post(url, json={'payload': {'data': base64.b64encode(payload).decode('ascii')}}, timeout=20)
    resp.raise_for_status()
    made = resp.json().get('name', '')
    if keep:
        try:
            prune(project, name, keep, session)
        except Exception as e:   # the write succeeded; a refused prune (a role without destroy) waits for the next
            logging.getLogger(__name__).warning('secret %s: the versions this write replaced were not destroyed: %s',
                                                name, str(e)[:160])
    return made


def versions(project: str, name: str, session=None) -> list:
    """[(number, state)] of every version of the secret, newest first; the state ENABLED, DISABLED or DESTROYED.
    Metadata only: no value is read. Raises on a refusal."""
    session = session or default_session()
    out, token = [], ''
    while True:
        url = f'{API}/projects/{project}/secrets/{name}/versions?pageSize=500' + (f'&pageToken={token}' if token else '')
        resp = session.get(url, timeout=20)
        resp.raise_for_status()
        body = resp.json() or {}
        for v in body.get('versions') or []:
            n = str(v.get('name', '')).rsplit('/', 1)[-1]
            if n.isdigit():
                out.append((int(n), v.get('state', '')))
        token = body.get('nextPageToken') or ''
        if not token:
            return sorted(out, reverse=True)


def prune(project: str, name: str, keep: int = 1, session=None) -> list:
    """Destroys every enabled or disabled version of the secret but its newest `keep` enabled ones; the numbers
    destroyed, newest first. A secret with no enabled version is left alone, so a retired secret's disabled versions
    wait for its `delete`. Destroying cannot be undone; nothing reads past `latest`. Raises on a refusal."""
    session = session or default_session()
    live = versions(project, name, session)
    kept = [n for n, state in live if state == 'ENABLED'][:max(int(keep), 1)]
    if not kept:
        return []
    gone = []
    for n, state in live:
        if state in ('ENABLED', 'DISABLED') and n not in kept:
            resp = session.post(f'{API}/projects/{project}/secrets/{name}/versions/{n}:destroy', json={}, timeout=20)
            resp.raise_for_status()
            gone.append(n)
    return gone


def delete(project: str, name: str, session=None) -> bool:
    """Deletes the secret and every version of it: True when this call deleted it, False when it was not there.
    Cannot be undone: a caller deletes only what its owner confirmed. Raises on another refusal."""
    session = session or default_session()
    resp = session.delete(f'{API}/projects/{project}/secrets/{name}', timeout=20)
    if resp.status_code == 404:
        return False
    resp.raise_for_status()
    return True


# A write to Secret Manager made anywhere but here: a version added, destroyed or disabled, a secret created or
# deleted, by gcloud (as words or as an argument list) or by the REST API; this module's own command
# (`python -m chuang_platform_kit.secrets delete ...`) is the way, never a finding (0.29.1).
_WRITE = re.compile(r"(?<!platform_kit\.)secrets\s+(?:versions\s+(?:add|destroy|disable)|create|delete)\b"
                    r"|['\"]secrets['\"],\s*['\"](?:create|delete)['\"]"
                    r"|['\"]versions['\"],\s*['\"](?:add|destroy|disable)['\"]"
                    r"|:addVersion\b|/versions/[^'\"\s]*:(?:destroy|disable)\b")


def writers(root: str, skip=('tests/', 'docs/')) -> list:
    """[(path, line number)] of each tracked file line in the git repository at `root` that writes Secret Manager
    itself, outside this module: a project's suite refuses any, so this module stays the one writer (Gene, 11/7/26:
    "secrets write and management should be done in ONE place in chuang-kit"). Markdown and the `skip` folders are
    left out; a line telling a person to run `gcloud secrets versions add` counts too, since that write skips the
    prune: it names `python -m chuang_platform_kit.secrets add` instead."""
    files = subprocess.run(['git', 'ls-files'], cwd=root, capture_output=True, text=True).stdout.split('\n')
    found = []
    for f in filter(None, files):
        if f.endswith('.md') or f.startswith(tuple(skip)) or f.endswith('chuang_platform_kit/secrets.py'):
            continue
        try:
            with open(os.path.join(root, f), encoding='utf-8') as fh:
                lines = fh.read().splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        found += [(f, i) for i, line in enumerate(lines, 1) if _WRITE.search(line)]
    return found


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


USAGE = ('usage: python -m chuang_platform_kit.secrets add PROJECT NAME [--data-file PATH]   (else the value on stdin)\n'
         '       python -m chuang_platform_kit.secrets prune PROJECT NAME\n'
         '       python -m chuang_platform_kit.secrets delete PROJECT NAME')


def main(argv=None, stdin=None, session=None) -> int:
    """`python -m chuang_platform_kit.secrets add|prune|delete PROJECT NAME`, the one way a script or a person writes a
    secret (a PowerShell store, a rotation): `add` reads the value from stdin (never echoed, never printed), creates the
    secret when it is missing and adds the version, which destroys the ones it replaced (`--data-file PATH` reads the
    value from a file instead, for a shell such as PowerShell 5.1 that cannot redirect a file into stdin); `prune`
    destroys all but the newest; `delete` deletes the secret. Prints names and version numbers only; 2 on a usage
    error, 1 on a refusal."""
    args = list(sys.argv[1:] if argv is None else argv)
    data_file = None
    if '--data-file' in args:
        i = args.index('--data-file')
        data_file = args[i + 1] if i + 1 < len(args) else ''
        del args[i:i + 2]
    if len(args) != 3 or args[0] not in ('add', 'prune', 'delete') or data_file == '' or (data_file and args[0] != 'add'):
        print(USAGE, file=sys.stderr)
        return 2
    cmd, project, name = args
    session = session or default_session()
    try:
        if cmd == 'add':
            if data_file:
                with open(data_file, 'rb') as fh:
                    data = fh.read()
            else:
                data = (stdin or sys.stdin.buffer).read()
            if isinstance(data, str):
                data = data.encode('utf-8')
            if not data.strip():
                print(f'{name}: nothing on stdin, nothing written', file=sys.stderr)
                return 2
            ensure(project, name, session)
            made = add_version(project, name, data.rstrip(b'\r\n'), session=session)
            print(f'{name}: version {made.rsplit("/", 1)[-1]} added; older versions destroyed')
        elif cmd == 'prune':
            gone = prune(project, name, session=session)
            print(f'{name}: destroyed ' + (', '.join(f'v{n}' for n in gone) if gone else 'nothing'))
        else:
            print(f'{name}: ' + ('deleted' if delete(project, name, session) else 'not there'))
    except Exception as e:   # the status, never a value
        print(f'{name}: {cmd} refused: {str(e)[:160]}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':   # pragma: no cover - the CLI
    sys.exit(main())
