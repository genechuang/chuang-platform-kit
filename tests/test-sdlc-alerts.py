#!/usr/bin/env python3
"""chuang_sdlc.alerts: an alert's incident filed as a GitHub issue once per opening, closed once it is fixed, and a
triage pass's create-only claim.

Pinned (the positive case first, on fakes and chuang_sdlc.testing's Records, never GitHub): act files a told incident
once (the second event finds its claim taken) and closes its open issue on Fixed; a triaged bug ticket is told once
and stays open; a kind that waits for a person is never closed; the host's words (title prefix, marker tag, subject
head, closer marks); the repository from configure (a callable) for the client; the opening's claim and the triage
claim in the configured collections; a collection not named is NotConfigured; the host's filed rule.

Run:  python -B tests/test-sdlc-alerts.py
"""
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))
os.environ.pop('CHUANG_PLATFORM_TZ', None)

from chuang_sdlc import alerts as A, tickets as T  # noqa: E402
from chuang_sdlc.testing import Records  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class FakeGH:
    """act's four calls, recorded."""

    def __init__(self, items):
        self.items, self.filed, self.closed, self.comments, self.labels = list(items), [], [], [], []

    def issues(self, pages=1):
        return list(self.items)

    def file(self, title, body):
        self.filed.append((title, body))
        return 618

    def close(self, number, why='', comment=''):
        self.closed.append((number, why))

    def comment(self, number, text):
        self.comments.append((number, text))

    def label(self, number, name):
        self.labels.append((number, name))


KEY = 'job:nightly'
INC = {'open': True, 'first_at': '2026-10-23T08:30:00+00:00', 'last_at': '2026-10-23T08:31:00+00:00', 'count': 2,
       'kind': 'job', 'label': 'The nightly job', 'subject': 'ACME: The nightly job failed', 'body': 'It failed.'}
A.configure(title_prefix='Cloud alert: ', marker_tag='cloud-alert', subject_head=r'^ACME:\s*',
            closer_marks=('Closed by the watcher:',), alert_titles=('Error issue ',), person_closes={'dlq': 'Wait for a person.'},
            repo=lambda: 'acme/app', collections={'claims': 'claims', 'triage': 'passes'}, claim_ttl_days=7)


def issue(n, key=KEY, first=INC['first_at'], state='open', labels=()):
    return {'number': n, 'state': state, 'labels': [{'name': x} for x in labels], 'body': f'x\n{A.marker(key, first)}'}


claims = set()
claim = lambda cid: cid not in claims and not claims.add(cid)  # noqa: E731
gh = FakeGH([issue(9, 'job:other')])
first, second = A.act(KEY, INC, gh, claim), A.act(KEY, INC, gh, claim)
check('the positive case: filed once, the second event finds its claim taken',
      (len(gh.filed), first, 'already filed' in second[0]), (1, [f'alert issues: {KEY} filed as #618'], True))
check("the host's title (its subject's head dropped) and marker", (gh.filed[0][0], gh.filed[0][1].splitlines()[-1]),
      ('Cloud alert: The nightly job failed', f'<!-- cloud-alert: {KEY} @ {INC["first_at"]} -->'))
fixed = dict(INC, open=False, closed_at='2026-10-23T09:29:15+00:00')
gh = FakeGH([issue(7), issue(9, 'job:other')])
A.act(KEY, fixed, gh, claim)
check("closed on Fixed, another incident's issue left", [(n, 'said Fixed at Fri 10/23/26 2:29 AM PT' in w) for n, w in gh.closed],
      [(7, True)])
gh = FakeGH([issue(8, labels=['bug'])])
lines = A.act(KEY, fixed, gh, claim)
check('a triaged bug ticket stays open, told once with the cleared label', (gh.closed, gh.labels, any('told its alert cleared' in ln for ln in lines)),
      ([], [(8, 'alert-cleared')], True))
check('a kind that waits for a person is never closed', A.plan({KEY: dict(fixed, kind='dlq')}, [issue(7)])[1], [])
check("an alert closer's close, by its mark or an alert title", (A.closed_by_closer('t', 'Closed by the watcher: fixed'),
                                                                A.closed_by_closer('Error issue X-1: boom', ''),
                                                                A.closed_by_closer('Cloud alert: x', ''), A.closed_by_closer('t', 'done')),
      (True, True, True, False))
