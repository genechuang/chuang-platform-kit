#!/usr/bin/env python3
"""chuang_platform_kit.neon: the public request and the branch functions (list, cut idempotently, delete).

Pinned (the positive case first, offline: a stand-in Neon):

- create_branch cuts from the default branch: one POST naming the parent, {id, name, made: True}
- a branch of that name already there is returned with made False and nothing posted; an explicit parent is used as
  given; a project with no default branch, or a POST that makes no branch, is a NeonError
- branches lists them; delete_branch sends DELETE to the branch's own address
- request: a refusal is NeonError "Neon answered HTTP <code>: <words>" with its status, the key masked by value; no
  answer is "Neon did not answer (<kind>)", status 0; an empty answer is {}

Run:  python -B tests/test-neon-branches.py
"""
import io
import json
import os
import sys
import urllib.error

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import neon  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


KEY = 'napi_' + 'k' * 20   # built at run time: no key-shaped literal in the file


class Answer:
    def __init__(self, body):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode('utf-8')

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Neon:
    """A stand-in Neon: (method, path) to an answer, an HTTP status or an exception; records each request."""

    def __init__(self, routes):
        self.routes, self.requests = routes, []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        path = req.full_url.split('/api/v2', 1)[1]
        got = self.routes.get((req.get_method(), path), 500)
        if isinstance(got, Exception):
            raise got
        if isinstance(got, int):
            raise urllib.error.HTTPError(req.full_url, got, 'x', {}, io.BytesIO(f'bad key {KEY} here'.encode('utf-8')))
        return Answer(got)


LISTED = {'branches': [{'id': 'br-main', 'name': 'main', 'default': True}, {'id': 'br-old', 'name': 'nightly-1'}]}
neon_api = Neon({('GET', '/projects/p1/branches'): LISTED,
                 ('POST', '/projects/p1/branches'): {'branch': {'id': 'br-new', 'name': 'nightly-2'}}})
got = neon.create_branch(KEY, 'p1', 'nightly-2', opener=neon_api)
check('create_branch cuts from the default branch: one POST naming the parent',
      (got, json.loads(neon_api.requests[-1].data), neon_api.requests[-1].get_header('Authorization')),
      ({'id': 'br-new', 'name': 'nightly-2', 'made': True}, {'branch': {'name': 'nightly-2', 'parent_id': 'br-main'}},
       f'Bearer {KEY}'))

neon_api = Neon({('GET', '/projects/p1/branches'): LISTED})
check('a branch of that name already there is returned with made False and nothing posted',
      (neon.create_branch(KEY, 'p1', 'nightly-1', opener=neon_api), [r.get_method() for r in neon_api.requests]),
      ({'id': 'br-old', 'name': 'nightly-1', 'made': False}, ['GET']))

neon_api = Neon({('POST', '/projects/p1/branches'): {'branch': {'id': 'b2'}}})
neon.create_branch(KEY, 'p1', 'x', parent_id='br-given', opener=neon_api, listed=[])
check('an explicit parent is used as given, and a listed answer saves the read',
      ([r.get_method() for r in neon_api.requests], json.loads(neon_api.requests[0].data)['branch']['parent_id']),
      (['POST'], 'br-given'))


def why(fn):
    try:
        fn()
        return 'no error'
    except neon.NeonError as e:
        return (str(e), e.status)


check('no default branch, or a POST that makes none, is a NeonError',
      (why(lambda: neon.create_branch(KEY, 'p1', 'x', opener=Neon({}), listed=[{'id': 'a', 'name': 'a'}])),
       why(lambda: neon.create_branch(KEY, 'p1', 'x', opener=Neon({('POST', '/projects/p1/branches'): {}}),
                                      listed=LISTED['branches']))),
      (('the project lists no default branch to cut from', 0), ('Neon made no branch for x', 0)))

neon_api = Neon({('GET', '/projects/p1/branches'): LISTED, ('DELETE', '/projects/p1/branches/br-old'): {'ok': True}})
check('branches lists them; delete_branch sends DELETE to the branch',
      ([b['id'] for b in neon.branches(KEY, 'p1', opener=neon_api)], neon.delete_branch(KEY, 'p1', 'br-old', opener=neon_api),
       neon_api.requests[-1].get_method()),
      (['br-main', 'br-old'], {'ok': True}, 'DELETE'))

refused = why(lambda: neon.request('POST', '/projects/p1/branches', KEY, {}, opener=Neon({})))
check('a refusal names the status and Neon\'s words, the key masked; no answer says its kind; empty is {}',
      (refused[0].startswith('Neon answered HTTP 500: bad key '), KEY in refused[0], refused[1],
       why(lambda: neon.request('GET', '/x', KEY, opener=Neon({('GET', '/x'): urllib.error.URLError('down')}))),
       neon.request('GET', '/e', KEY, opener=Neon({('GET', '/e'): b''}))),
      (True, False, 500, ('Neon did not answer (URLError)', 0), {}))

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
