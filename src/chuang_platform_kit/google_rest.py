"""Google's REST APIs without the client libraries: one JSON call with Google's own error, and Cloud Tasks, Cloud Scheduler
and Cloud Run on it.

Lifted from SMAD PickleBot (its #1016), where thirteen copies of one call had grown up beside each other: a Request with
a bearer token and a JSON body, the answer parsed, an HTTPError read for Google's `error.message`, a dropped connection
named by its kind. `call()` is that one copy; the three helper groups below use it and nothing else.

    from chuang_platform_kit import google_rest as G
    G.configure(allow_env='MYPROJECT_ALLOW_LIVE')            # once, in the project's own config module
    svc = G.call('GET', 'https://run.googleapis.com/v2/projects/p/locations/r/services/s', token)
    G.create_task(G.queue_path('p', 'r', 'q'), G.http_task('https://x.example/hook', name='t-1', oidc=(sa, aud)), token)
    G.run_job('p', 'r', 'my-job', ['--task', 'billing'], token=token)

**Errors.** A refusal raises `GoogleError` (or the caller's `error=` class) with `status` (Google's HTTP code, 0 when no
answer came), `method`, `url` and Google's `message`; its text is `<METHOD> <path>: HTTP <code> <message>` (the path after
`googleapis.com/`, at most 80 characters), or `<host> could not be reached (<kind>)`. Never a token.

**The live guard.** A call that would change something in production is refused before it asks for a token unless this
process is where a real call is meant: on Cloud Run (the platform sets K_SERVICE in a service or function, CLOUD_RUN_JOB in
a job), or a run by hand that sets the project's opt-in variable to `1`. The variable's name is the project's, given once
with `configure(allow_env=)`; the kit names none, so with no `configure` only Cloud Run is live. Only the real transport is
guarded (urllib.request.urlopen as this module imported it, or keepalive.urlopen): a caller's own `opener`, or urlopen
patched by a test, is a stand-in and goes. `guard=` picks what is guarded: 'writes' (the default: every method but GET and
HEAD), 'all' (reads too), or 'none' (a caller that means to write from anywhere, say so where it calls). A refusal is
`RefusedHere` (or the caller's `refused=` class), a configuration fault no retry fixes. Why: on 10/23/26 a test that forgot
its stand-in started fourteen production jobs from a desktop, gcloud handing it a production token.

The token is a string, a callable asked once per call, or None for `google_auth.access_token()` (asked only after the
guard). Standard library only.
"""
import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import keepalive

TIMEOUT_SECONDS = 15
TASKS_API = 'https://cloudtasks.googleapis.com/v2'
SCHEDULER_API = 'https://cloudscheduler.googleapis.com/v1'
RUN_API = 'https://run.googleapis.com/v2'
CLOUD_PLATFORM = 'https://www.googleapis.com/auth/cloud-platform'
PLATFORM_ENV = ('K_SERVICE', 'CLOUD_RUN_JOB')   # set by Cloud Run in a service or function, and in a job
_READS = ('GET', 'HEAD')
_GUARDS = ('writes', 'all', 'none')
_REAL_TRANSPORTS = (urllib.request.urlopen, keepalive.urlopen)   # as imported: anything else is a stand-in
_CONFIG = {'allow_env': '', 'platform_env': PLATFORM_ENV}


class GoogleError(RuntimeError):
    """A Google REST call was refused or could not be made: `status` (the HTTP code, 0 for no answer or no token),
    `method`, `url` and Google's own `message`; the text names which call and why, never a token."""

    def __init__(self, text: str, status: int = 0, method: str = '', url: str = '', message: str = ''):
        super().__init__(text)
        self.status, self.method, self.url, self.message = status, method, url, message


class RefusedHere(GoogleError):
    """A real call refused before it was made, because this process is not where one is meant (`live_allowed`): a
    configuration fault no retry fixes, so a caller logs it at ERROR."""


