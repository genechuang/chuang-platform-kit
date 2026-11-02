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

op = fake([('GET', '/issues/7', 200, {'title': ' A title ', 'state': 'open', 'labels': [{'name': 'Bug'}, {'name': 'area:product'}],
                                       'id': 9007, 'node_id': 'I_7', 'body': 'b', 'state_reason': None, 'html_url': 'u/7',
                                       'created_at': '2026-11-01T00:00:00Z'})], [])
got = GH.issue('tok', REPO, 7, op)
check('issue: the title trimmed, the labels lower-cased; 0.21.0: the ids, body, reason, url, stamp and pull_request beside them',
      ({k: got[k] for k in ('number', 'title', 'state', 'labels')}, got['label_names'], got['id'], got['node_id'], got['body'],
       got['state_reason'], got['html_url'], got['created_at'], got['pull_request']),
      ({'number': 7, 'title': 'A title', 'state': 'open', 'labels': ['bug', 'area:product']}, ['Bug', 'area:product'], 9007, 'I_7', 'b',
       None, 'u/7', '2026-11-01T00:00:00Z', False))

# The ticket verbs (0.21.0; SMAD PickleBot #685: the ticket tools smad-commands runs leave the gh CLI).
calls = []
op = fake([('POST', '/issues/7/labels', 200, [{'name': 'bug'}, {'name': 'needs-triage'}]),
           ('DELETE', '/issues/7/labels/told%3Afiled', 200, []), ('DELETE', '/issues/7/labels/gone', 404, {'message': 'Label does not exist'}),
           ('POST', '/labels', 422, {'message': 'Validation Failed'}), ('PATCH', '/labels/needs-triage', 200, {'name': 'needs-triage'}),
           ('POST', '/issues/7/assignees', 201, {'assignees': [{'login': 'gene'}]}),
           ('DELETE', '/issues/7/assignees', 200, {'assignees': []}),
           ('POST', '/issues/40/sub_issues', 201, {}), ('PATCH', '/issues/comments/77', 200, {'html_url': 'c/77'})], calls)
labels = (GH.add_labels('tok', REPO, 7, ['bug', 'needs-triage'], op), GH.remove_label('tok', REPO, 7, 'told:filed', op),
          GH.remove_label('tok', REPO, 7, 'gone', op))
made = GH.create_label('tok', REPO, 'needs-triage', '#D93F0B', 'waiting for triage', op)
people = (GH.add_assignees('tok', REPO, 7, ['gene'], op), GH.remove_assignees('tok', REPO, 7, ['gene'], op))
GH.add_sub_issue('tok', REPO, 40, 9300, op)
edited = GH.edit_comment('tok', REPO, 77, 'new body', op)
check('the positive case: labels added and one removed (a 404 is False, never an error); an existing label updated on a 422 '
      '(gh\'s --force) with the # dropped; assignees on and off; a sub-issue linked by its id; a comment edited',
      (labels, made, [c[2] for c in calls if '/labels' in c[1] and c[0] in ('POST', 'PATCH') and '/issues/' not in c[1]],
       people, [c[2] for c in calls if c[1].endswith('/sub_issues')], edited, [c[2] for c in calls if c[1].endswith('/comments/77')]),
      ((['bug', 'needs-triage'], True, False), 'needs-triage',
       [{'name': 'needs-triage', 'color': 'D93F0B', 'description': 'waiting for triage'}, {'color': 'D93F0B', 'description': 'waiting for triage'}],
       (['gene'], []), [{'sub_issue_id': 9300}], 'c/77', [{'body': 'new body'}]))

calls = []
op = fake([('PATCH', '/issues/7', 200, {}), ('POST', '/issues/7/comments', 201, {'html_url': 'c/1'}),
           ('GET', '/issues/7', 200, {'title': 'T', 'state': 'closed', 'labels': []})], calls)
GH.close_issue('tok', REPO, 7, 'completed', 'Live in release abc.', op)
GH.reopen_issue('tok', REPO, 7, op)
after = GH.edit_issue('tok', REPO, 7, title='New title', opener=op)
check('close_issue comments first then closes with its reason; reopen opens; edit_issue sends only what was given and reads back',
      ([(c[0], c[1].rsplit('/', 2)[-2:], c[2]) for c in calls if c[0] in ('PATCH', 'POST')], after['state']),
      ([('POST', ['7', 'comments'], {'body': 'Live in release abc.'}), ('PATCH', ['issues', '7'], {'state': 'closed', 'state_reason': 'completed'}),
        ('PATCH', ['issues', '7'], {'state': 'open'}), ('PATCH', ['issues', '7'], {'title': 'New title'})], 'closed'))


