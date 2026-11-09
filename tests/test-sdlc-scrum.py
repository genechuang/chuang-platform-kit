#!/usr/bin/env python3
"""chuang_sdlc.scrum: the scrum master's sweep of the shipyard column, wired to a host through configure().

Pinned (the positive case first, on chuang_sdlc.testing's Board and Records, never GitHub):
  - an owner's `Scrum:` line: the newest by the repository's owner, never the sweep's own comment; the lead's `needs
    <lead>` kind from configure(lead=), and `paused`; SCRUM_RE made again for a new lead; configure refuses a name;
  - the lead's answer after an ask, never an owner's line;
  - parse_eta in the kit's zone: a bare time today or tomorrow, a weekday, a full stamp with PT, ISO, none;
  - plan's rules: a working card asked for an ETA, a depends-on card back to the next column, paused, blocked by the
    lead, Needs you with and without an ask, no Light and no Priority, a next card Queued, a Queued card back after its
    idle clock;
  - closed_plan: done ships, an alert closer ships (the host's hook), unplanned wrecks, a person's close asks once a day;
  - the sweep: one letter per owner, its comment kept, a closed shipyard card to the ship column with the move marked
    first, the ship card kept through the host's ship record; GitHub.ship marks before it moves; a dry run writes
    nothing.

Run:  python -B tests/test-sdlc-scrum.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))
os.environ.pop('CHUANG_PLATFORM_TZ', None)

from chuang_sdlc import scrum as S, tickets as T  # noqa: E402
from chuang_sdlc.testing import Board, Records  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


CFG = {
    'repo': 'acme/app', 'owner': 'acme', 'number': 3, 'id': 'PVT_board',
    'fields': {
        'Status': {'id': 'F_status', 'options': {'Backlog': 'o_b', 'Up Next': 'o_n', 'Building': 'o_y', 'Ready': 'o_r',
                                                 'Done': 'o_d', 'Broken': 'o_x'}},
        'Light': {'id': 'F_light', 'options': {'busy': 'l_busy', 'later': 'l_q', 'warm': 'l_warm', 'ok': 'l_ok',
                                               'going': 'l_go', 'gone': 'l_gone', 'red': 'l_red', 'ask': 'l_ask'}},
        'Owner': {'id': 'F_owner', 'options': {'Ann': 'u_a', 'Cap': 'u_c', 'Lee': 'u_l'}},
        'Priority': {'id': 'F_pri', 'options': {'P1': 'p1', 'P2': 'p2', 'P3': 'p3', 'P4': 'p4'}}},
    'stages': {'backlog': 'Backlog', 'next': 'Up Next', 'shipyard': 'Building', 'dock': 'Ready', 'dev': 'Ready',
               'shipping': 'Done', 'shipped': 'Done', 'failed': 'Broken'},
    'flow': ['backlog', 'next', 'shipyard', 'dock', 'dev', 'shipping', 'shipped'],
    'lights': {'backlog': '', 'next': '', 'shipyard': '', 'working': 'busy', 'queued': 'later', 'dock': 'warm',
               'dev': 'ok', 'shipping': 'going', 'shipped': 'gone', 'failed': 'red', 'needs': 'ask'},
    'placeholders': {'shipyard': '— Building: a card here is being built —'},
    'ship_card': {'home': 'dock', 'word': 'SHIP IT', 'title': 'SHIP IT: drag me to Done'},
    'labels': {'Bug': 'bug'},
}
NOW = datetime(2026, 10, 29, 19, 40, tzinfo=timezone.utc)   # 12:40 PM PT; utc-ok: a fixed instant for the sweep's clock
LOGIN = 'acme'
marks, letters = [], []
T.configure(board=lambda: CFG, people={'captain': 'Cap'},
            before_ship_move=lambda numbers, run: marks.append(list(numbers)), collections={'starts': 'starts'},
            ship_run=lambda: {'state': 'done', 'at': '2026-10-29T19:30:00Z'})


def say(body, minutes_ago=5, login=LOGIN):
    return {'body': body, 'createdAt': (NOW - timedelta(minutes=minutes_ago)).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'author': {'login': login}, 'databaseId': 11}


def card(n=600, light='', prio='P2', owner='Ann', labels=()):
    return {'number': n, 'light': light, 'priority': prio, 'owner': owner, 'labels': list(labels), 'title': f't{n}'}


def plan(c, line=None, state=None, **facts):
    return S.plan(c, dict({'line': line or {}, 'blockers': []}, **facts), state or {}, NOW, CFG)


print('wiring')
check('the positive case: unwired, the lead is `lead` and its kind `needs lead`', (S.lead(), S.NEEDS_KIND), ('lead', 'needs lead'))
S.configure(lead='Lee', mail=lambda to, text, subject, sender='': letters.append((to, subject)) or f'mailed {to}',
            words={'LIGHT_CLEAR': 'board light {n} clear'})
check("configure(lead=) makes the lead's kind and the regex again", (S.NEEDS_KIND, bool(S.SCRUM_RE.search('Scrum: needs Lee | pick'))),
      ('needs lee', True))
check('the regex reads paused and done, never another name', [bool(S.SCRUM_RE.search(t)) for t in
                                                              ('Scrum: paused | later', 'Scrum: done', 'Scrum: needs Bob | x')],
      [True, True, False])
for bad in ({'nope': 1}, {'words': {'NOPE': 'x'}}):
    try:
        S.configure(**bad)
        check(f'configure refuses {bad}', 'accepted', 'TypeError')
    except TypeError:
        check(f'configure refuses {bad}', True, True)
check('the captain is the ticket tools\' people', S.captain(), 'Cap')

print('lines')
comments = [say('Scrum: next | after #12', 30), say('Scrum: building | eta 5 PM', 10),
            say('Scrum: blocked by #9 | Lee', 1, login='someone'), say(S.MARKER + ' · x\n\nScrum: depends on #3', 0)]
got = S.scrum_line(comments, LOGIN)
check("the positive case: the owner's newest line, never another account's or the sweep's", (got['kind'], got['eta']),
      ('building', '5 PM'))
check("the lead's ask", S.scrum_line([say('Scrum: needs Lee | pick A or B')], LOGIN)['ask'], 'pick A or B')
check('paused and its why', (S.scrum_line([say('Scrum: paused | after the launch')], LOGIN)['kind'],
                             S.scrum_line([say('Scrum: paused | after the launch')], LOGIN)['ask']), ('paused', 'after the launch'))
check('`done` read past when skipped', S.scrum_line([say('Scrum: building', 3), say('Scrum: done', 1)], LOGIN, skip=('done',))['kind'],
      'building')
asked = say('x', 20)['createdAt']
check("the lead's answer after the ask, never an owner's line", (S.lead_answer([say('Yes, A', 2)], LOGIN, asked)['text'],
                                                                 S.lead_answer([say('Scrum: next', 2)], LOGIN, asked)),
      ('Yes, A', {}))
check("  0.15.0: a signed comment after the ask is never the lead's answer (a session's or the pipeline's, same login); "
      "the lead's own reply before it still is",
      (S.lead_answer([say('Live in release abc.\n\n— **Snow White** · Claude session', 2)], LOGIN, asked),
       S.lead_answer([say('LOE: estimated 2h\n\n— **Picklebot** · pipeline', 1), say('Go with B', 3)], LOGIN, asked)['text'],
       S.scrum_line([say('Scrum: next | after #4\n\n— **Snow White** · Claude session')], LOGIN)['kind']),
      ({}, 'Go with B', 'next'))

print("0.16.0: the board's ETA field")
CFGE = dict(CFG, fields=dict(CFG['fields'], ETA={'id': 'F_eta', 'type': 'date'}))
busy = lambda **kw: dict(card(light='busy'), **kw)  # noqa: E731
recent = NOW - timedelta(hours=1)
at = lambda minutes: (NOW - timedelta(minutes=minutes)).strftime('%Y-%m-%dT%H:%M:%SZ')  # noqa: E731
bline = lambda eta, minutes=5: {'kind': 'building', 'eta': eta, 'at': at(minutes), 'ref': None}  # noqa: E731
ahead = S.plan(busy(eta='2026-10-30', eta_at=at(30)), {'line': {}, 'blockers': [], 'last_commit': recent}, {}, NOW, CFGE)
passed = S.plan(busy(eta='2026-10-28', eta_at=at(30)), {'line': {}, 'blockers': [], 'last_commit': recent}, {}, NOW, CFGE)
check("the positive case: a card's ETA field ahead is the sweep's ETA, its day's end, and nobody is asked; one past is asked about",
      ((ahead['state'], ahead['question'], ahead['actions']), passed['state'].endswith('has passed'), 'eta <when>' in passed['question']),
      (('building, ETA 10/30/26 11:59 PM PT', '', []), True, True))
newer = S.plan(busy(eta='', eta_at=''), {'line': bline('10/31/26 3 PM'), 'blockers': [], 'last_commit': recent}, {}, NOW, CFGE)
older = S.plan(busy(eta='2026-11-05', eta_at=at(1)), {'line': bline('10/31/26 3 PM', 10), 'blockers': [], 'last_commit': recent}, {}, NOW, CFGE)
day_only = S.plan(busy(eta='', eta_at=''), {'line': bline('11/2/26'), 'blockers': [], 'last_commit': recent}, {}, NOW, CFGE)
no_field = S.plan(busy(), {'line': bline('10/31/26 3 PM'), 'blockers': [], 'last_commit': recent}, {}, NOW, CFG)
check("  an owner's line newer than the field names the ETA and is copied into it (a date alone too); an older one is not; a board with no ETA field writes nothing",
      ((newer['state'], newer['actions']), (older['state'], older['actions']), (day_only['state'], day_only['actions']),
       (no_field['state'], no_field['actions'])),
      (('building, ETA 10/31/26 3:00 PM PT', [('eta', ('2026-10-31', at(5)))]), ('building, ETA 11/5/26 11:59 PM PT', []),
       ('building, ETA 11/2/26 11:59 PM PT', [('eta', ('2026-11-02', at(5)))]), ('building, ETA 10/31/26 3:00 PM PT', [])))
copied = {'eta_line_at': at(5)}   # the sweep copied this line into the field (the review of 11/9/26 11:03 AM PT)
after_copy = S.plan(busy(eta='2026-10-31', eta_at=at(1)), {'line': bline('10/31/26 3 PM'), 'blockers': [], 'last_commit': recent}, copied, NOW, CFGE)
cleared = S.plan(busy(eta='', eta_at=''), {'line': bline('10/31/26 3 PM'), 'blockers': [], 'last_commit': recent}, copied, NOW, CFGE)
moved = S.plan(busy(eta='2026-11-04', eta_at=at(1)), {'line': bline('10/31/26 3 PM'), 'blockers': [], 'last_commit': recent}, copied, NOW, CFGE)
fresh_line = S.plan(busy(eta='2026-11-04', eta_at=at(10)), {'line': bline('11/1/26 9 AM', 2), 'blockers': [], 'last_commit': recent}, copied, NOW, CFGE)
check("  review: a line the sweep copied keeps its minute after the copy; the owner's clear stays cleared; the owner's new day rules; a newer line still wins",
      ((after_copy['state'], after_copy['actions']), (cleared['state'], cleared['actions']), (moved['state'], moved['actions']),
       (fresh_line['state'], fresh_line['actions'])),
      (('building, ETA 10/31/26 3:00 PM PT', []), ('building, no ETA', []), ('building, ETA 11/4/26 11:59 PM PT', []),
       ('building, ETA 11/1/26 9:00 AM PT', [('eta', ('2026-11-01', at(2)))])))
S.configure(eta_command='tickets eta {n} <when>')
asked = S.plan(busy(eta='2026-10-28', eta_at=at(30)), {'line': {}, 'blockers': [], 'last_commit': recent}, {}, NOW, CFGE)['question']
S.configure(eta_command='')
check("  the host's command is named in the ask, the card's number in it", '`tickets eta 600 <when>` or `Scrum: building | eta <when>`' in asked, True)
check('  eta_day: a date alone, a weekday and PT read past, a month/day already past is next year, a time is its day, a word is none',
      [S.eta_day(t, NOW) for t in ('11/10', 'Tue 11/10/26 PT', '10/1', '5 PM', '2026-11-12', 'when it is done')],
      ['2026-11-10', '2026-11-10', '2027-10-01', '2026-10-29', '2026-11-12', ''])
check('  review: a year-less 2/29 reads in a leap year, and is the next leap day otherwise',
      (S.eta_day('2/29', datetime(2028, 2, 1, 20, tzinfo=timezone.utc)), S.eta_day('2/29', NOW)), ('2028-02-29', '2028-02-29'))  # utc-ok: a fixed instant
check("  the ticket tools write a date field: a day, '' empties it, anything else refused",
      [T._field_value('i', 'ETA', v, None, CFGE)[1] for v in ('2026-11-10', '', '11/10/26')],
      [{'date': '2026-11-10'}, None, False])


class _Writes:
    def __init__(self):
        self.calls = []

    def item_fields(self, item, fields):
        self.calls.append(('fields', item, fields))
        return True

    def item_field(self, item, fid, **kw):
        self.calls.append(('field', item, fid, kw))
        return True


w = _Writes()
check("  review: set_field writes a date in the one batched write, and clears it, never as text",
      (T.set_field('I1', 'ETA', '2026-11-10', w, CFGE), T.set_field('I1', 'ETA', '', w, CFGE), w.calls),
      (True, True, [('fields', 'I1', {'F_eta': {'date': '2026-11-10'}}), ('field', 'I1', 'F_eta', {'clear': True})]))

print('the ETA, in the kit zone')
check('the positive case: a bare time later today', S.pt(S.parse_eta('5 PM', NOW)), '10/29/26 5:00 PM PT')
check('a bare time already past is tomorrow', S.pt(S.parse_eta('9 AM', NOW)), '10/30/26 9:00 AM PT')
check('a weekday names the next such day', S.pt(S.parse_eta('Sat 1 PM', NOW)), '10/31/26 1:00 PM PT')
check('a full stamp with PT', S.pt(S.parse_eta('11/2/26 6:00 AM PT', NOW)), '11/2/26 6:00 AM PT')
check('ISO, and nothing readable', (S.parse_eta('2026-10-29T23:00:00Z', NOW), S.parse_eta('when it is done', NOW)),
      (datetime(2026, 10, 29, 23, tzinfo=timezone.utc), None))   # utc-ok: the parse's expected instant, in UTC
check('an hour the clock repeats (1:30 AM on 11/1/26) is read in standard time', S.localize(datetime(2026, 11, 1, 1, 30), S._zone()).utcoffset(),
      timedelta(hours=-8))

print('the plan')
p = plan(card(light='busy'))
check('the positive case: a working card with no line is asked its ETA', ("what's its ETA?" in p['question'], p['state']),
      (True, 'building, no ETA'))
p = plan(card(), {'kind': 'depends on', 'ref': 44})
check('depends on an open ticket: back to the next column, linked', (p['actions'], p['change']),
      ([('move', 'next'), ('link', 44)], 'Moved back to Up Next: it depends on #44, which is not finished.'))
p = plan(card(light='busy'), {'kind': 'paused', 'ask': 'the launch first'})
check('paused: back to the next column with its why', (p['actions'], p['state']),
      ([('move', 'next')], 'paused (the launch first): back to Up Next'))
p = plan(card(), {'kind': 'blocked by', 'ref': 9, 'who': 'Lee'})
check('blocked by the lead: the label, Needs you on the blocker, the link', p['actions'],
      [('label', 'blocked'), ('needs_on', 9), ('link', 9)])
p = plan(card(light='ask'), {'kind': 'needs lee', 'ask': 'pick A or B'})
check('Needs you with its ask: the lead assigned once', (p['state'], p['actions']), ('ask, Lee: pick A or B', [('assign', 600)]))
p = plan(card(light='ask', prio='P2'))
check('Needs you with no ask: the owner asked to write it, the host\'s clear command named',
      ('board light 600 clear' in p['question'], p['state']), (True, 'ask, but no ask on record'))
p = plan(card(prio=''))
check('no Light and no Priority: Needs you for the lead', (p['actions'], p['state']),
      ([('needs', 600)], f'no Light and no Priority: ask, Lee: {S.PRIORITY_ASK}'))
p = plan(card(), {'kind': 'next', 'ref': 12})
check('next: Queued', (p['actions'], p['change']), ([('light', 'queued')], 'Switched to later: its owner says it is next, after #12.'))
since = (NOW - timedelta(minutes=S.QUEUED_IDLE_MINUTES + 1)).isoformat()
p = plan(card(light='later'), state={'queued_idle_since': since})
check('Queued past its idle clock with nothing building: back', p['actions'], [('move', 'next'), ('forget', 'queued_idle_since')])

print('closed cards')
closed = {'number': 70, 'status': 'Ready', 'owner': 'Ann', 'title': 't70'}
check('the positive case: `Scrum: done` ships it', S.closed_plan(closed, [say('Scrum: done', 1)], LOGIN, {}, False, NOW, CFG)['action'],
      'ship')
S.configure(closer=lambda title, comment: comment.startswith('Closed by the watcher:'))
check("the host's alert closer ships it", S.closed_plan(closed, [say('Closed by the watcher: fixed', 1)], LOGIN, {}, False, NOW, CFG)['action'],
      'ship')
check('not planned wrecks it; a finished column is left', (S.closed_plan(dict(closed, reason='not_planned'), [], LOGIN, {}, False, NOW, CFG)['action'],
                                                          S.closed_plan(dict(closed, status='Done'), [], LOGIN, {}, False, NOW, CFG)['action']),
      ('wreck', ''))
p = S.closed_plan(closed, [say('closing', 1)], LOGIN, {}, False, NOW, CFG)
check("a person's close asks its owner", (p['action'], p['question']),
      ('ask', '#70 was closed while in Ready: if it is done, verified and closed, answer `Scrum: done`; otherwise reopen it.'))
check('asked within the day: wait', S.closed_plan(closed, [], LOGIN, {'closed_asked_at': (NOW - timedelta(hours=2)).isoformat()},
                                                  False, NOW, CFG)['action'], 'wait')
check('closed_to: the captain for no owner or the lead', (S.closed_to(''), S.closed_to('Lee'), S.closed_to('Ann')), ('Cap', 'Cap', 'Ann'))
check('the closed filter leaves out the finished columns', S.closed_filter(CFG), 'is:closed is:issue -status:"Done" -status:"Broken"')

print('the sweep')
issue = lambda n, comments=(), state='OPEN': {'id': f'G{n}', 'state': state, 'blockedBy': {'nodes': []},  # noqa: E731
                                              'comments': {'nodes': list(comments)}}

print('closed cards with a needs light (#1133)')
check('the positive case: the filter reads the closed cards carrying the needs Light, in any column',
      S.closed_needs_filter(CFG), 'is:closed is:issue light:"ask"')
needs = Board({'light:"ask"': [dict(card(700, light='ask'), status='Done'), dict(card(701, light='ask'), status='Done')]},
              issues={700: issue(700, state='CLOSED'), 701: issue(701)}, places={700: ('Done', 'ask'), 701: ('Done', 'ask')})
lines = S.closed_needs_sweep(CFG, S.GitHub(CFG, run=needs))
check("a closed card's needs Light cleared and the lead unassigned; one reopened since the filter keeps its Light",
      (lines, [w[0] for w in needs.writes]),
      (['scrum: closed #700 kept ask: cleared, the lead unassigned'], ['fields', 'unassign']))
check('the clear is the Light written to nothing, the unassign the lead',
      (needs.writes[0][-1], needs.writes[1][-1]), ({'F_light': None}, 'acme'))
lagged = Board({'light:"ask"': [dict(card(703, light=''), status='Done')]}, issues={703: issue(703, state='CLOSED')},
               places={703: ('Done', '')})
check("a card the filter still answers whose own Light was cleared this sweep is left", (S.closed_needs_sweep(CFG, S.GitHub(CFG, run=lagged)),
                                                                                       lagged.writes), ([], []))
dry = Board({'light:"ask"': [dict(card(702, light='ask'), status='Done')]}, issues={702: issue(702, state='CLOSED')},
            places={702: ('Done', 'ask')})
check('a dry run says it and writes nothing', (S.closed_needs_sweep(CFG, S.GitHub(CFG, run=dry, dry=True), dry=True), dry.writes),
      (['scrum: closed #702 kept ask: cleared, the lead unassigned'], []))
ship_card = {'number': None, 'id': 'SC', 'title': 'SHIP IT old title', 'status': 'Backlog', 'draft': 'DI_sc', 'body': 'b'}
gh = Board({'status:"Building"': [dict(card(600, light='busy'), status='Building'), dict(card(601), status='Building'),
                                  dict(card(602), status='Building')],
            'is:closed': [], 'is:draft': [ship_card]},
           issues={600: issue(600), 601: issue(601, [say('Scrum: next | after #600', 3)]), 602: issue(602, state='CLOSED')})
store = Records()
letters.clear()
marks.clear()
lines = S.sweep(CFG, S.GitHub(CFG, run=gh), store, now=NOW)
check('the positive case: one letter to the owner for the new questions', letters,
      [('Ann', 'Scrum master: 1 Shipyard card needs a word')])
check('a next card behind its owner\'s build is Queued; a closed one goes to Done with the move marked first',
      (any('#601 next, after #600: later' in ln for ln in lines), marks, any('#602 is closed: moved to Done' in ln for ln in lines)),
      (True, [[602]], True))
check("each card's one comment, its state kept", (any(w[0] == 'comment' and w[1] == 600 and w[2].startswith(S.MARKER + ' ·') for w in gh.writes),
                                                  bool(store.get('scrum', '600'))), (True, True))
check('the ship card retitled and sent home through the host\'s record', [ln for ln in lines if 'SHIP IT' in ln],
      ['scrum: the SHIP IT card retitled "SHIP IT: drag me to Done"', 'scrum: the SHIP IT card was in Backlog; moved back to Ready'])
again = S.sweep(CFG, S.GitHub(CFG, run=gh), store, now=NOW + timedelta(minutes=10))
check('the same question is not asked twice', (len(letters), any('asked Ann' in ln for ln in again)), (1, False))
dry = Board({'status:"Building"': [dict(card(603, light='busy'), status='Building')]}, issues={603: issue(603)})
S.sweep(CFG, S.GitHub(CFG, run=dry, dry=True), Records(), now=NOW, dry=True)
check('a dry run writes nothing', dry.writes, [])
# 0.14.0 (SMADPickleBot #1233): a Dock card Warming past WARMING_MINUTES is told to its owner once per Warming
at = lambda minutes: (NOW - timedelta(minutes=minutes)).strftime('%Y-%m-%dT%H:%M:%SZ')  # noqa: E731
docked = Board({'status:"Ready"': [dict(card(900, light='warm'), status='Ready', light_at=at(4)),
                                  dict(card(901, light='warm'), status='Ready', light_at=at(2)),
                                  dict(card(902, light='ok'), status='Ready', light_at=at(30)),
                                  dict(card(903, light='warm', owner='Lee'), status='Ready', light_at=at(9))]})
store, told = Records(), {}
first = S.warming_sweep(CFG, S.GitHub(CFG, run=docked), store, NOW, told)
told_first = {k: [i[0] for i in v] for k, v in told.items()}
second = S.warming_sweep(CFG, S.GitHub(CFG, run=docked), store, NOW + timedelta(minutes=10), {})
warm_again = Board({'status:"Ready"': [dict(card(900, light='warm'), status='Ready', light_at=at(-6))]})
third = {}
S.warming_sweep(CFG, S.GitHub(CFG, run=warm_again), store, NOW + timedelta(minutes=10), third)
subject, text = S.warming_letter('Ann', [(900, 't900', 4, 'u')])
check('the positive case: a Dock card Warming past 3 minutes is told once to its owner (the captain for the lead), '
      'a newer Warming or a Ready card never; a card that warms again is told again',
      (told_first, len(first), [ln.split()[1] for ln in second], [i[0] for i in third.get('Ann', [])], subject,
       '#900 t900: Warming 4 minutes' in text),
      ({'Ann': [900], 'Cap': [903]}, 2, ['#901'], [900], 'Scrum master: 1 Dock card stuck Warming', True))
longer = {}
S.warming_sweep(dict(CFG, warming_minutes=10), S.GitHub(CFG, run=docked), Records(), NOW, longer)
shown = Board({'status:"Ready"': [dict(card(904, light='hot'), status='Ready', light_at=at(5))]})
fell = {}
S.warming_sweep(dict(CFG, light_fallback={'warm': 'hot'}), S.GitHub(CFG, run=shown), Records(), NOW, fell)
check("  the board's warming_minutes, and the fallback option the board shows for the Warming light",
      ({k: [i[0] for i in v] for k, v in longer.items()}, [i[0] for i in fell.get('Ann', [])]), ({}, [904]))
marks.clear()
moved = Board(places={604: ('Ready', '')})
ok = S.GitHub(CFG, run=moved).ship(604)
check('GitHub.ship marks the move before it is written, then moves it forward', (ok, marks, moved.writes[0][0]), (True, [[604]], 'fields'))
try:
    S.configure(store=None)
    S.sweep(CFG, S.GitHub(CFG, run=Board({'': []})), now=NOW)
    check('no store wired is NotConfigured', 'swept', 'NotConfigured')
except T.NotConfigured:
    check('no store wired is NotConfigured', True, True)

print('batched (#1207)')


class Batched(Board):
    """A board that answers the sweep's one aliased card read and its one aliased field write, counting every call."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.queries = []

    def graphql(self, query, variables=None):
        import re as _re
        self.queries.append('card' if query == S.CARD_QUERY else 'prefetch' if _re.search(r'\bi\d+: issue\(number: \d+\)', query)
                            else query.split('(', 1)[0])
        if _re.search(r'\bi\d+: issue\(number: \d+\)', query):
            return {'repository': {f'i{n}': self.issues_[int(n)] for n in _re.findall(r'\bi(\d+): issue\(number: (?:\d+)\)', query)
                                   if int(n) in self.issues_}}
        if ' c0_0: ' in query:
            out = {}
            for k in variables:
                m = _re.fullmatch(r'f(\d+)_(\d+)', k)
                if m:
                    out[f'c{m.group(1)}_{m.group(2)}'] = {'projectV2Item': {'id': variables[f'i{m.group(1)}']}}
                    self.writes.append(('batch', variables[f'i{m.group(1)}']))
            return out
        return super().graphql(query, variables)


