#!/usr/bin/env python3
"""chuang_sdlc.tickets: tickets on a GitHub Project board, wired to a host through configure().

Pinned (the positive case first, on a fake runner and a fake store, never GitHub):
  - unwired, a call that needs the board raises NotConfigured; configure refuses an unknown name and a word it lacks;
  - the field writes: names to ids in ONE request, a role as its column, an option the board lacks is its fallback or
    nothing written, a number in hours, '' clears; a runner with no item_fields writes one by one;
  - moves: forward only, `back` the one step back, an unreadable column not moved, the ship column marked first for the
    host's own runner only, its cards put at the top; start lights `working` and keeps `needs`;
  - filing: every field in one write, the area label at opening, no body or the title again refused, an LOE that reads
    as none refused, the body's LOE line read; a bug a sub-issue of its feature in its Area or the host's default;
    similar() by shared words;
  - triage: the line's forms; triage() in place with the host's default owner; decide() now and backlog, never a step
    back, the host's letter; sweep() takes only the repo owner's lines, skips a filed ticket, decides a waiting one,
    works an admin's ask now, and runs the estimate stage on a line's LOE;
  - the estimate stage: calibrated by the host's factor, Estimate and LOE original in one request, the raw kept on the
    record unless retroactive; record_loe and report_loe through the store, once;
  - the shipped stage: Release `CO #n · head8`, closed with its commits, the branch linked at the last one, the LOE
    reported for the host's runner only; the change order's title, body and settle; open_bug_for;
  - the board kept: placeholders made and put back; sync_columns renames with every option's own id and refuses drift;
    board_items reads the host's card fields under an organization or a user, and a draft's own id and body;
  - the ship card: read among the drafts, retitled on its own id with its body sent back, sent home from any column but
    home and the ship column, left there while the host's ship record says one is under way, home once it ended,
    left with a WARNING when the record cannot be read; sync_columns reads the drafts once for both passes;
    mark_ship_move calls the host's mark whatever the runner;
  - the backfill's plan for each kind of card; the Client refuses without a token.

Run:  python -B tests/test-sdlc-tickets.py
"""
import io
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))
os.environ.pop('CHUANG_PLATFORM_TZ', None)

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
    'fields': {
        'Status': {'id': 'F_status', 'options': {'Backlog': 'o_b', 'Up Next': 'o_n', 'Building': 'o_y', 'Ready': 'o_r',
                                                 'Done': 'o_d', 'Broken': 'o_x', 'Changes': 'o_c'}},
        'Light': {'id': 'F_light', 'options': {'busy': 'l_busy', 'warm': 'l_warm', 'ok': 'l_ok', 'going': 'l_go',
                                               'gone': 'l_gone', 'red': 'l_red', 'ask': 'l_ask'}},
        'Owner': {'id': 'F_owner', 'options': {'Alice': 'u_a', 'Bob': 'u_b'}},
        'Priority': {'id': 'F_pri', 'options': {'P1': 'p1', 'P2': 'p2', 'P3': 'p3', 'P4': 'p4'}},
        'Kind': {'id': 'F_kind', 'options': {'Feature': 'k_f', 'Bug': 'k_b', 'Task': 'k_t', 'Action item': 'k_a'}},
        'Area': {'id': 'F_area', 'options': {'Product': 'a_p', 'Pipeline': 'a_pl', 'Internal tools': 'a_it'}},
        'Estimate': {'id': 'F_est', 'type': 'number'}, 'LOE original': {'id': 'F_orig', 'type': 'number'},
        'Actual': {'id': 'F_act', 'type': 'number'}, 'Release': {'id': 'F_rel'}},
    'stages': {'backlog': 'Backlog', 'next': 'Up Next', 'shipyard': 'Building', 'dock': 'Ready', 'dev': 'Ready',
               'shipping': 'Done', 'shipped': 'Done', 'failed': 'Broken', 'shipment': 'Changes'},
    'flow': ['backlog', 'next', 'shipyard', 'dock', 'dev', 'shipping', 'shipped'],
    'renamed': {'In progress': 'Building'}, 'merged': {},
    'lights': {'backlog': '', 'next': '', 'shipyard': '', 'working': 'busy', 'dock': 'warm', 'dev': 'ok',
               'shipping': 'going', 'shipped': 'gone', 'failed': 'red', 'needs': 'ask'},
    'placeholders': {'shipyard': '— Building: a card here is being built —', 'dock': '— Ready: built and tested —'},
    'descriptions': {'backlog': 'Filed'},
    'labels': {'Feature': 'feature', 'Bug': 'bug', 'Task': 'task', 'Action item': 'action-item'},
}


