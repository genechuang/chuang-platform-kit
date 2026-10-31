"""GitHub's REST API for one repository, with one token: the request, the token lookup and the verbs a project uses.

Gene, 10/25/26 11:45 PM PT: "GH access seems to belong in kit" (a project had grown a dozen hand-written GitHub clients
and five tokens; "one token per 3rd party API"). Everything takes the `token` and the repository as "owner/name";
`opener` is the seam (anything called as urlopen(request, timeout=...)), so the suites run offline. Standard library
only: it rides into functions that install nothing else.

    from chuang_platform_kit import github as gh
    tok = gh.token()                                   # GH_TOKEN / GITHUB_TOKEN, else the signed-in gh's
    commits = gh.compare(tok, 'owner/repo', base, head)
    url = gh.create_release(tok, 'owner/repo', 'v1', head, 'Release v1', 'notes')
    gh.dispatch(tok, 'owner/repo', 'deploy.yml', 'main', {'component': 'api'})

A refusal is GitHubError with the status and GitHub's own message; a 401 or 403 names the token permission the call
needs (`NEEDS`), so a missing right reads as one line. Nothing here prints or logs a token.
"""
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from . import keepalive

API = 'https://api.github.com'
TIMEOUT = 20
TOKEN_ENV = ('GH_TOKEN', 'GITHUB_TOKEN')
# The fine-grained permission each kind of call needs, for a refusal's message.
NEEDS = {'releases': 'Contents: read and write', 'git': 'Contents: read', 'compare': 'Contents: read',
         'issues': 'Issues: read and write', 'labels': 'Issues: read and write',
         'actions/runners': 'Administration: read', 'actions': 'Actions: read and write',
         'deployments': 'Deployments: read and write', 'settings/billing': 'Plan: read (an account permission)',
         'actions/variables': 'Variables: read and write'}


class GitHubError(RuntimeError):
    """A call GitHub refused or could not answer: `status` (0 for no answer) and GitHub's `message`."""

    def __init__(self, status: int, message: str, path: str = ''):
        super().__init__(f'GitHub {path}: HTTP {status}' + (f' ({message})' if message else ''))
        self.status, self.message, self.path = status, message, path


def token(env=None, run=subprocess.run) -> str:
    """The token: GH_TOKEN or GITHUB_TOKEN in the environment, else the signed-in `gh`'s (a desktop); '' with none."""
    env = os.environ if env is None else env
    for name in TOKEN_ENV:
        if (env.get(name) or '').strip():
            return env[name].strip()
    try:
        got = run(['gh', 'auth', 'token'], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ''
    return got.stdout.strip() if got.returncode == 0 else ''


def _needs(path: str) -> str:
    for key, need in NEEDS.items():
        if f'/{key}' in path:
            return need
    return ''


def request(tok: str, method: str, path: str, body=None, opener=None, timeout: int = TIMEOUT, accept: str = ''):
    """(status, parsed JSON or text) for one call; `path` relative to the API ("/repos/o/r/issues/1") or a full URL.
    GitHubError on a refusal (4xx/5xx) or no answer (status 0)."""
    url = path if path.startswith('http') else API + path
    headers = {'Accept': accept or 'application/vnd.github+json', 'User-Agent': 'chuang-platform-kit',
               'X-GitHub-Api-Version': '2022-11-28'}
    if tok:
        headers['Authorization'] = f'Bearer {tok}'
    data = json.dumps(body).encode('utf-8') if body is not None else None
    if data is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with (opener or keepalive.urlopen)(req, timeout=timeout) as r:   # kept-alive: 159 ms off a warm call (#562)
            text, status = r.read().decode('utf-8', 'replace'), getattr(r, 'status', 200)
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'replace') if e.fp else ''
        try:
            msg = json.loads(raw).get('message', '') if raw else ''
        except ValueError:
            msg = raw[:200]
        if e.code in (401, 403) and _needs(path):
            msg = f"{msg}; the token needs {_needs(path)}" if msg else f'the token needs {_needs(path)}'
        raise GitHubError(e.code, msg, path.split('?')[0]) from None
    except (urllib.error.URLError, OSError) as e:
        raise GitHubError(0, f'{type(e).__name__}: {e}', path.split('?')[0]) from None
    if not text:
        return status, {}
    try:
        return status, json.loads(text)
    except ValueError:
        return status, text


