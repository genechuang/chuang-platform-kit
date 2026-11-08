#!/usr/bin/env python3
"""chuang_sdlc.listeners: the board's event listeners, each told once per event, on fakes (chuang_sdlc.testing's Moves
and Records), never GitHub, a bus or a store.

Pinned (the positive case first): a card newly in the shipyard wakes its owner once, the drag recorded for the scrum
master, a session's own start telling nobody and a refused letter the host's retry with its claim freed; a dragged
card's light is set, a refusal a WARNING; a dock landing moves its tickets, a refused one a WARNING; a closed ticket
by an alert closer goes to the ship column marked first, a close as not planned to the failed one told once, any
other close asked once, the host's own word (`also`) added; the scrum nudge runs here first, skips its own echo and
claims one run a minute, the job only when the sweep failed; the triage sweep's catch, the job's refusal a retry; a
commit's working time through the host's record, the card's Actual written; the lines' head; NotConfigured unwired;
configure refuses an unknown name.

Run:  python -B tests/test-sdlc-listeners.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))

from chuang_sdlc import listeners as L, tickets as T  # noqa: E402
from chuang_sdlc.testing import Moves, Records  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class Again(Exception):
    """The host's own retry exception."""


NOW = datetime(2026, 11, 8, 8, 0, tzinfo=timezone.utc)   # utc-ok: a fixed instant for the fakes, as an event's `at` is UTC


def moved(n, col='Building', frm='Backlog', field='Status', owner='Ann', at='2026-11-08T07:59:00Z'):
    return {'item': f'IT_{n}', 'by': 'lead', 'from': frm, 'field_name': field,
            'node': {'id': f'IT_{n}', 'content': {'number': n, 'title': 't'}, 'status': {'name': col, 'updatedAt': at},
                     'owner': {'name': owner}}}


def wire(moves, **extra):
    L.configure(head='events', claims='claims', claim_ttl_days=7, moves=moves, retry=Again, scrum_state='scrum',
                acted_seconds=90, **extra)


print('a card newly in the shipyard')
moves, store, sent = Moves(), Records(), []
wire(moves)
mail = lambda to, text, sender, subject='': sent.append((to, sender, subject, text)) or {'id': 'M1'}  # noqa: E731
first = L.board_wake(moved(7), store, NOW, mail)
check('the owner is told once, by the board sender', (first, sent), (
    'events: board: #7 to the shipyard; Ann told (session mail M1)', [('Ann', 'Board', '#7 to Building', 'lead moved #7')]))
check('  again: already told, no second letter', (L.board_wake(moved(7), store, NOW, mail), len(sent)),
      ('events: board: #7 to the shipyard was already told', 1))
check('  the claim in the claims collection, expiring a week on, and the drag on the scrum record',
      (store.get('claims', 'board-wake-7-2026-11-08T075900Z')['expires_at'] - NOW, store.get('scrum', '7')),
      (timedelta(days=7), {'shipyard_at': '2026-11-08T07:59:00Z'}))
store.set('starts', '8', {'at_epoch': NOW.timestamp() - 60})
check("  a session's own start tells nobody", L.board_wake(moved(8), store, NOW, mail),
      "events: board: #8 to the shipyard by its own session's start; nobody to wake")
check('  a Light edit, a move within the column and a draft say nothing',
      (L.board_wake(moved(9, field='Light'), store, NOW, mail), L.board_wake(moved(9, frm='Building'), store, NOW, mail),
       L.board_wake({'node': {'content': {}, 'status': {'name': 'Building'}}}, store, NOW, mail)), ('', '', ''))


def refuse(to, text, sender, subject=''):
    raise OSError('bus down')


try:
    L.board_wake(moved(10), store, NOW, refuse)
    check("  a refused letter is the host's retry", 'no retry', 'Again')
except Again as e:
    check("  a refused letter is the host's retry, its claim freed", (str(e).startswith("WARNING: events: board: #10's letter"),
                                                                       store.get('claims', 'board-wake-10-2026-11-08T075900Z')),
          (True, None))