class FakeGH:
    """The runner: issues by number, cards by item id, every write recorded."""

    def __init__(self, issues=None, cards=None, options=None, drafts=None):
        self.issues_ = issues or {}            # number -> {'title', 'body', 'state', 'labels', 'comments', 'id'}
        self.cards = cards or {}               # item -> {'number', 'status', 'light'}
        self.options = options or []           # the Status field's options
        self.drafts = drafts or []             # board_items('is:draft') nodes
        self.writes, self.comments, self.closed, self.labels_added, self.removed = [], [], [], [], []
        self.created, self.subs, self.tops, self.renames, self.links, self.error = [], [], [], [], [], ''
        self.moves, self.refs, self.refuse_move = [], {}, ''
        self.next_number = 500

    def auth_ok(self):
        return True

    def issue(self, n):
        i = self.issues_.get(int(n))
        if i is None:
            return None
        return {'number': int(n), 'title': i.get('title', ''), 'body': i.get('body', ''), 'state': i.get('state', 'OPEN'),
                'labels': [{'name': x} for x in i.get('labels', [])], 'url': f'https://github.com/acme/app/issues/{n}',
                'pr': False, 'id': i.get('id', 9000 + int(n)), 'node': f'I_{n}'}

    def node_id(self, n):
        return f'I_{n}' if int(n) in self.issues_ else ''

    def item_add(self, node):
        n = int(node.split('_')[1])
        item = f'IT_{n}'
        self.cards.setdefault(item, {'number': n, 'status': '', 'light': ''})
        return item

    def issues(self, state='open', labels=(), search='', limit=100, comments=0):
        rows = []
        for n, i in sorted(self.issues_.items()):
            if state == 'open' and i.get('state', 'OPEN') != 'OPEN':
                continue
            if labels and not set(labels) <= set(i.get('labels', [])):
                continue
            if search and not any(w.strip('"').lower() in i.get('title', '').lower() for w in search.replace(' in:title', '').split()):
                continue
            rows.append({'number': n, 'title': i.get('title', ''), 'labels': [{'name': x} for x in i.get('labels', [])],
                         'createdAt': i.get('createdAt', ''), 'comments': i.get('comments', [])})
        return rows

    def create_issue(self, title, body, labels=()):
        n = self.next_number
        self.next_number += 1
        self.issues_[n] = {'title': title, 'body': body, 'labels': list(labels)}
        self.created.append((n, title, list(labels)))
        return n

    def comment(self, n, body):
        self.comments.append((int(n), body))
        return True

    def add_labels(self, n, labels):
        self.labels_added.append((int(n), list(labels)))
        self.issues_.setdefault(int(n), {}).setdefault('labels', []).extend(labels)
        return True

    def remove_label(self, n, label):
        self.removed.append((int(n), label))
        return True

    def create_label(self, name, color, description=''):
        return True

    def close_issue(self, n, reason='completed', comment=''):
        self.closed.append((int(n), reason, comment))
        return True

    def add_sub_issue(self, parent, child_id):
        self.subs.append((int(parent), int(child_id)))
        return True

    def item_fields(self, item, fields):
        self.writes.append((item, dict(fields)))
        self.cards.setdefault(item, {'number': None, 'status': '', 'light': ''})
        for fid, v in fields.items():
            if fid == 'F_status':
                self.cards[item]['status'] = {o: k for k, o in CFG['fields']['Status']['options'].items()}.get(v, '') if v else ''
            if fid == 'F_light':
                self.cards[item]['light'] = {o: k for k, o in CFG['fields']['Light']['options'].items()}.get(v, '') if v else ''
        return True

    def item_field(self, item, field_id, option_id=None, text=None, clear=False):
        return self.item_fields(item, {field_id: None if clear else ({'text': text} if text is not None else option_id)})

    def draft_add(self, title, body):
        item = f'D_{len(self.cards)}'
        self.cards[item] = {'number': None, 'status': '', 'light': '', 'title': title}
        return item

    def item_top(self, item):
        self.tops.append(item)
        return True

    def graphql(self, query, variables=None):
        v = variables or {}
        if 'fieldValueByName(name: "Status")' in query and 'node(id:' in query:   # ticket_place
            card = self.cards.get(v['id'])
            if card is None or card.get('unread'):
                return None
            return {'node': {'id': v['id'], 'status': {'name': card['status']}, 'light': {'name': card['light']}}}
        if 'estimate: fieldValueByName' in query and 'node(id:' in query:
            return {'node': {'id': v['id'], 'estimate': {'number': self.cards.get(v['id'], {}).get('estimate')}}}
        if 'content { ... on Issue { number } }' in query:
            return {'node': {'content': {'number': self.cards.get(v['id'], {}).get('number')}}}
        if 'options { id name color description }' in query:
            return {'node': {'options': [dict(o) for o in self.options]}}
        if 'updateProjectV2Field' in query:
            self.renames.append(v['options'])
            self.options = [dict(o) for o in v['options']]
            return {'updateProjectV2Field': {}}
        if 'createLinkedBranch' in query:
            self.links.append((v['issue'], v['name'], v['oid']))
            return {'createLinkedBranch': {'linkedBranch': {'id': 'LB'}}}
        if 'updateRef' in query:
            if getattr(self, 'refuse_move', ''):
                self.error = self.refuse_move
                return None
            self.moves.append((v['ref'], v['oid']))
            return {'updateRef': {'ref': {'target': {'oid': v['oid']}}}}
        if 'object(expression:' in query:
            at = getattr(self, 'refs', {}).get(v['ref'])
            return {'repository': {'id': 'R_1', 'object': {'oid': v['sha'] + '0' * (40 - len(v['sha']))},
                                   'ref': {'id': f"REF_{v['ref']}", 'target': {'oid': at}} if at else None}}
        if 'linkedBranches' in query:
            return {'organization': {'projectV2': {'items': {'pageInfo': {}, 'nodes': self.shipped}}}}
        if 'updateProjectV2DraftIssue' in query:   # the ship card's title
            self.retitled = getattr(self, 'retitled', []) + [dict(v)]
            return {'updateProjectV2DraftIssue': {'draftIssue': {'id': v['d']}}}
        if 'projectV2(number:' in query:
            owner = 'user' if 'user(login:' in query else 'organization'
            self.last_items_query = query
            self.item_reads = getattr(self, 'item_reads', []) + [v.get('q')]
            return {owner: {'projectV2': {'items': {'pageInfo': {}, 'nodes': self.drafts}}}}
        raise AssertionError(f'unexpected query: {query[:80]}')


