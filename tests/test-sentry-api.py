#!/usr/bin/env python3
"""chuang_platform_kit.sentry_api: Sentry's issues API, one client for every project (SMAD PickleBot #569).

Pinned (the positive case first): lookup() by short id and by numeric id with the clock readings in the client's
zone; resolve() sets the status and leaves the note naming the commit, the session and the post-mortem; unresolved()
walks the client's projects newest first; issues() follows the Link header's pages; latest_event() trims the event to
the triager's view; search() and comment(); run() names the actions and refuses an unknown one or a missing arg; a
403 is Refused naming the scope, any other status Refused with Sentry's words, no answer Refused with the error's
name; no token is Refused before any call. Offline: urllib is a fake.

Run:  python -B tests/test-sentry-api.py
"""
import io
import json
import os
import sys
import urllib.error

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import sentry_api as S  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class Resp(io.BytesIO):
    def __init__(self, body, link=''):
        super().__init__(json.dumps(body).encode() if not isinstance(body, bytes) else body)
        self.headers = {'Link': link}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener_for(answers: dict, calls: list):
    """answers: {(method, path or full url): body, (body, link), or an HTTP status to refuse with}."""
    def opener(req, timeout=0):
        key = (req.get_method(), req.full_url.replace(S.API, ''))
        calls.append((key[0], key[1], req.data and json.loads(req.data), req.headers.get('Authorization')))
        a = answers.get(key)
        if a is None:
            a = answers.get((key[0], req.full_url))
        if isinstance(a, int):
            raise urllib.error.HTTPError(req.full_url, a, 'no', {}, io.BytesIO(b'{"detail": "nope"}'))
        if isinstance(a, tuple):
            return Resp(*a)
        return Resp(a if a is not None else {})
    return opener


GROUP = {'id': '42', 'shortId': 'PROJ-6', 'status': 'unresolved', 'count': '3', 'userCount': 2, 'title': 'boom',
         'firstSeen': '2026-10-31T15:00:00Z', 'lastSeen': '2026-10-31T16:30:00Z', 'project': {'slug': 'proj-bot'},
         'permalink': 'https://sentry.io/x/6'}
calls = []
answers = {('GET', '/organizations/acme/shortids/PROJ-6/'): {'groupId': '42', 'group': GROUP},
           ('GET', '/organizations/acme/issues/42/'): GROUP,
           ('PUT', '/organizations/acme/issues/42/'): {},
           ('POST', '/organizations/acme/issues/42/comments/'): {'id': 'c1'}}
c = S.Client('acme', ('proj-bot', 'proj-api'), token='tok', opener=opener_for(answers, calls))
got = c.lookup('proj-6')
by_id = c.lookup('42')
done = c.resolve('PROJ-6', 'abc1234', session='Snow White', postmortem='https://x/pm')
check('the positive case: lookup by short id and by id, in the zone; resolve sets the status and leaves the note',
      (got['id'], got['shortId'], got['project'], got['lastSeenPT'], by_id['id'], calls[0][3], done['status'], done['note'],
       calls[-2][2], calls[-1][2]),
      ('42', 'PROJ-6', 'proj-bot', '10/31/26 9:30 AM PT', '42', 'Bearer tok', 'resolved',
       'Fixed by abc1234 (Snow White). Post-mortem: https://x/pm', {'status': 'resolved'},
       {'text': 'Fixed by abc1234 (Snow White). Post-mortem: https://x/pm'}))

calls2 = []
rows = [{'shortId': 'PROJ-1', 'count': '1', 'lastSeen': '2026-10-30T00:00:00Z', 'title': 'a'}]
rows2 = [{'shortId': 'PROJ-9', 'count': '9', 'lastSeen': '2026-10-31T00:00:00Z', 'title': 'b' * 100}]
page1 = f'<{S.API}/projects/acme/proj-bot/issues/?cursor=2>; rel="next"; results="true"'
answers2 = {('GET', '/projects/acme/proj-bot/issues/?query=is:unresolved&statsPeriod=14d'): rows,
            ('GET', '/projects/acme/proj-api/issues/?query=is:unresolved&statsPeriod=14d'): rows2,
            ('GET', '/projects/acme/proj-bot/issues/?query=is%3Aunresolved'): (rows, page1),
            ('GET', '/projects/acme/proj-bot/issues/?cursor=2'): (rows2, '<x>; rel="next"; results="false"'),
            ('GET', '/organizations/acme/issues/?query=level%3Aerror&limit=25&sort=date'): rows2}
