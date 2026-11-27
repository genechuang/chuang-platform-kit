"""chuang_sdlc.moves: a Project board's item helpers, on fakes only (a fake opener for GitHub's GraphQL, a fake store).

Pinned (the positive case first): one item is read and its fields answered; then one case per group: the board's names,
the writes, the fixed cards, the put-backs, an issue's card, an alert's card, a closed ticket's card and its letters;
and that the host's names are read at call time, a missing one refused, and no project's name is a default.

Run:  python tests/test-sdlc-moves.py      (prints PASS/FAIL per case and `N failure(s)`)
"""
import json
import os
import re
import sys
import traceback

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))

from chuang_sdlc import moves as M, tenant, tickets  # noqa: E402

CFG = {
    'repo': 'acme/widgets', 'id': 'PVT_1',
    'fields': {'Status': {'id': 'F_status', 'options': {'Backlog': 'o_backlog', 'Building': 'o_building', 'Ready': 'o_ready',
                                                        'Ship': 'o_ship', 'Wrecked': 'o_wrecked', 'Orders': 'o_orders',
                                                        'Apps': 'o_apps'}},
               'Light': {'id': 'F_light', 'options': {'Warming': 'l_warming', 'On dev': 'l_dev', 'Shipped': 'l_shipped',
                                                      'Working': 'l_working', 'Needs you': 'l_needs', 'Queued': 'l_queued'}},
               'Kind': {'id': 'F_kind', 'options': {'Defect': 'k_defect'}},
               'Owner': {'id': 'F_owner', 'options': {'Ada': 'p_ada', 'Cap': 'p_cap'}},
               'Estimate': {'id': 'F_estimate', 'type': 'number'},
               'Alert key': {'id': 'F_alert', 'type': 'text'}},
    'stages': {'backlog': 'Backlog', 'shipyard': 'Building', 'dock': 'Ready', 'dev': 'Ready', 'shipping': 'Ship',
               'shipped': 'Ship', 'failed': 'Wrecked', 'shipment': 'Orders', 'app_status': 'Apps'},
    'flow': ['backlog', 'shipyard', 'dock', 'dev', 'shipping', 'shipped'],
    'renamed': {'Launching': 'Ship'}, 'merged': {},
    'lights': {'dock': 'Warming', 'dev': 'On dev', 'shipped': 'Shipped', 'working': 'Working', 'needs': 'Needs you',
               'queued': 'Queued'},
    'light_fallback': {'Pushing': 'Working'},
    'placeholders': {'shipyard': '— Building: a card someone is working on —'},
    'ship_card': {'word': 'LAUNCH'},
}


class Answer:
    """One HTTP answer as urllib's opener gives it."""

    status, headers = 200, {}

    def __init__(self, body):
        self._body = json.dumps(body).encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


class Opener:
    """A fake opener for GitHub's GraphQL: `answer(query, variables)` gives each call's `data`; every call is kept."""

    def __init__(self, answer=None, errors=None):
        self.answer, self.errors, self.calls = answer or (lambda q, v: {}), errors, []

    def __call__(self, req, timeout=None):
        sent = json.loads(req.data.decode('utf-8'))
        self.calls.append((sent['query'], sent['variables']))
        if self.errors:
            return Answer({'errors': [{'message': self.errors}]})
        return Answer({'data': self.answer(sent['query'], sent['variables'])})


class Store:
    """A fake record store: {(collection, id): doc}."""

    def __init__(self, docs=None, broken=False):
        self.docs, self.broken, self.gets = dict(docs or {}), broken, 0

    def get(self, collection, doc_id):
        self.gets += 1
        if self.broken:
            raise OSError('store down')
        return self.docs.get((collection, doc_id))

    def set(self, collection, doc_id, doc):
        self.docs[(collection, doc_id)] = doc


def node(number=7, col='Building', light='', owner='Ada', labels=(), title='Fix the gate', state='OPEN', reason=None,
         item='IT_7'):
    content = {'title': title}
    if number:
        content.update(number=number, url=f'https://example.test/{number}', state=state, stateReason=reason,
                       labels={'nodes': [{'name': name} for name in labels]})
    return {'id': item, 'content': content, 'status': {'name': col} if col else None,
            'light': {'name': light} if light else None, 'owner': {'name': owner} if owner else None,
            'estimate': {'number': 1.5}}


def moved(n, was='Backlog', by='someone', field='Status'):
    return {'item': n['id'], 'node': n, 'from': was, 'by': by, 'field_name': field}


