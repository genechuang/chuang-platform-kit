#!/usr/bin/env python3
"""chuang_sdlc.notes: a ship's release notes, every ticket by its full title.

Pinned (the positive case first): the tickets grouped New (the readers' Area's features), Fixed (its bugs) and Behind
the scenes (every other Area, by title), each once in its first commit's order, a missing ticket left out and an
unanswered one named, a member-titled one listed for retitling, labels configured with capitals matched; the Slack text with the change order, the ship time
and the Release; the Markdown and the pull request's body (every closing reference broken, the compare link, the close
note); the blocks (the header, the facts line, each section under its emoji, a long section split under Slack's
limit, the empty and unread notes, the Post-Ship: steps in the host's words); the Release body; the go-live instant
and the tag in the host's zone; the change-order lookup by its label (none labelled, none looked for); the compare
read and the Release at the head, an existing tag's reused; configure refuses an unknown name. GitHub is a fake.

Run:  python -B tests/test-sdlc-notes.py
"""
import io
import json
import os
import sys
import urllib.error
from zoneinfo import ZoneInfo

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))

from chuang_platform_kit import github  # noqa: E402
from chuang_sdlc import notes as N  # noqa: E402
from chuang_sdlc import ship_pr as P  # noqa: E402
from chuang_sdlc import tickets as T  # noqa: E402
from chuang_sdlc import titles as TT  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


CFG = {'repo': 'acme/app', 'labels': {'Bug': 'bug'}}
T.configure(board=lambda: CFG, labels={'member_title': 'reword', 'change_order': 'release-order'})
TT.configure(area='Front Desk', tail=r'\s*\(OLD-\d+\)\s*$', token=lambda: 't', mapped=lambda: {})
N.configure(tz=ZoneInfo('America/Los_Angeles'), not_allowed='not on our list')

TICKETS = {
    356: {'number': 356, 'title': 'The note on Me says why', 'labels': ['feature', 'area:front-desk']},
    352: {'number': 352, 'title': 'Viewing as a member hides the controls', 'labels': ['bug', 'area:front-desk']},
    353: {'number': 353, 'title': 'Five keys were built from a name (OLD-12)', 'labels': ['bug', 'area:pipeline']},
    360: {'number': 360, 'title': 'Fix #12 seeding', 'labels': ['bug', 'area:front-desk', 'reword']},
    9: {'number': 9, 'missing': True},
}
COMMITS = [{'sha': 'a1', 'message': 'One\n\nTicket: #352'}, {'sha': 'a2', 'message': 'Two\n\nTicket: #356, #353'},
           {'sha': 'a3', 'message': 'Three\n\nTicket: #352'}, {'sha': 'a4', 'message': 'Four\n\nTicket: #9'},
           {'sha': 'a5', 'message': 'Five\n\nTicket: #77'}]
ISSUE = 'https://github.com/acme/app/issues/'

print('groups')
got = N.groups(COMMITS, {}, TICKETS.get)
check('the positive case: New, Fixed and Behind the scenes, each once in first-commit order; missing out, unread named',
      got, {'new': [(356, 'The note on Me says why')], 'fixed': [(352, 'Viewing as a member hides the controls')],
            'behind': [(353, 'Five keys were built from a name')], 'unknown': [77], 'member_titles': []})
member = N.groups(COMMITS + [{'sha': 'a6', 'message': 'Six\n\nTicket: #360'}], {}, TICKETS.get)
check('a ticket still in a member\'s words is listed as usual and named for retitling',
      (member['member_titles'], (360, 'Fix #12 seeding') in member['fixed']), ([360], True))
TT.configure(bug='Bug')
T.configure(labels={'member_title': 'Reword'})
capital = N.groups(COMMITS + [{'sha': 'a6', 'message': 'Six\n\nTicket: #360'}], {}, TICKETS.get)
T.configure(labels={'member_title': 'reword'})
TT.configure(bug='')
check('labels configured with capitals still match the lower-cased labels: the bug Fixed, the member title named',
      (capital['member_titles'], capital['fixed']),
      ([360], [(352, 'Viewing as a member hides the controls'), (360, 'Fix #12 seeding')]))

