#!/usr/bin/env python3
"""chuang_platform_kit.posthog_api: PostHog's REST API, one client for every project (SMAD PickleBot #1132).

Pinned (the positive case first): events() filters by its params and answers the results; query() posts HogQL and
answers {columns, results}; insight() asks for a blocking refresh and trims to id, name and result; the links name the
host and project with the id quoted, '' for none; run() names the actions and refuses an unknown one or a missing arg;
a refusal is Refused with PostHog's words, no answer Refused with the error's name, no key Refused before any call; a
key passed as a function is read at the first call, stripped. Offline: urllib is a fake.

Run:  python -B tests/test-posthog-api.py
"""
import io
import json
import os
import sys
import urllib.error
import urllib.parse

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import posthog_api as P  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener_for(routes, calls):
    """A fake urlopen: (method, path without the host and query) -> a JSON body, or an int status to refuse with."""
    def opener(req, timeout=None):
        url = urllib.parse.urlsplit(req.full_url)
        calls.append((req.get_method(), url.path, url.query, req.get_header('Authorization'),
                      json.loads(req.data) if req.data else None, timeout))
        got = routes.get((req.get_method(), url.path))
        if isinstance(got, int):
            raise urllib.error.HTTPError(req.full_url, got, 'no', {}, io.BytesIO(b'{"detail": "nope"}'))
        return Resp(b'' if got is None else json.dumps(got).encode())
    return opener


calls = []
c = P.Client(659921, key='phx_k', opener=opener_for({
    ('GET', '/api/projects/@current/events/'): {'results': [{'event': 'joined'}]},
    ('POST', '/api/projects/@current/query/'): {'columns': ['n'], 'results': [[3]], 'hogql': 'x'},
    ('GET', '/api/projects/@current/insights/42/'): {'id': 42, 'name': 'App: time to data', 'result': [1], 'filters': {}},
}, calls))

check('events: the filter as the query string, the results back, the key as a bearer, the timeout passed',
      (c.events({'event': 'joined', 'limit': 5}), calls[0][2], calls[0][3], calls[0][5]),
      ([{'event': 'joined'}], 'event=joined&limit=5', 'Bearer phx_k', 30))
check('query: HogQL posted as a HogQLQuery, {columns, results} back',
      (c.query('select count() from events'), calls[1][4]),
      ({'columns': ['n'], 'results': [[3]]}, {'query': {'kind': 'HogQLQuery', 'query': 'select count() from events'}}))
check('insight: a blocking refresh, trimmed to id, name and result',
      (c.insight('42'), calls[2][2]), ({'id': 42, 'name': 'App: time to data', 'result': [1]}, 'refresh=blocking'))
check('the links name the host and project, the id quoted, none as empty',
      (c.replay_url('s/1'), c.person_url('user_2x'), c.replay_url(''), c.person_url(None),
       P.Client(7, host='https://eu.posthog.com/').person_url('a')),
      ('https://us.posthog.com/project/659921/replay/s%2F1', 'https://us.posthog.com/project/659921/person/user_2x', '', '',
       'https://eu.posthog.com/project/7/person/a'))

calls.clear()
check('run: events with only its four filters, query and insight by name',
      (c.run('events', {'event': 'joined', 'nope': 1}), calls[0][2], c.run('query', {'hogql': 'select 1'})['results'],
       c.run('insight', {'id': 42})['id']),
      ([{'event': 'joined'}], 'event=joined', [[3]], 42))


def refused(fn):
    try:
        fn()
        return ''
    except P.Refused as e:
        return str(e)


def down(req, timeout=None):
    raise urllib.error.URLError('down')


check('run refuses an unknown action and a missing arg, before any call',
      (refused(lambda: c.run('delete', {})), refused(lambda: c.run('query', {}))),
      ("posthog: no action 'delete' (one of events, query, insight)", 'posthog query: needs hogql'))
check("a refusal is Refused with PostHog's words, no answer with the error's name, no key before any call",
      (refused(lambda: P.Client(1, key='k', opener=opener_for({('GET', '/x'): 401}, [])).api('GET', '/x')),
       refused(lambda: P.Client(1, key='k', opener=down).api('GET', '/x')),
       refused(lambda: P.Client(1, opener=opener_for({}, [])).events())),
      ('PostHog answered 401 to GET /x: {"detail": "nope"}', 'PostHog did not answer GET /x (URLError)',
       'no PostHog personal API key: the host passes one to the client'))
asked, seen = [], []
lazy = P.Client(1, key=lambda: asked.append(1) or ' phx_lazy ', opener=opener_for({('GET', '/y'): {'ok': 1}}, seen))
before = len(asked)
check('a key passed as a function is read at the first call, stripped, and a per-call key wins',
      (before, lazy.api('GET', '/y'), seen[0][3], len(asked), lazy.api('GET', '/y', k='other') and seen[1][3]),
      (0, {'ok': 1}, 'Bearer phx_lazy', 1, 'Bearer other'))
check('an empty answer is None', P.Client(1, key='k', opener=opener_for({('DELETE', '/z'): None}, [])).api('DELETE', '/z'), None)

print()
print(f"{len(fails)} failure(s)" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