def configure(allow_env: str = None, platform_env=None) -> None:
    """Name the project's opt-in variable for a real call off Cloud Run (`allow_env`, read as on when it is `1`) and,
    rarely, the platform variables that mean "on Cloud Run" (`platform_env`, K_SERVICE and CLOUD_RUN_JOB by default).
    Called once from the project's own config module; a value left None keeps what is set."""
    if allow_env is not None:
        _CONFIG['allow_env'] = allow_env
    if platform_env is not None:
        _CONFIG['platform_env'] = tuple(platform_env)


def live_allowed(env=None) -> bool:
    """True where a real call is meant: a platform variable is set (on Cloud Run) or the configured opt-in variable is
    `1`. `env` is os.environ unless given."""
    env = os.environ if env is None else env
    if any(env.get(k) for k in _CONFIG['platform_env']):
        return True
    name = _CONFIG['allow_env']
    return bool(name) and env.get(name) == '1'


def _where(url: str) -> str:
    return url.split('googleapis.com/', 1)[-1][:80]


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc or url


def _raise(cls, text, **kw):
    if cls is None:
        cls = GoogleError
    try:
        err = cls(text, **kw)
    except TypeError:   # a caller's own class that takes the text alone
        err = cls(text)
        for k, v in kw.items():
            setattr(err, k, v)
    raise err from None


def call(method: str, url: str, token=None, body=None, *, opener=None, timeout: float = TIMEOUT_SECONDS,
         guard: str = 'writes', label: str = None, what: str = 'Google call', error=None, refused=None) -> dict:
    """One Google REST call, JSON both ways: `body` (a dict, or None for no body) sent as JSON, the answer parsed ({} for
    an empty one). The live guard first (module docstring; `guard` 'writes', 'all' or 'none'), then the token (a string,
    a callable, or None for google_auth.access_token). A refusal raises `error` (GoogleError by default) with Google's
    status and message, no answer the same with status 0, a guarded call off Cloud Run `refused` (RefusedHere). `label`
    replaces `<METHOD> <path>` at the head of an error's text; `what` names the call in a refusal ("job start")."""
    method = method.upper()
    if guard not in _GUARDS:
        raise ValueError(f'guard must be one of {", ".join(_GUARDS)}, not {guard!r}')
    head = label or f'{method} {_where(url)}'
    opener = opener or urllib.request.urlopen
    guarded = guard == 'all' or (guard == 'writes' and method not in _READS)
    if guarded and any(opener is real for real in _REAL_TRANSPORTS) and not live_allowed():
        hint = f' (set {_CONFIG["allow_env"]}=1 for a run by hand)' if _CONFIG['allow_env'] else ''
        _raise(refused or RefusedHere, f'{head}: a real {what} refused off Cloud Run{hint}', method=method, url=url)
    if token is None:
        from . import google_auth
        token = google_auth.access_token()
    elif callable(token):
        token = token()
    if not token:
        _raise(error, f'{head}: no Google Cloud token (no metadata server and no gcloud)', method=method, url=url)
    data = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
    try:
        with opener(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        message = ''
        try:
            message = (json.loads(e.read().decode('utf-8') or '{}').get('error') or {}).get('message', '') or ''
        except Exception:   # noqa: BLE001 - not Google's JSON: the status alone
            pass
        _raise(error, f'{head}: HTTP {e.code} {message}'.strip(), status=e.code, method=method, url=url, message=message)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        prefix = f'{label}: ' if label else ''
        _raise(error, f'{prefix}{_host(url)} could not be reached ({type(e).__name__})', method=method, url=url)
    text = raw.decode('utf-8') if isinstance(raw, bytes) else str(raw or '')
    return json.loads(text) if text.strip() else {}


def list_all(url: str, key: str, token=None, *, page_size: int = 100, limit: int = None, **kw) -> list:
    """Every item under `key` of a Google list answer, following `nextPageToken` (pageSize and pageToken added to `url`'s
    query), up to `limit` items when given. `kw` goes to `call` (opener, timeout, guard, error...). A GET, so the default
    guard lets it read from anywhere."""
    out, page = [], ''
    sep = '&' if '?' in url else '?'
    if callable(token):
        token = token()
    while True:
        q = {'pageSize': page_size, **({'pageToken': page} if page else {})}
        got = call('GET', f'{url}{sep}{urllib.parse.urlencode(q)}', token, **kw)
        out += got.get(key) or []
        page = got.get('nextPageToken') or ''
        if not page or (limit is not None and len(out) >= limit):
            return out[:limit] if limit is not None else out


def _b64(body) -> str:
    if isinstance(body, (dict, list)):
        body = json.dumps(body)
    if isinstance(body, str):
        body = body.encode('utf-8')
    return base64.b64encode(body or b'').decode('ascii')


def _auth(oidc=None, oauth=None) -> dict:
    if oidc and oauth:
        raise ValueError('a target takes oidc or oauth, not both')
    if oidc:
        sa, audience = oidc
        return {'oidcToken': {'serviceAccountEmail': sa, **({'audience': audience} if audience else {})}}
    if oauth:
        sa, scope = (oauth, CLOUD_PLATFORM) if isinstance(oauth, str) else oauth
        return {'oauthToken': {'serviceAccountEmail': sa, 'scope': scope or CLOUD_PLATFORM}}
    return {}


def _stamp(at) -> str:
    if isinstance(at, datetime):
        at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)   # utc-ok: a naive instant is taken as UTC
        return at.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')   # utc-ok: Cloud Tasks' RFC 3339 stamp
    return str(at)