class raises:
    """`with raises(Error, match='words')`: the block must raise Error whose text holds the pattern."""

    def __init__(self, kind, match=None):
        self.kind, self.match = kind, match

    def __enter__(self):
        return self

    def __exit__(self, kind, error, tb):
        assert kind is not None and issubclass(kind, self.kind), f'{self.kind.__name__} was not raised'
        assert not self.match or re.search(self.match, str(error)), f'{error!r} does not say {self.match!r}'
        return True


def wired() -> dict:
    """The host's names, set for each case; the runner puts back what it returns."""
    before = dict(M._SET)
    M.configure(board=lambda: CFG, people={'captain': 'Cap', 'lead': 'Lee'},
                labels={'feedback': 'voice', 'admin_ask': 'from:boss', 'change_order': 'order', 'blocked': 'stuck'},
                senders={'board': 'The board', 'scrum': 'The scrum master'},
                collections={'starts': 'starts', 'put_backs': 'putbacks', 'board_moves': 'moves', 'closed_claims': 'claims'},
                alert_kind='Defect')
    return before


def aliases(opener, at=-1):
    """The {field id: value} a set_fields call sent, from its aliased mutation's variables."""
    variables = opener.calls[at][1]
    return {variables[k]: variables.get('v' + k[1:]) for k in variables if k.startswith('f')}


# ------------------------------------------------------------------ the item and its fields (the positive case)
def test_an_item_is_read_and_its_fields_answered():
    """One item read by its id: its column, Light, number, estimate, labels, Owner and title."""
    card = node(light='Working', labels=('voice', 'x'))
    opener = Opener(lambda q, v: {'node': card})
    got = M.read_item('IT_7', 'tok', opener)
    assert got == card and opener.calls[0] == (M.ITEM_QUERY, {'id': 'IT_7'})
    assert (M.column(got), M.light(got), M.number(got), M.owner(got), M.title(got)) == \
        ('Building', 'Working', 7, 'Ada', 'Fix the gate')
    assert M.estimate_minutes(got) == 90 and M.labels(got) == {'voice', 'x'} and M.is_feedback(got)
    assert M.column_now(node(col='Launching')) == 'Ship'
    assert M.read_item('IT_0', 'tok', Opener(lambda q, v: {'node': None})) == {}
    assert (M.column({}), M.light(None), M.number({}), M.estimate_minutes({}), M.labels({})) == ('', '', None, None, set())


def test_a_refusal_is_a_runtime_error_with_its_words():
    """No token, and GitHub's GraphQL error, each raise RuntimeError saying which."""
    with raises(RuntimeError, match='no GitHub token'):
        M.read_item('IT_7', '', Opener())
    with raises(RuntimeError, match='Could not resolve'):
        M.read_item('IT_7', 'tok', Opener(errors='Could not resolve to a node'))


# ------------------------------------------------------------------ the board's names
def test_the_boards_names_wrap_the_board_in_force():
    """stage, rank, named, stage_role, column_phase, newly_in and light_for read the configured board, or `cfg`."""
    assert M.stage('shipyard') == 'Building' and M.named('Launching') == 'Ship' and M.named('dock') == 'Ready'
    assert M.rank('Ready', 'On dev') == 3 and M.rank('Wrecked') is None
    assert M.stage_role('Ready', 'Warming') == {'stage': 'dock', 'stage_name': 'Ready'}
    assert M.stage_role('Wrecked') == {'stage': 'failed', 'stage_name': 'Wrecked'}
    assert M.column_phase('Building') == 'working' and M.column_phase('Backlog') == 'filed' and M.column_phase('') == 'filed'
    assert M.newly_in(moved(node(), was='Backlog'), 'shipyard')
    assert not M.newly_in(moved(node(), was='Building'), 'shipyard')
    assert not M.newly_in(moved(node(), field='Light'), 'shipyard')
    assert M.light_for('Backlog') == '' and M.light_for('Ready') is None and M.light_for('Nowhere') is None
    other = dict(CFG, stages=dict(CFG['stages'], shipyard='Yard'))
    assert M.stage('shipyard', other) == 'Yard'