print('text and markdown')
text = N.slack_text('c806535ceb8a', '10/25/26 10:51 PM PT', dict(got, change_order=900), 'https://rel', 86)
check('the Slack text: the head line, each ticket linked, the unread line',
      (text.splitlines()[0], f'• <{ISSUE}352|#352> Viewing as a member hides the controls' in text,
       '_Not read from GitHub: #77_' in text, N.slack_text('abc', 'x', {}).splitlines()),
      (f'*Shipped c806535c* · live 10/25/26 10:51 PM PT · <{ISSUE}900|change order #900> · 86 s from ship to live'
       ' · <https://rel|release notes>', True, True, ['*Shipped abc* · live x', 'No ticket named by these commits.']))
md = N.markdown_text('c806535ceb8a', member)
check('the Markdown: the head line, the sections, each ticket a link, the retitle line last',
      (md.splitlines()[0], '**Behind the scenes**' in md, f'- [#352]({ISSUE}352) Viewing as a member hides the controls' in md,
       md.splitlines()[-1]),
      ('**Shipping c806535c**', True, True,
       f"**Retitle before it ships:** [#360]({ISSUE}360) still carries the member's words, not the work that was built"))
body = N.pr_body('b' * 40, 'c806535ceb8a', COMMITS + [{'sha': 'a6', 'message': 'Six\n\nTicket: #360'}], {}, TICKETS.get)
check('the pull request\'s body: no closing reference left, the compare link, the close note',
      (P.CLOSING.findall(body), f'Fix #{P.BREAK}12 seeding' in body,
       'Commits: https://github.com/acme/app/compare/bbbbbbbbbbbb...c806535ceb8a' in body, body.splitlines()[-1]),
      ([], True, True, 'These tickets close when the production smoke passes, not when this merges.'))

print('blocks')
steps = [{'job': 'worker', 'args': ['refresh'], 'sha': 'aaaa1111', 'what': '', 'allowed': False, 'result': 'refused',
          'seconds': None, 'url': ''}]
blocks = N.slack_blocks('c806535ceb8a', '10/25/26 10:52:11 PM PT', dict(got, post_ship=steps), 'https://rel', 86)
long = N.slack_blocks('c' * 12, 'x', {'behind': [(n, 'x' * 90) for n in range(60)]})
empty = N.slack_blocks('c' * 12, 'x', {'new': [], 'fixed': [], 'behind': [], 'unknown': []})
check('the positive case: header, facts, rule, each section under its emoji, unread, the steps in the host\'s words',
      ([b['type'] for b in blocks], blocks[0]['text']['text'], blocks[1]['elements'][0]['text'], blocks[3]['text']['text'],
       blocks[-2]['elements'][0]['text'], blocks[-1]['text']['text'].splitlines()),
      (['header', 'context', 'divider', 'section', 'section', 'section', 'context', 'section'], '🚀 Shipped c806535c',
       '🟢 Live 10/25/26 10:52:11 PM PT   ·   ⏱️ 86 s from ship to live   ·   <https://rel|📝 Release notes>',
       f'*✨ New*\n• <{ISSUE}356|#356>  The note on Me says why', 'Not read from GitHub: #77',
       ['*🧰 Post-ship steps*', '• `worker refresh`: 🚫 refused, not on our list (aaaa1111)']))
check('a long section goes on in another block under the limit; nothing named says so',
      (len(long) > 4 and all(len(b['text']['text']) <= N.SLACK_TEXT_LIMIT for b in long[3:]), empty[-1]['text']['text']),
      (True, 'No ticket named by these commits.'))

print('release body, time and tag')
rel = N.release_body('c806535ceb8a', 'b' * 40, dict(got, change_order=900, post_ship=steps), '86', '2026-10-26T05:52:11Z')
check('the Release body: the change order, the instant and ship time, the sections, the steps, the compare link',
      (rel.split('\n\n')[:3], '- `worker refresh`: 🚫 refused, not on our list (aaaa1111)' in rel, rel.splitlines()[-1]),
      (['**Change order #900**', '**live 10/25/26 10:52:11 PM PT · 86 s from ship to live**',
        '## New\n- #356 The note on Me says why'], True,
       'Commits: https://github.com/acme/app/compare/bbbbbbbbbbbb...c806535ceb8a'))
