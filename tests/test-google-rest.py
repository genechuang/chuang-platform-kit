#!/usr/bin/env python3
"""chuang_platform_kit.google_rest: one Google REST call with Google's error, and Cloud Tasks, Scheduler and Run on it, over fakes.

Pinned (the positive case first): call() sends the bearer token and a JSON body and parses the answer; a refusal is
GoogleError with Google's status and message, no answer status 0 naming the host and the error's kind; the token may be a
callable or None (google_auth.access_token, faked here); the live guard refuses a write on the real transport off Cloud
Run before it asks for a token, lets a read through, takes the project's opt-in variable from configure(), and never
stops a stand-in opener; `error=` and `refused=` take the caller's own classes; list_all() follows nextPageToken; the
Cloud Tasks, Scheduler and Run helpers send what Google expects and read 404 and 409 as the answers they are.

Run:  python -B tests/test-google-rest.py
"""
import io
import json
import os
import sys
import urllib.error
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import google_auth  # noqa: E402
from chuang_platform_kit import google_rest as G  # noqa: E402

fails = []
LIVE_VARS = ('K_SERVICE', 'CLOUD_RUN_JOB', 'KIT_TEST_ALLOW_LIVE')
for _k in LIVE_VARS:          # off Cloud Run for the whole suite, whatever box runs it
    os.environ.pop(_k, None)
REAL = G._REAL_TRANSPORTS


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake(calls, answers=None):
    """An opener answering by (method, url prefix): a dict or list is JSON, an int an HTTPError of that status (with
    Google's error body), an exception instance is raised; anything unmatched answers {}."""
    answers = answers or []

    def opener(req, timeout=None):
        body = json.loads(req.data.decode()) if req.data else None
        calls.append((req.get_method(), req.full_url, body, req.get_header('Authorization'),
                      req.get_header('Content-type'), timeout))
        for (method, prefix), answer in answers:
            if req.get_method() == method and req.full_url.startswith(prefix):
                if isinstance(answer, int):
                    raw = json.dumps({'error': {'code': answer, 'message': f'Google says {answer}'}}).encode()
                    raise urllib.error.HTTPError(req.full_url, answer, 'no', {}, io.BytesIO(raw))
                if isinstance(answer, BaseException):
                    raise answer
                if isinstance(answer, bytes):
                    return Resp(answer)
                return Resp(json.dumps(answer).encode())
        return Resp(b'{}')
    return opener


def raised(fn):
    try:
        fn()
        return None
    except Exception as e:   # noqa: BLE001
        return e


def reset():
    G._REAL_TRANSPORTS = REAL
    G._CONFIG.update({'allow_env': '', 'platform_env': G.PLATFORM_ENV})
    for k in LIVE_VARS:
        os.environ.pop(k, None)


URL = 'https://run.googleapis.com/v2/projects/p/locations/r/services/s'

print("call() sends the bearer token and a JSON body, and parses Google's answer")
calls = []
got = G.call('PUT', URL, 'tok', {'a': 1}, opener=fake(calls, [(('PUT', URL), {'name': 's', 'ok': True})]), timeout=7)
check("answer", got, {'name': 's', 'ok': True})
check("request", calls, [('PUT', URL, {'a': 1}, 'Bearer tok', 'application/json', 7)])
calls = []
check("empty answer is {}", G.call('DELETE', URL, 'tok', opener=fake(calls, [(('DELETE', URL), b'')])), {})
check("no body sent", calls[0][2], None)
check("method upper-cased", G.call('get', URL, 'tok', opener=fake(calls := [])) or calls[0][0], 'GET')

print("a refusal is GoogleError with Google's status and message; no answer is status 0 naming the host and kind")
e = raised(lambda: G.call('POST', URL, 'tok', {}, opener=fake([], [(('POST', URL), 409)])))
check("type", type(e), G.GoogleError)
check("text", str(e), 'POST v2/projects/p/locations/r/services/s: HTTP 409 Google says 409')
check("fields", (e.status, e.method, e.url, e.message), (409, 'POST', URL, 'Google says 409'))


