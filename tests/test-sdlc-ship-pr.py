#!/usr/bin/env python3
"""chuang_sdlc.ship_pr and the shipped stage's pull request: one pull request a ship, linked to each ticket after it
closes.

Pinned (the positive case first): live_numbers finds the ship's pull request by its branch, closes every open ticket
first, then adds the pull request as each one's closing reference in one aliased document, links no branch, and
finishes it; a refused link falls back to each ticket's branch, a push with no ship pull request links branches as
before, and pr=False looks for none; the references go out ten a document; a refused batch is sent again one ticket a
document; only a closed ticket gets the pull request, and one closed unmerged is never found (one open is), close_head
closes every open one from the branch; a client that raises or lacks a verb never stops the close; open_for makes the
branch and opens one pull request into the base, labelled, reuses an open one, pushes a head GitHub lacks, says a
refusal, and close_unshipped closes it unmerged and leaves a merged one; no_closing_keyword breaks every closing
reference and nothing else, twice the same; open_for sends no closing keyword even when given one; change_order_body
names the pull request; configure() sets the prefix, base and label, and refuses an unknown name. The fake is
chuang_sdlc.testing's, which every host's suites import; ship_fake follows the configured prefix.

Run:  python -B tests/test-sdlc-ship-pr.py
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))

from chuang_sdlc import ship_pr as P  # noqa: E402
from chuang_sdlc import tickets as T  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


CFG = {
    'repo': 'acme/app', 'owner': 'acme', 'number': 3, 'id': 'PVT_board',
    'fields': {'Status': {'id': 'F_status', 'options': {'Building': 'o_y', 'Done': 'o_d', 'Broken': 'o_x'}},
               'Light': {'id': 'F_light', 'options': {'gone': 'l_gone', 'going': 'l_go', 'red': 'l_red'}},
               'Release': {'id': 'F_rel'}},
    'stages': {'shipyard': 'Building', 'shipping': 'Done', 'shipped': 'Done', 'failed': 'Broken'},
    'flow': ['shipyard', 'shipping', 'shipped'], 'renamed': {}, 'merged': {},
    'lights': {'shipyard': '', 'shipping': 'going', 'shipped': 'gone', 'failed': 'red'},
}
T.configure(board=lambda: CFG)
# The kit's own fake GitHub (chuang_sdlc.testing), every host's: this suite pins ship_pr through it, and a host's suites
# import the same one.
from chuang_sdlc.testing import RELEASE, Minimal, ShipFake, ship_fake  # noqa: E402


WORK = {20: [('aa11aa11', 'one')], 21: [('bb22bb22', 'two')], 22: [('cc33cc33', 'three')]}

print('live_numbers and the ship pull request')
gh = ship_fake(closed={21})
line = T.live_numbers([20, 21], RELEASE, run=gh, cfg=CFG, work=WORK)
kinds = [c[0] if c[0] != 'ship' else c[1] for c in gh.calls]
closes = [i for i, k in enumerate(kinds) if k == 'close_issue']
check('the positive case: every ticket closed, then the pull request linked to each in one document, then finished',
      (line, [c[1] for c in gh.calls if c[0] == 'close_issue'], max(closes) < kinds.index('link'), gh.links(),
       [c for c in gh.calls if c[0] == 'link_branch'], [c[2] for c in gh.calls if c[0] == 'comment' and c[1] == 900],
       'ship/abcdef12' in gh.ship_refs),
      ('tickets: live #20, #21 (release abcdef12); ship pr #900 linked to 2 of 2; ship pr #900: live', [20],
       True, [['NODE-20', 'NODE-21']], [], ['Live: production smoke passed; tickets closed: #20, #21. Release abcdef12.'], False))

refused = ship_fake(refuse={'link'})
refused_line = T.live_numbers([20, 21], RELEASE, run=refused, cfg=CFG, work=WORK)
lane = ship_fake()
lane_line = T.live_numbers([20], 'ffff0000' + '0' * 32, run=lane, cfg=CFG, work=WORK)
none = ship_fake()
T.live_numbers([20], RELEASE, run=none, cfg=CFG, work=WORK, pr=False)
check('a refused link falls back to the branch; a push with no ship pull request links branches; pr=False looks for none',
      (refused_line, [c[1:] for c in refused.calls if c[0] == 'link_branch'], lane_line,
       [c[1:] for c in lane.calls if c[0] == 'link_branch'], none.links(), [c for c in none.calls if c[1:2] == ('read',)]),
      ('tickets: live #20, #21 (release abcdef12); ship pr #900 linked to 0 of 2; ship pr #900: live',
       [('NODE-20', 'ticket/20'), ('NODE-21', 'ticket/21')], 'tickets: live #20 (release ffff0000)',
       [('NODE-20', 'ticket/20')], [], []))

kept = ship_fake()
kept_line = T.live_numbers([20], RELEASE, run=kept, cfg=CFG, work=WORK, settle=False)
check('settle=False links and leaves the pull request for the wreck to tell',
      (kept_line, [c for c in kept.calls if c[0] == 'comment'], 'ship/abcdef12' in kept.ship_refs,
       P.wrecked(900, 'api, bug #77', run=kept, cfg=CFG), [c[2] for c in kept.calls if c[0] == 'comment']),
      ('tickets: live #20 (release abcdef12); ship pr #900 linked to 1 of 1', [], True, '; ship pr #900: wrecked',
       ['Wrecked: api, bug #77']))

gh = ship_fake()
linked = P.link_after_live(900, list(range(1, 24)), run=gh, cfg=CFG)
docs = [c for c in gh.ship_calls if c[0] == 'link']
check('the closing references go out ten a document, each naming the one pull request',
      (len(linked), [len(c[2]) for c in docs], {c[1] for c in docs}, P.link_document(2)),
      (23, [10, 10, 3], {'PR-900'},
       'mutation($pr: ID!, $i0: ID!, $i1: ID!) { l0: addCloseIssueReferences(input: {issueId: $i0, pullRequestIds: [$pr]}) '
       '{ issue { number } } l1: addCloseIssueReferences(input: {issueId: $i1, pullRequestIds: [$pr]}) { issue { number } } }'))

gh = ship_fake(refuse_nodes={'NODE-13'})
linked = P.link_after_live(900, list(range(1, 24)), run=gh, cfg=CFG)
again = ship_fake(refuse_nodes={'NODE-21'})
again_line = T.live_numbers([20, 21], RELEASE, run=again, cfg=CFG, work=WORK)
check('a refused batch is sent again one ticket a document: 22 linked, the refused one left to its branch',
      (sorted(set(range(1, 24)) - linked), [len(c[2]) for c in gh.ship_calls if c[0] == 'link'],
       again_line, [c[1:] for c in again.calls if c[0] == 'link_branch']),
      ([13], [10, 10] + [1] * 10 + [3], 'tickets: live #20, #21 (release abcdef12); ship pr #900 linked to 1 of 2; ship pr #900: live',
       [('NODE-21', 'ticket/21')]))


class Refusing(ShipFake):
    def close_issue(self, number, reason='completed', comment=''):
        self.calls.append(('close_issue', number))
        return False

    def issue(self, number):
        return None if number == 22 else super().issue(number)


gh = Refusing({21}).ship_setup()
gh.ship_refs['ship/abcdef12'] = RELEASE
gh.prs[900] = {'number': 900, 'head': 'ship/abcdef12', 'state': 'MERGED', 'merged': True, 'body': '', 'title': 't'}
line = T.live_numbers([20, 21, 22], RELEASE, run=gh, cfg=CFG, work=WORK)
check('only a closed ticket gets the pull request; a refused close or an unread state gets its branch',
      (line, gh.links(), [c[1:] for c in gh.calls if c[0] == 'link_branch'], [c[2] for c in gh.calls if c[0] == 'comment' and c[1] == 900]),
      ('tickets: live #20, #21, #22 (release abcdef12); ship pr #900 linked to 1 of 3 (#20, #22 not closed, so not linked to it); '
       'ship pr #900: live', [['NODE-21']], [('NODE-20', 'ticket/20'), ('NODE-22', 'ticket/22')],
       ['Live: production smoke passed; tickets closed: #21. Release abcdef12.']))
shut = ship_fake()
shut.prs[900].update(state='CLOSED', merged=False)
shut_line = T.live_numbers([20], RELEASE, run=shut, cfg=CFG, work=WORK)
still = ship_fake()
still.prs[900].update(state='OPEN', merged=False)
check('one closed unmerged is never found, one open is',
      (P.find(RELEASE, run=shut, cfg=CFG), shut_line, shut.links(), [c for c in shut.calls if c[0] == 'comment'],
       [c[1:] for c in shut.calls if c[0] == 'link_branch'], P.find(RELEASE, run=still, cfg=CFG)),
      (None, 'tickets: live #20 (release abcdef12)', [], [], [('NODE-20', 'ticket/20')], 900))
heads = ship_fake()
heads.prs[900].update(state='OPEN', merged=False)
check('close_head closes every open one from the branch, then finds none',
      (P.close_head(RELEASE, 'its open met its cap', run=heads, cfg=CFG), heads.prs[900]['state'],
       P.close_head(RELEASE, 'again', run=heads, cfg=CFG)),
      ('ship pr #900: closed unmerged', 'CLOSED', 'ship pr: none open from ship/abcdef12, nothing to close'))


class Raising(Minimal):
    def graphql(self, query, variables=None):
        raise RuntimeError('GitHub went away')


class NoGraphql:
    error = ''


raising = Raising()
line = T.live_numbers([20], RELEASE, run=raising, cfg=CFG)
check('a client that raises or lacks a verb finds and opens nothing, and the close still happens',
      (P.find(RELEASE, run=raising, cfg=CFG), P.find(RELEASE, run=NoGraphql(), cfg=CFG),
       P.open_for('b' * 40, RELEASE, 't', 'b', run=NoGraphql(), cfg=CFG), line,
       [c[1] for c in raising.calls if c[0] == 'close_issue']),
      (None, None, (None, "WARNING: ship pr not opened (the repository was not read: AttributeError: 'NoGraphql' object has no attribute 'graphql')"),
       'tickets: live #20 (release abcdef12)', [20]))

print('open and close')
head = 'c1113ee7' + '9' * 32
gh = ShipFake().ship_setup()
n, line = P.open_for('b' * 40, head, 'Ship 11/7/26 4:30 PM PT: c1113ee7', 'notes', run=gh, cfg=CFG)
again = P.open_for('b' * 40, head, 'x', 'y', run=gh, cfg=CFG)
check('the positive case: the branch made at the head, one pull request into the base, labelled; an open one reused',
      (n, line, gh.ship_refs, [c[1:3] for c in gh.ship_calls if c[0] == 'create_pr'], [c for c in gh.calls if c[0] == 'add_labels'], again),
      (900, 'ship pr #900: opened from ship/c1113ee7 into main', {'ship/c1113ee7': head}, [('main', 'ship/c1113ee7')],
       [('add_labels', 900, ['ship'])], (900, 'ship pr #900: reused, open from ship/c1113ee7 into main')))
pushed = []
off = ShipFake().ship_setup(on_github=set())
got = P.open_for('b' * 40, head, 't', 'b', run=off, cfg=CFG, push=lambda br, sha: pushed.append((br, sha)) or True)
nopush = P.open_for('b' * 40, head, 't', 'b', run=ShipFake().ship_setup(on_github=set()), cfg=CFG)
said = P.open_for('b' * 40, head, 't', 'b', run=ShipFake().ship_setup(refuse={'pr'}), cfg=CFG)
check('a head GitHub lacks goes through the push; refusals are a WARNING and None',
      (got[0], pushed, [c for c in off.ship_calls if c[0] == 'create_ref'], nopush, said),
      (900, [('ship/c1113ee7', head)], [], (None, 'WARNING: ship pr not opened (ship/c1113ee7 was not made: c1113ee7 is not on GitHub and no push made it)'),
       (None, 'WARNING: ship pr not opened (createPullRequest refused: GitHub /graphql: HTTP 200 (pr refused)); '
              'ship/c1113ee7 deleted again')))
closed = P.close_unshipped(900, 'the push gate refused it', run=gh, cfg=CFG)
gh.prs[901] = {'number': 901, 'head': 'ship/x', 'state': 'MERGED', 'merged': True, 'body': '', 'title': 't'}
check('close_unshipped comments why, closes it unmerged and deletes the branch; a merged one is left',
      (closed, gh.prs[900]['state'], gh.ship_refs, [c[2] for c in gh.calls if c[0] == 'comment'],
       P.close_unshipped(901, 'x', run=gh, cfg=CFG)),
      ('ship pr #900: closed unmerged', 'CLOSED', {},
       ['Not shipped: the push gate refused it. Closed unmerged; the next ship opens its own pull request.'],
       'ship pr #901: merged already (main holds its head), left as it is'))
gh.prs[902] = {'number': 902, 'head': 'ship/y', 'state': 'OPEN', 'merged': False, 'body': 'notes', 'title': 't'}
check('the change order named at the top of the body once',
      (P.note_change_order(902, 55, run=gh, cfg=CFG), P.note_change_order(902, 55, run=gh, cfg=CFG), gh.prs[902]['body'],
       len([c for c in gh.ship_calls if c[0] == 'update_pr'])),
      (True, True, '**Change order #55**\n\nnotes', 1))

print('a refused pull request leaves no branch')
made = ShipFake().ship_setup(refuse={'pr'})
n, line = P.open_for('b' * 40, head, 't', 'b', run=made, cfg=CFG)
check('the positive case: createRef made the branch, createPullRequest was refused, and the branch is deleted again',
      (n, line, made.ship_refs, [c[0] for c in made.ship_calls]),
      (None, 'WARNING: ship pr not opened (createPullRequest refused: GitHub /graphql: HTTP 200 (pr refused)); '
             'ship/c1113ee7 deleted again', {}, ['read', 'create_ref', 'create_pr', 'delete_ref']))
off = ShipFake().ship_setup(on_github=set(), refuse={'pr'})
via_push = P.open_for('b' * 40, head, 't', 'b', run=off, cfg=CFG,
                      push=lambda br, sha: off.ship_refs.__setitem__(br, sha) or True)
kept = ShipFake().ship_setup(refuse={'pr'})
kept.ship_refs['ship/c1113ee7'] = head   # there before this open: not this call's to delete
was_there = P.open_for('b' * 40, head, 't', 'b', run=kept, cfg=CFG)
lost = P.open_for('b' * 40, head, 't', 'b', run=ShipFake().ship_setup(on_github=set(), refuse={'pr'}), cfg=CFG,
                  push=lambda br, sha: True)   # a push GitHub's read never shows
check('  a pushed branch is read back and deleted; a branch there before is left; one never read back is a WARNING',
      (via_push[1].endswith('; ship/c1113ee7 deleted again'), off.ship_refs,
       was_there[1], kept.ship_refs, [c for c in kept.ship_calls if c[0] == 'delete_ref'],
       lost[1].endswith('; WARNING: ship/c1113ee7 was left on GitHub (its ref was not read back)')),
      (True, {}, 'WARNING: ship pr not opened (createPullRequest refused: GitHub /graphql: HTTP 200 (pr refused))',
       {'ship/c1113ee7': head}, [], True))

print('stale ship pull requests')


def stale_fake(**kw):
    st = ShipFake().ship_setup(in_base={'ship/bbbbbbbb'}, **kw)
    for num, br, state in ((910, 'ship/aaaaaaaa', 'OPEN'), (911, 'ship/bbbbbbbb', 'OPEN'), (912, 'ship/c1113ee7', 'OPEN'),
                           (913, 'feature/x', 'OPEN'), (914, 'ship/dddddddd', 'MERGED')):
        st.prs[num] = {'number': num, 'head': br, 'state': state, 'merged': state == 'MERGED', 'body': '', 'title': 't'}
        st.ship_refs[br] = head if num == 912 else br[-1] * 40
    return st


st = stale_fake()
swept = P.close_stale(head, run=st, cfg=CFG)
check('the positive case: the ship pull request a killed ship left open, its head not in the base, is closed '
      'unmerged with why and its branch deleted; the one in the base, the current head, a non-ship one and a merged one '
      'are left',
      (swept, {n: st.prs[n]['state'] for n in (910, 911, 912, 913, 914)}, 'ship/aaaaaaaa' in st.ship_refs,
       [c[2] for c in st.calls if c[0] == 'comment'], [c[2] for c in st.ship_calls if c[0] == 'compare']),
      ('ship pr #910: closed unmerged', {910: 'CLOSED', 911: 'OPEN', 912: 'OPEN', 913: 'OPEN', 914: 'MERGED'}, False,
       ['Not shipped: its ship ended without pushing ship/aaaaaaaa into main. Closed unmerged; the next ship opens its '
        'own pull request.'], ['ship/bbbbbbbb', 'ship/aaaaaaaa']))   # newest first, as the list is read
no_current = stale_fake()
check('  a second sweep finds none; with no current head every stale one goes, the current head\'s too',
      (P.close_stale(head, run=st, cfg=CFG), P.close_stale('', run=no_current, cfg=CFG),
       {n: no_current.prs[n]['state'] for n in (910, 911, 912)}),
      ('ship pr: no stale pull request open', 'ship pr #912: closed unmerged; ship pr #910: closed unmerged',
       {910: 'CLOSED', 911: 'OPEN', 912: 'CLOSED'}))
unlisted, uncompared = stale_fake(refuse={'open'}), stale_fake(refuse={'compare'})
check('  an unread list is a WARNING and closes nothing; an unanswered comparison leaves that one open, named',
      (P.close_stale(head, run=unlisted, cfg=CFG), P.close_stale(head, run=uncompared, cfg=CFG), uncompared.prs[910]['state']),
      ('WARNING: stale ship prs not swept (the open pull requests were not read: GitHub /graphql: HTTP 200 (open refused))',
       'WARNING: stale check not answered for #911, #910, left open', 'OPEN'))

print('no closing keyword')
B = P.BREAK
check('the positive case: every closing reference broken, the text otherwise the same',
      [P.no_closing_keyword(s) for s in ('Fix #12 seeding', 'closes: acme/app#7', 'Resolved GH-3', 'fixes https://github.com/acme/app/issues/9',
                                         'see #12', 'prefix #12', 'the fixture #12')],
      [f'Fix #{B}12 seeding', f'closes: acme/app#{B}7', f'Resolved GH-{B}3', f'fixes https://github.com/acme/app/issues/{B}9',
       'see #12', 'prefix #12', 'the fixture #12'])
twice = P.no_closing_keyword(P.no_closing_keyword('Fixes #4 and closes #5'))
check('a second run changes nothing; no text is empty text',
      (twice, P.no_closing_keyword(None)), (f'Fixes #{B}4 and closes #{B}5', ''))
gh = ShipFake().ship_setup()
P.open_for('b' * 40, head, 'Fix #12 seeding', 'Closes #13', run=gh, cfg=CFG)
sent = [c[3:5] for c in gh.ship_calls if c[0] == 'create_pr']
check('open_for sends no closing keyword even when given one', sent, [(f'Fix #{B}12 seeding', f'Closes #{B}13')])

print('the change order and the settings')
body = T.change_order_body('a' * 40, 'b' * 40, [5], run=Minimal(), cfg=CFG, pr=900)
check("change_order_body names the ship's pull request; none without it",
      ("Pull request #900: the ship's diff and checks" in body,
       'Pull request #' in T.change_order_body('a' * 40, 'b' * 40, [5], run=Minimal(), cfg=CFG)), (True, False))
P.configure(prefix='release/', base='trunk', label='release')
gh = ShipFake().ship_setup()
n, line = P.open_for('b' * 40, head, 't', 'b', run=gh, cfg=CFG)
try:
    P.configure(colour='red')
    refused_name = False
except TypeError:
    refused_name = True
P.configure(prefix='ship/', base='main', label='ship')
check("configure() sets the host's prefix, base and label, and refuses an unknown name",
      (line, [c[1:3] for c in gh.ship_calls if c[0] == 'create_pr'], [c for c in gh.calls if c[0] == 'add_labels'], refused_name,
       P.branch_of(head)),
      ('ship pr #900: opened from release/c1113ee7 into trunk', [('trunk', 'release/c1113ee7')],
       [('add_labels', 900, ['release'])], True, 'ship/c1113ee7'))

print('a second run of the same release')
gh = ship_fake()
first = T.live_numbers([20], RELEASE, run=gh, cfg=CFG, work=WORK)
said_once = [c for c in gh.calls if c[0] == 'comment' and c[1] == 900]
second = T.live_numbers([20], RELEASE, run=gh, cfg=CFG, work=WORK)
check('the positive case: a second live_numbers run of the same release posts nothing new on the pull request',
      (first.endswith('; ship pr #900: live'), second.endswith('; ship pr #900: live (finished already)'),
       [c for c in gh.calls if c[0] == 'comment' and c[1] == 900] == said_once, len(said_once)),
      (True, True, True, 1))
kept = ship_fake()
kept.ship_refs.pop('ship/abcdef12')   # a host whose repository deletes head branches on merge: gone before any comment
check('  merged with its branch auto-deleted and no earlier comment: finish still posts its one Live comment',
      (P.finish(900, [20], RELEASE, run=kept, cfg=CFG),
       [c[2].startswith('Live:') for c in kept.calls if c[0] == 'comment'],
       [c for c in kept.ship_calls if c[0] == 'delete_ref']),
      ('; ship pr #900: live', [True], []))
wreck = ship_fake()
wreck.prs[900].update(state='OPEN', merged=False)
once = P.wrecked(900, 'api, bug #77', run=wreck, cfg=CFG)
twice = P.wrecked(900, 'api, bug #77', run=wreck, cfg=CFG)
check('  wrecked twice: the second says told already and posts nothing',
      (once, twice, len([c for c in wreck.calls if c[0] == 'comment'])),
      ('; ship pr #900: wrecked', '; ship pr #900: wrecked (told already)', 1))

P.configure(prefix='release/')
moved = ship_fake()
P.configure(prefix='ship/')
check("chuang_sdlc.testing's ship_fake opens its pull request from the configured prefix's branch",
      (sorted(moved.ship_refs), moved.prs[900]['head']), (['release/abcdef12'], 'release/abcdef12'))

print(f'\n{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
