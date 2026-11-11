#!/usr/bin/env python3
"""chuang_sdlc.tickets' commit check, range check and filing commands (0.25.0), moved from a host's scripts so every
board has one copy: a commit names an open ticket or `Ticket: none (why)`; the Session line is a trailer git reads; a
ticket another session owns is named in the body, not the trailer; a range's unticketed and foreign commits; and
`new` / `bug` refuse a filing without its Area, Priority, estimate and reuse line before anything is filed.

Run:  python -B tests/test-sdlc-commit-check.py
"""
import io
import os
import re
import sys
import types

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))

from chuang_sdlc import tickets as T  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


CFG = {'repo': 'acme/app', 'id': 'PVT_board', 'people': {'lead': 'Lee'},
       'fields': {'Area': {'options': {'Product': 'a_p', 'Pipeline': 'a_pl'}}},
       'areas': {'Product': 'what people use'}}


class GH:
    """A runner: issues by number ({state, pr, title}), each ticket's board Owner, every graphql read counted."""

    def __init__(self, issues=None, owners=None, signed_in=True, board=True):
        self.issues_, self.owners, self.signed_in, self.board, self.reads = issues or {}, owners or {}, signed_in, board, 0

    def auth_ok(self):
        return self.signed_in

    def issue(self, n):
        i = self.issues_.get(int(n))
        if i is None:
            return None
        return {'number': int(n), 'title': i.get('title', ''), 'body': '', 'state': i.get('state', 'OPEN'), 'labels': [],
                'url': '', 'pr': i.get('pr', False), 'id': n, 'node': f'I_{n}'}

    def graphql(self, query, variables=None):
        self.reads += 1
        if not self.board:
            return None
        return {'repository': {f'i{n}': {'projectItems': {'nodes': [
            {'project': {'id': 'PVT_other'}, 'owner': {'name': 'Someone Else'}},
            {'project': {'id': 'PVT_board'}, 'owner': {'name': self.owners.get(int(n), '')}}]}}
            for n in re.findall(r'i(\d+): issue', query)}}


T.configure(board=lambda: CFG, runner=lambda: GH())
OPEN = {12: {'title': 'Twelve'}, 14: {'title': 'Fourteen'}, 5: {'title': 'Old work', 'state': 'CLOSED'},
        6: {'title': 'A PR', 'pr': True}, 40: {'title': "Ann's"}}

print('the commit check')
gh = GH(OPEN, {12: '', 14: 'Lee', 40: 'Ann'})
check('the positive case: a message naming open tickets passes, and says them',
      T.check('A change\n\nTicket: #12, #14\nSession: Bob\n', gh), (True, 'tickets: #12, #14'))
check('no ticket is refused with the rule; none (why) passes; a merge and a fixup pass unread',
      (T.check('A change\n', gh), T.check('Notes\n\nTicket: none (session notes)\n', gh),
       T.check("Merge branch 'x'\n", gh), T.check('fixup! A change\n', gh)),
      ((False, T.NO_TICKET), (True, 'tickets: none (session notes)'), (True, ''), (True, '')))
check('a closed ticket, a pull request and a missing one are each refused',
      (T.check('x\n\nTicket: #5\n', gh), T.check('x\n\nTicket: #6\n', gh)[1], T.check('x\n\nTicket: #7\n', gh)[1]),
      ((False, 'tickets: refused, #5 is closed (Old work); work found after it is a new ticket'),
       'tickets: refused, #6 is a pull request, not an issue', 'tickets: refused, #7 not found'))
check('with no sign-in the form alone is checked, and said',
      T.check('x\n\nTicket: #5\n', GH(OPEN, signed_in=False)), (True, 'tickets: #5 not checked (no GitHub token here)'))

print('the Session line')
split = 'A change\n\nTicket: #12\nSession: Bob\n\nCo-Authored-By: C <n@a.com>\n'
check('a Session line outside the last paragraph is refused; inside it, or absent, it passes',
      (T.check(split, gh), T.session_unread('A change\n\nTicket: #12\nSession: Bob\nCo-Authored-By: C <n@a.com>\n'),
       T.session_unread('A change\n\nTicket: #12\n')), ((False, T.SESSION_UNREAD), '', ''))
check('a host without the convention turns the rule off', T.check(split, gh, session_rule=False), (True, 'tickets: #12'))

print('a ticket another session owns')
theirs = 'A step\n\nTicket: #40\nSession: Bob\n'
check("another owner's ticket is refused; the owner's own commit, the lead's ticket and an unowned one pass",
      (T.check(theirs, gh), T.check('A step\n\nTicket: #40\nSession: ann\n', gh)[0],
       T.check('A step\n\nTicket: #12, #14\nSession: Bob\n', gh)[0]),
      ((False, "tickets: refused, #40 is Ann's: name it in the body, or split your step into its own ticket"), True, True))