# ------------------------------------------------------------------------------------------------- Cloud Tasks

def queue_path(project: str, location: str, queue: str) -> str:
    """A queue's resource name, `projects/<p>/locations/<l>/queues/<q>`."""
    return f'projects/{project}/locations/{location}/queues/{queue}'


def task_name(queue: str, name: str) -> str:
    """A task's full resource name from its queue and its short name; a name already full is kept."""
    return name if name.startswith('projects/') else f'{queue}/tasks/{name}'


def http_task(url: str, body=None, *, queue: str = '', name: str = '', at=None, method: str = 'POST',
              headers: dict = None, oidc=None, oauth=None) -> dict:
    """A Cloud Tasks task that sends one HTTP request: `body` (bytes, str, or a dict sent as JSON) base64'd, `headers`
    (JSON's content type by default), due `at` (a datetime, naive read as UTC, or Google's own stamp; now when None),
    named `name` in `queue` when given (Cloud Tasks then refuses the same name twice: 409, `create_task`'s None), and
    signed with `oidc=(service account, audience)` or `oauth=(service account, scope)` (or the account alone for
    cloud-platform)."""
    req = {'url': url, 'httpMethod': method.upper(), 'headers': headers or {'Content-Type': 'application/json'}}
    if body is not None:
        req['body'] = _b64(body)
    req.update(_auth(oidc, oauth))
    task = {'httpRequest': req}
    if name:
        if not queue and not name.startswith('projects/'):
            raise ValueError('a named task needs its queue')
        task['name'] = task_name(queue, name)
    if at is not None:
        task['scheduleTime'] = _stamp(at)
    return task


def create_task(queue: str, task: dict, token=None, *, exists_ok: bool = True, **kw):
    """Queue `task` (the task itself, or a body already shaped {'task': ...}) on `queue` (its resource name): the created
    task as Google answers it, or None when a task of its name is already there or has just run (409) and `exists_ok`.
    GoogleError for any other refusal; `kw` goes to `call` (a POST, so off Cloud Run it is refused on the real
    transport)."""
    body = task if 'task' in task else {'task': task}
    try:
        return call('POST', f'{TASKS_API}/{queue}/tasks', token, body, **kw)
    except Exception as e:   # noqa: BLE001 - GoogleError or the caller's `error=` class; a refusal carries status 0
        if exists_ok and getattr(e, 'status', 0) == 409:
            return None
        raise


def delete_task(name: str, token=None, *, queue: str = '', **kw) -> bool:
    """Take one task off its queue: `name` full, or short with its `queue`. True when deleted, False when there was none
    (404: it ran, or was deleted). GoogleError for any other refusal."""
    try:
        call('DELETE', f'{TASKS_API}/{task_name(queue, name)}', token, **kw)
    except Exception as e:   # noqa: BLE001 - GoogleError or the caller's `error=` class; a refusal carries status 0
        if getattr(e, 'status', 0) == 404:
            return False
        raise
    return True


