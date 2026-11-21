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
full = S.scrum_line([say('Scrum: next | after genechuang/ChuangFinance#26')], LOGIN)
bare = S.scrum_line([say('Scrum: depends on #26')], LOGIN)
check("  0.42.0 (SMADPickleBot #2006): a full reference keeps its repository; a bare number is the card's own",
      ((full['ref'], full['ref_repo']), (bare['ref'], bare['ref_repo'])), ((26, 'genechuang/ChuangFinance'), (26, '')))


class _Cards:
    """GitHub.card's GraphQL stand-in: the owner, repository and number asked, one issue state each."""
    def __init__(self, states):
        self.asked, self.states = [], states

    def graphql(self, query, variables):
        key = (variables['owner'], variables['repo'], variables['n'])
        self.asked.append(key)
        return {'repository': {'issue': {'number': key[2], 'state': self.states.get(key, 'OPEN')}}}


_gh = S.GitHub.__new__(S.GitHub)
_gh.owner, _gh.repo, _gh._cards = 'genechuang', 'SMADPickleBot', {26: {'number': 26, 'state': 'CLOSED'}}
_gh.run = _Cards({('genechuang', 'ChuangFinance', 26): 'OPEN'})
check("  0.42.0: GitHub.card reads another repository's issue in that repository, never this board's prefetched one; "
      "its own repository's number reads as before",
      (_gh.card(26, repo='genechuang/ChuangFinance')['state'], _gh.card(26)['state'],
       _gh.card(26, repo='GeneChuang/SMADPickleBot')['state'], _gh.run.asked),
      ('OPEN', 'CLOSED', 'CLOSED', [('genechuang', 'ChuangFinance', 26)]))
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
pcfg = dict(CFG, lights=dict(CFG['lights'], pushing='push'))
pushed = S.plan(card(light='push'), {'line': {}, 'blockers': [], 'owner_building': []}, {}, NOW, pcfg)
check('0.21.0: a card whose dock push is running is asked nothing and moved nowhere',
      (pushed['state'], pushed['actions'], pushed.get('question', '')), ('push: its dock push is running', [], ''))
# 0.22.0 (SMADPickleBot #1336): the blocked Light, for a wait on another ticket or on something outside the board
bcfg = dict(CFG, lights=dict(CFG['lights'], blocked='stop'))
bplan = lambda c, line=None, state=None, **facts: S.plan(c, dict({'line': line or {}, 'blockers': []}, **facts), state or {}, NOW, bcfg)  # noqa: E731
line = S.scrum_line([say('Scrum: blocked on the Monday police email | eta 11/11/26 6 PM')], LOGIN)
p = bplan(card(light='busy'), line)
check('0.22.0, the positive case: `blocked on <what> | eta <when>` reads its wait, sets the blocked light and label, '
      'copies the ETA, and asks nothing before the ETA',
      (line['kind'], line['ask'], [a[0] for a in p['actions']], p['actions'][0], p['question'], p['state'].startswith('blocked on the Monday police email, ETA')),
      ('blocked on', 'the Monday police email', ['light', 'label'], ('light', 'blocked'), '', True))
ecfg = dict(bcfg, fields=dict(bcfg['fields'], ETA={'id': 'F_eta', 'type': 'date'}))
check('  a board with an ETA field gets the wait\'s end copied into it',
      S.plan(card(light='busy'), {'line': line, 'blockers': []}, {}, NOW, ecfg)['actions'][-1][0], 'eta')
late = S.scrum_line([say('Scrum: blocked on the dry run | eta 10/28/26 9 AM')], LOGIN)
p = bplan(card(light='stop', labels=('blocked',)), late)
check('  past its ETA its owner is asked whether the wait is over, its light and label left as they are',
      ([a for a in p['actions'] if a[0] in ('light', 'label')], 'is the wait over?' in p['question']), ([], True))
