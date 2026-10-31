#!/usr/bin/env python3
"""chuang_platform_kit.github: one repository's REST calls with one token, over a fake API.

Pinned (the positive case first): compare() lists the commits oldest first with their messages; create_release()
creates at the target and, for a tag that exists (422), returns that release; issue() lower-cases the labels;
dispatch() posts the ref and string inputs; runner_online() finds an online runner by label and says False when the
list is refused; a 403 names the permission the call needs; no answer is status 0; token() takes GH_TOKEN, then
GITHUB_TOKEN, then gh's; the Authorization header carries the token and nothing is sent without one.

Run:  python -B tests/test-github.py
"""
import io
import json
import os
import sys
import urllib.error

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import github as GH  # noqa: E402

fails = []
REPO = 'owner/repo'


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class Resp(io.BytesIO):
    def __init__(self, status, body):
        super().__init__(json.dumps(body).encode() if body is not None else b'')
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake(routes, calls):
    """routes: [(method, path part, status, body)]; the first match answers; 4xx and 5xx are HTTPErrors."""
    def opener(req, timeout=None):
        calls.append((req.get_method(), req.full_url, json.loads(req.data) if req.data else None, dict(req.header_items())))
        for m, part, status, body in routes:
            if m == req.get_method() and part in req.full_url:
                if status >= 400:
                    raise urllib.error.HTTPError(req.full_url, status, 'x', {}, io.BytesIO(json.dumps(body or {}).encode()))
                return Resp(status, body)
        raise urllib.error.URLError('no route')
    return opener


calls = []
op = fake([('GET', '/compare/a...b', 200, {'commits': [{'sha': 's1', 'commit': {'message': 'one\n\nTicket: #1'}},
                                                     {'sha': 's2', 'commit': {'message': 'two'}}]})], calls)
check('the positive case: compare lists the commits oldest first with their messages, the token in Authorization',
      (GH.compare('tok', REPO, 'a', 'b', op), calls[0][3].get('Authorization')),
      ([{'sha': 's1', 'message': 'one\n\nTicket: #1'}, {'sha': 's2', 'message': 'two'}], 'Bearer tok'))

calls = []
op = fake([('POST', '/releases', 201, {'html_url': 'https://r/1'})], calls)
made = GH.create_release('tok', REPO, 'v1', 'abc', 'Release', 'notes', opener=op)
op2 = fake([('POST', '/releases', 422, {'message': 'Validation Failed'}), ('GET', '/releases/tags/v1', 200, {'html_url': 'https://r/old'})], [])
check('create_release: made at the target; an existing tag returns its release',
      (made, calls[0][2]['tag_name'], calls[0][2]['target_commitish'], GH.create_release('tok', REPO, 'v1', 'abc', 'R', 'n', opener=op2)),
      ('https://r/1', 'v1', 'abc', 'https://r/old'))

op = fake([('GET', '/issues/7', 200, {'title': ' A title ', 'state': 'open', 'labels': [{'name': 'Bug'}, {'name': 'area:product'}]})], [])
check('issue: the title trimmed, the labels lower-cased', GH.issue('tok', REPO, 7, op),
      {'number': 7, 'title': 'A title', 'state': 'open', 'labels': ['bug', 'area:product']})

calls = []
GH.dispatch('tok', REPO, 'deploy.yml', 'main', {'component': 'api', 'n': 3}, fake([('POST', '/dispatches', 204, None)], calls))
check('dispatch: the ref and the inputs as strings', (calls[0][1].endswith('/actions/workflows/deploy.yml/dispatches'), calls[0][2]),
      (True, {'ref': 'main', 'inputs': {'component': 'api', 'n': '3'}}))

runners = {'runners': [{'name': 'a', 'status': 'offline', 'labels': [{'name': 'desk'}]},
                       {'name': 'b', 'status': 'online', 'busy': False, 'labels': [{'name': 'self-hosted'}, {'name': 'desk'}]}]}