class Store:
    def __init__(self, docs=None):
        self.docs, self.merges = docs or {}, []

    def get(self, c, i):
        return self.docs.get((c, i))

    def set(self, c, i, d):
        self.docs[(c, i)] = d

    def merge(self, c, i, d):
        self.merges.append((c, i, d))
        self.docs[(c, i)] = dict(self.docs.get((c, i)) or {}, **d)


print('wiring')
try:
    T.stage('shipyard')
    check('unwired, the board is NotConfigured', 'no raise', 'NotConfigured')
except T.NotConfigured:
    check('the positive case: unwired, the board is NotConfigured', True, True)
for bad in ({'nope': 1}, {'words': {'NOPE': 'x'}}):
    try:
        T.configure(**bad)
        check(f'configure refuses {bad}', 'accepted', 'TypeError')
    except TypeError:
        check(f'configure refuses {bad}', True, True)

for name, call in (('a label', lambda: T.label('triage')), ('a collection', lambda: T.collection('loe'))):
    try:
        call()
        check(f'unwired, {name} is NotConfigured (the kit names none of its own)', 'a default', 'NotConfigured')
    except T.NotConfigured:
        check(f'unwired, {name} is NotConfigured (the kit names none of its own)', True, True)
check("an optional label unwired is ''", T.label('admin_ask', required=False), '')

GH = FakeGH()
ST = Store()
marks, letters = [], []
T.configure(board=lambda: CFG, runner=lambda: GH, store=lambda: ST, loe_records=lambda: [],
            people={'triager': 'Alice', 'default_owner': 'Bob', 'captain': 'Bob'}, default_area='Product',
            labels={'triage': 'waiting', 'admin_ask': 'urgent', 'change_order': 'release', 'member_title': 'reword'},
            collections={'loe': 'loe_records', 'starts': 'loe_starts'},
            change_order_area='Pipeline', before_ship_move=lambda ns, run: marks.extend(ns), start_hint='tool start N',
            words={'CHANGE_ORDER_NOTE': 'The release post names it.', 'ESTIMATE_COMMAND': 'tool estimate'})
check('wired, a role is its column', T.stage('shipyard'), 'Building')


def fresh(issues=None, cards=None, **kw):
    """A new runner the host's provider answers, and the marks emptied."""
    global GH
    GH = FakeGH(issues, cards, **kw)
    marks.clear()
    return GH


print('field writes')
gh = fresh({1: {'title': 'One'}})
check('the positive case: several fields in ONE request',
      (T.set_fields('IT_1', {'Status': 'shipyard', 'Light': 'busy', 'Priority': 'P2', 'Estimate': '1.5'}, gh), gh.writes),
      (True, [('IT_1', {'F_status': 'o_y', 'F_light': 'l_busy', 'F_pri': 'p2', 'F_est': {'number': 1.5}})]))
gh = fresh()
check('an option the board lacks writes nothing', (T.set_fields('IT_1', {'Owner': 'Zed', 'Priority': 'P1'}, gh), gh.writes),
      (False, []))
check("'' clears; a text field is text; an old column name is its new one",
      (T.set_fields('IT_1', {'Light': '', 'Release': 'abc', 'Status': 'In progress'}, gh), gh.writes[-1][1]),
      (True, {'F_light': None, 'F_rel': {'text': 'abc'}, 'F_status': 'o_y'}))
check('a number that is none is refused', T.set_fields('IT_1', {'Estimate': 'soon'}, gh), False)


class OldGH:
    """A runner from before the one-request write: item_field alone."""

    def __init__(self):
        self.writes = []

    def item_field(self, item, field_id, option_id=None, text=None, clear=False):
        self.writes.append((field_id, option_id, text, clear))
        return True


old = OldGH()
check('a runner with no item_fields writes one by one', (T.set_fields('IT_1', {'Priority': 'P3', 'Kind': 'Bug'}, old), old.writes),
      (True, [('F_pri', 'p3', None, False), ('F_kind', 'k_b', None, False)]))

print('moves')
gh = fresh({1: {}, 2: {}, 3: {}, 4: {}}, {'IT_1': {'number': 1, 'status': 'Backlog', 'light': ''},
                                         'IT_2': {'number': 2, 'status': 'Ready', 'light': 'ok'},
                                         'IT_3': {'number': 3, 'status': 'Backlog', 'light': '', 'unread': True},
                                         'IT_4': {'number': 4, 'status': 'Ready', 'light': 'warm'}})
check('the positive case: forward moves, a card past the target stays, an unread one is not moved',
      T.move([1, 2, 3], 'dock', gh), [(1, True), (2, True), (3, False)])