def test_light_fix_leaves_what_a_drag_must_not_change():
    """A dragged card takes its column's light; needs, working, queued, a shared column, a change order and a draft stay."""
    assert M.light_fix(moved(node(col='Backlog', light='Working'))) == ''
    assert M.light_fix(moved(node(col='Backlog'))) is None                       # already none
    assert M.light_fix(moved(node(col='Backlog', light='Needs you'))) is None
    assert M.light_fix(moved(node(col='Building', light='Working'))) is None
    assert M.light_fix(moved(node(col='Building', light='Queued'))) is None
    assert M.light_fix(moved(node(col='Building', light='Shipped'))) == ''
    assert M.light_fix(moved(node(col='Ready', light='Working'))) is None        # two roles share it
    assert M.light_fix(moved(node(col='Orders', light='Working'))) is None
    assert M.light_fix(moved(node(number=None, col='Backlog', light='Working'))) is None
    # 0.63.1 (SMADPickleBot #2200): the scrum master's blocked and scheduled lights stay in the shipyard column too
    waits = dict(CFG, lights=dict(CFG['lights'], blocked='Blocked', scheduled='Scheduled'))
    assert M.light_fix(moved(node(col='Building', light='Scheduled')), waits) is None
    assert M.light_fix(moved(node(col='Building', light='Blocked')), waits) is None
    assert M.light_fix(moved(node(col='Backlog', light='Scheduled')), waits) == ''   # dragged out: its column's light


# ------------------------------------------------------------------ the writes
def test_fields_are_written_in_one_request():
    """set_fields sends every field in one mutation: an option's id, a number, a cut text, a clear, a fallback."""
    opener = Opener(lambda q, v: {f's{k}': {'projectV2Item': {'id': 'IT_7'}} for k in range(5)})
    M.set_fields('IT_7', {'Status': 'Ready', 'Light': 'Pushing', 'Estimate': '2', 'Alert key': 'k' * 1500, 'Owner': ''},
                 'tok', opener=opener)
    assert len(opener.calls) == 1
    sent = aliases(opener)
    assert sent['F_status'] == {'singleSelectOptionId': 'o_ready'}
    assert sent['F_light'] == {'singleSelectOptionId': 'l_working'}             # the fallback of a light the board lacks
    assert sent['F_estimate'] == {'number': 2.0} and sent['F_alert'] == {'text': 'k' * M.TEXT_MAX}
    assert sent['F_owner'] is None                                               # '' clears
    M.set_fields('IT_7', {}, 'tok', opener=opener)
    assert len(opener.calls) == 1
    M.set_light('IT_7', 'Warming', 'tok', opener=opener)
    assert aliases(opener) == {'F_light': {'singleSelectOptionId': 'l_warming'}}
    M.set_field('IT_7', 'Status', 'Ship', 'tok', opener=opener)
    assert aliases(opener) == {'F_status': {'singleSelectOptionId': 'o_ship'}}


# ------------------------------------------------------------------ the fixed cards
def test_the_ship_card_and_a_placeholder_are_told_apart_and_righted():
    """The ship card is a draft carrying the board's word; a moved placeholder goes back to its own column."""
    ship = node(number=None, title='🚢 Launch it! drag me', col='Ready')
    place = node(number=None, title='— Building: a card someone is working on —', col='Ready', item='IT_p')
    assert M.is_ship_card(ship) and not M.is_ship_card(place) and not M.is_ship_card(node(title='LAUNCH plan'))
    assert M.is_placeholder(M.title(place)) and not M.is_placeholder('Fix the gate')
    assert M.placeholder_role(M.title(place)) == 'shipyard' and M.placeholder_role('Fix the gate') == ''
    opener = Opener(lambda q, v: {'s0': {'projectV2Item': {'id': 'IT_p'}}})
    line = M.right_placeholder(place, 'tok', opener=opener)
    assert 'put back in Building' in line and aliases(opener) == {'F_status': {'singleSelectOptionId': 'o_building'}}
    home = node(number=None, title=M.title(place), col='Building')
    assert M.right_placeholder(home, 'tok', opener=opener).endswith('left') and len(opener.calls) == 1
    assert M.right_placeholder(node(), 'tok', opener=opener) == ''


def _column(cards, drafts=None):
    """An opener answering label_first's reads from `cards` (the column in board order) and its writes."""
    def answer(q, v):
        if q == M.COLUMN_ITEMS:
            rows = [c for c in cards if not M.number(c)] if 'is:draft' in v['q'] else cards
            return {'node': {'items': {'nodes': rows[:v['n']]}}}
        return {'updateProjectV2ItemPosition': {'items': {'nodes': []}}}
    return Opener(answer)


def _writes(opener):
    """(item, after) of each position write the opener saw, in order."""
    return [(v['i'], v.get('a')) for q, v in opener.calls if 'updateProjectV2ItemPosition' in q]


