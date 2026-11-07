#!/usr/bin/env python3
"""chuang_sdlc.board: a ticket board's names in one place, and the `Ticket:` trailers.

Pinned (the positive case first): a role is its column; an old or merged name is its new one; the flow in order; a
card's rank by its column and, where two roles share one, its Light (or the Light's fallback), a merged column's card at
the role it merged into, None off the flow; the Light a column's card shows (None when two roles share it); placeholder
titles by the config or the `— … —` shape, and their roles; the trailers: numbers sorted once, a `none (why)` naming no
ticket whatever its words, none at all; load() reads JSON.

Run:  python -B tests/test-sdlc-board.py
"""
import json
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))

from chuang_sdlc import board as B  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


CFG = {
    'stages': {'backlog': 'Backlog', 'next': 'Up Next', 'shipyard': 'Building', 'dock': 'Ready', 'dev': 'Ready',
               'shipping': 'Done', 'shipped': 'Done', 'failed': 'Broken'},
    'flow': ['backlog', 'next', 'shipyard', 'dock', 'dev', 'shipping', 'shipped'],
    'renamed': {'In progress': 'Building', 'Live': 'Done'},
    'merged': {'Staged': 'Ready', 'Releasing': 'Done'},
    'lights': {'backlog': '', 'shipyard': '', 'working': 'busy', 'dock': 'warm', 'dev': 'ok', 'shipping': 'going',
               'shipped': 'gone', 'failed': 'red', 'needs': 'ask'},
    'light_fallback': {'gone': 'ok'},
    'placeholders': {'shipyard': '— Building: a card here is being built —', 'dock': '— Ready: built —'},
}

print('columns')
check('the positive case: a role is its column', B.stage('shipyard', CFG), 'Building')
check('a role named is its column', B.named('dock', CFG), 'Ready')
check('an old name is its new one', B.named('In progress', CFG), 'Building')
check('a merged column is the one it joined', B.named('Releasing', CFG), 'Done')
check('anything else is itself', B.named('Elsewhere', CFG), 'Elsewhere')
check('the flow in order', B.flow(CFG), ('Backlog', 'Up Next', 'Building', 'Ready', 'Ready', 'Done', 'Done'))

print('rank')
check('a column of one role is its index', B.rank('Building', '', '', CFG), 2)
check('two roles in one column: the Light tells them apart', (B.rank('Ready', 'warm', '', CFG), B.rank('Ready', 'ok', '', CFG)), (3, 4))
check('no Light there is the first role', B.rank('Ready', '', '', CFG), 3)
check("a light's fallback counts as the light", B.rank('Done', 'ok', '', CFG), 6)
check('a card in a column merged into dev is dev', B.rank('Ready', '', 'Staged', CFG), 4)
check("a merged column's card reads by its Light in the column it joined", B.rank('', 'going', 'Releasing', CFG), 5)
check('off the flow is None', B.rank('Broken', 'red', '', CFG), None)

print('light_for')
check('a column of one role shows its light', B.light_for('Broken', CFG), 'red')
check("a role with no light is ''", B.light_for('Backlog', CFG), '')
check('two roles sharing a column is None', B.light_for('Ready', CFG), None)
check('an old name reads as its column', B.light_for('In progress', CFG), '')
check('a column off the board is None', B.light_for('Nowhere', CFG), None)

print('placeholders')
check('a configured title is a placeholder', B.is_placeholder('— Ready: built —', CFG), True)
check('any em-dash framed title is one', B.is_placeholder('— Done: an older wording —', CFG), True)
check('an ordinary title is not', B.is_placeholder('Fix the login', CFG), False)
check("'' is not", B.is_placeholder('', CFG), False)
check('its role by its configured title', B.placeholder_role('— Building: a card here is being built —', CFG), 'shipyard')
check('its role by the column its title names, an old name too', B.placeholder_role('— In progress: old words —', CFG), 'shipyard')
check("a column with no placeholder role is ''", B.placeholder_role('— Done: shipped —', CFG), '')
check("no placeholder is ''", B.placeholder_role('Fix the login', CFG), '')

print('trailers')
check('the positive case: numbers sorted, each once', B.ticket_trailers('Subject\n\nTicket: #14, #12\nTicket: #12'), ([12, 14], ''))
check('none (why) names no ticket, whatever its words', B.ticket_trailers('S\n\nTicket: none (notes; the deletion is #77)'),
      ([], 'notes; the deletion is #77'))
check('no trailer', B.ticket_trailers('Subject only'), ([], None))
check('a numbered trailer beside a none wins', B.ticket_trailers('S\n\nTicket: none (x y z)\nTicket: #3'), ([3], ''))
check('ticket_numbers', B.ticket_numbers('S\n\nTicket: #7'), [7])

print('load')
with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8') as f:
    json.dump(CFG, f)
check('a JSON file read back', B.load(f.name)['stages']['shipyard'], 'Building')
os.unlink(f.name)

print(f'\n{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