check('the instant in the host\'s zone with its year; the tag; ship_time of nothing',
      (N.when('2026-10-26T05:51:00Z'), N.when('2026-03-01T17:05:09Z'), N.when('2026-03-01T17:05:09'),
       N.tag_for('c806535ceb8a', '2026-10-26T05:51:00Z'), N.ship_time(None), N.ship_time('x')),
      ('10/25/26 10:51:00 PM PT', '03/01/26 09:05:09 AM PT', '03/01/26 09:05:09 AM PT', 'ship-20261025-2251-c806535c',
       '', ''))
N.configure(tag_prefix='release-')
check('the tag prefix is the host\'s', N.tag_for('c806535ceb8a', '2026-10-26T05:51:00Z'), 'release-20261025-2251-c806535c')

print('change order, compare and release')
asked = []
keep = github.issues
try:
    github.issues = lambda tok, repo, state, labels, search, limit, opener=None: asked.append(
        (repo, state, labels, search)) or [{'number': 4, 'title': 'Ship 1: ffffffff'}, {'number': 5, 'title': 'Ship: c806535c'}]
    found = N.change_order_number('c806535ceb8a', 't')
    github.issues = lambda *a, **k: (_ for _ in ()).throw(github.GitHubError(0, 'down', '/graphql'))
    down = N.change_order_number('c806535ceb8a', 't')
    T.configure(labels={'change_order': ''})
    unlabelled = N.change_order_number('c806535ceb8a', 't')
finally:
    github.issues = keep
check('the positive case: the change order by its label and the head in its title; GitHub down or no label is None',
      (found, asked, down, unlabelled), (5, [('acme/app', 'all', ['release-order'], '"c806535c" in:title')], None, None))


class Resp(io.BytesIO):
    def __init__(self, status, body):
        super().__init__(json.dumps(body).encode())
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener(routes, calls):
    def op(req, timeout=None):
        calls.append((req.get_method(), req.full_url, json.loads(req.data) if req.data else None))
        for m, part, status, answer in routes:
            if m == req.get_method() and part in req.full_url:
                if status >= 400:
                    raise urllib.error.HTTPError(req.full_url, status, 'no', {}, io.BytesIO(b'{}'))
                return Resp(status, answer)
        raise urllib.error.URLError('no route')
    return op


calls = []
compare = {'commits': [{'sha': 'a1', 'commit': {'message': 'One\n\nTicket: #352'}}]}
N.configure(tag_prefix='ship-')
op = opener([('GET', '/compare/', 200, compare), ('POST', '/releases', 201, {'html_url': 'https://rel/1'})], calls)
read, url = N.commits('b' * 40, 'c' * 40, 't', op), N.release('c' * 40, 'b' * 40, '2026-10-26T05:51:00Z', got, 't', op)
made = next(b for m, u, b in calls if u.endswith('/releases'))
calls2 = []
reused = N.release('c' * 40, 'b' * 40, '', got, 't', opener([('POST', '/releases', 422, {}),
                                                              ('GET', '/releases/tags/', 200, {'html_url': 'https://rel/old'})],
                                                             calls2))
check('the positive case: the compare read and the Release at the head, its tag and name',
      (read, url, calls[0][1], made['tag_name'], made['target_commitish'], made['name']),
      ([{'sha': 'a1', 'message': 'One\n\nTicket: #352'}], 'https://rel/1',
       f"https://api.github.com/repos/acme/app/compare/{'b' * 40}...{'c' * 40}", 'ship-20261025-2251-cccccccc', 'c' * 40,
       'Ship 10/25/26 10:51:00 PM PT (cccccccc)'))
check('an existing tag\'s Release is reused', reused, 'https://rel/old')
try:
    N.commits('b', 'c', 't', opener([('GET', '/compare/', 404, {})], []))
    check('a refused compare raises', 'read', 'GitHubError')
except RuntimeError:
    check('a refused compare raises', True, True)
try:
    N.configure(colour='red')
    check('configure refuses an unknown name', 'accepted', 'TypeError')
except TypeError:
    check('configure refuses an unknown name', True, True)

print(f'\n{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