def issues_answer(nodes, more=None, search=False):
    page = {'pageInfo': {'hasNextPage': bool(more), 'endCursor': more}, 'nodes': nodes}
    return {'data': {'search': page} if search else {'repository': {'issues': page}}}


def gql(answers, calls):
    """A GraphQL opener answering each POST /graphql from `answers` in turn; the variables kept in `calls`."""
    answers = list(answers)

    def opener(req, timeout=None):
        body = json.loads(req.data)
        calls.append(body['variables'])
        return Resp(200, answers.pop(0))
    return opener


node = {'number': 5, 'title': 'Five', 'body': 'Sentry feedback: X', 'state': 'OPEN', 'stateReason': None, 'createdAt': '2026-11-01T01:00:00Z',
        'url': 'u/5', 'labels': {'nodes': [{'name': 'feedback'}]},
        'comments': {'nodes': [{'databaseId': 9, 'body': 'Triage: P2', 'createdAt': '2026-11-01T02:00:00Z', 'author': {'login': 'gene'}}]}}
calls = []
listed = GH.issues('tok', REPO, 'open', ['feedback'], limit=51, comments=30,
                   opener=gql([issues_answer([node], more='C1'), issues_answer([dict(node, number=6, comments={'nodes': []})])], calls))
calls2 = []
found = GH.issues('tok', REPO, 'open', search='smoke in:title', limit=10, opener=gql([issues_answer([node], search=True)], calls2))
check("issues: the repository's issues in gh's --json shape, labels and the last comments with their authors, the cursor followed "
      "to the limit; a search puts repo:, is:issue and the state before the words",
      ([(i['number'], i['title'], i['state'], i['labels'], [(c['databaseId'], c['author']['login'], c['body']) for c in i['comments']])
        for i in listed], [(v['states'], v['labels'], v['first'], v['after']) for v in calls],
       [i['number'] for i in found], calls2[0]['q'], calls2[0]['first']),
      ([(5, 'Five', 'OPEN', [{'name': 'feedback'}], [(9, 'gene', 'Triage: P2')]), (6, 'Five', 'OPEN', [{'name': 'feedback'}], [])],
       [(['OPEN'], ['feedback'], 51, None), (['OPEN'], ['feedback'], 50, 'C1')],
       [5], 'repo:owner/repo is:issue is:open smoke in:title', 10))

calls = []
op = gql([{'data': {'addProjectV2ItemById': {'item': {'id': 'PVTI_1'}}}}, {'data': {'addProjectV2DraftIssue': {'projectItem': {'id': 'PVTI_2'}}}},
          {'data': {'updateProjectV2ItemFieldValue': {'projectV2Item': {'id': 'PVTI_1'}}}},
          {'data': {'updateProjectV2ItemFieldValue': {'projectV2Item': {'id': 'PVTI_1'}}}},
          {'data': {'clearProjectV2ItemFieldValue': {'projectV2Item': {'id': 'PVTI_1'}}}},
          {'data': {'updateProjectV2ItemPosition': {'items': {'nodes': []}}}}], calls)
project = (GH.project_add_item('tok', 'PVT_1', 'I_7', op), GH.project_add_draft('tok', 'PVT_1', '— Dock —', 'why', op),
           GH.project_set_field('tok', 'PVT_1', 'PVTI_1', 'F_status', option_id='abc', opener=op),
           GH.project_set_field('tok', 'PVT_1', 'PVTI_1', 'F_release', text='abcdef12', opener=op),
           GH.project_clear_field('tok', 'PVT_1', 'PVTI_1', 'F_light', op), GH.project_item_to_top('tok', 'PVT_1', 'PVTI_1', op))
check('the Projects v2 mutations: an item added by content id, a draft by title, a single-select by option id and a text field, '
      'a field cleared, an item to the top',
      (project, [v.get('v') for v in calls[2:5]], calls[0], calls[1]['t']),
      (('PVTI_1', 'PVTI_2', True, True, True, True), [{'singleSelectOptionId': 'abc'}, {'text': 'abcdef12'}, None],
       {'p': 'PVT_1', 'c': 'I_7'}, '— Dock —'))

queries, calls = [], []


def gql_q(answers):
    """A GraphQL opener that also keeps each request's query text."""
    inner = gql(answers, calls)

    def opener(req, timeout=None):
        queries.append(json.loads(req.data)['query'])
        return inner(req, timeout)
    return opener