check('the moved card is in Ready warming', (gh.cards['IT_1']['status'], gh.cards['IT_1']['light']), ('Ready', 'warm'))
check('dev beside dock: a warming card steps on to dev', (T.move([4], 'dev', gh), gh.cards['IT_4']['light']), ([(4, True)], 'ok'))
check('back steps a card past the target back, and only it', (T.move([2], 'shipyard', gh, back=True), gh.cards['IT_2']['status']),
      ([(2, True)], 'Building'))
# Marked by the move and again by its Status write, as the host's own code always did: the mark is idempotent.
check('the ship column: marked first for the host runner, put at the top',
      (T.move([1], 'shipping', gh), set(marks), gh.tops), ([(1, True)], {1}, ['IT_1']))
other = FakeGH({5: {}}, {'IT_5': {'number': 5, 'status': 'Ready', 'light': 'ok'}})
marks.clear()
check("a suite's own runner is never marked", (T.move([5], 'shipping', other), marks), ([(5, True)], []))
gh = fresh({6: {}, 7: {}}, {'IT_6': {'number': 6, 'status': 'Up Next', 'light': ''},
                            'IT_7': {'number': 7, 'status': 'Building', 'light': 'ask'}})
check('start: to the shipyard with the working light; needs is kept', T.start([6, 7], gh), [(6, True), (7, True)])
check('start lights', (gh.cards['IT_6']['light'], gh.cards['IT_7']['light']), ('busy', 'ask'))

print('filing')
gh = fresh()
n = T.new('feature', 'Add the export', 'The plan.\n\nLOE estimate: 2h', 'Alice', 'next', area='Product', priority='P3', run=gh)
check('the positive case: filed with the area label at opening', gh.created[0][1:], ('Add the export', ['feature', 'area:product']))
check("every field in one write, the column's empty Light clearing it", gh.writes[-1][1],
      {'F_kind': 'k_f', 'F_area': 'a_p', 'F_status': 'o_n', 'F_light': None, 'F_pri': 'p3', 'F_owner': 'u_a',
       'F_est': {'number': 2.0}})
for label, call in (('no body', lambda: T.new('task', 'T', '', area='Product', run=gh)),
                    ('the title again', lambda: T.new('task', 'Same', 'Same', area='Product', run=gh)),
                    ('an LOE that is none', lambda: T.new('task', 'T', 'Plan', area='Product', loe='soon', run=gh))):
    try:
        call()
        check(f'{label} is refused', 'filed', 'ValueError')
    except ValueError:
        check(f'{label} is refused', True, True)
check('an Area the board lacks files nothing', T.new('task', 'T', 'Plan', area='Moon', run=gh), None)
gh = fresh({40: {'title': 'Feature', 'labels': ['area:pipeline']}})
b = T.bug(40, 'It broke', 'Steps.', run=gh)
check("a bug: Kind Bug in the feature's Area, a sub-issue of it", (gh.writes[-1][1]['F_kind'], gh.writes[-1][1]['F_area'], gh.subs[-1][0]),
      ('k_b', 'a_pl', 40))
check('its body links the feature', gh.issues_[b]['body'].endswith('Found after #40 shipped.'), True)
gh = fresh({41: {'title': 'No area'}})
T.bug(41, 'Broke too', 'Steps.', run=gh)
check("a feature with no Area gives the host's default", gh.writes[-1][1]['F_area'], 'a_p')
gh = fresh({50: {'title': 'Export the monthly report as CSV'}, 51: {'title': 'Unrelated thing'}})
check('similar by shared title words', T.similar('Export monthly report CSV', gh), [(50, 'Export the monthly report as CSV')])

print('triage')
check('the positive case: a line with every part',
      T.triage_line('finding\nTriage: P2 | Owner: Alice | Area: Pipeline | Feature: #12 | LOE: 1h'),
      {'priority': 'P2', 'owner': 'Alice', 'area': 'Pipeline', 'feature': 12, 'loe': '1h'})
check('none (why)', T.triage_line('Triage: none (a blip the next run passed)'), {'none': 'a blip the next run passed'})
check('backlog', T.triage_line('**Triage: backlog**'), {'backlog': True})
check('the last line wins; an unreadable one is None', (T.triage_line('Triage: P3\nTriage: P1')['priority'], T.triage_line('Triage: soon')),
      ('P1', None))
gh = fresh({60: {'title': 'Alert'}})
line = T.triage(60, 'P1', 'Nobody', '', 0, gh)
check("triage in place: the host's default owner and area, a P1 to the shipyard",
      (line, gh.cards['IT_60']['status'], gh.labels_added[0]), ('tickets: #60 triaged P1, Bob, Product', 'Building', (60, ['bug'])))
gh = fresh({70: {'title': 'Members want X'}}, {'IT_70': {'number': 70, 'status': 'Backlog', 'light': ''}})
said = []
line = T.decide(70, 'now', 'Bob', 'P2', gh, mail=lambda to, text, subject: said.append((to, subject)) or 'mailed')
check('decide now: the shipyard at its priority on its owner, the label off, the owner told',
      (line, gh.cards['IT_70']['status'], gh.removed, said),
      ('tickets: #70 now, Building P2 Bob; mailed', 'Building', [(70, 'waiting')], [('Bob', 'New P2 for you now: #70')]))