check('runner_online: an online runner with the label; another label no; a refused list False',
      (GH.runner_online('tok', REPO, 'desk', fake([('GET', '/actions/runners', 200, runners)], [])),
       GH.runner_online('tok', REPO, 'mac', fake([('GET', '/actions/runners', 200, runners)], [])),
       GH.runner_online('tok', REPO, 'desk', fake([('GET', '/actions/runners', 403, {'message': 'Resource not accessible'})], []))),
      (True, False, False))

try:
    GH.create_deployment('tok', REPO, 'abc', 'prod', opener=fake([('POST', '/deployments', 403, {'message': 'Resource not accessible by personal access token'})], []))
    refused = None
except GH.GitHubError as e:
    refused = (e.status, 'Deployments: read and write' in str(e), 'Resource not accessible' in e.message)
try:
    GH.get('tok', f'/repos/{REPO}/issues/1', opener=fake([], []))
    silent = None
except GH.GitHubError as e:
    silent = e.status
check('a 403 names the permission the call needs; no answer is status 0', (refused, silent), ((403, True, True), 0))

run = lambda args, **kw: type('R', (), {'returncode': 0, 'stdout': 'from-gh\n'})()  # noqa: E731
check('token: GH_TOKEN first, then GITHUB_TOKEN, then gh auth token',
      (GH.token({'GH_TOKEN': ' a ', 'GITHUB_TOKEN': 'b'}, run), GH.token({'GITHUB_TOKEN': 'b'}, run), GH.token({}, run)),
      ('a', 'b', 'from-gh'))

calls = []
GH.get('', f'/repos/{REPO}', opener=fake([('GET', '/repos/owner/repo', 200, {})], calls))
check('no token, no Authorization header', 'Authorization' in calls[0][3], False)

# The throttle (SMAD PickleBot #679; Gene, 10/31/26: "add a throttle mechanism so we don't have this problem again").
import subprocess  # noqa: E402
import time  # noqa: E402

GH.reset_limits()
clock = [1_000_000.0]
GH._now = lambda: clock[0]


class Limited(Resp):
    def __init__(self, status, body, headers):
        super().__init__(status, body)
        self.headers = headers


def limited(headers, status=200, body=None, calls=None):
    """One answer with rate-limit headers; a 4xx is an HTTPError carrying them."""
    def opener(req, timeout=None):
        if calls is not None:
            calls.append(req.full_url)
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, 'x', headers, io.BytesIO(json.dumps(body or {}).encode()))
        return Limited(status, body or {}, headers)
    return opener


sent = []
GH.get('t', '/search/issues?q=x', opener=limited({'X-RateLimit-Resource': 'search', 'X-RateLimit-Remaining': '2',
                                                   'X-RateLimit-Reset': str(clock[0] + 40)}, calls=sent))
try:
    GH.get('t', '/search/issues?q=y', opener=limited({}, calls=sent))
    under = None
except GH.RateLimited as e:
    under = (e.resource, e.remaining, int(e.reset_at - clock[0]), e.status, isinstance(e, GH.GitHubError))
clock[0] += 41
GH.get('t', '/search/issues?q=z', opener=limited({'X-RateLimit-Resource': 'search', 'X-RateLimit-Remaining': '29',
                                                   'X-RateLimit-Reset': str(clock[0] + 60)}, calls=sent))
check("the throttle's positive case: GitHub's count read from the headers; under the floor the next search is refused "
      "locally and not sent; after the reset it goes", (len(sent), under, GH.limits()['search']['remaining']),
      (2, ('search', 2, 40, 429, True), 29))

GH.reset_limits()
sent = []
try:
    GH.get('t', f'/repos/{REPO}/issues', opener=limited({'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': str(clock[0] + 30)},
                                                        403, {'message': 'API rate limit exceeded for user'}, calls=sent))
    spent = None
except GH.RateLimited as e:
    spent = (e.resource, e.remaining, 'exceeded' in e.message, int(e.reset_at - clock[0]))