batched = GH.project_set_fields('tok', 'PVT_1', 'PVTI_1', {'F_status': 'abc', 'F_kind': 'def', 'F_light': None, 'F_release': {'text': 'abcdef12'}},
                                gql_q([{'data': {'s0': {'projectV2Item': {'id': 'PVTI_1'}}, 's1': {'projectV2Item': {'id': 'PVTI_1'}},
                                                 's2': {'projectV2Item': {'id': 'PVTI_1'}}, 's3': {'projectV2Item': {'id': 'PVTI_1'}}}}]))
short = GH.project_set_fields('tok', 'PVT_1', 'PVTI_1', {'F_status': 'abc'}, gql_q([{'data': {'s0': {'projectV2Item': {}}}}]))
none = GH.project_set_fields('tok', 'PVT_1', 'PVTI_1', {}, gql_q([]))
check('0.23.0 project_set_fields: four fields in ONE request, an aliased update per set field (an option id, a text value) and a clear '
      'for None, the variables beside them; an alias answered without the item is False; no fields is True with no call',
      (batched, short, none, len(queries), queries[0].count('updateProjectV2ItemFieldValue'), queries[0].count('clearProjectV2ItemFieldValue'),
       queries[0].startswith('mutation($p: ID!, $i: ID!, $f0: ID!, $v0: ProjectV2FieldValue!, $f1: ID!, $v1: ProjectV2FieldValue!, $f2: ID!, $f3: ID!, $v3: ProjectV2FieldValue!)'),
       calls[0]),
      (True, False, True, 2, 3, 1, True,
       {'p': 'PVT_1', 'i': 'PVTI_1', 'f0': 'F_status', 'v0': {'singleSelectOptionId': 'abc'}, 'f1': 'F_kind', 'v1': {'singleSelectOptionId': 'def'},
        'f2': 'F_light', 'f3': 'F_release', 'v3': {'text': 'abcdef12'}}))

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
GH.reset_limits()
try:
    GH.get('t', '/repos/o/r/issues/9', opener=limited({}, 404, None))
    reason = None
except GH.GitHubError as e:
    reason = (e.status, e.message)
check('0.17.1: a refusal with no message in its body reads as the HTTP reason phrase', reason, (404, 'x'))
seen = []


def dup_run(args, **kw):
    seen.append(kw)
    return subprocess.CompletedProcess(args, 0, 'ok', '')


GH.reset_limits()
got = GH.cli(['issue', 'list'], run=dup_run, capture_output=True, text=True, timeout=5)
check("0.17.1: cli takes a caller's own capture keywords over its defaults instead of raising",
      (got.stdout, seen[0]['timeout'], seen[0]['capture_output'], seen[0]['encoding']), ('ok', 5, True, 'utf-8'))
GH.reset_limits()


def raised(**kw):
    try:
        got = GH.cli(['issue', 'close', '7'], **kw)
        return ('returned', got.returncode)
    except subprocess.CalledProcessError as e:
        return ('raised', e.returncode, e.cmd[:3], e.stderr)


def missing_gh(args, **kw):
    raise FileNotFoundError('gh')


passed_check = []
check("0.19.1: check raises CalledProcessError on a failed run and a missing gh, never passes check to the runner; a pass returns",
      (raised(run=lambda args, **kw: passed_check.append('check' in kw) or subprocess.CompletedProcess(args, 1, '', 'not found'), check=True),
       raised(run=missing_gh, check=True),
       raised(run=lambda args, **kw: subprocess.CompletedProcess(args, 0, '', ''), check=True),
       raised(run=lambda args, **kw: subprocess.CompletedProcess(args, 1, '', 'not found')), passed_check),
      (('raised', 1, ['gh', 'issue', 'close'], 'not found'), ('raised', 127, ['gh', 'issue', 'close'], 'gh'), ('returned', 0),
       ('returned', 1), [False]))
GH.reset_limits()
try:
    GH.cli(['api', 'repos/o/r/tarball/x'], run=lambda args, **kw: subprocess.CompletedProcess(args, 1, b'', b'API rate limit exceeded'),
           text=False, encoding=None, errors=None)
    binary = 'returned'
except GH.RateLimited as e:
    binary = e.resource
check("0.19.1: a binary run's bytes stderr naming a rate limit spends core as a text one does, never a TypeError", binary, 'core')
GH._now = time.time
GH.reset_limits()

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