quiet = GH(OPEN, {40: 'Ann'})
check('the owners are ONE read; a message with no Session line is not checked at all',
      (T.owners_of([40, 12, 40], gh), T.check('A step\n\nTicket: #40\n', quiet), quiet.reads),
      ({40: 'Ann', 12: ''}, (True, 'tickets: #40'), 0))
down = GH(OPEN, board=False)
check('a board that does not answer is a warning, never a refusal',
      T.check(theirs, down), (True, 'tickets: #40 (WARNING: tickets: the board did not answer, so the Owner of #40 was not checked)'))
check('a host without the rule turns it off, and reads no board',
      (T.check(theirs, quiet, owner_rule=False), quiet.reads), ((True, 'tickets: #40'), 0))

print('a host words its own lines')
T.configure(words={'NO_TICKET': 'name a ticket: tool new', 'NOT_CHECKED': 'gh unavailable here',
                   'CLOSED_TICKET': '#{n} is closed ({title}); file: tool bug {n}',
                   'FOREIGN_TICKET': "#{n} is {owner}'s (tool new)", 'FOREIGN_IN_RANGE': "{sha}: #{n} is {owner}'s (rule 9)"})
check("the refusals in the host's words",
      (T.check('A change\n', gh)[1], T.check('x\n\nTicket: #5\n', gh)[1], T.check(theirs, gh)[1],
       T.check('x\n\nTicket: #5\n', GH(OPEN, signed_in=False))[1]),
      ('name a ticket: tool new', 'tickets: refused, #5 is closed (Old work); file: tool bug 5',
       "tickets: refused, #40 is Ann's (tool new)", 'tickets: #5 not checked (gh unavailable here)'))

print('a range')
LOG = '\x00'.join(['aaaa1111\x01p1\x01Named\n\nTicket: #12\n', 'bbbb2222\x01p1\x01Unnamed work\n',
                   'cccc3333\x01p1 p2\x01A merge of two\n', 'dddd4444\x01p1\x01Dock descends again: x\n',
                   'eeee5555\x01p1\x01From before the rule\n', 'ffff6666\x01p1\x01Notes\n\nTicket: none (notes)\n',
                   'abab7777\x01p1\x01fixup! Named\n'])
git = lambda args: types.SimpleNamespace(stdout=LOG)   # noqa: E731
check('the positive case: the one commit naming no ticket; a merge, a fixup, a mapped sha and a skipped subject pass',
      T.unticketed('a..b', git, mapped={'eeee': [9]}, skip_subjects=('Dock descends again:',)), [('bbbb2222', 'Unnamed work')])
pairs = [('a1', 'One\n\nTicket: #40\nSession: Bob\n'), ('b2', 'Two\n\nTicket: #40\nSession: Ann\n'),
         ('c3', 'Three\n\nTicket: #14\nSession: Bob\n'), ('d4', 'Four\n\nTicket: #40\n')]
gh = GH(OPEN, {40: 'Ann', 14: 'Lee'})
check("a commit naming another session's ticket is one line, in one board read",
      (T.foreign_in('a..b', log=pairs, run=gh), gh.reads), (["a1: #40 is Ann's (rule 9)"], 1))
check('no board answer is one WARNING; no tickets named reads nothing',
      (T.foreign_in('a..b', log=pairs, run=GH(board=False)), T.foreign_in('a..b', log=[('a1', 'x\n')], run=gh)),
      (['WARNING: tickets: the board did not answer, so the Owners in a..b were not checked'], []))
err = io.StringIO()
codes = (T.range_check('a..b', unnamed=lambda r: [('bbbb2222', 'Unnamed work')], err=err),
         T.range_check('a..b', unnamed=lambda r: [], foreign=lambda r: ['WARNING: tickets: no board'], err=io.StringIO()),
         T.range_check('a..b', unnamed=lambda r: [], foreign=lambda r: ["a1: #40 is Ann's"], err=io.StringIO()),
         T.range_check('a..b', unnamed=lambda r: [], err=io.StringIO()))
check('range_check: an unnamed commit and a foreign one are exit 1; a warning alone and a clean range 0',
      (codes, err.getvalue().splitlines()[0]), ((1, 0, 1, 0), 'tickets: bbbb2222 names no ticket: Unnamed work'))

print('filing')
filed, kept = [], []


def run_new(argv, number=77, refused=None, **kw):
    out, errs = io.StringIO(), io.StringIO()
    code = T.file_new(argv, CFG, file=lambda *a, **k: filed.append((a, k)) or number,
                      refused=refused or (lambda title, argv: False),
                      keep_raw=lambda n, raw: kept.append((n, raw)), out=out, err=errs, **kw)
    return code, out.getvalue().strip(), errs.getvalue().strip()


FULL = ['new', 'feature', 'A thing', '--area', 'Product', '--priority', 'p3', '--body', 'the plan', '--loe', '2h',
        '--reuse', 'none (new)']