LABEL = node(number=None, title='— Building: a card someone is working on —', item='IT_label')
DOCK_LABEL = node(number=None, title='— Ready: tested and ready to ship —', col='Ready', item='IT_dock')
SHIP = node(number=None, title='🚢 Launch it! drag me', col='Ready', item='IT_ship')
DOCK_CFG = dict(CFG, placeholders=dict(CFG['placeholders'], dock=M.title(DOCK_LABEL)))


def test_a_label_card_is_put_first_when_a_card_sits_above_it():
    """The positive case (0.62.0): a card that entered above the column's label card is answered by one write, the
    label card to the top; nothing else in the column is moved."""
    opener = _column([node(7), LABEL, node(8, item='IT_8')])
    assert M.label_first('Building', 'tok', opener=opener) == 'Building: its label card put first'
    assert _writes(opener) == [('IT_label', None)]
    assert [v['q'] for q, v in opener.calls if q == M.COLUMN_ITEMS] == ['status:"Building"', 'is:draft status:"Building"']


def test_a_label_card_already_first_costs_one_read_and_no_write():
    """A column in order is one read and no write, which is also where the keeper's own write settles when its
    reorder event comes round."""
    opener = _column([LABEL, node(7)])
    assert M.label_first('Building', 'tok', opener=opener).endswith('left')
    assert len(opener.calls) == 1 and _writes(opener) == []


def test_a_column_with_no_label_card_is_left():
    """A column the board names no label card for is not read; one whose card is missing says so and writes nothing."""
    opener = _column([node(7)])
    assert M.label_first('Backlog', 'tok', opener=opener) == '' and M.label_first('', 'tok', opener=opener) == ''
    assert opener.calls == []
    assert M.label_first('Building', 'tok', opener=opener) == 'Building: no label card to put first'
    assert _writes(opener) == []


def test_the_ship_card_stays_second_in_its_home_column():
    """In the ship card's home column the label card goes first and the ship card right after it; with both in place
    nothing is written, and a ship card that is away (in a door) is not looked for twice."""
    entered = _column([node(9, col='Ready', item='IT_9'), DOCK_LABEL, SHIP])
    assert M.label_first('Ready', 'tok', DOCK_CFG, entered) == 'Ready: its label card put first, the ship card put second'
    assert _writes(entered) == [('IT_dock', None), ('IT_ship', 'IT_dock')]
    behind = _column([DOCK_LABEL, node(9, col='Ready', item='IT_9'), SHIP])
    assert M.label_first('Ready', 'tok', DOCK_CFG, behind) == 'Ready: the ship card put second'
    assert _writes(behind) == [('IT_ship', 'IT_dock')]
    settled = _column([DOCK_LABEL, SHIP, node(9, col='Ready', item='IT_9')])
    assert M.label_first('Ready', 'tok', DOCK_CFG, settled).endswith('left') and len(settled.calls) == 1
    away = _column([DOCK_LABEL, node(9, col='Ready', item='IT_9')])
    assert M.label_first('Ready', 'tok', DOCK_CFG, away).endswith('left') and _writes(away) == []


def test_a_refused_write_is_said():
    """GitHub's refusal of the write is a RuntimeError with its words, for the receiver's WARNING."""
    refusing = Opener(errors='nope')
    try:
        M.label_first('Building', 'tok', opener=refusing)
    except RuntimeError as e:
        assert 'nope' in str(e)
    else:
        raise AssertionError('a refusal was not raised')


# ------------------------------------------------------------------ the put-backs
def test_a_move_that_breaks_a_rule_is_named_with_where_it_goes_back():
    """put_back_to: the ship column wants proof, the shipment column a change order, the app-status column the ship card."""
    assert M.shipped_live(node(state='CLOSED', reason='COMPLETED')) and not M.shipped_live(node())
    assert not M.shipped_live(node(state='CLOSED', reason='NOT_PLANNED'))
    to, why = M.put_back_to(moved(node(col='Ship'), was='Ready'))
    assert to == 'Ready' and '#7 is not shipped live yet' in why
    assert M.put_back_to(moved(node(col='Ship', state='CLOSED', reason='COMPLETED'), was='Ready')) == ('', '')
    assert M.put_back_to(moved(node(col='Ship', labels=('order',)), was='Ready')) == ('', '')
    assert M.put_back_to(moved(node(col='Ship'), was='Ready'), marked=lambda n: True) == ('', '')
    assert M.put_back_to(moved(node(col='Orders'), was='')) == ('Backlog', 'only a change order goes into Orders')
    assert M.put_back_to(moved(node(col='Orders', labels=('order',)), was='Backlog')) == ('', '')
    assert M.put_back_to(moved(node(col='Apps'), was='Ready'))[0] == 'Ready'
    ship = node(number=None, title='LAUNCH', col='Apps')
    assert M.put_back_to(moved(ship, was='Ready')) == ('', '')
    status = node(number=None, title='Store: in review', col='Backlog')
    assert M.put_back_to(moved(status, was='Apps')) == ('Apps', 'a status card stays in Apps')
    assert M.put_back_to(moved(node(col='Ship'), was='Ready', field='Light')) == ('', '')
    assert M.guarded(moved(node(col='Ship'), was='Ready')) and M.guarded(moved(node(col='Backlog'), was='Launching'))
    assert not M.guarded(moved(node(col='Building'), was='Backlog'))


