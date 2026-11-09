"""A fake GitHub for a host's suites of the ship's pull request (chuang_sdlc.ship_pr) and the shipped stage around it
(tickets.live_numbers), so no host keeps its own copy.

    from chuang_sdlc.testing import ShipPRs, Minimal, ShipFake, ship_fake, RELEASE

    gh = ship_fake()                        # pull request #900 from <prefix><RELEASE8>, merged by the push
    tickets.live_numbers([20], RELEASE, run=gh, work={20: [('aa11aa11', 'one')]})
    gh.links()                              # [['NODE-20']]: the closing references sent

    class HostGH(ShipPRs, HostFake): ...    # ShipPRs before a host's own fake, whose graphql answers the rest

Nothing here reaches a network. The documents answered are ship_pr's own and link_branch's (tickets.LINK_QUERY,
LINK_MUTATION); any other GraphQL document goes to the fake behind ShipPRs.

And for the scrum master's sweep, the board's reads and the claims (scrum, backlog, alerts):

    store = Records()                       # get / set / merge / create / delete on (collection, id)
    gh = Board({'status:"Building"': [{'number': 7, 'title': 't', 'light': 'busy', 'owner': 'Ann'}]},
               issues={7: {'id': 'G7', 'state': 'OPEN', 'blockedBy': {'nodes': []}, 'comments': {'nodes': []}}})
    scrum.sweep(cfg, scrum.GitHub(cfg, run=gh), store, mail)
    items_page(cards)                       # the board_items answer for these cards

And for the board's event listeners and a webhook's item route (listeners, webhooks):

    moves = Moves(cards={7: {'number': 7, 'column': 'Building', 'item': 'IT_7'}})
    listeners.configure(moves=moves, claims='claims')
"""
from . import ship_pr as P
from . import tickets as T

RELEASE = 'abcdef12' + '3' * 32   # a ship's head: its branch <prefix>abcdef12