def list_tasks(queue: str, token=None, **kw) -> list:
    """Every task waiting on `queue`, every page (Cloud Tasks tasks.list), as Google gives them; their `name`s are what
    `delete_task` takes."""
    return list_all(f'{TASKS_API}/{queue}/tasks', 'tasks', token, **kw)


# ------------------------------------------------------------------------------------------------- Cloud Scheduler

def location_path(project: str, location: str) -> str:
    """A location's resource name, `projects/<p>/locations/<l>`: Cloud Scheduler's parent of its jobs."""
    return f'projects/{project}/locations/{location}'


def scheduler_job_name(parent: str, job_id: str) -> str:
    """A scheduler job's full resource name from its parent and its id; a name already full is kept."""
    return job_id if job_id.startswith('projects/') else f'{parent}/jobs/{job_id}'


def http_job(uri: str, schedule: str, *, name: str = '', parent: str = '', time_zone: str = 'UTC', body=None,
             method: str = 'POST', headers: dict = None, oidc=None, oauth=None, description: str = '',
             retry_count: int = None, max_retry_seconds: int = None) -> dict:
    """A Cloud Scheduler job (REST's shape) that sends one HTTP request on `schedule` (cron) in `time_zone` (a zone the
    caller names; the kit assumes none but UTC): `body` (bytes, str or a dict sent as JSON), `headers`, signed with
    `oidc=(account, audience)` or `oauth=(account, scope)`, retried `retry_count` times within `max_retry_seconds`."""
    target = {'uri': uri, 'httpMethod': method.upper(), 'headers': headers or {'Content-Type': 'application/json'}}
    if body is not None:
        target['body'] = _b64(body)
    target.update(_auth(oidc, oauth))
    job = {'schedule': schedule, 'timeZone': time_zone, 'httpTarget': target}
    if name:
        if not parent and not name.startswith('projects/'):
            raise ValueError('a named job needs its parent')
        job['name'] = scheduler_job_name(parent, name)
    if description:
        job['description'] = description
    retry = {}
    if retry_count is not None:
        retry['retryCount'] = int(retry_count)
    if max_retry_seconds is not None:
        retry['maxRetryDuration'] = f'{int(max_retry_seconds)}s'
    if retry:
        job['retryConfig'] = retry
    return job


def create_job(parent: str, job: dict, token=None, *, replace: bool = False, **kw) -> dict:
    """Create the scheduler `job` under `parent`: the job as Google answers it. With `replace`, a job of the same name is
    deleted first (re-scheduling replaces it; none there is fine). GoogleError on a refusal (409 when it exists and
    `replace` is off)."""
    if replace and job.get('name'):
        delete_job(job['name'], token, **kw)
    return call('POST', f'{SCHEDULER_API}/{parent}/jobs', token, job, **kw)


def delete_job(name: str, token=None, *, parent: str = '', **kw) -> bool:
    """Delete one scheduler job: `name` full, or its id with `parent`. True when deleted, False when there was none
    (404). GoogleError for any other refusal."""
    try:
        call('DELETE', f'{SCHEDULER_API}/{scheduler_job_name(parent, name)}', token, **kw)
    except Exception as e:   # noqa: BLE001 - GoogleError or the caller's `error=` class; a refusal carries status 0
        if getattr(e, 'status', 0) == 404:
            return False
        raise
    return True


def get_job(name: str, token=None, *, parent: str = '', **kw) -> dict:
    """One scheduler job as Google gives it (`name` full, or its id with `parent`)."""
    return call('GET', f'{SCHEDULER_API}/{scheduler_job_name(parent, name)}', token, **kw)


def list_jobs(parent: str, token=None, **kw) -> list:
    """Every scheduler job under `parent`, every page (500 a page unless `page_size` says otherwise)."""
    kw.setdefault('page_size', 500)
    return list_all(f'{SCHEDULER_API}/{parent}/jobs', 'jobs', token, **kw)


# ------------------------------------------------------------------------------------------------- Cloud Run

def run_job_url(project: str, region: str, job: str) -> str:
    """The Cloud Run Admin API's address of a job (v2): `.../jobs/<job>`; `:run`, `/executions` go after it."""
    return f'{RUN_API}/projects/{project}/locations/{region}/jobs/{job}'