def test_a_waiting_ticket_moved_into_the_dock_goes_back_to_the_shipyard():
    """0.68.0 (SMADPickleBot #2531): a ticket moved into the Dock while its Light says it waits (Needs you, Queued, Blocked, Scheduled) goes back to the Shipyard; a working one, a draft, the pipeline's marked move and a board with no wait Light stand; the move is guarded and its put-back's echo is known"""
    to, why = M.put_back_to(moved(node(col='Ready', light='Needs you'), was='Building'))
    assert (to, why.startswith('#7 still waits (Needs you)')) == ('Building', True), (to, why)
    assert M.put_back_to(moved(node(col='Ready', light='Queued'), was='Building'))[0] == 'Building'
    assert M.put_back_to(moved(node(col='Ready', light='Working'), was='Building')) == ('', '')
    assert M.put_back_to(moved(node(col='Ready', light=''), was='Building')) == ('', '')
    assert M.put_back_to(moved(node(number=None, title='a note', col='Ready', light='Needs you'), was='Building')) == ('', '')
    assert M.put_back_to(moved(node(col='Ready', light='Needs you'), was='Building'), marked=lambda n: True) == ('', '')
    bare = dict(CFG, lights={'dock': 'Warming', 'working': 'Working'})
    assert M.put_back_to(moved(node(col='Ready', light='Needs you'), was='Building'), bare) == ('', '')
    assert M.guarded(moved(node(col='Ready'), was='Building')) and not M.guarded(moved(node(col='Ready'), was='Building'), bare)


def test_a_put_back_writes_its_mark_sets_the_status_and_tells_the_owner():
    """put_back: the echo mark first, the one Status write, one letter from the configured sender; its echo is dropped."""
    opener, store, letters = Opener(lambda q, v: {'s0': {'projectV2Item': {'id': 'IT_7'}}}), Store(), []
    data = moved(node(col='Ship', owner='Lee'), was='Ready', by='lee')
    line = M.put_back(data, 'tok', store, lambda *a: letters.append(a), opener=opener, now=lambda: 1000.0)
    assert line.startswith('#7 was moved into Ship by lee; put back in Ready')
    assert store.docs[('putbacks', 'IT_7')] == {'at_epoch': 1000.0, 'to': 'Ready'}
    assert aliases(opener) == {'F_status': {'singleSelectOptionId': 'o_ready'}}
    assert letters == [('Cap', f'Board: {line}. If the move was meant, tell Cap what it should prove.', 'The board')]
    echo = moved(node(col='Ready'), was='Ship')
    assert M.put_back_echo(echo, store, now=lambda: 1060.0)
    assert not M.put_back_echo(echo, store, now=lambda: 1000.0 + M.PUT_BACK_SECONDS + 1)
    assert not M.put_back_echo(echo, Store(broken=True)) and not M.put_back_echo(moved(node(), was='Backlog'), store)
    assert M.put_back(moved(node(col='Building')), 'tok', store, opener=opener) == '' and len(opener.calls) == 1


def test_a_pipelines_own_move_stands_and_an_unread_mark_undoes_nothing():
    """pipeline_marked: a fresh mark, an old one, an unreadable store (marked), no store (marked); LazyStore makes once."""
    store = Store({('moves', '7'): {'at_epoch': 100.0}})
    assert M.pipeline_marked(store, lambda: 200.0)(7)
    assert not M.pipeline_marked(store, lambda: 100.0 + M.PIPELINE_MARK_SECONDS + 1)(7)
    assert not M.pipeline_marked(store, lambda: 200.0)(8)
    assert M.pipeline_marked(Store(broken=True))(7) and M.pipeline_marked(None)(7)
    made = []
    lazy = M.LazyStore(lambda: made.append(1) or store)
    assert made == []
    lazy.set('moves', '9', {'at_epoch': 1.0})
    assert lazy.get('moves', '9') == {'at_epoch': 1.0} and made == [1]


