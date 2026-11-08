"""One pull request a ship, so each shipped ticket's Development shows the release that shipped it.

The shape:

  - at the ship, before its push of the base branch, `open_for` makes the branch `<prefix><head8>` at the ship's head
    (GitHub's createRef; a head GitHub does not hold yet is pushed there by the caller's `push`) and opens one pull
    request from it into the base, labelled; its title and body go through `no_closing_keyword`, so the merge closes
    nothing;
  - the push of the base is the merge: GitHub marks a pull request merged when its head is in the base. GitHub's merge
    API is never called, since every merge mode writes a commit of its own to the base, a second deploy;
  - a ship whose push is refused closes its pull request unmerged (`close_unshipped`, or `close_head` by its head when
    the open's answer was never read) and deletes the branch;
  - once the release's production smoke passes, the tickets close first (tickets.live_numbers), then `link_after_live`
    adds the pull request as each ticket's closing reference (addCloseIssueReferences, aliased, BATCH to a document: a
    closed issue lists the merged pull request in its Development, and a reopened one stays open), so a ticket still
    closes only after the smoke, and `finish` comments the result and deletes the branch; a wrecked release comments the
    wreck instead (`wrecked`). Only a ticket that is closed is linked, and only a pull request that is open or merged is
    found: one a refused ship closed unmerged is never read as the release's.

Every call goes through the ticket tools' GitHub client (tickets.Client, `run=`: graphql, node_id, comment, add_labels,
create_label), a suite's fake standing in; nothing here raises on GitHub's refusal, it answers None, False or a WARNING
line. Standard library alone, no subprocess: a git push fallback is the caller's (`push=`).

    configure(prefix='ship/', base='main', label='ship')    the host's branch prefix, base branch and label
"""
import re

from . import tickets as T

_SET = {
    'prefix': 'ship/',     # the ship's branch: <prefix><head8>
    'base': 'main',        # the branch a ship pushes, the pull request's base
    'label': 'ship',
    'label_color': '0E8A16',
    'label_description': 'One ship: its pull request into the base branch',
}
BATCH = 10   # aliased addCloseIssueReferences calls in one document, our own choice (GitHub's "Limit: 10" is pullRequestIds
#              per call; each call here sends one pull request)


def configure(**settings) -> None:
    """The host's settings: `prefix` (the ship branch's, `ship/`), `base` (`main`), `label` (`ship`), `label_color`,
    `label_description`. Unknown names are refused."""
    for name, value in settings.items():
        if name not in _SET:
            raise TypeError(f'ship_pr.configure() takes no {name!r}')
        _SET[name] = value


def setting(name: str) -> str:
    """One of the host's settings (configure)."""
    return _SET[name]


# GitHub's closing keywords before an issue reference (close, closes, closed, fix, fixes, fixed, resolve, resolves,
# resolved, any case, an optional colon, any spacing or none), the reference `#12`, `owner/repo#12`, `GH-12` or the
# issue's URL (http or https): in a pull request into the default branch, the merge closes it. `keep` is everything up
# to the number, `num` the number.
CLOSING = re.compile(r'(?P<keep>\b(?:close|fix|resolve)(?:s|d|es|ed)?:?\s*'
                     r'(?:[\w.-]+/[\w.-]+#|#|GH-|https?://github\.com/[\w.-]+/[\w.-]+/issues/))(?P<num>\d+)', re.I)
BREAK = '​'   # a zero-width space: shown as nothing, and no reference GitHub parses has one before its number


def no_closing_keyword(text: str) -> str:
    """`text` with every issue reference that follows a closing keyword broken: a zero-width space (BREAK) between the
    `#`, `GH-` or `/issues/` and the number, so the text reads the same and GitHub finds no reference after the keyword.
    A ship's pull request merges at the push, before the production smoke, and its tickets close only once the smoke
    passes, so a ticket titled "Fix #12 seeding" must never close #12. The reference itself is broken, not wrapped in a
    code span: a span depends on GitHub skipping code for references, and a title with its own backticks can leave a
    wrap unbalanced. Inside code spans too; a second run changes nothing. It rests on GitHub's references needing the
    number right after `#`, `GH-` or `/issues/`."""
    return CLOSING.sub(lambda m: m['keep'] + BREAK + m['num'], text or '')