def not_json(req, timeout=None):
    raise urllib.error.HTTPError(req.full_url, 502, 'bad', {}, io.BytesIO(b'<html>'))


e = raised(lambda: G.call('GET', URL, 'tok', opener=not_json))
check("not Google's JSON: the status alone", (str(e), e.status, e.message),
      ('GET v2/projects/p/locations/r/services/s: HTTP 502', 502, ''))
e = raised(lambda: G.call('GET', URL, 'tok', opener=fake([], [(('GET', URL), urllib.error.URLError('down'))])))
check("unreachable", (str(e), e.status), ('run.googleapis.com could not be reached (URLError)', 0))
e = raised(lambda: G.call('GET', URL, 'tok', label='my-job', opener=fake([], [(('GET', URL), TimeoutError())])))
check("unreachable with a label", str(e), 'my-job: run.googleapis.com could not be reached (TimeoutError)')
e = raised(lambda: G.call('GET', URL, 'tok', label='my-job', opener=fake([], [(('GET', URL), 404)])))
check("label heads a refusal", str(e), 'my-job: HTTP 404 Google says 404')
check("no token in any error text", 'tok' in ' '.join(str(x) for x in [e]), False)

print("the token: a callable asked per call, None for google_auth.access_token, empty an error")
calls = []
G.call('GET', URL, lambda: 'from-callable', opener=fake(calls))
check("callable", calls[0][3], 'Bearer from-callable')
keep = google_auth.access_token
google_auth.access_token = lambda *a, **k: 'from-google-auth'
try:
    calls = []
    G.call('GET', URL, opener=fake(calls))
    check("None asks google_auth", calls[0][3], 'Bearer from-google-auth')
finally:
    google_auth.access_token = keep
calls = []
e = raised(lambda: G.call('GET', URL, '', opener=fake(calls)))
check("empty token", (type(e), str(e), calls), (G.GoogleError, 'GET v2/projects/p/locations/r/services/s: no Google Cloud token (no metadata server and no gcloud)', []))
check("a bad guard name", type(raised(lambda: G.call('GET', URL, 't', guard='some', opener=fake([])))), ValueError)

print("the live guard: a write on the real transport off Cloud Run is refused before the token; a read goes")
reset()
asked = []
e = raised(lambda: G.call('POST', 'https://cloudtasks.googleapis.com/v2/q/tasks', lambda: asked.append(1) or 't', {}))
check("a forgotten opener: refused, no token asked", (type(e), str(e), asked),
      (G.RefusedHere, 'POST v2/q/tasks: a real Google call refused off Cloud Run', []))
calls = []
real_fake = fake(calls)
G._REAL_TRANSPORTS = REAL + (real_fake,)
check("a read on the real transport goes", (G.call('GET', URL, 't', opener=real_fake), len(calls)), ({}, 1))
check("guard='all' refuses the read", type(raised(lambda: G.call('GET', URL, 't', opener=real_fake, guard='all'))), G.RefusedHere)
check("guard='none' lets the write go", (G.call('POST', URL, 't', {}, opener=real_fake, guard='none'), len(calls)), ({}, 2))
check("a stand-in opener is never refused", G.call('POST', URL, 't', {}, opener=fake([])), {})
G.configure(allow_env='KIT_TEST_ALLOW_LIVE')
e = raised(lambda: G.call('DELETE', URL, 't', opener=real_fake))
check("the refusal names the project's variable", str(e),
      'DELETE v2/projects/p/locations/r/services/s: a real Google call refused off Cloud Run (set KIT_TEST_ALLOW_LIVE=1 for a run by hand)')
check("live_allowed", [G.live_allowed(env) for env in ({}, {'K_SERVICE': 'api'}, {'CLOUD_RUN_JOB': 'j'},
                                                       {'KIT_TEST_ALLOW_LIVE': '1'}, {'KIT_TEST_ALLOW_LIVE': 'yes'})],
      [False, True, True, True, False])