# ------------------------------------------------------------------ an issue's card
def issue_answers(cards, issues):
    """An opener's answers for the issue reads, the Project add, the item read and the writes."""
    def answer(query, variables):
        if 'addProjectV2ItemById' in query:
            return {'addProjectV2ItemById': {'item': {'id': 'IT_' + variables['c'].split('_')[1]}}}
        if query == M.ITEM_QUERY:
            return {'node': cards.get(variables['id'])}
        if query in (M.ISSUE_ID, M.CLOSED_ISSUE):
            return {'repository': {'issue': issues.get(variables['n'])}}
        if 'archiveProjectV2Item' in query:
            return {'archiveProjectV2Item': {'item': {'id': variables['i']}}, 'unarchiveProjectV2Item': {'item': {'id': variables['i']}}}
        return {f's{k}': {'projectV2Item': {'id': 'x'}} for k in range(6)}
    return answer


def test_a_landed_ticket_goes_to_the_dock_forward_only_and_sheds_its_blockers():
    """to_dock: moved with the dock's light and unblocked; a card past the dock, a queued one and a missing one stay."""
    issues = {7: {'id': 'I_7', 'labels': {'nodes': [{'id': 'L_1', 'name': 'stuck'}]},
                  'blockedBy': {'nodes': [{'id': 'I_3', 'number': 3, 'state': 'OPEN'}, {'id': 'I_4', 'number': 4, 'state': 'CLOSED'}]}},
              8: {'id': 'I_8'}, 9: {'id': 'I_9'}, 10: {'id': 'I_10'}}
    cards = {'IT_7': node(7), 'IT_8': node(8, col='Ship', item='IT_8'), 'IT_9': node(9, light='Queued', item='IT_9'),
             'IT_10': node(10, col='Ready', light='Needs you', item='IT_10')}
    opener = Opener(issue_answers(cards, issues))
    lines = M.to_dock([7, 8, 9, 10, 11], 'tok', opener=opener)
    assert lines[0] == '#7: Ready, Warming; unblocked from #3, the stuck label off'
    assert lines[1] == '#8: already in Ship; left there'
    assert lines[2].startswith('#9: Queued in Building, its owner not building it yet; left there')
    assert lines[3] == '#10: still Ready, Needs you kept'
    assert lines[4] == '#11: not found; not moved'
    removed = [v for q, v in opener.calls if q == M.UNBLOCK]
    assert removed == [{'i': 'I_7', 'b': 'I_3'}]
    assert M.item_for(7, 'tok', opener=opener) == 'IT_7' and M.item_for(11, 'tok', opener=opener) == ''
    assert M.issue_node(7, 'tok', opener=opener)['id'] == 'I_7'
    assert M.to_dock([7], 'tok', opener=Opener(errors='boom')) == ['#7: not moved (boom)']


# ------------------------------------------------------------------ an alert's card
def test_an_alert_issue_is_placed_in_the_backlog_with_its_key():
    """alert_key reads a body's marker on its own line; place_alert sets Status, Kind, Owner and the key in one write."""
    assert M.alert_key('x\n<!-- sentry-issue: 42 -->\n') == 'sentry:42'
    assert M.alert_key('<!-- gcp-alert: job @ 2026-01-01T00:00Z -->') == 'gcp:job@2026-01-01T00:00Z'
    assert M.alert_key('a plan quoting <!-- sentry-issue: 42 --> in a sentence') == '' and M.alert_key('') == ''
    opener = Opener(issue_answers({'IT_7': node(7, col='')}, {7: {'id': 'I_7'}}))
    line = M.place_alert(7, 'Ada', 'tok', opener=opener, key='sentry:42')
    assert line == '#7 on the board: Backlog, Defect, Owner Ada, Alert key sentry:42'
    assert aliases(opener) == {'F_status': {'singleSelectOptionId': 'o_backlog'}, 'F_kind': {'singleSelectOptionId': 'k_defect'},
                               'F_owner': {'singleSelectOptionId': 'p_ada'}, 'F_alert': {'text': 'sentry:42'}}
    past = Opener(issue_answers({'IT_7': node(7, col='Building')}, {7: {'id': 'I_7'}}))
    assert M.place_alert(7, 'Ada', 'tok', opener=past) == '#7 already in Building; left there'
    assert M.place_alert(11, 'Ada', 'tok', opener=past) == '#11 not found; not on the board'


