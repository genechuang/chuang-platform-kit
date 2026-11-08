#!/usr/bin/env python3
"""chuang_sdlc.titles: what a range of commits shipped, in its tickets' own words.

Pinned (the positive case first): the readers' Area's titles, each ticket once in its first commit's order, a closing
period and the host's tail dropped, a bug after the `fixed` words, the other Areas one count; a ticket GitHub did not
answer for is unknown, one it says does not exist left out; a commit with no trailer counts through `mapped` (given, or
configure's); fetch reads the board's repository over REST with the host's token, labels lower-cased, 404 missing, any
other failure None with a WARNING; configure(repo=) and configure(bug=) override the board's; a bug or member-title
label configured with capitals is lower-cased to match; no tail leaves a title as it is; no Area is NotConfigured; configure refuses an unknown name. GitHub is a fake opener.

Run:  python -B tests/test-sdlc-titles.py
"""
import contextlib
import io
import json
import os
import sys
import urllib.error

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))

from chuang_sdlc import tickets as T  # noqa: E402
from chuang_sdlc import titles as TT  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


CFG = {'repo': 'acme/app', 'labels': {'Feature': 'feature', 'Bug': 'defect'}}
T.configure(board=lambda: CFG, labels={'member_title': 'reword'})
TT.configure(area='Front Desk', tail=r'\s*\(OLD-\d+\)\s*$', token=lambda: 't0k', mapped=lambda: {'abc1234': [3]})

ISSUES = {
    1: {'number': 1, 'title': 'Scores save the moment you tap.', 'labels': ['feature', 'area:front-desk']},
    2: {'number': 2, 'title': 'The poll opens on time', 'labels': ['defect', 'area:front-desk']},
    3: {'number': 3, 'title': 'Deploys climb faster', 'labels': ['task', 'area:pipeline']},
    4: {'number': 4, 'title': 'Cancel shows its reason (OLD-54)', 'labels': ['task', 'area:front-desk']},
}


def c(sha, *numbers):
    trailer = ', '.join(f'#{n}' for n in numbers) if numbers else 'none (notes)'
    return {'sha': sha, 'message': f'subject\n\n- a bullet\n\nTicket: {trailer}\n'}


print('shipped')
check('the positive case: the Area\'s titles once each in first-commit order, a bug after its words, one count behind',
      TT.shipped([c('a1', 4), c('a2', 1, 3), c('a3', 2), c('a4', 4), c('a5')], mapped={}, get=ISSUES.get),
      {'new': ['Cancel shows its reason', 'Scores save the moment you tap'], 'fixed': ['Fixed: The poll opens on time'],
       'behind': 1, 'unknown': []})
check('unknown and missing',
      TT.shipped([c('b1', 1), c('b2', 9), c('b3', 8)], mapped={},
                 get=lambda n: {9: None, 8: {'number': 8, 'missing': True}}.get(n, ISSUES.get(n))),
      {'new': ['Scores save the moment you tap'], 'fixed': [], 'behind': 0, 'unknown': [9]})
check('read: (number, ticket) in first-commit order', TT.read([c('d1', 2, 1), c('d2', 1)], {}, ISSUES.get),
      [(1, ISSUES[1]), (2, ISSUES[2])])

print('numbers_of')
check('a commit before the trailer rule counts through `mapped` by its sha prefix',
      TT.numbers_of([{'sha': 'd7bf5950abc', 'message': 'old subject'}, c('e1', 2)], mapped={'d7bf5950': [1]}),
      {1: ['d7bf5950abc'], 2: ['e1']})
check("configure's mapped when none is given", TT.numbers_of([{'sha': 'abc1234ff', 'message': 'old'}]),
      {3: ['abc1234ff']})
check('`Ticket: none (why)` names none', TT.numbers_of([c('f1')], mapped={}), {})


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


seen = []


def ok(req, timeout=0):
    seen.append((req.full_url, req.headers.get('Authorization')))
    return _Resp(json.dumps({'title': ' Scores save ', 'state': 'closed',
                             'labels': [{'name': 'Feature'}, {'name': 'area:Front-Desk'}]}).encode())


def gone(req, timeout=0):
    raise urllib.error.HTTPError(req.full_url, 404, 'Not Found', {}, None)


def down(req, timeout=0):
    raise urllib.error.URLError('timed out')


print('fetch')
out = io.StringIO()
with contextlib.redirect_stdout(out):
    got, missing, failed = TT.fetch(5, ok), TT.fetch(7, gone), TT.fetch(8, down)
check('the positive case: title stripped, labels lower-cased, the state; the board\'s repo and the host\'s token',
      (got, seen), ({'number': 5, 'title': 'Scores save', 'labels': ['feature', 'area:front-desk'], 'state': 'closed'},
                    [('https://api.github.com/repos/acme/app/issues/5', 'Bearer t0k')]))
check('404 is missing; no answer is None with a WARNING',
      (missing, failed, 'WARNING: ticket #8 could not be read from GitHub' in out.getvalue()),
      ({'number': 7, 'missing': True}, None, True))

print('settings')
check('the board\'s repo and Bug label; the member-title label is tickets\'',
      (TT.repo(), TT.bug_label(), TT.member_label(), TT.issue_url(12)),
      ('acme/app', 'defect', 'reword', 'https://github.com/acme/app/issues/12'))
TT.configure(repo='acme/other', bug='bug')
check('configure(repo=, bug=) over the board\'s', (TT.repo(), TT.line({'title': 'X', 'labels': ['bug']})),
      ('acme/other', 'Fixed: X'))
CFG['labels']['Bug'] = 'Defect'
TT.configure(bug='')
board_bug = TT.bug_label()
CFG['labels']['Bug'] = 'defect'
TT.configure(bug='DEFECT')
T.configure(labels={'member_title': 'Title:Member'})
check('a label configured with capitals is lower-cased, as fetch reads labels, so a bug is still Fixed',
      (board_bug, TT.bug_label(), TT.member_label(), TT.shipped([c('h1', 2)], mapped={}, get=ISSUES.get)),
      ('defect', 'defect', 'title:member',
       {'new': [], 'fixed': ['Fixed: The poll opens on time'], 'behind': 0, 'unknown': []}))
T.configure(labels={'member_title': 'reword'})
TT.configure(repo='', bug='', tail=None, fixed='Repaired: ')
check('no tail leaves the title as it is; the fixed words are the host\'s',
      (TT.title(ISSUES[4]), TT.line(ISSUES[2])), ('Cancel shows its reason (OLD-54)', 'Repaired: The poll opens on time'))
TT.configure(area='')
try:
    TT.shipped([c('g1', 1)], mapped={}, get=ISSUES.get)
    check('no Area is NotConfigured', 'shipped', 'NotConfigured')
except T.NotConfigured:
    check('no Area is NotConfigured', True, True)
try:
    TT.configure(colour='red')
    check('configure refuses an unknown name', 'accepted', 'TypeError')
except TypeError:
    check('configure refuses an unknown name', True, True)

print(f'\n{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