c2 = S.Client('acme', ('proj-bot', 'proj-api'), token='tok', opener=opener_for(answers2, calls2))
un = c2.unresolved()
paged = c2.issues('proj-bot')
found = c2.search('level:error')
check('unresolved walks the projects newest first; issues() follows the pages; search() trims the titles',
      ([r['shortId'] for r in un], len(un[0]['title']), [r['shortId'] for r in paged], len(calls2), found[0]['shortId'], len(found[0]['title']),
       S.next_page(page1), S.next_page('<x>; rel="next"; results="false"'), S.next_page('')),
      (['PROJ-9', 'PROJ-1'], 90, ['PROJ-1', 'PROJ-9'], 5, 'PROJ-9', 100, f'{S.API}/projects/acme/proj-bot/issues/?cursor=2', '', ''))
cut, capped = c2.paged('/projects/acme/proj-bot/issues/?query=is%3Aunresolved', limit_pages=1)
whole, capped_whole = c2.paged('/projects/acme/proj-bot/issues/?query=is%3Aunresolved')
check('paged says when the list was cut at its cap with a page still to come',
      ([r['shortId'] for r in cut], capped, len(whole), capped_whole), (['PROJ-1'], True, 2, False))

event = {'message': 'boom', 'dateCreated': '2026-10-31T16:00:00Z', 'release': {'version': '1.2'},
         'tags': [{'key': 'env', 'value': 'prod'}],
         'entries': [{'type': 'exception', 'data': {'values': [{'type': 'KeyError', 'value': 'x', 'stacktrace': {'frames': [
             {'filename': 'a.py', 'lineNo': 1, 'function': 'f'}, {'filename': 'b.py', 'lineNo': 2, 'function': 'g'}]}}]}}]}
calls3 = []
answers3 = {**answers, ('GET', '/organizations/acme/issues/42/events/latest/'): event}
c3 = S.Client('acme', token='tok', opener=opener_for(answers3, calls3), zone='UTC')
ev = c3.latest_event('PROJ-6')
note = c3.comment('PROJ-6', 'a finding')
check("latest_event trims to the triager's view; comment leaves the note",
      (ev['exception'], ev['frames'], ev['tags'], ev['release'], ev['when'], ev['issue']['id'], note, calls3[-1][2]),
      ({'type': 'KeyError', 'value': 'x'}, ['a.py:1 f', 'b.py:2 g'], {'env': 'prod'}, '1.2', '10/31/26 4:00 PM UTC', '42',
       {'shortId': 'PROJ-6', 'commented': True}, {'text': 'a finding'}))

ran = c3.run('issue', {'id': 'PROJ-6'})
errs = []
for action, args in (('nothing', {}), ('comment', {'id': 'PROJ-6'}), ('resolve', {'id': 'PROJ-6'})):
    try:
        c3.run(action, args)
        errs.append('')
    except S.Refused as e:
        errs.append(str(e))
check('run names the actions; an unknown action or a missing arg is Refused',
      (ran['id'], errs[0].startswith('sentry: no action'), errs[1], errs[2]),
      ('42', True, 'sentry comment: needs text', 'sentry resolve: needs commit'))


def refusal(status):
    try:
        S.Client('acme', token='tok', opener=opener_for({('GET', '/organizations/acme/shortids/PROJ-6/'): status}, [])).lookup('PROJ-6')
        return ''
    except S.Refused as e:
        return str(e)


def down(req, timeout=0):
    raise urllib.error.URLError('down')


try:
    S.Client('acme', token='tok', opener=down).lookup('PROJ-6')
    no_answer = ''
except S.Refused as e:
    no_answer = str(e)
try:
    S.Client('acme', opener=opener_for({}, [])).lookup('PROJ-6')
    no_token = ''
except S.Refused as e:
    no_token = str(e)
check('a 403 is Refused naming the scope, another status with the words, no answer with the error, no token before any call',
      (refusal(403)[:40], 'lacks the scope' in refusal(403), refusal(500), no_answer, no_token),
      ('Sentry refused GET /organizations/acme/s', True, 'Sentry GET /organizations/acme/shortids/PROJ-6/ failed (500): {"detail": "nope"}',
       'Sentry GET /organizations/acme/shortids/PROJ-6/ did not answer (URLError)', 'no Sentry token: the host passes one to the client'))

check('pt: an ISO stamp in the zone with the label people write (PT in either season), a bare one as UTC, none as empty',
      (S.pt('2026-10-31T16:30:00Z'), S.pt('2026-01-08T16:30:00Z'), S.pt('2026-10-31T16:30:00', 'UTC'), S.pt(''), S.pt('garbage')),
      ('10/31/26 9:30 AM PT', '1/8/26 8:30 AM PT', '10/31/26 4:30 PM UTC', '', 'garbage'))

print()
print(f"{len(fails)} failure(s)" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
