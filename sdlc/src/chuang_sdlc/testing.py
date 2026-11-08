"""A fake GitHub for a host's suites of the ship's pull request (chuang_sdlc.ship_pr) and the shipped stage around it
(tickets.live_numbers), so no host keeps its own copy.

    from chuang_sdlc.testing import ShipPRs, Minimal, ShipFake, ship_fake, RELEASE

    gh = ship_fake()                        # pull request #900 from <prefix><RELEASE8>, merged by the push
    tickets.live_numbers([20], RELEASE, run=gh, work={20: [('aa11aa11', 'one')]})
    gh.links()                              # [['NODE-20']]: the closing references sent

    class HostGH(ShipPRs, HostFake): ...    # ShipPRs before a host's own fake, whose graphql answers the rest

Nothing here reaches a network. The documents answered are ship_pr's own and link_branch's (tickets.LINK_QUERY,
LINK_MUTATION); any other GraphQL document goes to the fake behind ShipPRs.
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
