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

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
