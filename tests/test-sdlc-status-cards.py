#!/usr/bin/env python3
"""chuang_sdlc.status_cards: draft cards in one board column, each rewritten only when its words change.

Pinned (the positive case first): sync creates a missing card in the role's column and records its ids and words; an
unchanged card is not touched; a changed one is edited in place; a refused card leaves the others and is tried next
time; no call or no column writes nothing and keeps the old cards; keys keep their order and a key with no facts is
skipped. since keeps a mark while the signature holds, starts a new one on a change, and takes the outside instant
while one is given. waited's three shapes and '' for nothing; when's words in the zone with its year.

Run:  python -B tests/test-sdlc-status-cards.py
"""
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))
os.environ.pop('CHUANG_PLATFORM_TZ', None)

from chuang_sdlc import status_cards as S  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


CFG = {'id': 'P1', 'stages': {'status': 'Status Column'},
       'fields': {'Status': {'id': 'F1', 'options': {'Status Column': 'O9'}}}}
NOW = datetime(2026, 11, 10, 20, 0, tzinfo=timezone.utc)   # utc-ok: a fixed instant for the marks' arithmetic, never a calendar day


def render(key, f, mark):
    return f"{key} · {f['state']}", f"since {mark.get('at', '?')}"


class Board:
    def __init__(self, refuse=()):
        self.calls, self.refuse, self.n = [], set(refuse), 0

    def __call__(self, doc, v):
        self.calls.append((doc, v))
        if v.get('t', '').split(' ·')[0] in self.refuse:
            raise RuntimeError('refused')
        if doc is S.ADD:
            self.n += 1
            return {'addProjectV2DraftIssue': {'projectItem': {'id': f'I{self.n}', 'content': {'id': f'D{self.n}'}}}}
        return {}


print('sync')
facts = {'a': {'state': 'waiting'}, 'b': {'state': 'ready'}}
marks = {'a': {'at': 't1'}, 'b': {'at': 't2'}}
b = Board()
out = S.sync(b, CFG, 'status', ('a', 'b', 'c'), facts, marks, render, {})
check('the positive case: two cards created in the column, ids and words kept, two written',
      ([(d is S.ADD, d is S.COLUMN, v.get('o')) for d, v in b.calls], out['cards'],
       out['written'], out['since'] is marks),
      ([(True, False, None), (False, True, 'O9'), (True, False, None), (False, True, 'O9')],
       {'a': {'item': 'I1', 'draft': 'D1', 'words': 'a · waiting\nsince t1'},
        'b': {'item': 'I2', 'draft': 'D2', 'words': 'b · ready\nsince t2'}}, 2, True))
b2 = Board()
again = S.sync(b2, CFG, 'status', ('a', 'b'), facts, marks, render, out)
check('the same words again: nothing called, nothing written', (b2.calls, again['written']), ([], 0))
b3 = Board()
moved = S.sync(b3, CFG, 'status', ('a', 'b'), dict(facts, a={'state': 'approved'}), marks, render, out)
check('a changed card is edited in place by its draft, the other left',
      ([(d is S.EDIT, v) for d, v in b3.calls], moved['cards']['a']['item'], moved['written']),
      ([(True, {'d': 'D1', 't': 'a · approved', 'b': 'since t1'})], 'I1', 1))
b4 = Board(refuse={'a'})
part = S.sync(b4, CFG, 'status', ('a', 'b'), facts, marks, render, {})
check('a refused card leaves the other written and itself unkept', (sorted(part['cards']), part['written']), (['b'], 1))
kept = {'cards': {'a': {'item': 'X', 'draft': 'Y', 'words': 'old'}}}
check('no call writes nothing and keeps the old cards',
      S.sync(None, CFG, 'status', ('a',), facts, marks, render, kept)['cards'], kept['cards'])
check('no column for the role writes nothing',
      (S.sync(Board(), CFG, 'other', ('a',), facts, marks, render, kept)['written'],
       S.sync(Board(), {}, 'status', ('a',), facts, marks, render, kept)['written']), (0, 0))

print('since')


def sig(key, f):
    return f"{key}|{f['state']}"


old = {'a': {'sig': 'a|waiting', 'at': '2026-11-09T00:00:00+00:00'}}
got = S.since(old, {'a': {'state': 'waiting'}, 'b': {'state': 'ready'}}, NOW, sig)
check('the positive case: the same signature keeps its mark; a new key starts now',
      got, {'a': old['a'], 'b': {'sig': 'b|ready', 'at': '2026-11-10T20:00:00+00:00'}})
check('a changed signature starts now', S.since(old, {'a': {'state': 'approved'}}, NOW, sig)['a']['at'],
      '2026-11-10T20:00:00+00:00')
check("an outside instant wins while it is given (a store's own submission time)",
      S.since(old, {'a': {'state': 'waiting', 'sent': 'T0'}}, NOW, sig, lambda f: f.get('sent'))['a'], {'sig': 'a|waiting', 'at': 'T0'})

print('waited and when')
check('waited: days and hours, hours and minutes, minutes',
      [S.waited('2026-11-08T16:00:00+00:00', NOW), S.waited('2026-11-10T14:48:00Z', NOW), S.waited('2026-11-10T19:53:00', NOW)],
      ['2d 4h', '5h 12m', '7m'])
check("waited of nothing or nonsense is ''", [S.waited('', NOW), S.waited('soon', NOW)], ['', ''])
from zoneinfo import ZoneInfo  # noqa: E402
check('when: the instant in the zone with its year and zone word; nothing is empty',
      [S.when('2026-11-10T20:00:00Z', ZoneInfo('America/Los_Angeles')), S.when('', None)], ['11/10/26 12:00 PM PT', ''])

print(f'\n{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
