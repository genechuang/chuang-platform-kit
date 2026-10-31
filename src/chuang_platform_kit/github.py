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

**The throttle** (Gene, 10/31/26 12:29 PM PT: "at least the 2nd time broke github GraphQL quota by runaway queries ...
add a throttle mechanism so we don't have this problem again"; a board watch spent an hour's 5,000 GraphQL points in
a minute of reads on 10/27/26, and a retried event searched issues 300 times a minute into the search quota's 403 on
10/31/26): every call through `request`, `graphql` or `cli` draws on one of GitHub's rate-limit resources (core,
search, graphql, code_search). The client keeps GitHub's own count of the token from every answer's `X-RateLimit-*`
headers, the truth shared by every process that uses the token, and refuses a call locally, with no network call, once
the resource is under its `FLOOR` (what a person's gh and the hourly sweeps keep), spent (a 403 or 429 with remaining
0, a `Retry-After`, or a message naming a rate limit), or over this process's `LOCAL_CAP` a minute, so a loop in one
process is stopped by its client before GitHub sees it. The refusal is `RateLimited`, a GitHubError with status 429,
the resource, `remaining` and `reset_at`; nothing is retried here, and `wait_until` is for a script that can sleep.
"""
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import keepalive

API = 'https://api.github.com'
TIMEOUT = 20
TOKEN_ENV = ('GH_TOKEN', 'GITHUB_TOKEN')
# GitHub's rate-limit resources for one user token (core 5,000 an hour, search 30 a minute, graphql 5,000 points an
# hour, code_search 10 a minute): the floor kept for people and the hourly sweeps, and the calls one process may send
# a minute. A project with a reason sets them on the module before its first call.
FLOOR = {'core': 100, 'search': 3, 'graphql': 250, 'code_search': 2}
LOCAL_CAP = {'core': 120, 'search': 10, 'graphql': 60, 'code_search': 5}
SPENT_FOR = 60          # seconds a resource stays spent after a refusal naming a rate limit with no reset to read
RATE_WORDS = re.compile(r'rate limit', re.I)
_lock = threading.Lock()
_limits = {}            # resource -> {'remaining', 'reset' (epoch seconds), 'limit'}: GitHub's count, from its headers
_spent = {}             # resource -> epoch seconds until which no call is sent
_recent = {}            # resource -> the epoch seconds of this process's calls in the last minute
_now = time.time        # the clock, a seam for the suite
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


class RateLimited(GitHubError):
    """A call not sent, or refused by GitHub, because `resource`'s quota is spent, under its floor or over this
    process's cap: `remaining` (GitHub's count, -1 unknown) and `reset_at` (epoch seconds) say when it returns. Status
    429 whatever GitHub answered, so an `except GitHubError` covers it; nothing is retried here."""

    def __init__(self, resource: str, remaining: int, reset_at: float, why: str = '', path: str = ''):
        wait = max(0, int(reset_at - _now()))
        super().__init__(429, f"{resource} rate limit: {why or 'spent'}, {remaining} left, back in {wait} s", path)
        self.resource, self.remaining, self.reset_at = resource, remaining, reset_at


def resource_of(path: str) -> str:
    """GitHub's rate-limit resource a path draws on: graphql, code_search, search or core."""
    p = path.split('?')[0]
    if '/graphql' in p:
        return 'graphql'
    if '/search/code' in p:
        return 'code_search'
    if '/search/' in p:
        return 'search'
    return 'core'


def _check(resource: str, path: str = '') -> None:
    """Before a call: RateLimited, with no network call, when the resource is spent, under its floor or over this
    process's cap for the minute; otherwise the call is counted."""
    now = _now()
    with _lock:
        lim = _limits.get(resource) or {}
        until = _spent.get(resource, 0)
        if until > now:
            raise RateLimited(resource, lim.get('remaining', 0), until, 'spent', path)
        if lim and lim['remaining'] <= FLOOR.get(resource, 0) and lim['reset'] > now:
            raise RateLimited(resource, lim['remaining'], lim['reset'], f"under the floor of {FLOOR.get(resource, 0)}", path)
        recent = [t for t in _recent.get(resource, ()) if t > now - 60]
        if len(recent) >= LOCAL_CAP.get(resource, 120):
            _recent[resource] = recent
            raise RateLimited(resource, lim.get('remaining', -1), recent[0] + 60,
                              f'this process sent {len(recent)} in a minute, its cap', path)
        recent.append(now)
        _recent[resource] = recent