os.environ['KIT_TEST_ALLOW_LIVE'] = '1'
check("opted in, the write goes", G.call('DELETE', URL, 't', opener=real_fake), {})
reset()
check("unconfigured, no variable opts in", G.live_allowed({'KIT_TEST_ALLOW_LIVE': '1', '': '1'}), False)
os.environ['K_SERVICE'] = 'svc'
G._REAL_TRANSPORTS = REAL + (real_fake,)
check("on Cloud Run the write goes", G.call('POST', URL, 't', {}, opener=real_fake), {})
reset()

print("error= and refused= take the caller's own classes, with the status still on them")


class TaskFailed(Exception):
    pass


class TaskRefusedHere(TaskFailed):
    pass


e = raised(lambda: G.call('POST', URL, 't', {}, opener=fake([], [(('POST', URL), 503)]), error=TaskFailed))
check("own error class", (type(e), e.status, str(e)), (TaskFailed, 503, 'POST v2/projects/p/locations/r/services/s: HTTP 503 Google says 503'))
e = raised(lambda: G.call('POST', URL, 't', {}, error=TaskFailed, refused=TaskRefusedHere))
check("own refusal class", (type(e), isinstance(e, TaskFailed)), (TaskRefusedHere, True))

print("list_all() follows nextPageToken")
LIST = 'https://cloudtasks.googleapis.com/v2/q/tasks'
pages = {'': {'tasks': [{'name': 'a'}], 'nextPageToken': 'p2'}, 'p2': {'tasks': [{'name': 'b'}]}}
seen = []


def pager(req, timeout=None):
    seen.append(req.full_url)
    token = req.full_url.split('pageToken=')[1] if 'pageToken=' in req.full_url else ''
    return Resp(json.dumps(pages[token]).encode())


check("items", G.list_all(LIST, 'tasks', 't', opener=pager, page_size=2), [{'name': 'a'}, {'name': 'b'}])
check("urls", seen, [LIST + '?pageSize=2', LIST + '?pageSize=2&pageToken=p2'])
seen.clear()
check("limit stops paging", (G.list_all(LIST, 'tasks', 't', opener=pager, limit=1), len(seen)), ([{'name': 'a'}], 1))

print("Cloud Tasks: the task's shape, create (409 is None), delete (404 is False), list")
Q = G.queue_path('p', 'r', 'q')
check("queue path", Q, 'projects/p/locations/r/queues/q')
t = G.http_task('https://x.example/hook', {'seq': 3}, queue=Q, name='t-1', at=datetime(2026, 11, 6, 8, 5),
                oidc=('sa@p.iam', 'https://x.example/hook'))
check("task", t, {'httpRequest': {'url': 'https://x.example/hook', 'httpMethod': 'POST',
                                  'headers': {'Content-Type': 'application/json'}, 'body': 'eyJzZXEiOiAzfQ==',
                                  'oidcToken': {'serviceAccountEmail': 'sa@p.iam', 'audience': 'https://x.example/hook'}},
                  'name': 'projects/p/locations/r/queues/q/tasks/t-1', 'scheduleTime': '2026-11-06T08:05:00Z'})
t2 = G.http_task('https://run.example/x:run', b'{}', at=datetime(2026, 11, 6, 8, 5, tzinfo=timezone.utc), oauth='sa@p.iam')  # utc-ok: an instant
check("oauth task, no name", (t2['httpRequest']['oauthToken'], 'name' in t2, t2['scheduleTime']),
      ({'serviceAccountEmail': 'sa@p.iam', 'scope': G.CLOUD_PLATFORM}, False, '2026-11-06T08:05:00Z'))
