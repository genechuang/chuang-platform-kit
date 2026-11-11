"""What shipped, in its tickets' own words: the tickets a range of commits names, read from GitHub, each as the line a
reader sees.

A commit names its work by its `Ticket:` trailers (chuang_sdlc.board.ticket_trailers), and a player-facing ticket's
title is already the sentence its readers read, so a release's notes are those titles, word for word: no model call,
nothing that can be refused. A ticket's kind and Area are read from its labels (the board's kind labels, `labels`
{'Bug': ...}, and an `area:<Area>` label, tickets.area_label), over GitHub's REST API, so a bare runner reads them with
no Project scope.

    configure(area='Product', tail=r'\\s*\\(B\\d+\\)\\s*$', token=my_token, mapped=lambda: old_commit_map)
    got = shipped(commits)    # {'new': [lines], 'fixed': [lines], 'behind': N, 'unknown': [numbers]}

The repository is the board's (`repo`, through chuang_sdlc.tickets' configure(board=)) unless configure(repo=) names
one; the member-title label is tickets.label('member_title'). Standard library and the kit's github client only.
"""
import re
from concurrent.futures import ThreadPoolExecutor

from . import board as B
from . import tickets as T

_SET = {
    'repo': '',         # 'owner/name'; '' for the board's own `repo`
    'area': '',         # the Area whose tickets are the readers' lines (its `area:` label); none by default
    'bug': '',          # the bug kind's label; '' for the board's `labels` {'Bug': ...}
    'tail': None,       # a regex for a tail a title may carry that is no reader's (an old backlog's number), or None
    'fixed': 'Fixed: ',     # the words a bug's line opens with
    'token': None,      # () -> the GitHub token; the kit's github.token (the environment's, else a signed-in gh's)
    'mapped': None,     # () -> {sha prefix: [numbers]} for commits from before the trailer rule; {} when None
    'workers': 8,       # the tickets read side by side
}
TIMEOUT = 10


def configure(**settings) -> None:
    """The host's settings: `repo`, `area`, `bug`, `tail`, `fixed`, `token`, `mapped`, `workers` (the module's _SET says
    what each is). Unknown names are refused."""
    for name, value in settings.items():
        if name not in _SET:
            raise TypeError(f'titles.configure() takes no {name!r}')
        _SET[name] = value


def setting(name: str):
    """One of the host's settings (configure)."""
    return _SET[name]


def repo() -> str:
    """The repository the tickets live in: configure(repo=), else the board's `repo`."""
    return _SET['repo'] or T._board()['repo']


def area() -> str:
    """The `area:` label of the readers' Area (configure(area=)); NotConfigured when the host named none."""
    if not _SET['area']:
        raise T.NotConfigured("no readers' Area: titles.configure(area=)")
    return T.area_label(_SET['area'])


def bug_label() -> str:
    """The bug kind's label, lower-cased as fetch() reads labels: configure(bug=), else the board's `labels`
    {'Bug': ...}, else 'bug'."""
    return (_SET['bug'] or (T._board().get('labels') or {}).get('Bug') or 'bug').lower()


def member_label() -> str:
    """The label of a ticket still titled in a member's words (tickets.label('member_title')), lower-cased as fetch()
    reads labels; '' when none."""
    return (T.label('member_title', required=False) or '').lower()


def issue_url(number: int) -> str:
    """The ticket's page on GitHub."""
    return f'https://github.com/{repo()}/issues/{int(number)}'


def numbers_of(commits: list, mapped: dict = None) -> dict:
    """{ticket number: [shas]} for commits [{'sha', 'message'}] (the message with its trailers), oldest commit first; a
    commit with no `Ticket:` trailer counts through `mapped` ({sha prefix: [numbers]}, configure(mapped=) when None) by
    its sha's first seven characters. A commit that another commit of the list reverts names none (0.24.0,
    trailers.unreverted): its change is not in what the list ends as."""
    from chuang_sdlc import trailers
    if mapped is None:
        mapped = _SET['mapped']() if _SET['mapped'] else {}
    out = {}
    for c in trailers.unreverted(commits):
        numbers = B.ticket_numbers(c.get('message') or '')
        if not numbers:
            numbers = next((v for k, v in mapped.items() if str(c.get('sha', '')).startswith(k[:7])), [])
        for n in numbers:
            out.setdefault(int(n), []).append(c.get('sha', ''))
    return out


def _token() -> str:
    if _SET['token'] is not None:
        return _SET['token']()
    from chuang_platform_kit import github
    return github.token()


def fetch(number: int, opener=None) -> dict | None:
    """{'number', 'title', 'labels', 'state'} for #number, its labels lower-cased; {'number', 'missing': True} when
    GitHub has no such issue (a mistyped or deleted ticket, left out for good); None when GitHub does not answer (a
    WARNING line; the caller retries). Through the kit's throttled github client."""
    from chuang_platform_kit import github
    try:
        got = github.get(_token(), f'https://api.github.com/repos/{repo()}/issues/{int(number)}', opener=opener,
                         timeout=TIMEOUT)
    except github.GitHubError as e:
        if e.status in (404, 410):
            return {'number': int(number), 'missing': True}
        if e.status:
            print(f"WARNING: ticket #{number} could not be read from GitHub (HTTP {e.status})")
        else:
            print(f"WARNING: ticket #{number} could not be read from GitHub ({e.message})")
        return None
    if not isinstance(got, dict):
        print(f"WARNING: ticket #{number} could not be read from GitHub (not JSON)")
        return None
    return {'number': int(number), 'title': str(got.get('title') or '').strip(),
            'labels': [str(lb.get('name', '')).lower() for lb in got.get('labels') or [] if isinstance(lb, dict)],
            'state': got.get('state')}


def read(commits: list, mapped: dict = None, get=None) -> list:
    """[(number, ticket or None)] for the tickets `commits` name, each once, in the order its first commit came, read
    side by side (`workers` at once). `get(number)` is fetch() unless given."""
    numbers = list(numbers_of(commits, mapped))
    with ThreadPoolExecutor(max_workers=_SET['workers']) as pool:
        return list(zip(numbers, pool.map(get or fetch, numbers)))


def title(ticket: dict) -> str:
    """A ticket's full title as notes print it: word for word, without the host's `tail`."""
    text = ticket['title']
    if _SET['tail']:
        text = re.sub(_SET['tail'], '', text)
    return text.strip()


def line(ticket: dict) -> str:
    """A reader's line for a ticket: its title (title()) without a closing period, after `fixed` for a bug."""
    text = title(ticket).rstrip('.')
    return f"{_SET['fixed']}{text}" if bug_label() in ticket['labels'] else text


def shipped(commits: list, mapped: dict = None, get=None) -> dict:
    """What `commits` shipped, by their tickets: {'new': [lines], 'fixed': [lines], 'behind': N, 'unknown': [numbers]}.
    'new' and 'fixed' are the readers' Area's tickets' lines (line()), each once, in the order its first commit came;
    'behind' counts the other Areas' tickets; 'unknown' the tickets GitHub did not answer for (left out of both; a
    ticket GitHub says does not exist is left out without a word). `get(number)` is the test seam for fetch()."""
    out = {'new': [], 'fixed': [], 'behind': 0, 'unknown': []}
    want = area()
    for number, t in read(commits, mapped, get):
        if t is None:
            out['unknown'].append(number)
            continue
        if t.get('missing') or not t.get('title'):
            continue
        if want not in t['labels']:
            out['behind'] += 1
            continue
        text = line(t)
        bucket = out['fixed'] if bug_label() in t['labels'] else out['new']
        if text not in bucket:
            bucket.append(text)
    return out
