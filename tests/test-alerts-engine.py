#!/usr/bin/env python3
"""chuang_alerts.engine: an alert watcher's engine, configured by its host (SMADPickleBot #1966).

Pinned (the positive case first):

- a service's first ERROR line is told at once, once, with the host's subject prefix and zone label, and kept as an
  open incident in the host's collection, its write handed to the host's publish_incident
- the same service's next ERROR within a minute is folded in memory (no store read, nothing told); the sweep writes
  the fold in, and 30 quiet minutes later, a new revision serving, the sweep tells its Fixed once and closes it
- a job's ERROR lines are held under its execution and told once, at the execution's end, with its step summary
- configure: the watcher's own service is required, an unknown setting refused, and the watcher's own lines ignored

Run:  python -B tests/test-alerts-engine.py
"""
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'alerts', 'src'))

from chuang_alerts import engine as E  # noqa: E402

fails = []
UTC = timezone.utc   # utc-ok: fixed test instants


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=UTC)   # utc-ok: fixed test instants


class Store:
    """The store: documents by (collection, id), every call counted."""

    def __init__(self):
        self.d, self.gets = {}, 0

    def get(self, coll, key):
        self.gets += 1
        v = self.d.get((coll, key))
        return dict(v) if v is not None else None

    def set(self, coll, key, data):
        self.d[(coll, key)] = dict(data)
        if coll == E._CFG['incidents'] and E._CFG['publish_incident']:
            E._CFG['publish_incident'](key, data)

    def delete(self, coll, key):
        self.d.pop((coll, key), None)

    def all(self, coll):
        return [(k, dict(v)) for (c, k), v in list(self.d.items()) if c == coll]


class Google:
    """Google's reads: the serving revisions, an execution's lines, a job's history."""

    def __init__(self, serving=None, lines=None):
        self.serving, self.lines = serving or set(), lines or []

    def serving_revisions(self, service):
        return set(self.serving)

    def execution_entries(self, job, execution, since):
        return list(self.lines)

    def executions(self, job):
        return []


class Outbox:
    def __init__(self):
        self.sent = []

    def __call__(self, subject, body, link=''):
        self.sent.append((subject, body, link))
        return ['slack']


PUBLISHED = []
ENTRY = {'name': 'the web site', 'broke': 'the site answered with an error', 'who': 'every visitor', 'todo': 'read it'}
JOB = {'name': 'the nightly job', 'broke': 'the nightly job failed', 'who': 'nobody', 'todo': 'rerun it', 'next': 'tomorrow'}
JOBS = {'nightly': {'steps': {'fetch': {'broke': 'nothing was fetched', 'who': 'the readers', 'todo': 'fetch by hand'},
                              'post': {}}}}


def configure():
    E._FOLD.clear()
    E._JOB_FULL.clear()
    return E.configure(project='proj', region='r1', incidents='inc', pending='pend', service='watcher',
                       subject_prefix='HOST', zone=UTC, zone_label='UTC', logger=logging.getLogger('test-engine'),
                       jobs=JOBS, job_entry=lambda job, variant: dict(JOB), service_entry=lambda s, line='': dict(ENTRY),
                       line_rule=lambda s, line: None, publish_incident=lambda k, inc: PUBLISHED.append((k, inc['open'])))


def ctx(store, out, now, google=None):
    return E.Context(store=store, google=google or Google(), send=out, now=at(now))


def service_line(when, revision='site-001'):
    return {'resource': {'type': 'cloud_run_revision', 'labels': {'service_name': 'site', 'revision_name': revision}},
            'severity': 'ERROR', 'timestamp': when, 'textPayload': 'Traceback: boom'}


# The positive case: a service's first error is told at once and kept.
cfg = configure()
store, out = Store(), Outbox()
said = E.handle(service_line('2026-10-10T18:00:00Z'), ctx(store, out, '2026-10-10T18:00:00'))
inc = store.d.get(('inc', 'service:site'))
check('a service ERROR is told at once, once, with the host prefix and zone label, and kept in the host collection',
      (said, len(out.sent), out.sent[0][0], bool(inc and inc['open']), inc and inc['revision'], PUBLISHED[-1]),
      ('service:site: told', 1, 'HOST: the site answered with an error (site, Sat 10/10/26 6:00 PM UTC)', True, 'site-001',
       ('service:site', True)))
check('  its body says what broke, who it hurts and what to do',
      [line.split(':')[0] for line in out.sent[0][1].split('\n')],
      ['What broke', ' - Traceback', 'Still fine', "Who's affected", 'Status', 'What to do'])
check('  its link is the service log page in the host project', out.sent[0][2],
      'https://console.cloud.google.com/run/detail/r1/site/logs?project=proj')

# A repeat within the debounce is counted, then the repeats within the fold's minute need no store read.
said2 = E.handle(service_line('2026-10-10T18:00:20Z'), ctx(store, out, '2026-10-10T18:00:20'))
gets = store.gets
said3 = E.handle(service_line('2026-10-10T18:00:40Z', 'site-002'), ctx(store, out, '2026-10-10T18:00:40'))
check('the next error is counted, and one within its fold minute is folded with no store read and nothing told',
      (said2, said3.split(' (')[0], store.gets - gets, len(out.sent), E._FOLD['service:site']['n']),
      ('service:site: counted (1 since the last message)', 'service:site: folded', 0, 1, 1))