T.configure(factor_of=lambda raw: (0.5, 12, 'over 1h'))
code, said, _ = run_new(FULL)
args, kw = filed[-1]
check('the positive case: a full filing is filed with its Area, Priority, reuse line and estimate, the raw minutes kept',
      (code, said, args[:3], args[4], kw['area'], kw['priority'], bool(kw['loe']), kept[-1]),
      (0, '#77', ('feature', 'A thing', 'the plan\n\nReuse: none (new)'), 'backlog', 'Product', 'P3', True, (77, 120)))


def without(flag):
    i = FULL.index(flag)
    return FULL[:i] + FULL[i + 2:]


before = len(filed)
check("no Area is refused with the board's Areas, and what each is",
      run_new(without('--area')), (2, '', 'tickets: --area is required: Product (what people use); Pipeline'))
check('no Priority, an unknown flag, a body that is a path, no estimate, no reuse line: each exit 2, each its rule',
      (run_new(without('--priority'))[::2], run_new(FULL + ['--bogus'])[2].startswith('tickets: not filed: new does not take --bogus'),
       run_new(['new', 'task', 'X', '--area', 'Product', '--priority', 'P2', '--body', 'plan.md'])[2],
       run_new(without('--loe'))[::2], run_new(without('--reuse'))[::2]),
      ((2, T.PRIORITY_RULE), True, T.BODY_PATH_RULE.format(path='plan.md'), (2, T.LOE_RULE), (2, T.REUSE_RULE)))
check('a duplicate is exit 1, before the estimate is asked for; nothing refused was filed',
      (run_new(without('--loe'), refused=lambda title, argv: True)[0], len(filed)), (1, before))
check("a body's own LOE and Reuse lines count; GitHub refusing the filing is exit 1 with why",
      (run_new(['new', 'task', 'X', '--area', 'Product', '--priority', 'P2', '--body', 'plan\n\nLOE estimate: 1h\nReuse: none (x)'])[0],
       run_new(FULL, number=0, failed=lambda: 'HTTP 422 bad')[:2]), (0, (1, 'tickets: not filed (HTTP 422 bad)')))
T.configure(words={'AREA_RULE': 'pick an Area'})
check("a host's own Area line", run_new(without('--area'))[2], 'pick an Area')
T.configure(words={'AREA_RULE': ''})

print('the estimate in the body')
check('calibrated: the body loses its line and the raw minutes come back',
      T.calibrated_body(['new', 'task', 'X', '--body', 'the plan\n\nLOE estimate: 2 hours'], say=lambda line: None)[::2],
      ('the plan', 120))
check('not calibrated: the body keeps or gains the line, the estimate is the raw as given, nothing to keep',
      (T.calibrated_body(['new', 'task', 'X', '--body', 'the plan', '--loe', '2h'], calibrate=False),
       T.calibrated_body(['new', 'task', 'X', '--body', 'the plan\n\nLOE estimate: 1h'], calibrate=False)),
      (('the plan\n\nLOE estimate: 2h', '2h', None), ('the plan\n\nLOE estimate: 1h', '1h', None)))
check('filed_loe: a 0 or a word is no estimate; strip_loe takes the line out',
      (T.filed_loe(['new', 'task', 'X', '--loe', '0']), T.filed_loe(['new', 'task', 'X', '--loe', 'soon']),
       T.filed_loe(['new', 'task', 'X', '--loe', '30m']), T.strip_loe('a\n\nLOE estimate: 2h\n')), (False, False, True, 'a\n'))

print('a bug')
bugs = []
out, errs = io.StringIO(), io.StringIO()
code = T.file_bug(['bug', '#12', 'It broke', '--body', 'what broke', '--loe', '30m'], CFG,
                  file=lambda *a, **k: bugs.append((a, k)) or 78, refused=lambda t, a: False,
                  keep_raw=lambda n, raw: kept.append((n, raw)), default_owner='Lee', out=out, err=errs)
check('the positive case: a bug under its feature, P2 unless told, with no Area or reuse line asked, the default owner',
      (code, out.getvalue().strip(), bugs[-1][0][:2], bugs[-1][0][3], bugs[-1][1]['priority'], kept[-1]),
      (0, '#78', (12, 'It broke'), 'Lee', 'P2', (78, 30)))
errs = io.StringIO()
check('a bug with a body and no estimate, or a flag bug does not take, is refused',
      (T.file_bug(['bug', '12', 'It broke', '--body', 'what broke'], CFG, refused=lambda t, a: False, err=errs),
       T.file_bug(['bug', '12', 'It broke', '--area', 'Product'], CFG, refused=lambda t, a: False, err=errs),
       errs.getvalue().splitlines()[0]), (2, 2, T.LOE_RULE))

print(f'\n{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