class ShipPRs:
    """A mixin before a client fake (`class HostGH(ShipPRs, HostFake)`), whose `calls` list and `graphql` answer the
    rest: the ship branches and pull requests in memory, every GraphQL document ship_pr sends answered, every call
    recorded in `ship_calls` and, as ('ship', ...), in the fake's own `calls`, so the two orders read as one.
    `ship_setup()` first: ship_refs {name: oid}; prs {number: {number, head, state, merged, body, title}}; `on_github`
    the commits GitHub holds (None: every one); `refuse` the documents answered None (by name: 'ref', 'pr', 'link',
    'close', 'open' for the open pull requests' list, 'compare'); `refuse_nodes` issue node ids whose link is refused, a
    document carrying one refused whole, as a client that raises on any error answers; `in_base` the branches whose head
    the base holds (Ref.compare answers BEHIND for them, AHEAD for any other)."""

    def ship_setup(self, on_github=None, refuse=(), refuse_nodes=(), in_base=()):
        """The fake's ship state, empty: pull requests numbered from 900. Returns the fake."""
        self.ship_refs, self.prs, self.on_github, self.refuse = {}, {}, on_github, set(refuse)
        self.refuse_nodes, self.in_base = set(refuse_nodes), set(in_base)
        self.next_pr, self.ship_calls = 900, []
        return self

    def _log(self, entry):
        self.ship_calls.append(entry)
        self.calls.append(('ship',) + entry)

    def _refused(self, what):
        if what in self.refuse:
            self.error = f'GitHub /graphql: HTTP 200 ({what} refused)'
            return True
        return False

    def graphql(self, query, variables=None):
        """ship_pr's documents answered from memory; any other goes to the fake behind this one."""
        v = variables or {}
        if query == P.REPO_QUERY:
            self._log(('read', v['head']))
            known = self.on_github is None or v['sha'] in self.on_github
            name = v['ref'].removeprefix('refs/heads/')
            ref = {'id': 'REF-' + name, 'target': {'oid': self.ship_refs[name]}} if name in self.ship_refs else None
            nodes = [{'id': f'PR-{p["number"]}', 'number': p['number'], 'state': p['state'], 'merged': p['merged']}
                     for p in sorted(self.prs.values(), key=lambda p: -p['number']) if p['head'] == v['head']]
            return {'repository': {'id': 'REPO-ID', 'object': {'oid': v['sha']} if known else None, 'ref': ref,
                                   'pullRequests': {'nodes': nodes}}}
        if query == P.PR_QUERY:
            p = self.prs.get(v['number'])
            if not p:
                return {'repository': {'pullRequest': None}}
            head = {'id': 'REF-' + p['head'], 'name': p['head']} if p['head'] in self.ship_refs else None
            said = [{'body': c[2]} for c in self.calls if c[:2] == ('comment', p['number'])][-20:]   # comment()'s
            return {'repository': {'pullRequest': {'id': f'PR-{p["number"]}', 'number': p['number'], 'state': p['state'],
                                                   'merged': p['merged'], 'body': p['body'], 'headRef': head,
                                                   'comments': {'nodes': said}}}}
        if query == P.CREATE_REF:
            self._log(('create_ref', v['name'], v['oid']))
            if self._refused('ref'):
                return None
            branch = v['name'].removeprefix('refs/heads/')
            self.ship_refs[branch] = v['oid']
            return {'createRef': {'ref': {'id': 'REF-' + branch}}}   # the id REPO_QUERY and PR_QUERY answer for it
        if query == P.OPEN_QUERY:
            self._log(('open_prs', v['base']))
            if self._refused('open'):
                return None
            nodes = [{'number': p['number'], 'headRefName': p['head'], 'headRefOid': self.ship_refs.get(p['head'])}
                     for p in sorted(self.prs.values(), key=lambda p: -p['number']) if p['state'] == 'OPEN']
            return {'repository': {'pullRequests': {'nodes': nodes}}}
        if query == P.COMPARE_QUERY:
            self._log(('compare', v['base'], v['head']))
            if self._refused('compare'):
                return None
            status = 'BEHIND' if v['head'] in self.in_base else 'AHEAD'
            return {'repository': {'ref': {'compare': {'status': status}}}}
        if query == P.CREATE_PR:
            self._log(('create_pr', v['base'], v['head'], v['title'], v['body']))
            if self._refused('pr'):
                return None
            n, self.next_pr = self.next_pr, self.next_pr + 1
            self.prs[n] = {'number': n, 'head': v['head'], 'state': 'OPEN', 'merged': False, 'body': v['body'], 'title': v['title']}
            return {'createPullRequest': {'pullRequest': {'id': f'PR-{n}', 'number': n}}}
        if query == P.CLOSE_PR:
            self._log(('close_pr', v['id']))
            if self._refused('close'):
                return None
            self.prs[int(v['id'].split('-')[1])]['state'] = 'CLOSED'
            return {'closePullRequest': {'pullRequest': {'state': 'CLOSED'}}}
        if query == P.DELETE_REF:
            self._log(('delete_ref', v['id']))
            self.ship_refs.pop(v['id'].removeprefix('REF-'), None)
            return {'deleteRef': {'clientMutationId': None}}
        if query == P.UPDATE_PR:
            self._log(('update_pr', v['id'], v['body']))
            self.prs[int(v['id'].split('-')[1])]['body'] = v['body']
            return {'updatePullRequest': {'pullRequest': {'number': int(v['id'].split('-')[1])}}}
        if query.startswith('mutation($pr: ID!'):   # link_document: aliased addCloseIssueReferences
            issues = [v[k] for k in sorted((k for k in v if k != 'pr'), key=lambda k: int(k[1:]))]
            self._log(('link', v['pr'], issues))
            if self._refused('link'):
                return None
            if set(issues) & self.refuse_nodes:
                self.error = 'GitHub /graphql: HTTP 200 (one issue refused)'
                return None
            return {f'l{k}': {'issue': {'number': int(node.rsplit('-', 1)[1])}} for k, node in enumerate(issues)}
        return super().graphql(query, variables)

    def links(self) -> list:
        """Every link document sent: the issue node ids it carried."""
        return [c[2] for c in self.ship_calls if c[0] == 'link']