gh = fresh({71: {'title': 'Already ready'}}, {'IT_71': {'number': 71, 'status': 'Ready', 'light': 'ok'}})
line = T.decide(71, 'backlog', run=gh, mail=lambda to, text, subject: f'{to}: {subject}')
check("decide backlog never steps a card back; the triager is told where it stayed",
      (line, gh.cards['IT_71']['status']), ('tickets: #71 backlog, Backlog (kept in Ready); Alice: #71 stays in Ready', 'Ready'))
T.configure(decide_letter=lambda *a: ('S', 'T'))
check("the host's own letter", T.decide(71, 'backlog', run=gh, mail=lambda to, text, subject: subject).endswith('; S'), True)
T.configure(decide_letter=None)
mine = lambda body: {'body': body, 'author': {'login': 'acme'}}  # noqa: E731
gh = fresh({80: {'title': 'Alert A', 'comments': [mine('x\nTriage: P2 | Owner: Alice | LOE: 2h')]},
            81: {'title': 'Filed', 'labels': ['area:product'], 'comments': [mine('Triage: P1')]},
            82: {'title': 'Stranger', 'comments': [{'body': 'Triage: P1', 'author': {'login': 'someone'}}]},
            83: {'title': 'Ask', 'labels': ['waiting', 'urgent'], 'comments': [mine('Triage: P4')]},
            84: {'title': 'Feedback', 'labels': ['waiting'], 'comments': [mine('Triage: P3 | Owner: Bob')]}})
lines = T.sweep(gh, factor_of=None) if False else T.sweep(gh)
check('the positive case: the sweep acts on the owner\'s lines alone, a filed ticket left', [ln.split(' ')[1] for ln in lines],
      ['#80', '#83', '#84'])
check("an alert's line triaged with its LOE stage, no factor: LOE original alone, the Estimate left empty",
      lines[0].startswith('tickets: #80 triaged P2, Alice, Product; #80 LOE original 2 h; Estimate left empty (no calibration factor'), True)
check("an admin's ask is now whatever its rank", lines[1].startswith('tickets: #83 now'), True)
check('a waiting P3 goes to the backlog, said with no LOE', lines[2],
      'tickets: #84 backlog, Backlog P3 Bob; no LOE in its Triage line (`tool estimate #84 <loe>` writes it)')
gh = fresh({86: {'title': 'Alert B', 'comments': [mine('finding\nTriage: none (fixed in October)')]},
            87: {'title': 'Feedback none', 'labels': ['waiting'], 'comments': [mine('Triage: none (a duplicate)')]},
            88: {'title': 'Filed none', 'labels': ['area:product'], 'comments': [mine('Triage: none (done)')]}})
lines = T.sweep(gh)
check("a none line on an alert issue closes it as not planned with its reason; a waiting or filed one is left",
      (lines, gh.closed),
      (['tickets: #86 closed as not planned: Triage: none (fixed in October)'],
       [(86, 'not_planned', T.NONE_COMMENT.format(why='fixed in October'))]))
gh.close_issue = lambda number, reason='completed', comment='': False
gh.error = 'HTTP 403 Resource not accessible'
check('a refused close is a WARNING the next sweep tries again', T.close_none(91, 'x', gh),
      'WARNING: tickets: #91 not closed for its Triage: none line (GitHub: HTTP 403 Resource not accessible); '
      'the next sweep tries again')
gh = fresh({85: {'title': 'Configured mail'}}, {'IT_85': {'number': 85, 'status': 'Backlog', 'light': ''}})
told = []
T.configure(mail=lambda to, text, subject: told.append(to) or 'mailed by the host')
check("decide with no mail= sends through the host's configured mail", (T.decide(85, 'backlog', run=gh).endswith('; mailed by the host'), told),
      (True, ['Alice']))
T.configure(mail=None)

print('LOE text')
check('the positive case: compound and spelled-out estimates',
      [T.loe_minutes(s) for s in ('30m', '2h', '1.5h', '1d', '45', '1h 30m', '1h30m', '2 hours', '30 min', '1d 2h', '1h.')],
      [30, 120, 90, 480, 45, 90, 90, 120, 30, 600, 60])
check('what is no estimate', [T.loe_minutes(s) for s in ('1 30', 'soon', '', None, 'h')], [None, None, None, None, None])
check("a body's LOE line, the whole rest of the line", (T.body_loe('Plan.\nLOE estimate: 1h 30m  \nmore'), T.body_loe('Plan.')),
      ('1h 30m', None))
check('a Triage LOE that does not read is kept to say', T.triage_line('Triage: P2 | LOE: soonish'),
      {'priority': 'P2', 'owner': '', 'area': '', 'feature': 0, 'loe_unread': 'soonish'})
check('a Triage LOE of 0 is no estimate', 'loe' in T.triage_line('Triage: P2 | LOE: 0m'), False)

print('the estimate stage')
gh = fresh({90: {}})
ST.docs.clear()
T.configure(factor_of=lambda raw: (0.05, 12, 'over 4h'))
line = T.estimate_stage(90, '8h', gh)
check('the positive case: calibrated, both fields in one request, the raw on the record',
      (line, gh.writes[-1][1], ST.docs.get(('loe_records', '90'))),
      ('tickets: #90 Estimate 0.42 h, LOE original 8 h (raw 1d x 0.05, over 4h: the median actual/estimate of 12 tickets)',
       {'F_est': {'number': 0.42}, 'F_orig': {'number': 8.0}}, {'estimate_raw_min': 480}))