check("both signers refused", type(raised(lambda: G.http_task('u', oidc=('a', 'b'), oauth='a'))), ValueError)
check("a named task needs its queue", type(raised(lambda: G.http_task('u', name='x'))), ValueError)
calls = []
TASKS = f'https://cloudtasks.googleapis.com/v2/{Q}/tasks'
got = G.create_task(Q, t, 'tok', opener=fake(calls, [(('POST', TASKS), {'name': t['name']})]))
check("created", (got, calls[0][:3]), ({'name': t['name']}, ('POST', TASKS, {'task': t})))
calls = []
G.create_task(Q, {'task': t}, 'tok', opener=fake(calls))
check("a body already shaped is sent as it is", calls[0][2], {'task': t})
check("409 is None", G.create_task(Q, t, 'tok', opener=fake([], [(('POST', TASKS), 409)])), None)
check("409 with exists_ok off raises", getattr(raised(lambda: G.create_task(Q, t, 'tok', exists_ok=False,
                                                                             opener=fake([], [(('POST', TASKS), 409)]))), 'status', None), 409)
check("409 under the caller's error class is still None",
      G.create_task(Q, t, 'tok', error=TaskFailed, opener=fake([], [(('POST', TASKS), 409)])), None)
check("another refusal raises", getattr(raised(lambda: G.create_task(Q, t, 'tok', opener=fake([], [(('POST', TASKS), 403)]))), 'status', None), 403)
check("a forgotten opener is refused", type(raised(lambda: G.create_task(Q, t, 'tok'))), G.RefusedHere)
calls = []
check("deleted by short name", (G.delete_task('t-1', 'tok', queue=Q, opener=fake(calls)), calls[0][:2]),
      (True, ('DELETE', TASKS + '/t-1')))
calls = []
check("deleted by full name", (G.delete_task(t['name'], 'tok', opener=fake(calls)), calls[0][1]), (True, TASKS + '/t-1'))
check("404 is False", G.delete_task('t-1', 'tok', queue=Q, opener=fake([], [(('DELETE', TASKS), 404)])), False)
check("delete's other refusal raises", getattr(raised(lambda: G.delete_task('t-1', 'tok', queue=Q, opener=fake([], [(('DELETE', TASKS), 500)]))), 'status', None), 500)
calls = []
check("list", (G.list_tasks(Q, 'tok', opener=fake(calls, [(('GET', TASKS), {'tasks': [{'name': 'n1'}]})])), calls[0][1]),
      ([{'name': 'n1'}], TASKS + '?pageSize=100'))

print("Cloud Scheduler: the job's shape, create (replace deletes first), delete (404 is False), get, list")
P = G.location_path('p', 'r')
JOBS = f'https://cloudscheduler.googleapis.com/v1/{P}/jobs'
j = G.http_job('https://run.example/j:run', '5 19 6 11 *', name='game-plan-1', parent=P, time_zone='Area/Zone',
               body=b'{}', oauth=('sa@p.iam', None), description='one', retry_count=3, max_retry_seconds=300)
check("job", j, {'schedule': '5 19 6 11 *', 'timeZone': 'Area/Zone',
                 'httpTarget': {'uri': 'https://run.example/j:run', 'httpMethod': 'POST',
                                'headers': {'Content-Type': 'application/json'}, 'body': 'e30=',
                                'oauthToken': {'serviceAccountEmail': 'sa@p.iam', 'scope': G.CLOUD_PLATFORM}},
                 'name': f'{P}/jobs/game-plan-1', 'description': 'one',
                 'retryConfig': {'retryCount': 3, 'maxRetryDuration': '300s'}})
calls = []
G.create_job(P, j, 'tok', replace=True, opener=fake(calls, [(('DELETE', JOBS), 404)]))
check("replace: delete then create", [c[:2] for c in calls], [('DELETE', JOBS + '/game-plan-1'), ('POST', JOBS)])
check("created body", calls[1][2], j)
calls = []
G.create_job(P, j, 'tok', opener=fake(calls))
check("no replace: create alone", [c[0] for c in calls], ['POST'])
check("delete by id", G.delete_job('game-plan-1', 'tok', parent=P, opener=fake(calls := [])) and calls[0][1], JOBS + '/game-plan-1')
check("scheduler 404 is False", G.delete_job(f'{P}/jobs/x', 'tok', opener=fake([], [(('DELETE', JOBS), 404)])), False)
check("get", G.get_job('x', 'tok', parent=P, opener=fake([], [(('GET', JOBS + '/x'), {'name': 'x'})])), {'name': 'x'})
calls = []
check("list, 500 a page", (G.list_jobs(P, 'tok', opener=fake(calls, [(('GET', JOBS), {'jobs': [{'name': 'a'}]})])), calls[0][1]),
      ([{'name': 'a'}], JOBS + '?pageSize=500'))