# The sweep writes the fold in; 30 quiet minutes later, a new revision serving, the Fixed is told once.
done = E.sweep(ctx(store, out, '2026-10-10T18:05:00', Google(serving={'site-001'})))
inc = store.d[('inc', 'service:site')]
check('the sweep writes the folded error into the incident, tells nothing yet',
      (done, inc['count'], inc['revision'], len(out.sent), E._FOLD),
      (['service:site: 1 folded error(s) written in'], 3, 'site-002', 1, {}))
done = E.sweep(ctx(store, out, '2026-10-10T18:31:00', Google(serving={'site-003'})))
check('30 quiet minutes and a revision other than the last failing one serving: Fixed, once, and the incident closed',
      (done, len(out.sent), out.sent[-1][0], store.d[('inc', 'service:site')]['open'], PUBLISHED[-1]),
      (['service:site: Fixed sent'], 2, 'HOST: Fixed: site (Sat 10/10/26 6:31 PM UTC)', False, ('service:site', False)))
check('  a second sweep tells nothing more', (E.sweep(ctx(store, out, '2026-10-10T18:45:00')), len(out.sent)), ([], 2))

# The same revision still serving after a quiet spell: quiet, not fixed.
store2, out2 = Store(), Outbox()
E.handle(service_line('2026-10-10T18:00:00Z'), ctx(store2, out2, '2026-10-10T18:00:00'))
E.sweep(ctx(store2, out2, '2026-10-10T18:31:00', Google(serving={'site-001'})))
check('the failing revision still serving after 30 quiet minutes: "quiet, not fixed", never Fixed',
      out2.sent[-1][0], 'HOST: quiet, not fixed: site (Sat 10/10/26 6:31 PM UTC)')

# A job's lines are held under its execution and told once, at its end, with its step summary.
store3, out3 = Store(), Outbox()
line = {'resource': {'type': 'cloud_run_job', 'labels': {'job_name': 'nightly'}},
        'labels': {'run.googleapis.com/execution_name': 'nightly-abc'}, 'severity': 'ERROR',
        'timestamp': '2026-10-10T08:00:05Z', 'jsonPayload': {'message': 'nightly fetch failed (exit 1): no answer', 'step': 'fetch'}}
held = E.handle(line, ctx(store3, out3, '2026-10-10T08:00:05'))
sent_at_hold = len(out3.sent)
summary = {'jsonPayload': {'steps': {'fetch': 1, 'post': 0}}, 'severity': 'INFO'}
end = {'resource': {'type': 'cloud_run_job', 'labels': {'job_name': 'nightly'}},
       'logName': 'projects/proj/logs/cloudaudit.googleapis.com%2Fsystem_event', 'timestamp': '2026-10-10T08:01:00Z',
       'protoPayload': {'resourceName': 'namespaces/proj/executions/nightly-abc',
                        'status': {'message': 'Execution nightly-abc has failed to complete, 1/1 tasks failed.'},
                        'response': {'metadata': {'creationTimestamp': '2026-10-10T08:00:00Z'},
                                     'spec': {'template': {'spec': {'containers': [{'args': ['-u', 'run.py']}]}}},
                                     'status': {'failedCount': 1, 'completionTime': '2026-10-10T08:01:00Z'}}}}
told = E.handle(end, ctx(store3, out3, '2026-10-10T08:01:00', Google(lines=[line, summary])))
check("a job's lines are held, nothing told until its end", (held, sent_at_hold, ('pend', 'nightly-abc') in store3.d),
      ('nightly-abc: held', 0, True))
check('  its end tells one message with the failed step first, the passing step still fine',
      (told.split(' (')[0], len(out3.sent), out3.sent[0][0], out3.sent[0][1].split('\n')[:3]),
      ('nightly-abc: told', 1, 'HOST: nothing was fetched (nightly, Sat 10/10/26 8:00 AM UTC)',
       ['What broke: the nightly job, run nightly-abc of nightly, started Sat 10/10/26 8:00 AM UTC. 1 of 2 steps failed.',
        ' - fetch: no answer', 'Still fine: post']))
check('  a second end of the same execution is not told again',
      (E.handle(end, ctx(store3, out3, '2026-10-10T08:02:00', Google(lines=[line, summary]))), len(out3.sent)),
      ('nightly-abc: already told', 1))

# configure itself.
try:
    E._CFG['service'] = None
    E.configure(project='x')
    refused = ''
except ValueError as e:
    refused = str(e)
check("configure refuses to run without the watcher's own service name", 'is required' in refused, True)
configure()
try:
    E.configure(service='watcher', bogus=1)
    unknown = ''
except TypeError as e:
    unknown = str(e)
check('configure refuses an unknown setting by name', 'bogus' in unknown, True)
own = {'resource': {'type': 'cloud_run_revision', 'labels': {'service_name': 'watcher'}}, 'severity': 'ERROR'}
check("the watcher's own line is ignored", E.handle(own, ctx(Store(), Outbox(), '2026-10-10T18:00:00')),
      'my own line: ignored (the backstop policy covers it)')
check('configure answers the settings in force', (cfg['incidents'], cfg['subject_prefix'], cfg['service']),
      ('inc', 'HOST', 'watcher'))
check('the fold and the held set are module objects, never replaced', (E._FOLD is E.__dict__['_FOLD'],
      type(E._JOB_FULL)), (True, set))

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