# ------------------------------------------------------------------ a closed ticket's card
def test_a_closed_tickets_card_is_read_archived_restored_and_shipped():
    """closed_card reads one issue and its card; to_unplanned, restore and to_shipped each write once."""
    issue = {'id': 'I_7', 'title': 'Fix the gate', 'url': 'https://example.test/7', 'state': 'CLOSED', 'stateReason': 'NOT_PLANNED',
             'closedAt': '2026-01-02T03:04:05Z', 'projectsV2': {'nodes': [{'id': 'PVT_1'}]}, 'comments': {'nodes': [{'body': 'done'}]}}
    off = dict(issue, id='I_8', projectsV2={'nodes': [{'id': 'PVT_other'}]})
    opener = Opener(issue_answers({'IT_7': node(7, col='Launching', light='Working')}, {7: issue, 8: off}))
    card = M.closed_card(7, 'tok', opener=opener)
    assert card == {'number': 7, 'title': 'Fix the gate', 'url': 'https://example.test/7', 'state': 'CLOSED',
                    'reason': 'not_planned', 'closed_at': '2026-01-02T03:04:05Z', 'comment': 'done', 'item': 'IT_7',
                    'column': 'Ship', 'light': 'Working', 'owner': 'Ada'}
    assert M.closed_card(8, 'tok', opener=opener) == {} and M.closed_card(11, 'tok', opener=opener) == {}
    assert M.closed_key(7, '2026-01-02T03:04:05Z') == 'ticket-closed-7-20260102T030405Z'
    assert M.is_unplanned('NOT_PLANNED') and M.is_unplanned('duplicate') and not M.is_unplanned('completed')
    assert M.is_finished('Ship') and M.is_finished('Launching') and M.is_finished('Wrecked') and M.is_finished('Orders')
    assert not M.is_finished('Building')
    M.to_unplanned('IT_7', 'tok', opener=opener)
    assert opener.calls[-1][1] == {'p': 'PVT_1', 'i': 'IT_7'}
    assert M.restore(7, 'tok', opener=opener) == 'IT_7' and M.restore(8, 'tok', opener=opener) == ''
    with raises(RuntimeError, match='archived no item'):
        M.to_unplanned('IT_7', 'tok', opener=Opener())
    M.to_shipped('IT_7', 'tok', opener=opener)
    assert aliases(opener) == {'F_status': {'singleSelectOptionId': 'o_ship'}, 'F_light': {'singleSelectOptionId': 'l_shipped'}}


def test_the_letters_go_to_the_owner_or_the_captain_in_the_hosts_words():
    """closed_to, closed_letter, unplanned_line and wake_letter: the captain for no Owner or the lead's; the host's words."""
    assert (M.closed_to('Ada'), M.closed_to(''), M.closed_to('Lee')) == ('Ada', 'Cap', 'Cap')
    to, text, subject = M.closed_letter({'number': 7, 'owner': 'Lee', 'column': 'Building', 'title': 'T', 'url': 'u'})
    assert to == 'Cap' and subject == '#7 closed in Building' and 'answer `Scrum: done`' in text
    assert text.endswith('It has Owner Lee on the board, so it is yours to settle.')
    assert M.closed_letter({'number': 7, 'owner': 'Ada', 'column': ''})[2] == '#7 closed in no column'
    to, text, subject = M.unplanned_line({'number': 7, 'owner': '', 'title': ' T ', 'url': 'u'}, 'DUPLICATE')
    assert (to, subject) == ('Cap', 'Closed as a duplicate: #7') and text.endswith('T\nu')
    to, text, subject = M.wake_letter(node(), 'lee')
    assert (to, subject) == ('Ada', '#7 to Building') and text.startswith('lee moved #7 to Building on the board: Fix the gate')
    assert 'yours to build now (its Owner on the board)' in text
    assert M.wake_letter(node(owner=''), '')[1].endswith('It has no Owner on the board: take it, or set its Owner and tell them.')
    M.configure(words={'board_name': 'the Widgets board', 'wake_start': ' Then run: start {number}.'})
    text = M.wake_letter(node(labels=('from:boss',)), 'lee')[1]
    assert 'on the Widgets board' in text and 'carries the from:boss label' in text and text.endswith(' Then run: start 7.')


