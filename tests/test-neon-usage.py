#!/usr/bin/env python3
"""chuang_platform_kit.neon, the metering half: consumption summed, the transfer budget judged, compute capped, over a fake opener.

Pinned (the positive case first): consumption() sums every *_bytes and *_seconds metric across the
period's entries and keeps the storage gauge's latest value; transfer_budget() answers used bytes,
percent and a printable line, warns from 80%, and says 'not reported' (percent None) when Neon gave no
transfer metric; cap_compute() PATCHes every endpoint with the max (and min) CU; month_to_date() asks
from the first of the UTC month; the CLI's --usage prints the month and the budget line and provisions
nothing; --cap-cu caps after a provision.

Run:  python -B tests/test-neon-usage.py
"""
import contextlib
import io
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import neon as N  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class Resp:
    def __init__(self, body):
        self._body = json.dumps(body).encode('utf-8')

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


GB = 1024 ** 3
HISTORY = {'projects': [{'project_id': 'proj-1', 'periods': [{'period_id': 'p', 'consumption': [
    {'timeframe_start': 't1', 'data_transfer_bytes': 3 * GB, 'written_data_bytes': 10, 'compute_time_seconds': 1800,
     'active_time_seconds': 3600, 'synthetic_storage_size_bytes': 100},
    {'timeframe_start': 't2', 'data_transfer_bytes': 1.2 * GB, 'written_data_bytes': 5, 'compute_time_seconds': 1800,
     'active_time_seconds': 1800, 'synthetic_storage_size_bytes': 120, 'note': 'not a number'},
]}]}, {'project_id': 'other', 'periods': [{'consumption': [{'data_transfer_bytes': 99 * GB}]}]}]}


def fake(calls, history=HISTORY):
    def opener(req, timeout=None):
        path = req.full_url[len(N.API):]
        body = json.loads(req.data.decode('utf-8')) if req.data else None
        calls.append((req.get_method(), path, body))
        if path.startswith('/consumption_history/projects?'):
            return Resp(history)
        if path.endswith('/endpoints') and req.get_method() == 'GET':
            return Resp({'endpoints': [{'id': 'ep-a'}, {'id': 'ep-b'}]})
        if '/endpoints/' in path and req.get_method() == 'PATCH':
            return Resp({'endpoint': {'id': path.rsplit('/', 1)[-1], **body['endpoint']}})
        if path.startswith('/users/me/organizations'):
            return Resp({'organizations': [{'id': 'org-1', 'name': 'Gene'}]})
        if path.startswith('/projects?'):
            return Resp({'projects': [{'id': 'proj-1', 'name': 'chuang-finance'}], 'pagination': {}})
        raise AssertionError('unexpected call ' + path)
    return opener


# 1. The positive case: the month's metrics summed, the other project's ignored, the gauge's latest kept.
calls = []
totals = N.consumption('k', 'proj-1', '2026-10-01T00:00:00Z', '2026-10-08T21:00:00Z', org_id='org-1', opener=fake(calls))
check("sums bytes and seconds across entries, keeps the storage gauge, ignores the other project and non-metrics",
      (totals['data_transfer_bytes'], totals['written_data_bytes'], totals['compute_time_seconds'],
       totals['active_time_seconds'], totals['synthetic_storage_size_bytes'], 'note' in totals),
      (4.2 * GB, 15, 3600, 5400, 120, False))
check("asked with the project, the window, daily granularity and the organization",
      all(x in calls[0][1] for x in ('project_ids=proj-1', 'from=2026-10-01T00%3A00%3A00Z', 'granularity=daily', 'org_id=org-1')), True)

# 2. The transfer budget.
used, pct, line = N.transfer_budget(totals)
check("4.2 of 5 GB is 84%, warned", (used, round(pct), '4.20 GB of 5 GB (84%)' in line, 'WARNING' in line), (4.2 * GB, 84, True, True))
used, pct, line = N.transfer_budget({'data_transfer_bytes': GB}, budget_bytes=500 * GB)
check("1 of 500 GB (Launch) is 0%, no warning", (round(pct), 'WARNING' in line), (0, False))
check("no transfer metric: not reported, percent None", N.transfer_budget({'compute_time_seconds': 5})[:2], (None, None))

# 3. cap_compute PATCHes every endpoint.
calls = []
capped = N.cap_compute('k', 'proj-1', 0.25, min_cu=0.25, opener=fake(calls))
patches = [c for c in calls if c[0] == 'PATCH']
check("both endpoints capped at 0.25 CU, floor 0.25",
      ([p[1].rsplit('/', 1)[-1] for p in patches], patches[0][2], [e['autoscaling_limit_max_cu'] for e in capped]),
      (['ep-a', 'ep-b'], {'endpoint': {'autoscaling_limit_max_cu': 0.25, 'autoscaling_limit_min_cu': 0.25}}, [0.25, 0.25]))
calls = []
N.cap_compute('k', 'proj-1', 1, endpoint_id='ep-b', opener=fake(calls))
check("one endpoint when named, no listing", [c[1].rsplit('/', 1)[-1] for c in calls], ['ep-b'])

# 4. month_to_date asks from the first of the UTC month.
calls = []
N.month_to_date('k', 'proj-1', now=datetime(2026, 10, 8, 21, 30, tzinfo=timezone.utc), opener=fake(calls))
check("from the 1st at midnight UTC to now", ('from=2026-10-01T00%3A00%3A00Z' in calls[0][1], 'to=2026-10-08T21%3A30%3A00Z' in calls[0][1]), (True, True))

# 5. The CLI: --usage prints the month and the budget line, provisions nothing; --cap-cu caps after a provision.
keep = (N._call, N.provision)
calls = []
opener = fake(calls)
N._call = lambda path, key, method='GET', body=None, opener=None, timeout=60: keep[0](path, key, method, body, opener=globals()['opener'], timeout=timeout)
os.environ['NEON_API_KEY'] = 'k-env'
out = io.StringIO()
try:
    with contextlib.redirect_stdout(out):
        code = N.main(['--name', 'chuang-finance', '--usage'])
    text = out.getvalue()
    check("--usage: the project, each metric, the budget line, nothing provisioned",
          (code, 'proj-1' in text, 'data_transfer_bytes: 4300.8 MB' in text, 'network transfer: 4.20 GB of 5 GB (84%)' in text,
           any(c[0] == 'POST' for c in calls)), (0, True, True, True, False))
    calls.clear()
    N.provision = lambda key, name, **kw: {'project_id': 'proj-1', 'project_name': name, 'region_id': 'aws-us-west-2', 'pg_version': 18,
                                            'branch_id': 'br', 'database': 'ledger', 'role': 'r', 'host': 'h', 'created_project': False,
                                            'created_database': False, 'connection_uri': 'postgresql://' + 'u:' + 'p' + '@h/ledger'}
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = N.main(['--name', 'chuang-finance', '--database', 'ledger', '--cap-cu', '0.25'])
    check("--cap-cu after a provision: both endpoints patched, the line printed",
          (code, sum(1 for c in calls if c[0] == 'PATCH'), 'compute capped at 0.25 CU on 2 endpoint(s)' in out.getvalue()), (0, 2, True))
finally:
    N._call, N.provision = keep
    os.environ.pop('NEON_API_KEY', None)

print(f"\n{len(fails)} failure(s)" + (': ' + '; '.join(fails) if fails else ''))
sys.exit(1 if fails else 0)