p = bplan(card(light='stop', labels=('blocked',)), S.scrum_line([say('Scrum: blocked on the club')], LOGIN))
check('  with no ETA its owner is asked for one', 'with no ETA: when should the wait be over?' in p['question'], True)
p = bplan(card(light='stop', labels=('blocked',)), {'kind': 'building', 'eta': '5 PM', 'at': NOW.isoformat()})
check('  `building` after the wait: the working light back, the label off',
      (sorted(a for a in p['actions'] if a[0] in ('light', 'unlabel')), p['change'].endswith('the wait is over.')),
      ([('light', 'working'), ('unlabel', 'blocked')], True))
p = bplan(card(), {'kind': 'blocked by', 'ref': 9, 'who': 'Lee'})
check('  `blocked by #N` sets the blocked light beside its label and link',
      [a for a in p['actions'] if a[0] in ('light', 'label', 'link')], [('light', 'blocked'), ('label', 'blocked'), ('link', 9)])
p = bplan(card(light='stop', labels=('blocked',)), {'kind': 'blocked by', 'ref': 9}, blockers=[(9, 'CLOSED')])
check('  every blocker closed: the label and the blocked light off, its owner asked',
      (sorted(a for a in p['actions'] if a[0] in ('light', 'unlabel')), 'is unblocked' in p['question']),
      ([('light', 'shipyard'), ('unlabel', 'blocked')], True))
check('  a board with no blocked light is unchanged: no light action for `blocked on`',
      [a[0] for a in plan(card(light='busy'), line)['actions']], ['label'])
# the review of #1336, a case each
check('  review: Needs you and a running push are never relit to Blocked',
      [[a for a in bplan(card(light=l), line)['actions'] if a[0] == 'light'] for l in ('ask',)]
      + [[a for a in S.plan(card(light='push'), {'line': line, 'blockers': []}, {}, NOW,
                            dict(bcfg, lights=dict(bcfg['lights'], pushing='push')))['actions'] if a[0] == 'light']],
      [[], []])
p = bplan(card(light='busy'), {'kind': 'building', 'eta': '5 PM', 'at': NOW.isoformat()}, blockers=[(49, 'OPEN')])
check('  review: an open linked blocker alone keeps the label but never the light: the owner says building',
      [a for a in p['actions'] if a[0] in ('light', 'label')], [('label', 'blocked')])
p = plan(card(light='busy', labels=('blocked',)), {'kind': 'building', 'eta': '5 PM', 'at': NOW.isoformat()})
check('  review: a `blocked` label no line or link backs comes off, on a board with or without the blocked light',
      ([a for a in p['actions'] if a[0] == 'unlabel'],
       [a for a in bplan(card(light='busy', labels=('blocked',)), {'kind': 'building', 'eta': '5 PM', 'at': NOW.isoformat()})['actions'] if a[0] == 'unlabel']),
      ([('unlabel', 'blocked')], [('unlabel', 'blocked')]))
odd = S.scrum_line([say('Scrum: blocked on ETA from #12 support | eta 11/12/26 5 PM')], LOGIN)
check('  review: "ETA" and "#N" inside the wait are part of it, the ETA read from after the bar',
      (odd['ask'], odd['eta'], odd['ref']), ('ETA from #12 support', '11/12/26 5 PM', None))
p = bplan(dict(card(light='stop', labels=('blocked',)), eta='2026-10-01'), S.scrum_line([say('Scrum: blocked on the club')], LOGIN))
check("  review: a wait with no ETA never borrows the field's old build date: it is asked for one",
      ('with no ETA' in p['question'], p['state'].endswith('no ETA')), (True, True))
# 0.43.0 (SMADPickleBot, Gene 11/21/26 10:56 AM PT): #1829 sat Blocked 11 hours on "Gene's pick on #2079" after #2079
# closed, and #230 18 minutes after its three linked blockers closed: a `blocked on` wait unblocks once every ticket it
# names or links is closed.
pick = S.scrum_line([say("Scrum: blocked on Gene's pick on #2079 | eta 11/21/26 6 PM")], LOGIN)
p = bplan(card(light='stop', labels=('blocked',)), pick)
check('0.43.0: a ticket the wait names is linked like `blocked by`, and the card stays blocked while it is open',
      ([a for a in p['actions'] if a[0] in ('link', 'unlabel')], p['state'].startswith('blocked on')), ([('link', 2079)], True))