T.estimate_stage(90, 120, gh, record=False)
check('a retroactive estimate is kept off the record', ST.docs.get(('loe_records', '90')), {'estimate_raw_min': 480})
RAW = Store()
T.configure(raw_store=lambda: RAW)
T.keep_record(91, 60)
check("a raw store wired takes the raw, the record store untouched",
      (RAW.docs.get(('loe_records', '91')), ST.docs.get(('loe_records', '91'))), ({'estimate_raw_min': 60}, None))
T.configure(raw_store=None)
check('no LOE is said', T.estimate_stage(90, 'later', gh), "tickets: #90 no estimate written ('later' is no LOE: 30m, 2h, 1d)")
check('a raw of 0 is no estimate', T.estimate_stage(90, 0, gh), "tickets: #90 no estimate written (0 is no LOE: 30m, 2h, 1d)")
writes_before = len(gh.writes)
T.estimate_stage(90, '1h', gh, item='IT_held')
check("the caller's item spares the lookup", gh.writes[writes_before][0], 'IT_held')
T.configure(factor_of=None)
line = T.estimate_stage(90, '2h', gh, raw_unfactored=False)
check("no factor and raw_unfactored off: LOE original alone, said", (line.split(';')[0], gh.writes[-1][1]),
      ('tickets: #90 LOE original 2 h', {'F_orig': {'number': 2.0}}))
check('no factor at a terminal: the raw filed as the Estimate', T.estimate_stage(90, '2h', gh).startswith(
      'tickets: #90 Estimate 2 h, LOE original 2 h (raw, uncalibrated'), True)
try:
    T.new('task', 'T', 'Plan', area='Product', loe='0h', run=gh)
    check('a filing with an LOE of 0 is refused', 'filed', 'ValueError')
except ValueError:
    check('a filing with an LOE of 0 is refused', True, True)
check('no factor: the raw, said', T.calibrated('2h'), ('2h', 120, '(raw, uncalibrated: 0 ticket(s) with an actual)'))
gh = fresh({91: {}}, {'IT_91': {'number': 91, 'status': 'Building', 'light': 'busy', 'estimate': 1.0}})
ST.docs[('loe_starts', '91')] = {'first_epoch': 1000}
ev = T.loe_event([91, 92], now=4600, lines=T.L.parse('1000 x 91 delegated tu1\n1000 x agent a tu1\n1300 x agent a tu1\n'
                                                      '1600 x agent a tu1\n1600 x stop'))
check("loe_event carries the working minutes and the delegates' part", ev,
      {'tickets': [91, 92], 'at': 4600, 'key': '4600-91-92', 'active': {'91': 10, '92': None}, 'delegated': {'91': 5, '92': None}})
got = T.record_loe([91], run=gh, data={'tickets': [91], 'at': 4600, 'key': 'c1', 'active': {'91': 30}})
check('record_loe through the host store, the Actual on the board',
      (got[0].startswith('tickets: #91 LOE so far: estimated 1h, actual 30m of work (-50%), 1h elapsed'), gh.writes[-1][1]),
      (True, {'F_act': {'number': 0.5}}))
check('report_loe comments once', (T.report_loe([91], run=gh, now=5000), T.report_loe([91], run=gh, now=5001), len(gh.comments)),
      ([f'tickets: #91 LOE: {T.loe_text(ST.docs[("loe_records", "91")])}'], [], 1))

print('the shipped stage')
check('release_text', (T.release_text('1a2b3c4dabc', 12), T.release_text('1a2b3c4dabc')), ('CO #12 · 1a2b3c4d', '1a2b3c4d'))
gh = fresh({100: {'title': 'Shipped thing'}})
line = T.live_numbers([100], 'abcdef1234', gh, work={100: [('aaaa1111', 'first'), ('bbbb2222', 'last')]}, co=7)
check('the positive case: shipped, the Release, closed with its commits, the branch at its last commit',
      (line, gh.writes[-1][1], gh.closed[0][2].splitlines()[-1], gh.links[0][1:]),
      ('tickets: live #100 (release CO #7 · abcdef12)', {'F_rel': {'text': 'CO #7 · abcdef12'}}, '- bbbb2222 last',
       ('ticket/100', 'bbbb2222' + '0' * 32)))
check('the host runner was marked into the ship column', set(marks), {100})
gh = fresh({101: {}})
gh.refs['refs/heads/ticket/101'] = 'cccc3333' + '0' * 32
check('a branch already at the commit is done, nothing written', (T.link_branch(101, 'cccc3333', gh), gh.moves, gh.links),
      (True, [], []))
check('a branch behind is moved forward, never re-made', (T.link_branch(101, 'dddd4444', gh), gh.moves, gh.links),
      (True, [('REF_refs/heads/ticket/101', 'dddd4444' + '0' * 32)], []))