print('the light and the dock')
moves = Moves(fix='busy')
wire(moves)
check("a dragged card's light set to its column's", (L.board_light(moved(7), 'tok'), moves.writes),
      ('events: board: #7 in Building: Light busy', [('light', 'IT_7', 'busy')]))
wire(Moves(fix=None))
check('  nothing to change: no line', L.board_light(moved(7), 'tok'), '')
wire(Moves(fix='', refuse={'light'}))
check('  a refusal is a WARNING', L.board_light(moved(7), 'tok'), "WARNING: events: board: #7's Light was not set to none (HTTP 502)")
wire(Moves(refuse={'dock-5'}))
check("a landing's tickets to the dock, a refused one a WARNING",
      (L.dock_landed({'sha': 'abcdef12345', 'tickets': [4]}, 'tok'), L.dock_landed({'sha': 'abcdef12345', 'tickets': [4, 5]}, 'tok'),
       L.dock_landed({'sha': 'abcdef12345'}, 'tok')),
      ('events: dock: abcdef1 landed: #4: Dock, warming',
       'WARNING: events: dock: abcdef1 landed: #4: Dock, warming; #5: not moved (HTTP 502)',
       'events: dock: abcdef1 names no ticket; no card moved'))

print('a closed ticket')
cards = {20: {'number': 20, 'title': 'Alert: x down', 'comment': '', 'column': 'Building', 'item': 'IT_20', 'closed_at': 'A'},
         21: {'number': 21, 'title': 'work', 'comment': '', 'column': 'Ready', 'item': 'IT_21', 'closed_at': 'B', 'owner': 'Bo'},
         22: {'number': 22, 'title': 'work', 'column': 'Backlog', 'item': 'IT_22', 'closed_at': 'C', 'reason': 'not_planned'},
         23: {'number': 23, 'title': 'work', 'column': 'Shipped', 'item': 'IT_23'}}
moves, store, sent = Moves(cards=cards), Records(), []
wire(moves, closer=lambda title, comment: title.startswith('Alert: '))
closed = lambda n, reason='completed': L.ticket_closed({'number': n, 'state_reason': reason}, store, 'tok', NOW, mail=mail)  # noqa: E731
check("an alert closer's close goes to the ship column, its move marked first", (closed(20), moves.writes, store.get('board_moves', '20')),
      ('events: closed #20 by an alert closer: moved from Building to Ship, Shipped', [('shipped', 'IT_20')],
       {'at_epoch': int(NOW.timestamp())}))
check('  any other close asks its owner once', (closed(21), closed(21), sent[-1][:3]),
      ('events: closed #21 while in Ready; Bo asked to confirm it done or reopen it', 'events: closed #21 in Ready was already told',
       ('Bo', 'Scrum master', 'Closed')))
check('  a close as not planned: the failed column, no Light, told once', (closed(22, ''), closed(22, ''), moves.writes[-1]),
      ('events: closed #22 (not_planned): moved from Backlog to Shipwreck, no Light; Captain told',
       'events: closed #22 (not_planned) in Backlog was already settled', ('unplanned', 'IT_22')))
check('  a finished card, a card off the board and no number say so',
      (closed(23), closed(24), L.ticket_closed({}, store, 'tok', NOW, mail=mail)),
      ('events: closed #23 is in Shipped already; nothing to do', 'events: closed #24 is not on the board; nothing to do',
       'events: a ticket.closed with no number; nothing to do'))
check("  the host's own word is added", L.ticket_closed({'number': 23}, store, 'tok', NOW, mail=mail,
                                                         also=lambda n, card, reason: f'; #{n} noted'),
      'events: closed #23 is in Shipped already; nothing to do; #23 noted')
wire(Moves(cards=cards, refuse={'unplanned'}))
check('  a refused move frees the claim and the mark',
      (L.ticket_closed({'number': 22}, Records(), 'tok', NOW, mail=mail).startswith('WARNING: events: closed #22 (not_planned) was not moved'),),
      (True,))