p = bplan(card(light='stop', labels=('blocked',)), pick, blockers=[(2079, 'CLOSED')])
check('  every ticket it names closed: the label and the blocked light off, its owner told it is unblocked (#1829)',
      (sorted(a for a in p['actions'] if a[0] in ('light', 'unlabel')), 'is unblocked' in p['question'], '#2079' in p['change']),
      ([('light', 'shipyard'), ('unlabel', 'blocked')], True, True))
watch = S.scrum_line([say("Scrum: blocked on the Watch app's store build | eta 11/22/26")], LOGIN)
p = bplan(card(light='stop', labels=('blocked',)), watch, blockers=[(2118, 'CLOSED'), (1616, 'CLOSED')])
check('  every linked blocker closed, none named: unblocked the same way (#230)',
      (sorted(a for a in p['actions'] if a[0] in ('light', 'unlabel')), 'is unblocked' in p['question']),
      ([('light', 'shipyard'), ('unlabel', 'blocked')], True))
p = bplan(card(light='stop', labels=('blocked',)), watch, blockers=[(2118, 'CLOSED'), (1616, 'OPEN')])
check('  a linked blocker still open keeps it blocked, asked nothing before its ETA',
      ([a for a in p['actions'] if a[0] == 'unlabel'], p['state'].startswith('blocked on'), p['question']), ([], True, ''))
started_on = Board(places={970: ('Building', 'stop'), 971: ('Backlog', 'stop')})
T.start([970, 971], started_on, bcfg)
check("  review: a commit's start keeps a Shipyard card's Blocked light, and turns one elsewhere to working",
      [w for w in started_on.writes if w[0] == 'fields'],
      [('fields', 'IT_971', {'F_status': 'o_y', 'F_light': 'l_busy'})])
p = plan(card(), {'kind': 'next', 'ref': 12})
check('next: Queued', (p['actions'], p['change']), ([('light', 'queued')], 'Switched to later: its owner says it is next, after #12.'))
p = plan(card(light='later'), state={'queued_idle_since': (NOW - timedelta(minutes=31)).isoformat()})
check('0.40.0: a Queued card with nothing of its owner\'s building stays where it was queued: no move back, no question, '
      'an old idle clock forgotten',
      (p['actions'], p['question'], 'its owner is told to start a card' in p['state']),
      ([('forget', 'queued_idle_since')], '', True))
check('  and with no old clock there is nothing to do to the card at all', plan(card(light='later'))['actions'], [])

print('idle owners (0.40.0, SMADPickleBot #2066)')
row = lambda n, light='', prio='P2', owner='Ann', labels=(): dict(card(n, light, prio, owner, labels),  # noqa: E731
                                                                  content={'number': n, 'url': f'https://x/{n}'})
ago = lambda m: NOW - timedelta(minutes=m)  # noqa: E731
got = S.idle_owners([row(21, prio='P3'), row(22, light='later', prio='P3'), row(23, prio='P1'), row(24, light='ask'),
                     row(25, labels=('blocked',)), row(26, prio='')], CFG, {}, NOW)
check('the positive case: nothing building and cards to work: start the first by Priority, counted without the Needs-you, '
      'blocked and unranked cards',
      ({k: (v['kind'], v['number'], v['free']) for k, v in got.items()}), {'Ann': ('start', 23, 3)})
check('  within a Priority a Queued card comes before one with no Light, then the lowest number',
      S.idle_owners([row(31), row(32, light='later'), row(30)], CFG, {}, NOW)['Ann']['number'], 32)
check('  an owner building, with a fresh start or none on record, is not idle',
      (S.idle_owners([row(41, light='busy'), row(42)], CFG, {41: ago(10)}, NOW),
       S.idle_owners([row(41, light='busy'), row(42)], CFG, {41: None}, NOW)), ({}, {}))
stale = S.idle_owners([row(41, light='busy', prio='P3'), row(43, light='busy', prio='P2'), row(42)], CFG,
                      {41: ago(200), 43: ago(S.STALE_BUILD_MINUTES)}, NOW)