gh.refuse_move = 'Update is not a fast-forward'
check("one GitHub will not move as no fast forward is already past it", T.link_branch(101, 'eeee5555', gh), True)
gh.refuse_move = 'Something else'
check('any other refusal is False', T.link_branch(101, 'ffff6666', gh), False)
check('no sha is False', T.link_branch(101, '', gh), False)
check('change_order_title in the kit zone', T.change_order_title('5e6f7a8baa', 1793655660), 'Ship 11/2/26 1:41 PM PT: 5e6f7a8b')
gh = fresh({110: {'title': 'A ticket'}})
body = T.change_order_body('base0001', 'head0002', [110, 111], gh, 'the board')
check("the change order's body lists the tickets and the range, with the host's note",
      (body.splitlines()[2:4], 'compare/base0001...head0002' in body, body.endswith('The release post names it.')),
      (['- #110 A ticket', '- #111'], True, True))
gh = fresh({120: {'title': 'Ship 11/7/26 12:08 PM PT: 1a2b3c4d', 'labels': ['release']}})
check('settle: the change order shipped and closed', (T.change_order_settle('1a2b3c4dcafe', 'shipped', 'done', gh), gh.closed),
      ('; change order #120 gone', [(120, 'completed', 'done')]))
gh = fresh({130: {'title': 'api failed its smoke: 502'}})
check('open_bug_for', (T.open_bug_for('api', gh), T.open_bug_for('site', gh)), (130, None))

print('the board kept')
gh = fresh(drafts=[{'id': 'D_1', 'content': {'__typename': 'DraftIssue', 'title': '— Ready: built and tested —'},
                    'status': {'name': 'Backlog'}}])
line = T.placeholders(gh)
check('the positive case: a missing placeholder made, a strayed one put back', line,
      'tickets: placeholders made: Building; put back: Ready')
gh = fresh(options=[{'id': 'o_b', 'name': 'Backlog', 'color': 'GRAY', 'description': ''},
                    {'id': 'o_y', 'name': 'In progress', 'color': 'BLUE', 'description': ''}])
gh.options += [{'id': i, 'name': nm, 'color': 'GRAY', 'description': ''} for nm, i in
               (('Up Next', 'o_n'), ('Ready', 'o_r'), ('Done', 'o_d'), ('Broken', 'o_x'), ('Changes', 'o_c'))]
line = T._sync_options(gh, CFG)
check('sync renames an old name, every option sent with its own id, a description written',
      (line, [o['id'] for o in gh.renames[0]], [o['name'] for o in gh.renames[0]][:2], gh.renames[0][0]['description']),
      ("tickets: the board's columns renamed: In progress is Building; described: Backlog",
       ['o_b', 'o_y', 'o_n', 'o_r', 'o_d', 'o_x', 'o_c'], ['Backlog', 'Building'], 'Filed'))
gh.options.append({'id': 'o_z', 'name': 'Handmade', 'color': 'RED', 'description': ''})
check('a column the config does not name is drift, nothing written', T._sync_options(gh, CFG).startswith('WARNING') and len(gh.renames) == 1, True)
T.configure(card_fields={'needs': 'Needs'})
gh = fresh(drafts=[{'id': 'IT_9', 'content': {'__typename': 'Issue', 'number': 9, 'title': 'Nine', 'url': 'u',
                                              'labels': {'nodes': [{'name': 'bug'}]}},
                    'status': {'name': 'Ready'}, 'light': {'name': 'ok'}, 'needs': {'text': 'a decision'}}])
cards = T.board_items('is:open', gh)
check("board_items reads the host's card fields", (cards[0]['status'], cards[0]['light'], cards[0]['needs'], cards[0]['labels'],
                                                    cards[0]['content']['number']), ('Ready', 'ok', 'a decision', ['bug'], 9))
user_cfg = dict(CFG, owner_type='user')
T.board_items('is:open', gh, user_cfg)
check("a user's Project is read under user(login:)", 'user(login: $owner)' in gh.last_items_query, True)

print('the ship card')
SHIP_CFG = dict(CFG, ship_card={'home': 'dock', 'word': 'SHIP IT', 'title': 'SHIP IT: drag me to Done'})
T.configure(board=lambda: SHIP_CFG)
draft = lambda status, title='SHIP IT old', body='keep me': {'id': 'SC', 'status': {'name': status},  # noqa: E731
                                                             'content': {'__typename': 'DraftIssue', 'id': 'DI_sc', 'title': title,
                                                                         'body': body}}
gh = fresh(drafts=[draft('Backlog')])
cards = T.board_items('is:draft', gh)
check("the positive case: a draft's own id and body are read", (cards[0]['content']['draft'], cards[0]['content']['body'],
                                                                'DraftIssue { id title body }' in gh.last_items_query),
      ('DI_sc', 'keep me', True))
check('the ship card is a draft carrying the word, never a placeholder', (T.is_ship_card(cards[0]),
                                                                       T.is_ship_card({'title': '— Building: SHIP IT —', 'content': {'type': 'DraftIssue'}}),
                                                                       T.is_ship_card({'title': 'SHIP IT', 'content': {'type': 'Issue', 'number': 4}})),
      (True, False, False))
lines = T.ship_card_keep(gh)
check('kept: retitled on its own id with its body sent back, and sent home', (lines, gh.retitled, gh.writes[-1]),
      (['the SHIP IT card retitled "SHIP IT: drag me to Done"', 'the SHIP IT card was in Backlog; moved back to Ready'],
       [{'d': 'DI_sc', 't': 'SHIP IT: drag me to Done', 'b': 'keep me'}], ('SC', {'F_status': 'o_r'})))
