#!/usr/bin/env python3
"""chuang_platform_kit.google_rest.log_entries: Cloud Logging's entries:list, every page, through the one call.

Pinned (the positive case first, offline: a stand-in transport):

- two pages read in order: the project, the filter and the order in each POST, the second carrying the page token
- `limit` stops early and trims, asking no more than it needs; a callable token is asked once
- it only reads, so a stand-in opener and the real transport alike are never refused off Cloud Run (guard 'none')

Run:  python -B tests/test-log-entries.py
"""
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import google_rest as G  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class Answer:
    def __init__(self, body):
        self.body = json.dumps(body).encode('utf-8')

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Logging:
    def __init__(self, pages):
        self.pages, self.bodies = list(pages), []

    def __call__(self, req, timeout=None):
        self.bodies.append(json.loads(req.data))
        return Answer(self.pages.pop(0))


op = Logging([{'entries': [{'n': 1}, {'n': 2}], 'nextPageToken': 'p2'}, {'entries': [{'n': 3}]}])
got = G.log_entries('proj', 'severity=WARNING', 'tok', order='timestamp asc', opener=op)
check('two pages in order, each POST carrying the project, filter and order; the second the page token',
      ([e['n'] for e in got], op.bodies[0], op.bodies[1].get('pageToken')),
      ([1, 2, 3], {'resourceNames': ['projects/proj'], 'filter': 'severity=WARNING', 'orderBy': 'timestamp asc',
                   'pageSize': 1000}, 'p2'))

asked = []
op = Logging([{'entries': [{'n': 1}, {'n': 2}], 'nextPageToken': 'p2'}, {'entries': [{'n': 3}, {'n': 4}]}])
got = G.log_entries('proj', 'x', lambda: asked.append(1) or 'tok', limit=3, page_size=2, opener=op)
check('limit stops and trims, asking no more than it needs; a callable token is asked once',
      ([e['n'] for e in got], [b['pageSize'] for b in op.bodies], len(asked)), ([1, 2, 3], [2, 1], 1))

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