REPO_QUERY = ('query($owner: String!, $name: String!, $sha: String!, $ref: String!, $head: String!) { '
              'repository(owner: $owner, name: $name) { id object(expression: $sha) { oid } '
              'ref(qualifiedName: $ref) { id target { oid } } '
              'pullRequests(headRefName: $head, first: 5, states: [OPEN, MERGED, CLOSED], '
              'orderBy: {field: CREATED_AT, direction: DESC}) { nodes { id number state merged } } } }')
PR_QUERY = ('query($owner: String!, $name: String!, $number: Int!) { repository(owner: $owner, name: $name) { '
            'pullRequest(number: $number) { id number state merged body headRef { id name } '
            'comments(last: 20) { nodes { body } } } } }')
CREATE_REF = ('mutation($repo: ID!, $name: String!, $oid: GitObjectID!) { createRef(input: {repositoryId: $repo, '
              'name: $name, oid: $oid}) { ref { id } } }')
CREATE_PR = ('mutation($repo: ID!, $base: String!, $head: String!, $title: String!, $body: String!) { '
             'createPullRequest(input: {repositoryId: $repo, baseRefName: $base, headRefName: $head, title: $title, '
             'body: $body, draft: false}) { pullRequest { id number } } }')
CLOSE_PR = 'mutation($id: ID!) { closePullRequest(input: {pullRequestId: $id}) { pullRequest { state } } }'
DELETE_REF = 'mutation($id: ID!) { deleteRef(input: {refId: $id}) { clientMutationId } }'
UPDATE_PR = ('mutation($id: ID!, $body: String!) { updatePullRequest(input: {pullRequestId: $id, body: $body}) { '
             'pullRequest { number } } }')


def _repo(cfg) -> tuple:
    return tuple(cfg['repo'].split('/', 1))


def branch_of(head: str) -> str:
    """The ship's branch for its head: `<prefix><head8>`."""
    return f"{_SET['prefix']}{head[:8]}"


def _why(run) -> str:
    return (getattr(run, 'error', '') or 'no answer').strip()[:160]


def _call(run, verb: str, *args):
    """One of the client's verbs (graphql, comment, add_labels, create_label, node_id); None when it raised or the
    client has no such verb (an older fake), the reason kept in `run.error` where it can be: this module never raises."""
    try:
        return getattr(run, verb)(*args)
    except Exception as e:   # noqa: BLE001 - a ship's pull request never stops a ship or a release's close
        try:
            run.error = f'{type(e).__name__}: {str(e)[:120]}'
        except Exception:   # noqa: BLE001
            pass
        return None


def _gql(run, query: str, variables: dict):
    """One GraphQL document's `data` through _call; None on any refusal."""
    return _call(run, 'graphql', query, variables)


def _repository(head: str, run, cfg) -> dict:
    """The repository's id, `head` resolved, the ship branch and the pull requests from it, newest first; {} unanswered."""
    owner, name = _repo(cfg)
    branch = branch_of(head)
    data = _gql(run, REPO_QUERY, {'owner': owner, 'name': name, 'sha': head, 'ref': f'refs/heads/{branch}', 'head': branch})
    return ((data or {}).get('repository') or {}) if isinstance(data, dict) else {}


def _pull(number: int, run, cfg) -> dict:
    """One pull request {id, number, state, merged, body, headRef, comments: its last 20}; {} when GitHub does not
    answer."""
    owner, name = _repo(cfg)
    data = _gql(run, PR_QUERY, {'owner': owner, 'name': name, 'number': int(number)})
    return (((data or {}).get('repository') or {}).get('pullRequest') or {}) if isinstance(data, dict) else {}