def get(tok, path, opener=None, **kw):
    """The JSON of a GET."""
    return request(tok, 'GET', path, opener=opener, **kw)[1]


def issue(tok, repo, number, opener=None) -> dict:
    """{'number', 'title', 'labels' (lower-case names), 'state'}; GitHubError (404) when there is none."""
    got = get(tok, f'/repos/{repo}/issues/{int(number)}', opener)
    return {'number': int(number), 'title': str(got.get('title') or '').strip(), 'state': got.get('state'),
            'labels': [str(lb.get('name', '')).lower() for lb in got.get('labels') or [] if isinstance(lb, dict)]}


def create_issue(tok, repo, title, body='', labels=(), opener=None) -> dict:
    """The new issue's {'number', 'html_url'}."""
    got = request(tok, 'POST', f'/repos/{repo}/issues', {'title': title, 'body': body, 'labels': list(labels)},
                  opener)[1]
    return {'number': got.get('number'), 'html_url': got.get('html_url', '')}


def comment(tok, repo, number, body, opener=None) -> str:
    """The comment's URL."""
    return request(tok, 'POST', f'/repos/{repo}/issues/{int(number)}/comments', {'body': body}, opener)[1].get('html_url', '')


def compare(tok, repo, base, head, opener=None) -> list:
    """[{'sha', 'message'}] after `base` up to `head`, oldest first (GitHub's compare: 250 at most)."""
    got = get(tok, f'/repos/{repo}/compare/{base}...{head}', opener)
    return [{'sha': c.get('sha', ''), 'message': (c.get('commit') or {}).get('message', '')}
            for c in got.get('commits') or []]


def create_release(tok, repo, tag, target, name, body, latest=True, opener=None) -> str:
    """The release's URL: created at `target` with `tag`, or the existing release of that tag (a second call adds
    nothing)."""
    try:
        got = request(tok, 'POST', f'/repos/{repo}/releases', {'tag_name': tag, 'target_commitish': target,
                      'name': name, 'body': body, 'make_latest': 'true' if latest else 'false'}, opener)[1]
        return got.get('html_url', '')
    except GitHubError as e:
        if e.status != 422:
            raise
    return get(tok, f'/repos/{repo}/releases/tags/{urllib.parse.quote(tag, safe="")}', opener).get('html_url', '')


def dispatch(tok, repo, workflow, ref='main', inputs=None, opener=None) -> None:
    """Start `workflow` (its file name or id) on `ref` with `inputs`; GitHub answers 204 and no run id."""
    request(tok, 'POST', f'/repos/{repo}/actions/workflows/{workflow}/dispatches',
            {'ref': ref, 'inputs': {k: str(v) for k, v in (inputs or {}).items()}}, opener)


def runners(tok, repo, opener=None) -> list:
    """The repository's self-hosted runners: [{'name', 'status', 'busy', 'labels' (names)}]."""
    got = get(tok, f'/repos/{repo}/actions/runners?per_page=100', opener)
    return [{'name': r.get('name'), 'status': r.get('status'), 'busy': r.get('busy'),
             'labels': [lb.get('name') for lb in r.get('labels') or [] if isinstance(lb, dict)]}
            for r in got.get('runners') or []]


def runner_online(tok, repo, label, opener=None) -> bool:
    """Whether a runner with `label` is online; False when the list cannot be read."""
    try:
        return any(r['status'] == 'online' and label in r['labels'] for r in runners(tok, repo, opener))
    except GitHubError:
        return False


def create_deployment(tok, repo, ref, environment, description='', opener=None) -> int:
    """A deployment record's id (no merge check, no required contexts: a record, never a gate)."""
    got = request(tok, 'POST', f'/repos/{repo}/deployments',
                  {'ref': ref, 'environment': environment, 'description': description, 'auto_merge': False,
                   'required_contexts': [], 'transient_environment': False}, opener)[1]
    return int(got.get('id') or 0)


def deployment_status(tok, repo, deployment_id, state, environment_url='', description='', opener=None) -> None:
    """A deployment's state: in_progress, success, failure, error or inactive."""
    body = {'state': state, 'description': description[:140]}
    if environment_url:
        body['environment_url'] = environment_url
    request(tok, 'POST', f'/repos/{repo}/deployments/{int(deployment_id)}/statuses', body, opener)