wire(Moves(refuse={'read'}))
check('  an unreadable card is a WARNING', closed(25), "WARNING: events: closed #25 could not be read (HTTP 502); the scrum master's sweep catches it")

print('the two sweeps')
store, started = Records(), []
wire(Moves())
start = lambda args: started.append(args) or {'execution': 'job-1'}  # noqa: E731
check('the scrum nudge runs the sweep here, its lines under the head',
      L.scrum_nudge(7, store, NOW, start, 'its Status changed', sweep=lambda: ['scrum: #7 asked']),
      'events: scrum: #7 run here (its Status changed)\nevents: scrum: #7 asked')
check('  once a minute', L.scrum_nudge(7, store, NOW, start, sweep=lambda: []), 'events: scrum: #7 already started this minute')
store.set('scrum', '8', {'acted_at': (NOW - timedelta(seconds=30)).isoformat()})
check('  its own echo is skipped', L.scrum_nudge(8, store, NOW, start, 'x', sweep=lambda: []),
      'events: scrum: #8: its own edit (x) within 90 s; not run again')


def broken():
    raise RuntimeError('no token')


check('  a failed sweep starts the job', (L.scrum_nudge(9, store, NOW, start, sweep=broken), started),
      ('WARNING: events: scrum: #9 not run here (RuntimeError: no token); started (an event)', [['scrum-sweep', '--ticket', '9']]))
check('  no card, no line', L.scrum_nudge(None, store, NOW, start), '')
check('the triage catch sweeps here', L.triage_sweep({'number': 5}, start, sweep=lambda: ['tickets: #5 filed']),
      "events: #5's Triage line: swept here\nevents: tickets: #5 filed")
check('  a failed sweep starts the job', L.triage_sweep({'number': 5}, start, sweep=broken),
      "WARNING: events: #5's Triage line: not swept here (RuntimeError: no token); the sweep started (job-1)")
try:
    L.triage_sweep({'number': 5}, lambda args: broken(), sweep=broken)
    check("  a refused start is the host's retry", 'no retry', 'Again')
except Again as e:
    check("  a refused start is the host's retry", str(e).startswith("WARNING: events: #5's ticket sweep failed here"), True)

print("a commit's working time")
moves = Moves(estimate=120)
wire(moves, loe_record=lambda data, store, est, write: [f'#{n}: estimate {est(n)}, actual {write(n, 1.5)}' for n in data['tickets']])
check('the record runs with the estimate and writes the Actual', (L.ticket_committed({'tickets': [7]}, Records(), 'tok'), moves.writes),
      (['#7: estimate 120, actual True'], [('field', 'IT_7', 'Actual', 1.5)]))
check('  a commit naming no ticket', L.ticket_committed({}, Records(), 'tok'), ['events: a ticket.committed naming no ticket; nothing to record'])
wire(Moves(), loe_record=lambda *a: broken())
try:
    L.ticket_committed({'tickets': [7]}, Records(), 'tok')
    check("  a refused write is the host's retry", 'no retry', 'Again')
except Again as e:
    check("  a refused write is the host's retry", str(e), 'WARNING: events: the LOE of [7] not recorded (RuntimeError: no token); '
                                                          'Pub/Sub delivers it again')

print('unwired')
L.configure(moves=None, retry=None)
try:
    L.dock_landed({'tickets': [1]}, 'tok')
    check('no board helpers is NotConfigured', 'ran', 'NotConfigured')
except T.NotConfigured:
    check('no board helpers is NotConfigured', True, True)
L.configure(moves=Moves(), claims='')
try:
    L.scrum_nudge(1, Records(), NOW, start, sweep=lambda: [])
    check('no claims collection is NotConfigured', 'ran', 'NotConfigured')
except T.NotConfigured:
    check('no claims collection is NotConfigured', True, True)
try:
    L.configure(nope=1)
    check('configure refuses an unknown name', 'accepted', 'TypeError')
except TypeError:
    check('configure refuses an unknown name', True, True)
check("this module's own Retry when the host names none", issubclass(L.Retry, Exception) and L.setting('retry') is None, True)

print(f'\n{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