check('  building cards gone quiet 90 minutes by their newest start or commit: carry on with the first by Priority',
      ({k: (v['kind'], v['number'], v['minutes']) for k, v in stale.items()}), {'Ann': ('carry on', 43, 90)})
check('  one minute short of it, nothing',
      S.idle_owners([row(41, light='busy')], CFG, {41: ago(S.STALE_BUILD_MINUTES - 1)}, NOW), {})
check('  the lead, a card with no owner, an owner whose every card waits, and an owner mid-push are never told',
      S.idle_owners([row(51, owner='Lee'), row(52, owner=''), row(53, owner='Cap', light='ask'),
                     row(54, owner='Cap', labels=('blocked',))], CFG, {}, NOW), {})
pcfg = dict(CFG, lights=dict(CFG['lights'], pushing='push'))
check('  a dock push under way is work: nothing told',
      S.idle_owners([row(61, light='push'), row(62)], pcfg, {}, NOW), {})
subject, text = S.letter('Ann', [], dict(got['Ann'], sweeps=1, since=NOW.isoformat()))
check('the letter: the card to start leads, with the count of what can be worked and the reasons that are none',
      (subject, 'Start #23 t23 now' in text, '3 of your Building cards can be worked' in text,
       'A full context, a ship, a warm, a run or a reply' in text, 'https://x/23' in text, 'Told' in text),
      ('Scrum master: start #23 now', True, True, True, True, False))
subject, text = S.letter('Ann', [(21, 't21', 'is it your next?', 'https://x/21')], dict(got['Ann'], sweeps=3, since=NOW.isoformat()))
check('  with questions in the same letter, and the sweeps counted from the second on',
      (subject, 'Told 3 sweeps running; first at ' in text, "And the scrum master's sweep has a question on each of these" in text,
       '- #21 t21: is it your next?' in text),
      ('Scrum master: start #23 now (1 Shipyard card needs a word)', True, True, True))
subject, text = S.letter('Ann', [], dict(stale['Ann'], sweeps=1, since=NOW.isoformat()))
check('  a quiet build: carry on, and nothing to answer if it is under way',
      (subject, 'no start or commit has named any card you are building for 90 minutes' in text,
       'carry on and answer nothing' in text), ('Scrum master: carry on with #43 now', True, True))
check('  a letter with no idle item is the questions alone, as before',
      S.letter('Ann', [(21, 't21', 'q?', 'u')]),
      ('Scrum master: 1 Shipyard card needs a word',
       "Ann, the scrum master's sweep has a question on each of these. Answer on the ticket with one `Scrum:` line:\n- #21 t21: q?\n  u"))
irec = Records()
lines1, idle1 = S.idle_sweep(CFG, [row(23, prio='P1')], {}, irec, NOW)
lines2, idle2 = S.idle_sweep(CFG, [row(23, prio='P1')], {}, irec, NOW + timedelta(minutes=30))
check('the sweep of them: the owner\'s spell counted from its first sweep, and said',
      (lines1, idle2['Ann']['sweeps'], idle2['Ann']['since'], irec.get('scrum', 'idle-ann')),
      (['scrum: Ann is idle (nothing being built and 1 card to work: told to start #23; sweep 1 of this spell)'], 2,
       NOW.isoformat(), {'since': NOW.isoformat(), 'sweeps': 2, 'ticket': 23, 'kind': 'start'}))
lines3, idle3 = S.idle_sweep(CFG, [row(23, light='busy', prio='P1')], {23: NOW + timedelta(minutes=40)}, irec, NOW + timedelta(minutes=60))
lines4, idle4 = S.idle_sweep(CFG, [row(23, prio='P1')], {}, irec, NOW + timedelta(minutes=90))
check('  working again empties the record, so the next spell counts from one',
      (lines3, idle3, irec.get('scrum', 'idle-ann') == {'since': (NOW + timedelta(minutes=90)).isoformat(), 'sweeps': 1,
                                                        'ticket': 23, 'kind': 'start'}, idle4['Ann']['sweeps']),
      ([], {}, True, 1))
drec = Records()
S.idle_sweep(CFG, [row(23, prio='P1')], {}, drec, NOW, dry=True)
check('  a dry run keeps no record', drec.docs, {})