def run_url(project: str, region: str, job: str) -> str:
    """The Cloud Run Admin API's run endpoint for `job` (what a scheduler job or task POSTs to start it)."""
    return run_job_url(project, region, job) + ':run'


def service_url(project: str, region: str, service: str) -> str:
    """The Cloud Run Admin API's address of a service (v2)."""
    return f'{RUN_API}/projects/{project}/locations/{region}/services/{service}'


def run_body(args=None, env: dict = None, timeout_seconds: int = None) -> dict:
    """The `:run` request body: `args` (a list handed to the container as its argv, never a shell line; it replaces ALL
    of the job's deployed arguments, so a job deployed as `python -u <file>` needs its file first) and `env` as one
    container override, `timeout_seconds` this execution's task limit; {} with none of them (the deployed job).
    TypeError when an argument is not a string."""
    override = {}
    if args is not None:
        args = list(args)
        bad = [a for a in args if not isinstance(a, str)]
        if bad:
            raise TypeError(f'every argument must be a string, not {type(bad[0]).__name__}')
        override['args'] = args
    if env:
        pairs = [{'name': k, 'value': str(v)} for k, v in env.items() if v not in (None, '')]
        if pairs:
            override['env'] = pairs
    body = {}
    if override:
        body['containerOverrides'] = [override]
    if timeout_seconds:
        body['timeout'] = f'{int(timeout_seconds)}s'
    return {'overrides': body} if body else {}


def console_url(project: str, region: str, execution: str) -> str:
    """An execution's page in the Cloud console (its tasks and their logs), or '' for no execution."""
    if not execution:
        return ''
    short = execution.rsplit('/', 1)[-1]
    return f'https://console.cloud.google.com/run/jobs/executions/details/{region}/{short}/tasks?project={project}'


def run_job(project: str, region: str, job: str, args=None, env: dict = None, timeout_seconds: int = None,
            token=None, **kw) -> dict:
    """Start one execution of `job` (run_body's `args`, `env`, `timeout_seconds`): {'job', 'execution' (its short name,
    '' when the answer named none), 'url' (console_url)}. Guarded on every transport but a stand-in (a start is a
    write); GoogleError headed by the job's name on a refusal or no answer, RefusedHere off Cloud Run."""
    kw.setdefault('label', job)
    kw.setdefault('what', 'job start')
    kw.setdefault('guard', 'all')
    op = call('POST', run_url(project, region, job), token, run_body(args, env, timeout_seconds), **kw)
    execution = ((op.get('metadata') or {}).get('name') or '').rsplit('/', 1)[-1]
    return {'job': job, 'execution': execution, 'url': console_url(project, region, execution)}


def get_job_spec(project: str, region: str, job: str, token=None, **kw) -> dict:
    """One Cloud Run job as Google gives it (its template, image and settings)."""
    return call('GET', run_job_url(project, region, job), token, **kw)


def get_service(project: str, region: str, service: str, token=None, **kw) -> dict:
    """One Cloud Run service as Google gives it (v2: its template and `trafficStatuses`)."""
    return call('GET', service_url(project, region, service), token, **kw)


def list_executions(project: str, region: str, job: str, token=None, *, limit: int = None, **kw) -> list:
    """A job's executions, newest first as Google lists them, up to `limit` when given (one page of `limit` then)."""
    if limit is not None:
        kw.setdefault('page_size', limit)
    return list_all(run_job_url(project, region, job) + '/executions', 'executions', token, limit=limit, **kw)


def get_execution(project: str, region: str, job: str, execution: str, token=None, **kw) -> dict:
    """One execution of a job (its short name, or the full one) as Google gives it."""
    short = execution.rsplit('/', 1)[-1]
    return call('GET', f'{run_job_url(project, region, job)}/executions/{short}', token, **kw)


def execution_state(ex: dict) -> str:
    """'running', 'succeeded', 'failed' or 'cancelled', from an execution's counts and its completion time."""
    if not ex.get('completionTime'):
        return 'running'
    if ex.get('cancelledCount'):
        return 'cancelled'
    if ex.get('failedCount'):
        return 'failed'
    return 'succeeded'
