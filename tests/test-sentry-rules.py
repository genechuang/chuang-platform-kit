#!/usr/bin/env python3
"""sentry_rules: which Sentry issues are alarms (error or fatal, production, not a developer's machine).

Run:  python -B tests/test-sentry-rules.py
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import sentry_rules as R  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


DEV = ('dev.example.com', 'QA.example.com')

# The positive case: a production error is an alarm.
check("a production error is an alarm", R.is_alarm({'level': 'error'}, {'environment': 'prod'}), (True, ''))
check("fatal", R.is_alarm({'level': 'fatal'}, {'environment': 'prod'}), (True, ''))
check("the old word for prod is still prod", R.is_alarm({'level': 'fatal'}, {'environment': 'production'}), (True, ''))
check("no environment reads as production", R.is_alarm({'level': 'error'}, {'tags': [['url', 'https://example.com/app/']]}), (True, ''))

check("tags: the API's dicts", R.tags({'tags': [{'key': 'a', 'value': '1'}]}), {'a': '1'})
check("tags: a webhook's pairs", R.tags({'tags': [['a', '1']]}), {'a': '1'})
check("tags: no event", (R.tags(None), R.tags({})), ({}, {}))

check("no event, an error: the level decides", R.is_alarm({'level': 'error'}, {}), (True, ''))
check("no event, info", R.is_alarm({'level': 'info'}, None), (False, 'level info, not error or fatal'))
check("no level at all", R.is_alarm({}, None), (False, 'level unknown, not error or fatal'))
check("the event's level when the issue has none", R.is_alarm({}, {'level': 'error', 'environment': 'prod'}), (True, ''))

check("a dev rung is not production", R.is_alarm({'level': 'error'}, {'environment': 'dev'}), (False, 'environment dev, not prod'))
check("the environment as a tag", R.is_alarm({'level': 'error'}, {'tags': [['environment', 'qa']]}), (False, 'environment qa, not prod'))

# A container's own hostname is localhost: never a sign of a developer.
cloud = {'environment': 'prod', 'tags': [['service', 'worker'], ['server_name', 'localhost']]}
check("a service at server_name localhost is an alarm", R.is_alarm({'level': 'error'}, cloud), (True, ''))
check("a developer's box is told by its service tag",
      R.is_alarm({'level': 'error'}, {'environment': 'prod', 'tags': [['service', 'local'], ['server_name', 'localhost']]}),
      (False, "a developer's machine (service tag local)"))
check("a page at localhost is a developer's",
      R.is_alarm({'level': 'error'}, {'tags': [['url', 'http://localhost:8090/app']]}), (False, "a developer's machine (localhost)"))
check("a request at a loopback is a developer's",
      R.is_alarm({'level': 'error'}, {'request': {'url': 'http://127.0.0.1:8000/x'}}), (False, "a developer's machine (127.0.0.1)"))

check("a development host the caller names, in a page's url",
      R.is_alarm({'level': 'error'}, {'request': {'url': 'https://dev.example.com/app/'}}, dev_hosts=DEV),
      (False, "a developer's machine (dev.example.com)"))
check("its name in any case, in server_name",
      R.is_alarm({'level': 'error'}, {'tags': [['server_name', 'qa.example.com']]}, dev_hosts=DEV),
      (False, "a developer's machine (qa.example.com)"))
check("only a whole name counts", R.is_alarm({'level': 'error'}, {'tags': [['url', 'https://notdev.example.com/']]}, dev_hosts=DEV), (True, ''))
check("with no development hosts named, that host is production",
      R.is_alarm({'level': 'error'}, {'request': {'url': 'https://dev.example.com/app/'}}), (True, ''))
check("a url that cannot be parsed is no host", R.is_alarm({'level': 'error'}, {'tags': [['url', 'http://[bad']]}), (True, ''))

check("the Android emulator and the simulator are alarms, a dev-rung test build too; a dev rung otherwise is not",
      (R.is_alarm({'level': 'fatal'}, {'tags': [['device', 'sdk_gphone64_x86_64']]}),
       R.is_alarm({'level': 'fatal'}, {'tags': [['simulator', 'true']]}),
       R.is_alarm({'level': 'error'}, {'environment': 'dev', 'tags': [['device', 'sdk_gphone64_x86_64']]}),
       R.is_alarm({'level': 'error'}, {'environment': 'dev', 'tags': [['emulator', 'True']]}),
       R.is_alarm({'level': 'error'}, {'environment': 'dev', 'tags': [['device', 'Pixel 8']]})[0],
       R.is_alarm({'level': 'fatal'}, {'tags': [['device', 'Pixel 8']]})),
      ((True, ''), (True, ''), (True, ''), (True, ''), False, (True, '')))

perf = {'environment': 'prod', 'tags': [['service', 'api'], ['server_name', 'localhost']]}
check("a performance issue at info is an alarm, by category, type or title; one from a developer's box is not",
      (R.is_alarm({'level': 'info', 'issueCategory': 'performance'}, perf),
       R.is_alarm({'level': 'info', 'issueType': 'performance_n_plus_one_db_queries'}, perf)[0],
       R.is_alarm({'level': 'info', 'title': 'Consecutive HTTP'}, perf)[0],
       R.is_alarm({'level': 'info', 'title': 'Slow page'}, perf)[0],
       R.is_alarm({'level': 'info', 'issueCategory': 'performance'}, {'tags': [['service', 'local']]})[0]),
      ((True, ''), True, True, False, False))
check("is_performance on nothing", (R.is_performance(None), R.is_performance({})), (False, False))

print(f"\n{len(fails)} failure(s)")
if fails:
    sys.exit(1)