class Minimal:
    """The ticket client's other methods, recorded in `calls` (verb first): every issue open unless `closed` names it,
    node ids NODE-<n>, items ITEM-<n>, every write taken; link_branch's documents answered (every commit resolves, no
    branch yet), any other GraphQL document {}."""

    def __init__(self, closed=()):
        self.calls, self.closed, self.error = [], set(closed), ''

    def node_id(self, number):
        """`NODE-<n>`."""
        return f'NODE-{number}'

    def item_add(self, node):
        """`ITEM-<n>` for `NODE-<n>`."""
        return 'ITEM-' + node.rsplit('-', 1)[1]

    def item_field(self, item, field_id, option_id=None, text=None, clear=False):
        """One field write, taken."""
        self.calls.append(('item_field', item, field_id, option_id))
        return True

    def issue(self, number):
        """#number, open unless `closed` names it."""
        return {'number': number, 'title': f'Issue {number}', 'state': 'CLOSED' if number in self.closed else 'OPEN',
                'url': '', 'pr': False}

    def close_issue(self, number, reason='completed', comment=''):
        """The close, taken."""
        self.calls.append(('close_issue', number))
        self.closed.add(number)
        return True

    def comment(self, number, body):
        """A comment, taken."""
        self.calls.append(('comment', number, body))
        return True

    def add_labels(self, number, labels):
        """Labels, taken."""
        self.calls.append(('add_labels', number, list(labels)))
        return True

    def create_label(self, name, color, description=''):
        """A label made."""
        self.calls.append(('create_label', name))
        return True

    def graphql(self, query, variables=None):
        """link_branch's documents answered (the commit resolves, no branch yet); any other {}."""
        v = variables or {}
        if query == T.LINK_QUERY:
            return {'repository': {'id': 'REPO-ID', 'object': {'oid': v['sha']}, 'ref': None}}
        if query == T.LINK_MUTATION:
            self.calls.append(('link_branch', v['issue'], v['name']))
            return {'createLinkedBranch': {'linkedBranch': {'id': 'LB-' + v['name']}}}
        self.calls.append(('graphql', query[:40]))
        return {}


class ShipFake(ShipPRs, Minimal):
    """The ticket client with the ship's pull requests: Minimal behind ShipPRs."""


def ship_fake(closed=(), refuse=(), refuse_nodes=(), release: str = RELEASE):
    """A client with the ship's pull request #900 opened from the branch of `release` (ship_pr.branch_of) and merged
    by the push; `closed` the issues closed already, `refuse` and `refuse_nodes` as ship_setup takes them."""
    gh = ShipFake(closed).ship_setup(refuse=refuse, refuse_nodes=refuse_nodes)
    branch = P.branch_of(release)
    gh.ship_refs[branch] = release
    gh.prs[900] = {'number': 900, 'head': branch, 'state': 'MERGED', 'merged': True, 'body': '', 'title': 't'}
    return gh


class Records:
    """A record store in memory, the shape chuang_sdlc's callers take (get, set, merge, create, delete on
    (collection, id)): `docs` {(collection, id): doc}; `create` answers False for a record already held."""

    def __init__(self, docs=None):
        self.docs = dict(docs or {})

    def get(self, collection, doc_id):
        """The record, or None."""
        return self.docs.get((collection, str(doc_id)))

    def set(self, collection, doc_id, doc):
        """The record replaced."""
        self.docs[(collection, str(doc_id))] = dict(doc)

    def merge(self, collection, doc_id, doc):
        """The fields merged into the record."""
        self.docs[(collection, str(doc_id))] = dict(self.get(collection, doc_id) or {}, **doc)

    def create(self, collection, doc_id, doc) -> bool:
        """The record made unless one is held: True when this call made it."""
        if (collection, str(doc_id)) in self.docs:
            return False
        self.set(collection, doc_id, doc)
        return True

    def delete(self, collection, doc_id):
        """The record gone, held or not."""
        self.docs.pop((collection, str(doc_id)), None)


def items_page(cards, owner_type: str = 'organization') -> dict:
    """The GraphQL answer to tickets.board_items' query for these cards, one page: each {number (None for a draft),
    title, status, light, owner, priority, labels, url, type ('Issue' or 'DraftIssue'), draft (a draft's own id), body}."""
    nodes = []
    for c in cards:
        draft = c.get('number') is None
        content = ({'__typename': 'DraftIssue', 'id': c.get('draft', ''), 'title': c.get('title', ''), 'body': c.get('body')}
                   if draft else
                   {'__typename': c.get('type', 'Issue'), 'number': c['number'], 'title': c.get('title', ''),
                    'url': c.get('url', ''), 'labels': {'nodes': [{'name': n} for n in c.get('labels', ())]}})
        node = {'id': c.get('id') or (f"IT_{c['number']}" if not draft else 'DRAFT'), 'content': content}
        for key in ('status', 'light', 'owner', 'priority', 'area'):
            if c.get(key):
                node[key] = {'name': c[key]}
                if c.get(f'{key}_at'):   # 0.14.0: a card's status_at / light_at as the single select's updatedAt
                    node[key]['updatedAt'] = c[f'{key}_at']
        nodes.append(node)
    return {owner_type: {'projectV2': {'items': {'pageInfo': {'hasNextPage': False}, 'nodes': nodes}}}}