print('closed cards')
closed = {'number': 70, 'status': 'Ready', 'owner': 'Ann', 'title': 't70'}
check('the positive case: `Scrum: done` ships it', S.closed_plan(closed, [say('Scrum: done', 1)], LOGIN, {}, False, NOW, CFG)['action'],
      'ship')
S.configure(closer=lambda title, comment: comment.startswith('Closed by the watcher:'))
check("the host's alert closer ships it", S.closed_plan(closed, [say('Closed by the watcher: fixed', 1)], LOGIN, {}, False, NOW, CFG)['action'],
      'ship')
check('not planned archives it (0.20.0); a finished column is left',
      (S.closed_plan(dict(closed, reason='not_planned'), [], LOGIN, {}, False, NOW, CFG)['action'],
       S.closed_plan(dict(closed, reason='duplicate'), [], LOGIN, {}, False, NOW, CFG)['action'],
       S.closed_plan(dict(closed, status='Done'), [], LOGIN, {}, False, NOW, CFG)['action']),
      ('archive', 'archive', ''))
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
# 0.26.0 (SMADPickleBot #1450): a card in the working column nobody has started is asked about again every
# UNSTARTED_AGAIN_MINUTES, where every other question waits ASK_AGAIN_HOURS (a card dragged in at 9:22 AM sat until
# 1:14 PM on one missed letter)
idle = Board({'status:"Building"': [dict(card(610), status='Building')], 'is:closed': [], 'is:draft': []}, issues={610: issue(610)})
idle_store = Records()
letters.clear()
texts = []
S.configure(mail=lambda to, text, subject, sender='': (letters.append((to, subject)), texts.append(text)) and f'mailed {to}')
first = S.sweep(CFG, S.GitHub(CFG, run=idle), idle_store, now=NOW)
soon = S.sweep(CFG, S.GitHub(CFG, run=idle), idle_store, now=NOW + timedelta(minutes=29))
later = S.sweep(CFG, S.GitHub(CFG, run=idle), idle_store, now=NOW + timedelta(minutes=30))
third = S.sweep(CFG, S.GitHub(CFG, run=idle), idle_store, now=NOW + timedelta(minutes=61))
check('the positive case: an unstarted card is asked at once, not at 29 minutes, again at 30 and again 31 later; and '
      '(0.40.0) its owner, building nothing, is told to start it at every one of the four sweeps',
      ([any('#610 no Light' in ln and 'asked Ann' in ln for ln in x) for x in (first, soon, later, third)], len(letters),
       [any('Ann is idle' in ln and 'told to start #610' in ln for ln in x) for x in (first, soon, later, third)]),
      ([True, False, True, True], 4, [True, True, True, True]))
check('the repeated questions say how many times and how long',
      ('Asked 2 times now; first asked 30 minutes ago.' in texts[2], 'Asked 3 times now; first asked 61 minutes ago.' in texts[3],
       'Asked' in texts[0], any('asked Ann again (3 times since' in ln for ln in third)), (True, True, False, True))
check('  0.40.0: the idle word leads each letter, alone at the sweep with no new question, its sweeps counted',
      (letters[0], letters[1], 'Start #610 t610 now' in texts[1], 'question on each' in texts[1],
       'Told 2 sweeps running' in texts[1], 'Told 4 sweeps running' in texts[3]),
      (('Ann', 'Scrum master: start #610 now (1 Shipyard card needs a word)'), ('Ann', 'Scrum master: start #610 now'),
       True, False, True, True))
one_card = S.sweep(CFG, S.GitHub(CFG, run=idle), Records(), now=NOW, only=610)
check('  a one-card run (an event named the card) tells nobody they are idle: the full sweep alone does',
      (any('is idle' in ln for ln in one_card), letters[-1][1]), (False, 'Scrum master: 1 Shipyard card needs a word'))
letters.pop()
started = Board({'status:"Building"': [dict(card(610, light='busy'), status='Building')], 'is:closed': [], 'is:draft': []},
                issues={610: issue(610, [say('Scrum: building | eta 10/29/26 5 PM PT', 1)])})