print("Cloud Run: the run body, a job start, executions, a service")
check("run body", G.run_body(['-u', 'x.py', '--a'], {'K': 'v', 'E': '', 'N': None}, 600),
      {'overrides': {'containerOverrides': [{'args': ['-u', 'x.py', '--a'], 'env': [{'name': 'K', 'value': 'v'}]}],
                     'timeout': '600s'}})
check("no overrides is {}", G.run_body(), {})
check("empty env leaves env out", G.run_body([], {'E': ''}), {'overrides': {'containerOverrides': [{'args': []}]}})
check("a non-string argument", type(raised(lambda: G.run_body(['a', 3]))), TypeError)
RUN = 'https://run.googleapis.com/v2/projects/p/locations/r/jobs/j'
check("urls", (G.run_url('p', 'r', 'j'), G.service_url('p', 'r', 's')), (RUN + ':run', URL))
calls = []
got = G.run_job('p', 'r', 'j', ['--x'], token='tok',
                opener=fake(calls, [(('POST', RUN + ':run'), {'metadata': {'name': 'projects/p/locations/r/jobs/j/executions/j-abc'}})]))
check("started", got, {'job': 'j', 'execution': 'j-abc',
                       'url': 'https://console.cloud.google.com/run/jobs/executions/details/r/j-abc/tasks?project=p'})
check("start request", calls[0][:3], ('POST', RUN + ':run', {'overrides': {'containerOverrides': [{'args': ['--x']}]}}))
check("no execution named", G.run_job('p', 'r', 'j', token='t', opener=fake([]))['execution'], '')
check("console url for none", G.console_url('p', 'r', ''), '')
e = raised(lambda: G.run_job('p', 'r', 'j', token='t', opener=fake([], [(('POST', RUN), 403)])))
check("a refused start names the job", str(e), 'j: HTTP 403 Google says 403')
G.configure(allow_env='KIT_TEST_ALLOW_LIVE')
asked = []
e = raised(lambda: G.run_job('p', 'r', 'j', ['--x'], token=lambda: asked.append(1) or 't'))
check("a forgotten opener: the start refused before the token", (type(e), str(e), asked),
      (G.RefusedHere, 'j: a real job start refused off Cloud Run (set KIT_TEST_ALLOW_LIVE=1 for a run by hand)', []))
reset()
calls = []
exs = G.list_executions('p', 'r', 'j', 'tok', limit=1,
                        opener=fake(calls, [(('GET', RUN + '/executions'), {'executions': [{'name': 'e1'}, {'name': 'e2'}], 'nextPageToken': 'z'})]))
check("one execution, one page", (exs, len(calls), calls[0][1]), ([{'name': 'e1'}], 1, RUN + '/executions?pageSize=1'))
calls = []
G.get_execution('p', 'r', 'j', 'projects/p/locations/r/jobs/j/executions/j-abc', 'tok', opener=fake(calls))
check("execution by full name", calls[0][1], RUN + '/executions/j-abc')
check("states", [G.execution_state(x) for x in ({}, {'completionTime': 't', 'cancelledCount': 1},
                                                {'completionTime': 't', 'failedCount': 1}, {'completionTime': 't'})],
      ['running', 'cancelled', 'failed', 'succeeded'])
check("service and job reads", (G.get_service('p', 'r', 's', 't', opener=fake([], [(('GET', URL), {'name': 's'})])),
                                G.get_job_spec('p', 'r', 'j', 't', opener=fake([], [(('GET', RUN), {'name': 'j'})]))),
      ({'name': 's'}, {'name': 'j'}))

print()
print(f"{len(fails)} failure(s)" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