try:
    GH.get('t', f'/repos/{REPO}/issues/2', opener=limited({}, calls=sent))
    again = None
except GH.RateLimited as e:
    again = e.resource
check('a 403 with remaining 0 spends core until the reset; the next call is not sent', (spent, again, len(sent)),
      (('core', 0, True, 30), 'core', 1))

GH.reset_limits()
sent = []
for _ in range(GH.LOCAL_CAP['search']):
    GH.get('t', '/search/issues?q=a', opener=limited({}, calls=sent))
try:
    GH.get('t', '/search/issues?q=a', opener=limited({}, calls=sent))
    capped = None
except GH.RateLimited as e:
    capped = ('its cap' in e.message, e.resource)
clock[0] += 61
GH.get('t', '/search/issues?q=a', opener=limited({}, calls=sent))
check("the process's own cap: the eleventh search in a minute is refused before it is sent; the minute after, it goes",
      (capped, len(sent)), ((True, 'search'), 11))

GH.reset_limits()
data = GH.graphql('t', 'query { x }', {'a': 1}, opener=limited({'X-RateLimit-Resource': 'graphql', 'X-RateLimit-Remaining': '4000',
                                                                   'X-RateLimit-Reset': str(clock[0] + 3000)}, body={'data': {'x': 1}}))
try:
    GH.graphql('t', 'query { x }', opener=limited({}, body={'errors': [{'message': 'API rate limit exceeded for this resource'}]}))
    gql = None
except GH.RateLimited as e:
    gql = e.resource
try:
    GH.graphql('t', 'query { x }', opener=limited({}, body={'data': {}}))
    after = None
except GH.RateLimited as e:
    after = e.resource
try:
    GH.reset_limits()
    GH.graphql('t', 'query { x }', opener=limited({}, body={'errors': [{'message': 'Field x does not exist'}]}))
    plain = None
except GH.GitHubError as e:
    plain = (type(e).__name__, e.message)
check("graphql: the data; GitHub's count kept; a rate-limit error spends the resource and the next call is not sent; "
      "another error is a GitHubError", (data, gql, after, plain), ({'x': 1}, 'graphql', 'graphql', ('GitHubError', 'Field x does not exist')))

GH.reset_limits()
ran = []


def gh_run(args, **kw):
    ran.append(args)
    return subprocess.CompletedProcess(args, 1, '', 'gh: HTTP 403: API rate limit exceeded for user')


try:
    GH.cli(['api', 'graphql', '-f', 'query=x'], run=gh_run)
    c1 = None
except GH.RateLimited as e:
    c1 = e.resource
try:
    GH.cli(['api', 'graphql', '-f', 'query=y'], run=gh_run)
    c2 = None
except GH.RateLimited as e:
    c2 = e.resource
fine = GH.cli(['issue', 'list'], run=lambda args, **kw: subprocess.CompletedProcess(args, 0, 'fine', ''))
check('cli: a rate-limited gh run spends its resource, the next graphql run is refused before gh runs, core still runs',
      (c1, c2, len(ran), fine.stdout), ('graphql', 'graphql', 1, 'fine'))
check('resource_of: graphql, code_search, search, core',
      (GH.resource_of('/graphql'), GH.resource_of('/search/code?q=a'), GH.resource_of('/search/issues?q=a'), GH.resource_of('/repos/o/r')),
      ('graphql', 'code_search', 'search', 'core'))
slept = []
GH.reset_limits()
GH._note('search', {'X-RateLimit-Remaining': '1', 'X-RateLimit-Reset': str(clock[0] + 25)})
check('wait_until sleeps to the reset while under the floor, at most the cap; nothing when the resource is free',
      (GH.wait_until('search', sleep=slept.append), GH.wait_until('search', cap=10, sleep=slept.append), GH.wait_until('core', sleep=slept.append)),
      (25.0, 10.0, 0))
GH._now = time.time
GH.reset_limits()

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