def _delete_branch(pull: dict, run) -> bool:
    """The pull request's head branch deleted (deleteRef); True when there was none left to delete."""
    ref = (pull.get('headRef') or {}).get('id')
    if not ref:
        return True
    got = _gql(run, DELETE_REF, {'id': ref})
    return isinstance(got, dict) and 'deleteRef' in got


def _usable(p: dict) -> bool:
    """A ship pull request the release may name: merged, or still open (GitHub not yet marking the push's merge). One
    closed unmerged is a refused ship's (close_unshipped), never the release's, even when the same head reached the base
    another way afterwards."""
    return bool(p.get('number')) and (bool(p.get('merged')) or p.get('state') == 'OPEN')


def find(release: str, run=None, cfg: dict = None):
    """The number of the pull request a ship opened for its head `release` (from `<prefix><release8>`, merged or open,
    the newest; never one closed unmerged), or None: a push with no ship pull request, a ship whose pull request was
    closed when its push was refused, GitHub not answering."""
    if not release:
        return None
    run, cfg = T._run(run), T._cfg(cfg)
    nodes = (_repository(release, run, cfg).get('pullRequests') or {}).get('nodes') or []
    return next((p['number'] for p in nodes if _usable(p)), None)


def open_for(base: str, head: str, title: str, body: str, run=None, cfg: dict = None, push=None) -> tuple:
    """The ship's pull request before its push: (its number or None, the line `ship pr #P: ...`). An open one from
    `<prefix><head8>` is reused (a ship run again on the same head); else the branch is made at `head` (createRef; a
    head GitHub does not hold, or a refused createRef, goes through `push(branch, head)`, the caller's git push, when
    given) and the pull request opened into the base, not a draft, labelled (the label made when the repository lacks
    it). The title and body go through no_closing_keyword here. `base` is the ship's base commit, kept for the
    caller's line. Never raises: a refusal is a WARNING line and (None, line), and the ship goes on without one."""
    run, cfg = T._run(run), T._cfg(cfg)
    branch, into = branch_of(head), _SET['base']
    repo = _repository(head, run, cfg)
    if not repo.get('id'):
        return None, f'WARNING: ship pr not opened (the repository was not read: {_why(run)})'
    for p in (repo.get('pullRequests') or {}).get('nodes') or []:
        if p.get('state') == 'OPEN':
            return p['number'], f'ship pr #{p["number"]}: reused, open from {branch} into {into}'
    oid = (repo.get('object') or {}).get('oid')
    ref = repo.get('ref') or {}
    if ref.get('id'):
        if oid and (ref.get('target') or {}).get('oid') != oid:
            return None, f'WARNING: ship pr not opened ({branch} exists at another commit)'
    else:
        made = oid and isinstance(_gql(run, CREATE_REF, {'repo': repo['id'], 'name': f'refs/heads/{branch}', 'oid': oid}), dict)
        if not made and not (push and push(branch, head)):
            why = f'createRef refused, {_why(run)}' if oid else f'{head[:8]} is not on GitHub and no push made it'
            return None, f'WARNING: ship pr not opened ({branch} was not made: {why})'
    got = _gql(run, CREATE_PR, {'repo': repo['id'], 'base': into, 'head': branch,
                                'title': no_closing_keyword(title), 'body': no_closing_keyword(body)})
    made_pr = ((got or {}).get('createPullRequest') or {}).get('pullRequest') if isinstance(got, dict) else None
    number = (made_pr or {}).get('number')
    if not number:
        return None, f'WARNING: ship pr not opened (createPullRequest refused: {_why(run)})'
    label = _SET['label']
    labelled = _call(run, 'add_labels', number, [label])
    if not labelled:
        _call(run, 'create_label', label, _SET['label_color'], _SET['label_description'])
        labelled = _call(run, 'add_labels', number, [label])
    return number, f'ship pr #{number}: opened from {branch} into {into}' + ('' if labelled else f' (WARNING: not labelled {label})')