class Board:
    """A ticket client for the scrum master's sweep and the board's reads (scrum.sweep, tickets.board_items, move,
    ticket_place, set_fields), in memory: `columns` {GitHub filter substring: [card]} answers board_items by the first
    key its filter contains ('' for any); `issues` {number: CARD_QUERY's issue}; `places` {number: (column, light)}.
    Every write is recorded in `writes` as (verb, ...); `refuse` names verbs answered False ('fields', 'comment')."""

    def __init__(self, columns=None, issues=None, places=None, refuse=()):
        self.columns, self.issues_, self.places = dict(columns or {}), dict(issues or {}), dict(places or {})
        self.refuse, self.writes, self.queries, self.error = set(refuse), [], [], ''

    def node_id(self, number):
        """An issue's node id, `I_<n>`."""
        return f'I_{number}'

    def item_add(self, node):
        """The issue's Project item, `IT_<n>`."""
        return 'IT_' + node.split('_', 1)[1]

    def item_fields(self, item, fields):
        """Fields written in one request, recorded as ('fields', item, {field id: value})."""
        self.writes.append(('fields', item, dict(fields)))
        return 'fields' not in self.refuse

    def item_field(self, item, field_id, option_id=None, text=None, clear=False):
        """One field, recorded as item_fields records it."""
        return self.item_fields(item, {field_id: None if clear else ({'text': text} if text is not None else option_id)})

    def item_top(self, item):
        """A card to the top of its column, recorded."""
        self.writes.append(('top', item))
        return True

    def comment(self, number, body):
        """A comment, recorded."""
        self.writes.append(('comment', int(number), body))
        return 'comment' not in self.refuse

    def edit_comment(self, comment_id, body):
        """A comment's body replaced, recorded."""
        self.writes.append(('edit', int(comment_id), body))
        return True

    def assign(self, number, login, on=True):
        """An assignee added or taken off, recorded."""
        self.writes.append(('assign' if on else 'unassign', int(number), login))
        return True

    def add_labels(self, number, labels):
        """Labels added, recorded."""
        self.writes.append(('label', int(number), list(labels)))
        return True

    def remove_label(self, number, label):
        """A label taken off, recorded."""
        self.writes.append(('unlabel', int(number), label))
        return True

    def graphql(self, query, variables=None):
        """board_items' query by its filter, CARD_QUERY, ticket_place's item read, the blocked-by link and the draft
        edit; anything else answers {}."""
        from . import scrum as S
        v = variables or {}
        if 'projectV2(number:' in query:
            q = v.get('q') or ''
            self.queries.append(q)
            key = next((k for k in self.columns if k and k in q), '' if '' in self.columns else None)
            return items_page(self.columns.get(key, []) if key is not None else [],
                              'user' if 'user(login:' in query else 'organization')
        if query == S.CARD_QUERY:
            return {'repository': {'issue': self.issues_.get(int(v['n']))}}
        if 'fieldValueByName(name: "Status")' in query and 'node(id:' in query:
            n = int(v['id'].split('_', 1)[1])
            col, light = self.places.get(n, ('', ''))
            return {'node': {'id': v['id'], 'status': {'name': col}, 'light': {'name': light}}}
        if query == S.LINK_MUTATION:
            self.writes.append(('link', v['i'], v['b']))
            return {'addBlockedBy': {'issue': {'number': 0}}}
        if 'updateProjectV2DraftIssue' in query:
            self.writes.append(('retitle', v.get('d'), v.get('t'), v.get('b')))
            return {'updateProjectV2DraftIssue': {'draftIssue': {'id': v.get('d')}}}
        return {}