gh = fresh(drafts=[draft('Done', 'SHIP IT: drag me to Done')])
reads = []
check('in the ship column while a ship is under way: left, the record read once',
      (T.ship_card_keep(gh, ship_run=lambda: reads.append(1) or {'state': 'shipping', 'at': '2026-10-29T19:36:00Z'},
                        now=1793302800.0), reads, gh.writes), ([], [1], []))   # now: 10/29/26 12:40 PM PT
check('in the ship column with its ship ended: home, and why', T.ship_card_keep(gh, ship_run=lambda: {'state': 'done'}),
      ['the SHIP IT card was in Done with no ship under way (the last ship is done); moved back to Ready'])
check("no ship record wired: left, said as a WARNING naming the record", T.ship_card_keep(fresh(drafts=[draft('Done', 'SHIP IT: drag me to Done')])),
      ["WARNING: the SHIP IT card is in Done and its ship record could not be read (NotConfigured: no ship record: "
       "configure(ship_run=)), so it was left there"])
check('ship_ended: a recent under-way record is none; a stale one, a zoneless one read as UTC, no record',
      (T.ship_ended({'state': 'requested', 'at': '2026-10-29T19:36:00Z'}, 1793302800.0),
       T.ship_ended({'state': 'shipping', 'at': '2026-10-29T18:00:00'}, 1793302800.0), T.ship_ended(None)),
      ('', 'the last ship has said shipping for 100 minutes', 'no ship on record'))
gh = fresh(options=[{'id': i, 'name': nm, 'color': 'GRAY', 'description': 'Filed' if nm == 'Backlog' else ''}
                    for nm, i in CFG['fields']['Status']['options'].items()],
           drafts=[draft('Backlog', 'SHIP IT: drag me to Done')])
line = T.sync_columns(gh, ship_run=lambda: None)
check('sync_columns reads the drafts once for the placeholders and the ship card', (gh.item_reads, line.endswith(
    'the SHIP IT card was in Backlog; moved back to Ready')), (['is:draft'], True))
gh = fresh()
gh.graphql = lambda q, v=None: None if 'projectV2(number:' in q else FakeGH.graphql(gh, q, v)
check('drafts unread: the placeholders say so', T.placeholders(gh), T.DRAFTS_UNREAD)
marks.clear()
T.mark_ship_move([7], gh)
check("mark_ship_move calls the host's mark whatever the runner", marks, [7])
T.configure(board=lambda: CFG)

print('the backfill plan')
card = {'number': 5, 'release': '', 'original': None, 'estimate': None, 'branches': 0, 'body': ''}
check('the positive case: everything missing',
      T.backfill_plan(card, 'sha5', lambda s: 'head8888', {'head8888': 9}, {'estimate_raw_min': 90}, {}),
      {'release': 'CO #9 · head8888', 'raw': (90, 'record'), 'original': None, 'branch': 'sha5'})
check('Estimate set, LOE original empty: the original alone, from the body, never retro',
      (T.backfill_plan(dict(card, estimate=0.5, body='LOE estimate: 3h'), '', None, {}, {}, {'5': '1h'})['original'],
       T.backfill_plan(dict(card, estimate=0.5), '', None, {}, {}, {'5': '1h'})['original']), ((180, 'body'), None))
T.configure(calibrated_since=1000)
check('raw_on_record: the raw, else a pre-calibration estimate_min, else None',
      (T.raw_on_record({'estimate_raw_min': 40, 'estimate_min': 5}), T.raw_on_record({'estimate_min': 60, 'started_epoch': 999}),
       T.raw_on_record({'estimate_min': 60, 'started_epoch': 1001}), T.raw_on_record({})), (40, 60, None, None))
T.configure(calibrated_since=None)
check('no calibrated_since: an estimate_min is never the raw', T.raw_on_record({'estimate_min': 60, 'started_epoch': 1}), None)
check('a head alone gains its change order', T.backfill_plan(dict(card, release='abcdef12', branches=1), '', None,
                                                            {'abcdef12': 3}, {}, {'5': '1h'})['release'], 'CO #3 · abcdef12')
check('a retro estimate when nothing is recorded', T.backfill_plan(card, '', lambda s: None, {}, {}, {'5': '1h'})['raw'], (60, 'retro'))
check('only Estimate missing reads LOE original', T.backfill_plan(dict(card, original=1.5), '', None, {}, {}, {})['raw'], (90, 'board'))

print('the Client')
c = T.Client(tok=None, cfg=CFG)
check('the positive case: no token is a refusal, never a call', (c.issue(1), c.error.startswith('no GitHub token')), (None, True))
c = T.Client(tok=lambda: '', cfg=CFG)
check("a token callable answering '' is no token", (c.auth_ok(), c.repo), (False, 'acme/app'))

print('the command line helpers')
check('a body that is a path is said', T.body_is_path(['new', '--body', 'plan.md']), 'plan.md')
check('unknown flags are named', T._unknown_flags(['--body', 'x', '--bogus'], T.NEW_FLAGS), ['--bogus'])
out = io.StringIO()
gh = fresh({50: {'title': 'Export the monthly report as CSV'}})
check('a duplicate title is refused unless --new', (T.refused_as_duplicate('Export monthly report CSV', [], gh, out),
                                                     T.refused_as_duplicate('Export monthly report CSV', ['--new'], gh, out)),
      (True, False))

print(f'\n{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