S.sweep(CFG, S.GitHub(CFG, run=started), idle_store, now=NOW + timedelta(minutes=95))
check('once started, its next question (here its ETA) is a new one with its own count, on the day-long wait again',
      (len(letters), idle_store.get('scrum', '610').get('asked_count'), 'Asked' in texts[-1]), (5, 1, False))
quiet = S.sweep(CFG, S.GitHub(CFG, run=started), idle_store, now=NOW + timedelta(minutes=95 + 31))
check('and that one is not asked again 31 minutes on, nor is its owner, now building, told they are idle',
      (len(letters), any('asked Ann' in ln for ln in quiet), any('is idle' in ln for ln in quiet), idle_store.get('scrum', 'idle-ann')),
      (5, False, False, {}))
S.configure(mail=lambda to, text, subject, sender='': letters.append((to, subject)) or f'mailed {to}')
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
# 0.29.0 (SMADPickleBot #1600): a card is stuck only when nothing is warming it; the host's warm record says
asked = []
S.configure(warm_state=lambda st: asked.append(st) or (True, 'warming abc1234, 190 s in'))
running, run_store, reads = {}, Records(), []
counted = Board({'status:"Ready"': [dict(card(900, light='warm'), status='Ready', light_at=at(9))]})
live = S.warming_sweep(CFG, S.GitHub(CFG, run=lambda *a, **k: reads.append(a) or counted(*a, **k)), run_store, NOW, running)
check('while the host says its warm is running, nobody is told, no card is marked told and the board is not read',
      (running, live, run_store.get('scrum', '900'), reads, asked == [run_store]),
      ({}, ['scrum: the dock is warming now (warming abc1234, 190 s in); no warm card is asked about'], None, [], True))
S.configure(warm_state=lambda st: (False, 'abc1234 failed at images (exit 1)'))
dead = {}
S.warming_sweep(CFG, S.GitHub(CFG, run=counted), run_store, NOW, dead)
subject5, text5 = S.warming_letter('Ann', dead.get('Ann', []))
check('  once no warm is running the limit decides as before, and the letter carries the warm\'s last word once',
      ([i[0] for i in dead.get('Ann', [])], text5.count("No warm is running now. The warm's last word: abc1234 failed at images (exit 1)"),
       '#900' in text5), ([900], 1, True))


def unread(st):
    raise RuntimeError('the record store is down')


S.configure(warm_state=unread)
down = {}
S.warming_sweep(CFG, S.GitHub(CFG, run=counted), Records(), NOW, down)
check('  a warm record that cannot be read never silences the sweep: the limit alone decides, no note',
      ([i[0] for i in down.get('Ann', [])], [i[4] for i in down.get('Ann', [])]), ([900], ['']))
S.configure(warm_state=None)
# 0.20.0 (SMADPickleBot #1337): the failed column holds a release's failures alone; its owner asked about each daily
hours = lambda h: (NOW - timedelta(hours=h)).strftime('%Y-%m-%dT%H:%M:%SZ')  # noqa: E731
broken = Board({'is:closed': [dict(card(950), status='Broken'), dict(card(951), status='Broken')],
                'is:open': [dict(card(952, light='red'), status='Broken', status_at=hours(30)),
                            dict(card(953, light='red', owner='Lee'), status='Broken', status_at=hours(25)),
                            dict(card(954, light='red'), status='Broken', status_at=hours(3)),
                            {'number': None, 'title': '— Broken: a card here failed —', 'status': 'Broken'}]},
               issues={950: dict(issue(950, state='CLOSED'), stateReason='NOT_PLANNED'),
                       951: dict(issue(951, state='CLOSED'), stateReason='COMPLETED')},
               places={950: ('Broken', 'red'), 951: ('Broken', 'red')})