# ------------------------------------------------------------------ the host's names
def test_names_are_read_at_call_time_and_a_missing_one_is_refused():
    """The constants answer the settings at each read; a missing name raises NotConfigured; an unknown one is refused."""
    assert (M.SENDER, M.SCRUM_SENDER, M.STARTS, M.BOARD_MOVES, M.PUT_BACKS, M.CLOSED_CLAIMS) == \
        ('The board', 'The scrum master', 'starts', 'moves', 'putbacks', 'claims')
    assert (M.CAPTAIN, M.LEAD, M.STARTED_MINUTES, M.ALERT_KIND, M.CHANGE_ORDER_LABEL) == ('Cap', 'Lee', 20, 'Defect', 'order')
    M.configure(senders={'board': 'Another board'}, collections={})
    assert M.SENDER == 'Another board'
    with raises(tickets.NotConfigured):
        M.SCRUM_SENDER
    with raises(tickets.NotConfigured):
        M.pipeline_marked(Store())(7)
    with raises(TypeError):
        M.configure(nonsense=1)
    with raises(AttributeError):
        M.NO_SUCH_NAME


def test_unwired_the_board_and_people_are_tickets_own_and_no_project_is_a_default():
    """With no board or people of its own the module reads chuang_sdlc.tickets', a tenant's layer over it included."""
    dict.update(M._SET, board=None, people={}, labels={}, senders={}, collections={}, alert_kind='', words={})
    host = dict(tickets._HOST)
    tickets.configure(board=lambda: CFG, people={'captain': 'Host Cap'})
    try:
        assert M.stage('dock') == 'Ready' and M.captain() == 'Host Cap' and M.lead() == ''
        other = dict(CFG, stages=dict(CFG['stages'], dock='Pier'))
        with tenant.using('other', tickets={'board': lambda: other, 'people': {'captain': 'Other Cap'}}):
            assert M.stage('dock') == 'Pier' and M.closed_to('') == 'Other Cap'
        assert not M.is_feedback(node(labels=('feedback',)))
        assert M.put_back_to(moved(node(col='Orders', labels=('change-order',)), was='Backlog'))[0] == 'Backlog'
        assert M.unblock({'id': 'I_7', 'labels': {'nodes': [{'id': 'L', 'name': 'blocked'}]}}, 'tok', Opener()) == []
    finally:
        dict.update(tickets._HOST, host)
    defaults = json.dumps([M.WORDS, {k: v for k, v in M._SET.items() if k not in ('board', 'github')}], ensure_ascii=False)
    for name in ('SMAD', 'ickle', 'Gene', 'Snow White', 'Black Widow', 'Sprint board', 'ticket_starts', 'board_putbacks',
                 'ci_releases', 'change-order', 'from:admin'):
        assert name not in defaults


def test_a_tenant_lays_its_own_names_over_the_hosts():
    """tenant.using(moves=): a tenant's labels, people and words hold inside its block alone; a name moves does not take is refused."""
    card = node(labels=('ask',))
    with tenant.using('other', moves={'labels': {'feedback': 'ask'}, 'people': {'captain': 'Other Cap'},
                                      'words': {'board_name': 'their board'}}):
        inside = (M.is_feedback(card), M.captain(), M.word('board_name'), M.label('blocked'))
    after = (M.is_feedback(card), M.captain(), M.word('board_name'))
    assert inside == (True, 'Other Cap', 'their board', 'stuck')
    assert after == (False, 'Cap', 'the board')
    with raises(TypeError, match='takes no'):
        with tenant.using('other', moves={'nonsense': 1}):
            pass


if __name__ == '__main__':
    FAILURES = []
    for name, fn in list(globals().items()):   # in the file's order: the positive case first
        if not (name.startswith('test_') and callable(fn)):
            continue
        label = (fn.__doc__ or name).strip().splitlines()[0]
        before = wired()
        try:
            fn()
            print(f'  PASS  {label}')
        except Exception:   # an assertion or the case's own error: the case failed, the rest still run
            FAILURES.append(label)
            print(f'  FAIL  {label}')
            traceback.print_exc(file=sys.stdout)
        finally:
            dict.update(M._SET, before)
            # Each case its own budget of the client's per-process GraphQL cap (60 a minute): the fakes count as calls.
            from chuang_platform_kit import github as _github
            _github.reset_limits()
    print(f'{len(FAILURES)} failure(s)')
    sys.exit(1 if FAILURES else 0)