check('the client takes the repository from configure', A.GitHub('tok').repo, 'acme/app')


# 0.28.0 (SMADPickleBot #1586): `issues` finds an alert issue by its body marker's tag as well as its title, so one
# retitled to its fix is still found and never filed a second time.
class Searched(A.GitHub):
    """GitHub.issues over canned search answers: {a word of the query: its items}, every query kept."""

    def __init__(self, answers, **kw):
        super().__init__('tok', **kw)
        self.answers, self.queries = answers, []

    def _call(self, method, path, body=None):
        import urllib.parse
        q = urllib.parse.unquote(path.split('q=', 1)[1].split('&', 1)[0])
        self.queries.append(q + (' page 2' if '&page=2' in path else ''))
        return {'items': next((v for k, v in self.answers.items() if k in q), [])}


def found(n, title, created, state='OPEN'):
    return {'number': n, 'title': title, 'created_at': created, 'state': state, 'body': f'<!-- err: {n} -->'}


RETITLED = found(1583, 'The app no longer restarts for an update', '2026-11-16T03:42:00Z')
TITLED = found(1354, 'Error issue X-23: slow', '2026-11-10T01:00:00Z', 'CLOSED')
gh = Searched({'in:title': [TITLED], 'in:body': [RETITLED, TITLED]}, prefix='Error issue ', tag='err')
got = gh.issues()
check('the positive case: a retitled issue is found by its body marker, each issue once, newest first, states lowered',
      ([(i['number'], i['state']) for i in got], gh.queries),
      ([(1583, 'open'), (1354, 'closed')],
       ['repo:acme/app is:issue "Error issue" in:title', 'repo:acme/app is:issue "err" in:body']))
gh = Searched({'in:title': [TITLED], 'in:body': [RETITLED]}, prefix='Error issue ')
check("a sweep's own prefix with no tag searches titles alone, as before 0.28.0",
      ([i['number'] for i in gh.issues()], len(gh.queries)), ([1354], 1))
gh = Searched({'in:title': [], 'in:body': [RETITLED]})
check("this module's own prefix brings its own marker tag", ([i['number'] for i in gh.issues()], gh.tag, len(gh.queries)),
      ([1583], A._SET['marker_tag'], 2))
full = [found(n, 'Error issue', f'2026-10-{n % 28 + 1:02d}T00:00:00Z') for n in range(100)]
gh = Searched({'in:title': full, 'in:body': []}, prefix='Error issue ', tag='err')
check('each search pages on its own: a full first page asks for the second, a short one stops',
      (len(gh.issues(pages=2)), [q.endswith('page 2') for q in gh.queries]), (100, [False, True, False]))
store = Records()
now = datetime(2026, 10, 23, 9, tzinfo=timezone.utc)   # utc-ok: a fixed instant for the fakes, as an event's `at` is UTC
cid = A.claim_key(KEY, INC)
check("the opening's claim in the claims collection, once", (A.claim_opening(store, KEY, cid, now), A.claim_opening(store, KEY, cid, now),
                                                          A.opening_claimed(store, KEY, INC), store.get('claims', cid)['expires_at'].day),
      (True, False, True, 30))
check('a triage claim: taken once, then held by whom; released', (A.triage_claim(5, 'Ann', store, lambda: 100),
                                                                  A.triage_claim(5, 'Bob', store, lambda: 200),
                                                                  A.triage_release(5, store), store.get('passes', '5')),
      ((True, 'claimed #5'), (False, '#5 is taken: Ann since 100'), 'released #5', None))
A.configure(collections={'triage': ''})
try:
    A.triage_claim(6, 'Ann', store, lambda: 1)
    check('a collection the host named none of is NotConfigured', 'claimed', 'NotConfigured')
except T.NotConfigured:
    check('a collection the host named none of is NotConfigured', True, True)
A.configure(filed=lambda inc: inc.get('kind') == 'job')
check("the host's filed rule", (A.is_filed({'kind': 'job'}), A.is_filed(INC | {'kind': 'other'})), (True, False))
try:
    A.configure(nope=1)
    check('configure refuses an unknown name', 'accepted', 'TypeError')
except TypeError:
    check('configure refuses an unknown name', True, True)

print(f'\n{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
