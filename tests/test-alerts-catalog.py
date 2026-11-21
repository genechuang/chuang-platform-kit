#!/usr/bin/env python3
"""chuang_alerts.catalog: an alert catalog's lookups over the host's own tables.

Pinned (the positive case first):

- a listed job's entry with its variant's keys over its own, `variants` itself left out; a dev twin of a listed job
  says a release failed on its dev rung and was rolled back; an unlisted job names itself, the host's unknown words
- a schedule by its id, else by the first prefix it starts with, else one that names it
- a task action by name, else one that names it (or "(unnamed)")
- a service's line rule wins over its own entry, only for its own service and only when the pattern matches; an
  unlisted service names itself
- a dead letter by topic, else one that names the topic; a build by its tag, else the untagged one

Run:  python -B tests/test-alerts-catalog.py
"""
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'alerts', 'src'))

from chuang_alerts import catalog as C  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


U = 'not catalogued (host)'
JOBS = {'smad-nightly': {'name': 'the nightly job', 'broke': 'nightly failed', 'who': 'players', 'todo': 'rerun',
                         'next': 'tomorrow', 'variants': {'book': {'broke': 'a court may not be booked'}}}}

check('a listed job with its variant over its own keys, variants left out',
      C.job_entry(JOBS, 'smad-nightly', 'book', U),
      {'name': 'the nightly job', 'broke': 'a court may not be booked', 'who': 'players', 'todo': 'rerun', 'next': 'tomorrow'})
check('a listed job with no such variant is its own entry', C.job_entry(JOBS, 'smad-nightly', 'other', U)['broke'],
      'nightly failed')
twin = C.job_entry(JOBS, 'smad-nightly-dev', '', U)
check('a dev twin of a listed job: a release rolled back on its dev rung',
      (twin['name'], twin['broke']), ('the nightly job, its dev twin smad-nightly-dev during a deploy',
                                      'a release of smad-nightly failed on its dev rung and was rolled back'))
check('a twin of an unlisted job, and an unlisted job, name themselves with the unknown words',
      (C.job_entry(JOBS, 'x-dev', '', U)['name'], C.job_entry(JOBS, 'x', '', U)),
      ('the Cloud Run Job x-dev', {'name': 'the Cloud Run Job x', 'broke': 'x failed', 'who': U, 'todo': U,
                                    'next': 'its next scheduled start'}))

SCHED, PREF = {'daily-8am': {'name': 'the 8 AM run'}}, {'game-': {'name': 'a game action'}, 'game-cool-': {'name': 'never'}}
check('a schedule by id, else the first prefix, else one that names it',
      (C.schedule_entry(SCHED, 'daily-8am', PREF, U)['name'], C.schedule_entry(SCHED, 'game-cool-7', PREF, U)['name'],
       C.schedule_entry(SCHED, 'nope', PREF, U)),
      ('the 8 AM run', 'a game action', {'name': 'the Cloud Scheduler job nope',
                                         'broke': 'the schedule nope could not start its run', 'who': U, 'todo': U}))

check('a task action by name, else one that names it',
      (C.task_entry({'check': {'name': 'the court check'}}, 'check', U)['name'], C.task_entry({}, '', U)['name']),
      ('the court check', 'the game action (unnamed)'))

SERVICES = {'svc': {'name': 'the service'}}
RULES = (('svc', r'GREEN-API (down|unreachable)', {'name': 'the connection check'}),
         ('other', r'.', {'name': 'not this service'}))
check('a line rule wins for its own service when it matches; else the service; else one that names it',
      (C.service_entry(SERVICES, 'svc', 'oops: GREEN-API unreachable', RULES)['name'],
       C.service_entry(SERVICES, 'svc', 'something else', RULES)['name'], C.line_rule(RULES, 'svc', ''),
       C.line_rule(RULES, 'other', 'x'), C.service_entry(SERVICES, 'ghost', '', RULES, U)['who']),
      ('the connection check', 'the service', None, 1, U))

check('a dead letter by topic, else one that names the topic; a build by tag, else the untagged one',
      (C.dead_letter_entry({'votes-dl': {'queue': 'votes'}}, 'votes-dl', U)['queue'],
       C.dead_letter_entry({}, '', U)['name'], C.build_entry({'': {'name': 'a deploy'}, 'tests': {'name': 'tests'}}, 'x')['name'],
       C.build_entry({'': {}, 'tests': {'name': 'tests'}}, 'tests')['name']),
      ('votes', 'the queue behind an unnamed topic', 'a deploy', 'tests'))

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