def close_unshipped(pr: int, why: str, run=None, cfg: dict = None) -> str:
    """A ship whose push the base refused: its pull request commented with why, closed unmerged and its branch
    deleted, so the next ship opens its own. A merged one is left (the base took the head after all). The line."""
    run, cfg = T._run(run), T._cfg(cfg)
    pull = _pull(pr, run, cfg)
    if not pull.get('id'):
        return f'WARNING: ship pr #{pr} not closed (not read: {_why(run)})'
    if pull.get('merged'):
        return f"ship pr #{pr}: merged already ({_SET['base']} holds its head), left as it is"
    if pull.get('state') == 'OPEN':
        _call(run, 'comment', int(pr), f'Not shipped: {why}. Closed unmerged; the next ship opens its own pull request.')
        got = _gql(run, CLOSE_PR, {'id': pull['id']})
        if not (isinstance(got, dict) and got.get('closePullRequest')):
            return f'WARNING: ship pr #{pr} not closed ({_why(run)})'
    gone = _delete_branch(pull, run)
    return f'ship pr #{pr}: closed unmerged' + ('' if gone else f' (WARNING: its branch was not deleted: {_why(run)})')


def close_head(head: str, why: str, run=None, cfg: dict = None) -> str:
    """A refused ship's pull request closed by its head, not its number: every open one from `<prefix><head8>` through
    close_unshipped. For a ship whose open never answered (killed at its cap after GitHub made the pull request), so no
    pull request is left open behind a refused push. The line."""
    run, cfg = T._run(run), T._cfg(cfg)
    branch = branch_of(head)
    repo = _repository(head, run, cfg)
    if not repo.get('id'):
        return f'WARNING: ship pr from {branch} not closed (the repository was not read: {_why(run)})'
    opened = [p['number'] for p in (repo.get('pullRequests') or {}).get('nodes') or [] if p.get('state') == 'OPEN' and p.get('number')]
    if not opened:
        return f'ship pr: none open from {branch}, nothing to close'
    return '; '.join(close_unshipped(n, why, run, cfg) for n in opened)


def link_document(count: int) -> str:
    """The aliased mutation for `count` closing references to one pull request: `l0: addCloseIssueReferences(...)`."""
    names = ', '.join(f'$i{k}: ID!' for k in range(count))
    body = ' '.join(f'l{k}: addCloseIssueReferences(input: {{issueId: $i{k}, pullRequestIds: [$pr]}}) {{ issue {{ number }} }}'
                    for k in range(count))
    return f'mutation($pr: ID!, {names}) {{ {body} }}'


def link_after_live(pr: int, numbers, run=None, cfg: dict = None) -> set:
    """Each ticket's Development shows the ship's pull request, added after the ticket closed: the pull request as the
    issue's closing reference (addCloseIssueReferences), BATCH an aliased document. The numbers linked. A GraphQL client
    that raises on any error drops `data`, though GitHub runs a document's mutations one by one and an error nulls only
    its own field, so a refused batch may have linked the rest: each of its tickets is sent again in a document of its
    own (a link GitHub already holds is taken again as a no-op), and only the ones still refused are left for the caller
    to link another way. The caller passes only tickets that are closed."""
    run, cfg = T._run(run), T._cfg(cfg)
    numbers = [int(n) for n in numbers or []]
    pull = _pull(pr, run, cfg) if pr and numbers else {}
    if not pull.get('id'):
        return set()
    nodes = [(n, _call(run, 'node_id', n)) for n in numbers]
    nodes = [(n, node) for n, node in nodes if node]
    linked = set()
    for start in range(0, len(nodes), BATCH):
        chunk = nodes[start:start + BATCH]
        variables = {'pr': pull['id'], **{f'i{k}': node for k, (_n, node) in enumerate(chunk)}}
        got = _gql(run, link_document(len(chunk)), variables)
        if isinstance(got, dict):
            linked |= {n for k, (n, _node) in enumerate(chunk) if ((got.get(f'l{k}') or {}).get('issue'))}
            continue
        for n, node in (chunk if len(chunk) > 1 else []):   # the refused batch, one ticket a document
            one = _gql(run, link_document(1), {'pr': pull['id'], 'i0': node})
            if isinstance(one, dict) and (one.get('l0') or {}).get('issue'):
                linked.add(n)
    return linked