many = [dict(card(800 + i, prio=''), status='Building', id=f'IT_{800 + i}') for i in range(8)]
big = Batched({'status:"Building"': many, 'is:closed': [], 'is:draft': []}, issues={800 + i: issue(800 + i) for i in range(8)})
letters.clear()
S.sweep(CFG, S.GitHub(CFG, run=big), Records(), now=NOW)
check('the positive case: eight Shipyard cards read in one aliased query, never one a card',
      (big.queries.count('prefetch'), big.queries.count('card')), (1, 0))
check('their eight Needs you writes in one aliased mutation',
      (sum(1 for q in big.queries if q.startswith('mutation')), len([w for w in big.writes if w[0] == 'batch'])), (1, 8))

print('the dock catch (#1203)')


class Docked(Batched):
    """A board whose dock branch names tickets (compare) and whose cards answer places' aliased read."""
    def __init__(self, commits, *a, **k):
        super().__init__(*a, **k)
        self.commits = commits

    def compare(self, base, head):
        self.queries.append(f'compare {base}...{head}')
        return self.commits

    def graphql(self, query, variables=None):
        import re as _re
        if 'projectItems(first: 10)' in query:
            self.queries.append('places')
            return {'repository': {f'i{n}': {'projectItems': {'nodes': [
                {'id': f'IT_{n}', 'project': {'id': CFG['id']}, 'status': {'name': self.places[int(n)][0]},
                 'light': {'name': self.places[int(n)][1]}}] if int(n) in self.places else []}}
                for n in _re.findall(r'\bi(\d+): issue\(number: \d+\)', query)}}
        return super().graphql(query, variables)


