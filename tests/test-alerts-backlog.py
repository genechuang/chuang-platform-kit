#!/usr/bin/env python3
"""chuang_alerts.backlog: a Pub/Sub subscription's backlog read from Cloud Monitoring and sorted by kind (chuang-alerts
0.1.0, SMADPickleBot #1964; lifted from SMAD PickleBot's watchdog `check_subscriptions`).

Pinned (positive case first): a push subscription with a message older than AGE_S is `refusing`; one pulled by the
host the same way is `pulled`; quiet and info prefixes take any size; a small young backlog is `in_flight`, an older
one `small`; an empty subscription is no finding; the dead-letter holds are never read; Monitoring's pages are all read.

Run:  python -B tests/test-alerts-backlog.py
"""
import datetime as dt
import io
import json
import os
import sys
import urllib.parse

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'alerts', 'src'))

from chuang_alerts import backlog as B  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


check("the positive case: a push subscription whose oldest message is past AGE_S is refusing",
      B.classify({'events-sub': {'undelivered': 3, 'oldest_s': 400}}), [('refusing', 'events-sub', 3, 400)])

found = {'a-push': {'undelivered': 25, 'oldest_s': 30}, 'desk': {'undelivered': 1, 'oldest_s': 900},
         'ci-runs': {'undelivered': 500, 'oldest_s': 9000}, 'mail-me': {'undelivered': 40, 'oldest_s': 5000},
         'young': {'undelivered': 2, 'oldest_s': 10}, 'older': {'undelivered': 2, 'oldest_s': 90},
         'empty': {'undelivered': 0, 'oldest_s': 0}}
check("over COUNT waiting is stuck; a pulled one is `pulled`; quiet and info prefixes at any size; in flight and small; "
      "an empty subscription is no finding",
      B.classify(found, quiet=('ci-',), info=('mail-',), pulled=('desk',)),
      [('refusing', 'a-push', 25, 30), ('quiet', 'ci-runs', 500, 9000), ('pulled', 'desk', 1, 900),
       ('info', 'mail-me', 40, 5000), ('small', 'older', 2, 90), ('in_flight', 'young', 2, 10)])
check("the thresholds are the host's",
      B.classify({'s': {'undelivered': 5, 'oldest_s': 100}}, age_s=60, count=50), [('refusing', 's', 5, 100)])
check("a dead-letter hold is recognised by either suffix",
      [B.is_dead_letter_hold(n) for n in ('votes-dead-letter', 'whatsapp-messages-dlq', 'votes-worker')],
      [True, True, False])
check("an age is said in minutes from two minutes on", [B.minutes(45), B.minutes(119), B.minutes(420)],
      ['45 s', '119 s', '7 min'])

asked = []


def monitoring(req, timeout=None):
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)
    asked.append((req.full_url.split('?')[0], q.get('pageToken', [''])[0], req.headers.get('Authorization')))
    metric = 'count' if 'num_undelivered' in q['filter'][0] else 'age'
    page = q.get('pageToken', [''])[0]
    if metric == 'count' and not page:
        body = {'timeSeries': [{'resource': {'labels': {'subscription_id': 'a'}}, 'points': [{'value': {'int64Value': '4'}}]},
                               {'resource': {'labels': {'subscription_id': 'x-dead-letter'}}, 'points': [{'value': {'int64Value': '9'}}]}],
                'nextPageToken': 'p2'}
    elif metric == 'count':
        body = {'timeSeries': [{'resource': {'labels': {'subscription_id': 'b'}}, 'points': [{'value': {'int64Value': '1'}}]}]}
    else:
        body = {'timeSeries': [{'resource': {'labels': {'subscription_id': 'a'}}, 'points': [{'value': {'doubleValue': 33.0}}]}]}
    return Answer(json.dumps(body).encode())


now = dt.datetime(2026, 11, 19, 6, 0, tzinfo=dt.timezone.utc)  # utc-ok: Cloud Monitoring's interval is UTC
got = B.backlogs('proj-1', 'tok', now, opener=monitoring)
check("every page read, both metrics joined, the dead-letter hold left out",
      got, {'a': {'undelivered': 4, 'oldest_s': 33}, 'b': {'undelivered': 1, 'oldest_s': 0}})
check("the project's own URL and the token sent; the second page asked by its token",
      (asked[0][0], asked[0][2], [p for _u, p, _t in asked]),
      ('https://monitoring.googleapis.com/v3/projects/proj-1/timeSeries', 'Bearer tok', ['', 'p2', '']))

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
