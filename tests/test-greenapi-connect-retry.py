#!/usr/bin/env python3
"""GREEN-API reads retry a connect timeout; nothing else is retried.

2026-09-29 PT: one 10s connect timeout to api.green-api.com failed the 1:55
PM PT Sync Group Members run ("Failed to get group data"). Since a9873cf the
sync fails its run on any error, so GitHub emailed Gene over a blip the next
attempt would have cleared. Pinned: call_api() retries a CONNECT timeout only
when the caller asks (connect_retries), 5s then 10s apart, logging each at
WARNING; a read timeout is never retried (the request may have landed); the
default is no retry, so sends and the Cloud Functions' callers are unchanged;
the member sync asks for two.

Run:  python -B tests/test-greenapi-connect-retry.py
"""
import logging
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

import requests  # noqa: E402
from chuang_platform_kit import greenapi as ga  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class _Resp:
    status_code = 200
    text = ''
    def json(self): return {'participants': [{'id': '1@c.us'}]}


calls, sleeps = [], []


def script(*outcomes):
    """requests.post that raises or answers in order."""
    it = iter(outcomes)
    def post(url, json=None, timeout=None):
        calls.append(url)
        o = next(it)
        if isinstance(o, Exception):
            raise o
        return o
    return post


class _Levels(logging.Handler):
    def __init__(self):
        super().__init__()
        self.levels, self.lines = [], []
    def emit(self, record):
        self.levels.append(record.levelname)
        self.lines.append(record.getMessage())


_post, _sleep = requests.post, ga.time.sleep
ga.time.sleep = lambda s: sleeps.append(s)
h = _Levels()
ga.logger.addHandler(h)
CT = requests.exceptions.ConnectTimeout
try:
    requests.post = script(CT('connect timeout=10'), CT('connect timeout=10'), _Resp())
    got = ga.get_group_data('i', 't', 'g@g.us', connect_retries=2)
    check("the positive case: two connect timeouts, then the answer; waited 5s then 10s",
          (got['participants'][0]['id'], len(calls), sleeps), ('1@c.us', 3, [5, 10]))
    check("  each retry logged at WARNING, no ERROR", (h.levels, 'ERROR' in h.levels), (['WARNING', 'WARNING'], False))

    calls.clear(); sleeps.clear(); h.levels.clear()
    requests.post = script(CT('x'), CT('x'), CT('x'))
    check("  three connect timeouts: None after three attempts, one ERROR at the end",
          (ga.get_group_data('i', 't', 'g@g.us', connect_retries=2), len(calls), h.levels[-1]), (None, 3, 'ERROR'))

    calls.clear(); sleeps.clear()
    requests.post = script(requests.exceptions.ReadTimeout('read timeout'), _Resp())
    check("a read timeout is never retried (the request may have landed)",
          (ga.get_group_data('i', 't', 'g@g.us', connect_retries=2), len(calls), sleeps), (None, 1, []))

    calls.clear()
    requests.post = script(CT('x'), _Resp())
    check("no retry by default: sends and the functions' callers are unchanged",
          (ga.get_group_data('i', 't', 'g@g.us'), len(calls)), (None, 1))
    check("  no log line carries the URL, where the API token is a path segment",
          any('waInstance' in line or '/t' in line.split(' ')[0] for line in h.lines), False)
finally:
    requests.post, ga.time.sleep = _post, _sleep
    ga.logger.removeHandler(h)

# (Which host callers ask for retries is each host's own check: SMADPickleBot's
# tests/test-greenapi-connect-retry.py pins its member sync at connect_retries=2.)

print()
if fails:
    print(f"{len(fails)} failure(s):")
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)
print("0 failure(s)")