LIVE_WORD, WRECK_WORD = 'Live:', 'Wrecked:'   # the openings of finish's and wrecked's one comment each


def _told(pull: dict, word: str) -> bool:
    """Whether the pull request was told its end already: one of its last comments opens with `word`, so a second run
    of the same release (a retried event, a rerun by hand) posts nothing new. Its branch is never read for this: a host
    whose repository deletes head branches on merge has a merged pull request with no branch before its one comment."""
    return any(str((c or {}).get('body') or '').startswith(word) for c in ((pull.get('comments') or {}).get('nodes') or []))


def finish(pr: int, numbers, release: str = '', run=None, cfg: dict = None) -> str:
    """The ship's pull request once its release is live: merged read (a WARNING when GitHub has not marked it, whose
    head is then not in the base), one comment naming the tickets closed, the branch deleted; nothing again when it was
    finished already (_told: a `Live:` comment among its last ones). The line's tail,
    `; ship pr #P: ...`."""
    run, cfg = T._run(run), T._cfg(cfg)
    pull = _pull(pr, run, cfg)
    if not pull.get('id'):
        return f'; WARNING: ship pr #{pr} not finished (not read: {_why(run)})'
    if _told(pull, LIVE_WORD):
        return f'; ship pr #{pr}: live (finished already)'
    names = ', '.join(f'#{n}' for n in numbers) or 'none'
    said = f'{LIVE_WORD} production smoke passed; tickets closed: {names}.' + (f' Release {release[:8]}.' if release else '')
    _call(run, 'comment', int(pr), said)
    gone = _delete_branch(pull, run)
    return (f'; ship pr #{pr}: live' + ('' if pull.get('merged') else ' (WARNING: GitHub has not marked it merged)')
            + ('' if gone else ' (its branch was not deleted)'))


def wrecked(pr: int, note: str, run=None, cfg: dict = None) -> str:
    """A release whose production smoke failed: its pull request commented `Wrecked: <note>` (the components and the
    bug), and its branch deleted, the head being in the base already; nothing again when it was told already (_told: a
    `Wrecked:` comment among its last ones). The blamed tickets stay open with no link. The line's tail."""
    run, cfg = T._run(run), T._cfg(cfg)
    pull = _pull(pr, run, cfg)
    if not pull.get('id'):
        return f'; WARNING: ship pr #{pr} not told of the wreck (not read: {_why(run)})'
    if _told(pull, WRECK_WORD):
        return f'; ship pr #{pr}: wrecked (told already)'
    _call(run, 'comment', int(pr), f'{WRECK_WORD} {note}')
    _delete_branch(pull, run)
    return f'; ship pr #{pr}: wrecked'


def note_change_order(pr: int, co: int, run=None, cfg: dict = None) -> bool:
    """The ship's change order named at the top of its pull request's body (edited once, after the push files the
    change order); True when it is there."""
    run, cfg = T._run(run), T._cfg(cfg)
    pull = _pull(pr, run, cfg)
    if not pull.get('id'):
        return False
    head = f'**Change order #{co}**'
    body = pull.get('body') or ''
    if body.startswith(head):
        return True
    got = _gql(run, UPDATE_PR, {'id': pull['id'], 'body': f'{head}\n\n{body}'})
    return isinstance(got, dict) and bool(got.get('updatePullRequest'))
