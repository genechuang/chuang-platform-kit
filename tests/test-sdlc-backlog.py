#!/usr/bin/env python3
"""chuang_sdlc.backlog: the one backlog read from the board and rendered by state, P1 first.

Pinned (the positive case first, on chuang_sdlc.testing's Board, never GitHub): read_open's one filtered read (open
issues, the shipped column left out on GitHub's side); a moving column is in progress, a label the host maps waits in
its state, anything else next; the title's tail dropped; the id with the host's card field; P1 first, an unset priority
last; a shipped card and a pull request no item; GitHub not answering is None; render by the configured states with
each state's count, an owner filter and the done ones; configure refuses a name.

Run:  python -B tests/test-sdlc-backlog.py
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))

from chuang_sdlc import backlog as B, tickets as T  # noqa: E402
from chuang_sdlc.testing import Board  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


CFG = {
    'repo': 'acme/app', 'owner': 'acme', 'number': 3, 'id': 'PVT_board', 'fields': {},
    'stages': {'backlog': 'Backlog', 'next': 'Up Next', 'shipyard': 'Building', 'dock': 'Ready', 'dev': 'Ready',
               'shipping': 'Done', 'shipped': 'Done', 'failed': 'Broken'},
    'flow': ['backlog', 'next', 'shipyard', 'dock', 'dev', 'shipping', 'shipped'],
    'renamed': {'In progress': 'Building'},
}
T.configure(board=lambda: CFG, card_fields={'ref': 'Ref', 'needs': 'Needs'})
B.configure(states=('in progress', 'next', 'waiting'), label_states={'later': 'waiting'}, tail=r'\s*\[old\]$',
            id_field='ref', needs_field='needs')


class RefBoard(Board):
    """The Board, its cards carrying the host's Ref and Needs fields."""

    def graphql(self, query, variables=None):
        got = super().graphql(query, variables)
        if 'projectV2(number:' in query:
            for node, c in zip(got['organization']['projectV2']['items']['nodes'], self.columns.get('is:open', [])):
                for key in ('ref', 'needs'):
                    if c.get(key):
                        node[key] = {'text': c[key]}
        return got


cards = [{'number': 10, 'title': 'Build it [old]', 'status': 'In progress', 'owner': 'Ann', 'priority': 'P3', 'ref': 'R4'},
         {'number': 11, 'title': 'Wait for it', 'status': 'Backlog', 'owner': 'Bob', 'priority': 'P1', 'labels': ['later'],
          'needs': 'Monday'},
         {'number': 12, 'title': 'Pick me', 'status': 'Up Next', 'owner': 'Ann'},
         {'number': 13, 'title': 'Shipped', 'status': 'Done'},
         {'number': 14, 'title': 'A PR', 'status': 'Ready', 'type': 'PullRequest'}]
gh = RefBoard({'is:open': cards})
got = B.read_open(gh)
check('the positive case: the open tickets in one filtered read, P1 first, an unset priority last',
      ([(r['id'], r['item'], r['state'], r['priority']) for r in got], gh.queries),
      ([('#11', 'Wait for it', 'waiting', 'P1'), ('#10 R4', 'Build it', 'in progress', 'P3'), ('#12', 'Pick me', 'next', '')],
       ['is:open is:issue -status:"Done"']))
check("what an item needs is the host's field", got[0]['needs'], 'Monday')
check('state_of: a moving column, a mapped label, else next', (B.state_of('Ready', []), B.state_of('Backlog', ['later']),
                                                            B.state_of('Backlog', ['other'])), ('in progress', 'waiting', 'next'))


class Down(Board):
    def graphql(self, query, variables=None):
        return None


check('GitHub not answering is None', B.read_open(Down()), None)
text = B.render({'open': got, 'done': [{'id': 'D1', 'item': 'Old thing', 'closed': 'shipped 10/1/26'}]}, show_done=True)
check('render: the states in order with their counts, P1 first in a state, the done ones last',
      text.splitlines(), ['in progress (1)', '  P3 #10 R4 Ann: Build it', 'next (1)', '  #12  Ann: Pick me', 'waiting (1)',
                          '  P1 #11  Bob: Wait for it — Monday', 'done and closed (1)', '  D1   Old thing — shipped 10/1/26'])
check('an owner filter', B.render({'open': got, 'done': []}, owner='bob').splitlines()[0], 'waiting (1)')
check('short cuts with an ellipsis', B.short('x' * 10, 5), 'xxxx…')
try:
    B.configure(nope=1)
    check('configure refuses an unknown name', 'accepted', 'TypeError')
except TypeError:
    check('configure refuses an unknown name', True, True)

print(f'\n{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
