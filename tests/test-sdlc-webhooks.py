#!/usr/bin/env python3
"""chuang_sdlc.webhooks: what one of GitHub's deliveries means for the ticket tools, decided from the delivery alone.

Pinned (the positive case first, on payloads written here and chuang_sdlc.testing's Moves): an owner's `Triage:` line
is `triage`, a `Scrum:` line or the owner's plain reply on an issue assigned to them is `scrum` (the regex read only
when needed), anyone else's comment, an edit, a scrum master's own comment and a comment with neither are ignored with
their lines; an issue's close (a feedback ticket's flagged), a feedback ticket by its label or its body mark, an alert,
and the ignored (an investigation's output, a filed work ticket, another action); a dock push's tickets from its
commits' trailers, a deleted branch None; a Project item's move data before its read and the ignored edits; the routes
after the read (a placeholder, an app status draft, the ship card there, a draft's Light, an unread item's Light, a
move); the delivery's age.

Run:  python -B tests/test-sdlc-webhooks.py
"""
import os
import re
import sys
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))

from chuang_sdlc import webhooks as W  # noqa: E402
from chuang_sdlc.testing import Moves  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


SCRUM = re.compile(r'^\s*Scrum:\s*(building|next|done)\b', re.I | re.M)
read = []


def scrum_re():
    read.append(1)
    return SCRUM


def comment(body, by='owner', action='created', assignees=()):
    return {'action': action, 'issue': {'number': 7, 'assignees': [{'login': a} for a in assignees]},
            'comment': {'id': 1, 'body': body, 'user': {'login': by}}}


def route(payload):
    got = W.comment_route(payload, 'Owner', scrum_re)
    return got['route'], got['line']


print('a comment')
check("the owner's Triage line is the sweep's", route(comment('Found it.\n**Triage:** P2 | Owner: Ann')), ('triage', ''))
check("  an owner's Scrum: line is the scrum master's", route(comment('Scrum: building | eta 3 PM')), ('scrum', ''))
read.clear()
check("  the owner's plain reply on an issue assigned to them, the regex never read",
      (route(comment('yes, go', assignees=('owner',))), read), (('scrum', ''), []))
check("  the scrum master's own comment is no reply", route(comment('**Scrum master** asks', assignees=('owner',))),
      ('ignored', 'ignored: a comment on #7 with no Triage line'))
check('  a Scrum: line beside a Triage line is the sweep\'s', route(comment('Scrum: done\nTriage: P1')), ('triage', ''))
check("  anyone else's, an edit, and neither", (route(comment('Triage: P1', by='someone')), route(comment('x', action='edited')),
                                                route(comment('thanks'))),
      (('ignored', 'ignored: a comment on #7 by someone, not the owner'), ('ignored', 'ignored: an issue_comment edited'),
       ('ignored', 'ignored: a comment on #7 with no Triage line')))
check('  a bare regex works too', W.comment_route(comment('Scrum: next'), 'owner', SCRUM)['route'], 'scrum')
SESSION, PIPE = '\n\n— **Snow White** · Claude session', '\n\n— **Picklebot** · pipeline'
check("  0.15.0: a signed comment on an assigned card is no reply of the owner's; a session's signed Scrum: and Triage "
      "lines still count; the pipeline's own Triage note is never applied",
      (route(comment('Live in release abc.' + SESSION, assignees=('owner',))),
       route(comment('LOE: estimated 2h' + PIPE, assignees=('owner',))),
       route(comment('Scrum: building | eta 3 PM' + SESSION, assignees=('owner',)))[0],
       route(comment('Found it.\nTriage: P2 | Owner: Ann' + SESSION))[0],
       route(comment('Triage: now. Moved to the Shipyard.' + PIPE))),
      (('ignored', 'ignored: a comment on #7 with no Triage line'), ('ignored', 'ignored: a comment on #7 with no Triage line'),
       'scrum', 'triage', ('ignored', "ignored: the pipeline's own Triage note on #7 (Picklebot)")))

print('an issue')
MARK = re.compile(r'Feedback item:\s*FB-[A-Z0-9]+\s*\Z')


def issue(action='opened', labels=(), title='Something broke', body='', reason=None):
    return {'action': action, 'sender': {'login': 'owner'},
            'issue': {'number': 9, 'title': title, 'body': body, 'html_url': 'u', 'state_reason': reason,
                      'labels': [{'name': n} for n in labels]}}


def iroute(payload):
    got = W.issue_route(payload, ('TEST',), MARK)
    return got['route'], got['line']