S.configure(start_command='tickets start {n}')
store, wrecked = Records(), {}
marks.clear()
first = S.wrecked_sweep(CFG, S.GitHub(CFG, run=broken), store, NOW, wrecked)
subject, text = S.wrecked_letter('Ann', wrecked['Ann'])
check('the positive case: a closed-as-not-planned card in the failed column is archived (marked first), one closed as '
      'completed is left; an open card past a day asks its owner (the captain for the lead), a newer one and the '
      'placeholder never',
      (first, [w for w in broken.writes if w[0] == 'archive'], marks, {k: [i[0] for i in v] for k, v in wrecked.items()}),
      (['scrum: #950 in Broken was closed as not planned: archived off the board',
        'scrum: #952 has sat in Broken 30 hours: Ann asked to re-ship or drop it',
        'scrum: #953 has sat in Broken 25 hours: Cap asked to re-ship or drop it'],
       [('archive', 'IT_950')], [[950]], {'Ann': [952], 'Cap': [953]}))
check("  the letter names the re-ship command and the drop, under its own subject",
      (subject, '`tickets start 952`' in text, 'close it as not planned, which archives its card' in text),
      ('Scrum master: 1 card left in the failed column', True, True))
again, later = {}, {}
S.wrecked_sweep(CFG, S.GitHub(CFG, run=broken), store, NOW + timedelta(hours=2), again)
S.wrecked_sweep(CFG, S.GitHub(CFG, run=broken), store, NOW + timedelta(hours=25), later)
check('  asked once a day: not again two hours on, again a day on',
      ({k: [i[0] for i in v] for k, v in again.items()}, sorted(i[0] for v in later.values() for i in v)), ({}, [952, 953, 954]))
refused = Board({'is:closed': [dict(card(950), status='Broken')], 'is:open': []},
                issues={950: dict(issue(950, state='CLOSED'), stateReason='NOT_PLANNED')}, places={950: ('Broken', 'red')},
                refuse={'archive'})
check('  a refused archive says so for the next sweep',
      S.wrecked_sweep(CFG, S.GitHub(CFG, run=refused), Records(), NOW, {}),
      ['scrum: #950 in Broken was closed as not planned: the archive was refused; next sweep tries again'])
dry = Board({'is:closed': [dict(card(950), status='Broken')], 'is:open': [dict(card(952), status='Broken', status_at=hours(30))]},
            issues={950: dict(issue(950, state='CLOSED'), stateReason='NOT_PLANNED')}, places={950: ('Broken', 'red')})
dry_store = Records()
S.wrecked_sweep(CFG, S.GitHub(CFG, run=dry, dry=True), dry_store, NOW, {}, dry=True)
check('  a dry run writes nothing and keeps no record', (dry.writes, dry_store.get('scrum', '952')), ([], None))
# 0.38.0 (SMADPickleBot #1918): a card with no column is in no other read, so the sweep places it and tells the captain
lost = Board({'no:status': [dict(card(960, owner='', prio=''), status=''), dict(card(961), status=''),
                            dict(card(962), status='Backlog'),
                            {'number': None, 'title': '— Broken: a card here failed —', 'status': ''}]})
unplaced = {}
lines = S.unplaced_sweep(CFG, S.GitHub(CFG, run=lost), unplaced)
subject, text = S.unplaced_letter(S.captain(), unplaced.get(S.captain(), []))
check('the positive case: each issue with no column is put in the backlog and goes into the captain\'s one letter; a '
      'card that has a column and a fixed card are left alone',
      (lines, [i[0] for i in unplaced.get(S.captain(), [])], len([w for w in lost.writes if w[0] == 'fields']), subject,
       '#960' in text and 'no Owner, Priority' in text),
      (['scrum: #960 had no column: put in Backlog; it still has no Owner, Priority, Area',
        'scrum: #961 had no column: put in Backlog; it still has no Area'],
       [960, 961], 2, 'Scrum master: 2 cards had no column', True))
class _Down(Board):
    def graphql(self, query, variables=None):
        return None


check('  a board that does not answer says so, and a refused move is said for the next sweep and tells nobody',
      (S.unplaced_sweep(CFG, S.GitHub(CFG, run=_Down()), {}),
       S.unplaced_sweep(CFG, S.GitHub(CFG, run=Board({'no:status': [dict(card(960), status='')]}, refuse={'fields'})), unplaced)[0][:60],
       len(unplaced[S.captain()])),
      (['scrum: the cards with no column did not answer; next sweep'],
       'scrum: #960 had no column: the move to Backlog was refused; ', 2))