def _note(resource: str, headers, status: int = 200, message: str = '') -> str:
    """GitHub's count from an answer's headers (its X-RateLimit-Resource names the resource when it differs from the
    path's); a 403 or 429 with remaining 0, a Retry-After or a message naming a rate limit marks the resource spent.
    The resource's name when it is spent, '' otherwise."""
    h = {}
    try:
        h = {str(k).lower(): str(v) for k, v in (headers.items() if headers is not None else ())}
    except (AttributeError, TypeError):
        pass
    res = h.get('x-ratelimit-resource') or resource
    now = _now()
    with _lock:
        try:
            _limits[res] = {'remaining': int(h['x-ratelimit-remaining']), 'reset': float(h['x-ratelimit-reset']),
                            'limit': int(h.get('x-ratelimit-limit') or 0)}
        except (KeyError, ValueError):
            pass
        if status in (403, 429):
            retry, lim = h.get('retry-after', ''), _limits.get(res) or {}
            if retry.isdigit():
                _spent[res] = now + int(retry)
            elif lim.get('remaining') == 0 and lim.get('reset', 0) > now:
                _spent[res] = lim['reset']
            elif RATE_WORDS.search(message or ''):
                _spent[res] = now + SPENT_FOR
        return res if _spent.get(res, 0) > now else ''


def limits() -> dict:
    """GitHub's last count per resource as the answers gave it: {resource: {'remaining', 'reset', 'limit'}}, a copy."""
    with _lock:
        return {k: dict(v) for k, v in _limits.items()}


def reset_limits() -> None:
    """Forget every count, spent mark and cap (a suite's seam; nothing in production calls it)."""
    with _lock:
        _limits.clear()
        _spent.clear()
        _recent.clear()


def wait_until(resource: str, cap: float = 120, sleep=time.sleep) -> float:
    """For a script that can afford it: sleep until `resource` is back (its spent mark, or its reset while it is under
    the floor), at most `cap` seconds; the seconds slept."""
    now = _now()
    with _lock:
        lim = _limits.get(resource) or {}
        until = _spent.get(resource, 0)
        if lim and lim.get('remaining', 1) <= FLOOR.get(resource, 0):
            until = max(until, lim.get('reset', 0))
    wait = min(max(0.0, until - now), cap)
    if wait:
        sleep(wait)
    return wait


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
    GitHubError on a refusal (4xx/5xx) or no answer (status 0); RateLimited, before any network call, when the path's
    rate-limit resource is spent, under its floor or over this process's cap, and after a 403 or 429 that spends it."""
    url = path if path.startswith('http') else API + path
    short, resource = path.split('?')[0], resource_of(path)
    _check(resource, short)
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
            _note(resource, getattr(r, 'headers', None), status)
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'replace') if e.fp else ''
        try:
            msg = json.loads(raw).get('message', '') if raw else ''
        except ValueError:
            msg = raw[:200]
        spent = _note(resource, getattr(e, 'headers', None), e.code, msg)
        if spent:
            lim = _limits.get(spent) or {}
            raise RateLimited(spent, lim.get('remaining', 0), _spent.get(spent, _now() + SPENT_FOR), msg or f'HTTP {e.code}',
                              short) from None
        if e.code in (401, 403) and _needs(path):
            msg = f"{msg}; the token needs {_needs(path)}" if msg else f'the token needs {_needs(path)}'
        raise GitHubError(e.code, msg, short) from None
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


def graphql(tok: str, query: str, variables: dict = None, opener=None, timeout: int = TIMEOUT) -> dict:
    """The `data` of one GraphQL call (POST /graphql), through the throttle on the graphql resource; GitHubError with
    the first error's message when GitHub answers errors, RateLimited when that message names the rate limit."""
    status, body = request(tok, 'POST', '/graphql', {'query': query, 'variables': variables or {}}, opener, timeout)
    if not isinstance(body, dict):
        raise GitHubError(status, 'not JSON', '/graphql')
    if body.get('errors'):
        msg = str((body['errors'][0] or {}).get('message') or 'GraphQL error')[:200]
        if RATE_WORDS.search(msg):
            until = _now() + SPENT_FOR
            with _lock:
                _spent['graphql'] = until
            raise RateLimited('graphql', 0, until, msg, '/graphql')
        raise GitHubError(status, msg, '/graphql')
    return body.get('data') or {}


def cli(args, run=subprocess.run, timeout: int = 60, **kw) -> subprocess.CompletedProcess:
    """`gh <args>` through the same local cap, for a script or daemon on the CLI (gh hides GitHub's headers): the
    CompletedProcess; RateLimited before the run when the resource the arguments name (graphql, a search, else core)
    is spent or over this process's cap, and after a failed run whose stderr names a rate limit, the resource then spent
    for SPENT_FOR seconds. A missing gh is returncode 127, never an exception."""
    args = [str(a) for a in args]
    resource = ('graphql' if 'graphql' in args
                else 'search' if '--search' in args or any(a.startswith('search/') or '/search/' in a for a in args)
                else 'core')
    _check(resource, 'gh ' + ' '.join(args[:2]))
    try:
        got = run(['gh', *args], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout, **kw)
    except (OSError, subprocess.SubprocessError) as e:
        return subprocess.CompletedProcess(['gh', *args], 127, '', str(e))
    err = getattr(got, 'stderr', '') or ''
    if getattr(got, 'returncode', 0) and RATE_WORDS.search(err):
        until = _now() + SPENT_FOR
        with _lock:
            _spent[resource] = until
        raise RateLimited(resource, 0, until, err.strip()[:160], 'gh ' + ' '.join(args[:2]))
    return got


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