check('an alert opened goes to its investigator', iroute(issue()), ('alert', ''))
got = W.issue_route(issue('closed', ('Feedback',), reason='not_planned'), ('TEST',), MARK)
check('  a close, with its reason, by whom, and a feedback ticket flagged', (got['route'], got['reason'], got['by'], got['feedback']),
      ('closed', 'not_planned', 'owner', True))
check('  a close with no reason is completed', W.issue_route(issue('closed'))['reason'], 'completed')
check('  a feedback ticket by its label or the filer\'s body mark, before its area label',
      (iroute(issue(labels=('feedback', 'area:Product'))), iroute(issue('reopened', body='words\n\nFeedback item: FB-ABC1'))),
      (('feedback', ''), ('feedback', '')))
check("  an investigation's own output, a filed work ticket, another action",
      (iroute(issue(labels=('machine',))), iroute(issue(title='TEST run')), iroute(issue(labels=('area:Pipeline',))),
       iroute(issue('labeled'))),
      (('ignored', "ignored: #9 is an investigation's own output"), ('ignored', "ignored: #9 is an investigation's own output"),
       ('ignored', 'ignored: #9 is a filed work ticket, not an alert'), ('ignored', 'ignored: an issues labeled')))

print('a dock push')
push = {'after': 'a' * 40, 'before': ' b ', 'forced': 1, 'pusher': {'name': 'ann'},
        'commits': [{'message': 'one\n\nTicket: #12, #14'}, {'message': 'two\n\nTicket: #12'}, {'message': 'Ticket: none (notes #3)'}]}
check("the tickets its commits' trailers name, sorted and once", W.dock_landed(push),
      {'sha': 'a' * 40, 'before': 'b', 'tickets': [12, 14], 'forced': True, 'by': 'ann'})
check('  a host reader of the trailers', W.dock_landed(push, lambda m: [1])['tickets'], [1])
check('  a deleted branch is None', (W.dock_landed({'after': '0' * 40}), W.dock_landed({})), (None, None))

print('a Project item')


def item(action='edited', ftype='single_select', name='Status', node='PVTI_1', frm=None):
    change = {'field_type': ftype, 'field_name': name, 'field_node_id': 'F1'}
    if frm is not None:
        change['from'] = frm
    return {'action': action, 'sender': {'login': 'lead'}, 'projects_v2_item': {'node_id': node},
            'changes': {'field_value': change}}


FIELDS = ('Status', 'Light', 'Priority', 'Owner')
check('a Status edit is a move to read, with the column it left', W.project_item(item(frm={'name': 'Backlog'}), FIELDS),
      ({'item': 'PVTI_1', 'field': 'F1', 'field_name': 'Status', 'by': 'lead', 'from': 'Backlog'}, ''))
check('  the ignored edits', [W.project_item(p, FIELDS)[1] for p in (item('created'), item(ftype='text'), item(name='Iteration'),
                                                                     item(node=''))],
      ['ignored: a projects_v2_item created', 'ignored: a projects_v2_item edited of a text field',
       'ignored: a projects_v2_item edited of its Iteration', 'ignored: a projects_v2_item with no node id'])
check("  0.16.0: a date field the host names (a card's ETA) is carried; one it does not name is ignored",
      (W.project_item(item(ftype='date', name='ETA', frm='2026-11-01'), FIELDS + ('ETA',)),
       W.project_item(item(ftype='date', name='Due'), FIELDS + ('ETA',))[1]),
      (({'item': 'PVTI_1', 'field': 'F1', 'field_name': 'ETA', 'by': 'lead', 'from': ''}, ''),
       'ignored: a projects_v2_item edited of its Due'))
moves = Moves()
card = lambda n=None, title='t', col='Building': {'id': 'I', 'content': {'number': n, 'title': title}, 'status': {'name': col}}  # noqa: E731
check('after the read: a move, a placeholder, an app status draft, the ship card there, a draft\'s and an unread Light',
      [W.item_route(card(5), 'Status', moves), W.item_route(card(title='— Ready: done —'), 'Status', moves),
       W.item_route(card(col='Apps'), 'Status', moves), W.item_route(card(title='SHIP IT', col='Apps'), 'Status', moves),
       W.item_route(card(), 'Light', moves), W.item_route({}, 'Light', moves), W.item_route({}, 'Status', moves)],
      ['send', 'placeholder', 'app_status', 'send', 'draft_light', 'draft_light', 'send'])

print("the delivery's age")
now = datetime(2026, 11, 8, 8, 0, 1, tzinfo=timezone.utc)   # utc-ok: a fixed instant against GitHub's UTC stamp
check('seconds after the change', (W.delivery_age('2026-11-08T08:00:00Z', now), W.delivery_age('', now), W.delivery_age('soon', now)),
      ('; GitHub delivered it 1.0 s after the change', '', ''))

print(f'\n{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