dry = Board({'no:status': [dict(card(960), status='')]})
S.unplaced_sweep(CFG, S.GitHub(CFG, run=dry, dry=True), {}, dry=True)
check('  a dry run writes nothing', dry.writes, [])
S.configure(start_command='')
check("  with no start command the question says to move it to the shipyard",
      'move it to the shipyard' in S.wrecked_question(9, 'Broken', 30), True)
back = Board(places={960: ('Backlog', ''), 961: ('Backlog', '')})
back.archived.add('IT_960')
started = T.start([960, 961], back, CFG)
check('0.20.0: a pick-up restores an archived card before it moves it, and restores nothing on a card on the board',
      (started, [w[:2] for w in back.writes if w[0] in ('unarchive', 'fields')], T.unarchive(961, back, CFG)),
      ([(960, True), (961, True)], [('unarchive', 'IT_960'), ('fields', 'IT_960'), ('fields', 'IT_961')], True))
refuse_back = Board(places={962: ('Backlog', '')}, refuse={'unarchive'})
refuse_back.archived.add('IT_962')
check('  a refused restore is a failed pick-up, nothing moved, and unarchive says False',
      (T.start([962], refuse_back, CFG), [w[0] for w in refuse_back.writes], T.unarchive(962, refuse_back, CFG)),
      ([(962, False)], ['unarchive'], False))
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

print('release_train (0.31.0, SMADPickleBot #1652):')
from chuang_sdlc import tenant as TN  # noqa: E402
S.configure(store=lambda: Records())
called = []
kept = {name: getattr(S, name) for name in ('dock_catch', 'ship_card_home', 'warming_sweep', 'wrecked_sweep')}
for name in kept:
    setattr(S, name, (lambda name: lambda *a, **k: called.append(name) or [])(name))
try:
    S.sweep(CFG, S.GitHub(CFG, run=Board({'': []})), Records(), now=NOW)
    check('the positive case: a release train board runs the dock, ship card and Warming parts',
          sorted(called), ['dock_catch', 'ship_card_home', 'warming_sweep', 'wrecked_sweep'])
    called.clear()
    bare = {k: v for k, v in CFG.items() if k not in ('ship_card', 'placeholders')}
    with TN.using('other', scrum={'release_train': False}):
        none = S.sweep(bare, S.GitHub(bare, run=Board({'': []})), Records(), now=NOW)
    check('a board with no release train, its config naming no ship card or label cards, skips them and keeps the '
          'failed column sweep', (called, [ln for ln in none if 'placeholder' in ln]), (['wrecked_sweep'], []))
    # 0.32.0 (#1688): the ship card and the label cards are the board's own, kept whenever its config names them
    called.clear()
    made = []
    keep_t = {name: getattr(S._tickets, name) for name in ('placeholders', 'card_order_keep')}
    S._tickets.placeholders = lambda run, cfg: made.append('placeholders') or 'tickets: placeholders made: Dock'
    S._tickets.card_order_keep = lambda run, cfg, dry=False: made.append('card_order_keep') or []
    try:
        own = dict(bare, ship_card={'home': 'dock'}, placeholders={'dock': '— Dock —'})
        with TN.using('other', scrum={'release_train': False}):
            lines = S.sweep(own, S.GitHub(own, run=Board({'': []})), Records(), now=NOW)
            live = len(made)
            S.sweep(own, S.GitHub(own, run=Board({'': []})), Records(), now=NOW, dry=True)
        check('no train, its config names both: the ship card kept, a missing label card made and said, the order kept; '
              'never the dock or Warming parts', (called[:2], made[:live], 'scrum: tickets: placeholders made: Dock' in lines),
              (['ship_card_home', 'wrecked_sweep'], ['placeholders', 'card_order_keep'], True))
        check('a dry run makes no label card', made[live:], ['card_order_keep'])
    finally:
        for name, fn in keep_t.items():
            setattr(S._tickets, name, fn)
finally:
    for name, fn in kept.items():
        setattr(S, name, fn)

print(f'\n{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