dock = Docked([{'sha': 'a1', 'message': 'One\n\nTicket: #900\n'}, {'sha': 'a2', 'message': 'Two\n\nTicket: #901, #902\n'}],
              {'': []}, places={900: ('Building', 'busy'), 901: ('Done', 'gone'), 902: ('Ready', 'ok')})
lines = S.dock_catch(CFG, S.GitHub(CFG, run=dock))
check('the positive case: a ticket the dock names that sits before Dock moves there in one batched write; one past it stays',
      (lines, dock.queries.count('places') >= 1, [w for w in dock.writes if w[0] == 'batch']),
      (['scrum: #900 is on dock but was not in Ready: moved there'], True, [('batch', 'IT_900'), ('batch', 'IT_900')]))
down = Docked(None, {'': []})
check('a compare GitHub does not answer is one line, nothing moved', (S.dock_catch(CFG, S.GitHub(CFG, run=down)), down.writes),
      (['scrum: main...dock could not be read; the dock catch runs next sweep'], []))
check('a dry run says what it would move and writes nothing',
      (S.dock_catch(CFG, S.GitHub(CFG, run=Docked([{'message': 'Ticket: #900'}], {'': []}, places={900: ('Building', '')})), dry=True)),
      ['scrum: #900 is on dock but not in Ready; it would move there (dry run)'])

print(f'\n{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