class Moves:
    """A host's board helpers for chuang_sdlc.listeners and webhooks.item_route, in memory: a card's node is
    {id, content: {number, title}, status: {name, updatedAt}, light: {name}, owner: {name}}; the columns by role are
    `stages`; `cards` {number: closed_card's answer}; `fix` the light light_fix answers (None leaves it); every write is
    recorded in `writes` as (verb, ...), and `refuse` names verbs that raise RuntimeError('HTTP 502')."""

    STARTS, STARTED_MINUTES, SENDER = 'starts', 20, 'Board'
    BOARD_MOVES, SCRUM_SENDER = 'board_moves', 'Scrum master'

    def __init__(self, stages=None, cards=None, fix=None, refuse=(), estimate=None):
        self.stages = dict(stages or {'backlog': 'Backlog', 'shipyard': 'Building', 'dock': 'Ready', 'shipped': 'Shipped',
                                      'failed': 'Wrecked', 'app_status': 'Apps'})
        self.cards, self.fix, self.refuse, self.estimate, self.writes = dict(cards or {}), fix, set(refuse), estimate, []

    def _write(self, verb, *args):
        if verb in self.refuse:
            raise RuntimeError('HTTP 502')
        self.writes.append((verb,) + args)

    def number(self, node):
        """The card's issue number (None for a draft)."""
        return ((node or {}).get('content') or {}).get('number')

    def title(self, node):
        """The card's title."""
        return ((node or {}).get('content') or {}).get('title') or ''

    def column(self, node):
        """The card's column."""
        return ((node or {}).get('status') or {}).get('name') or ''

    def stage(self, role):
        """A column by its role."""
        return self.stages[role]

    def newly_in(self, data, role):
        """A Status move into the role's column from another."""
        col = self.stage(role)
        return ((data or {}).get('field_name') or 'Status') == 'Status' and self.column((data or {}).get('node')) == col \
            and ((data or {}).get('from') or '') != col

    def light_fix(self, data):
        """The light this move should leave (`fix`), None to leave it."""
        return self.fix

    def set_light(self, item, value, token):
        """The card's Light written."""
        self._write('light', item, value)

    def wake_letter(self, node, by):
        """(to, text, subject): the card's Owner, else 'Captain'."""
        n = self.number(node)
        return (((node or {}).get('owner') or {}).get('name') or 'Captain', f'{by or "Someone"} moved #{n}', f'#{n} to Building')

    def to_dock(self, numbers, token):
        """Each ticket to the dock column, a line each ('not moved' for a number whose `dock-<n>` is in `refuse`)."""
        return [f'#{n}: not moved (HTTP 502)' if f'dock-{n}' in self.refuse else f'#{n}: Dock, warming' for n in numbers]

    def closed_card(self, number, token):
        """The closed card (`cards`), {} off the board; RuntimeError when 'read' is refused."""
        if 'read' in self.refuse:
            raise RuntimeError('HTTP 502')
        return self.cards.get(int(number), {})

    def is_finished(self, col):
        """The ship or the failed column."""
        return col in (self.stage('shipped'), self.stage('failed'))

    def is_unplanned(self, reason):
        """Closed as not planned or a duplicate."""
        return str(reason or '').lower() in ('not_planned', 'duplicate')

    def closed_key(self, number, closed_at):
        """One close's claim id."""
        return f'ticket-closed-{number}-{closed_at or "now"}'

    def to_unplanned(self, item, token):
        """The card to the failed column, no Light."""
        self._write('unplanned', item)

    def to_shipped(self, item, token):
        """The card to the ship column."""
        self._write('shipped', item)

    def unplanned_line(self, card, reason):
        """(to, text, subject) of the one line about a close as not planned."""
        return card.get('owner') or 'Captain', f"#{card.get('number')} closed as {reason}", 'Closed'

    def closed_letter(self, card):
        """(to, text, subject) of the letter about any other close."""
        return card.get('owner') or 'Captain', f"#{card.get('number')} was closed: done?", 'Closed'

    def item_for(self, number, token, opener=None):
        """The ticket's item id, `IT_<n>`."""
        return f'IT_{number}'

    def read_item(self, item, token, opener=None):
        """The item's node."""
        return {'id': item}

    def estimate_minutes(self, node):
        """The card's estimate (`estimate`)."""
        return self.estimate

    def set_field(self, item, field, value, token, opener=None):
        """One field written."""
        self._write('field', item, field, value)

    def is_placeholder(self, title):
        """A placeholder card's title: `— … —`."""
        return title.startswith('— ') and title.endswith(' —')

    def is_ship_card(self, node):
        """The ship card: a draft titled with SHIP."""
        return not self.number(node) and 'SHIP' in self.title(node)
