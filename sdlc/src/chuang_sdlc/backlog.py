"""The one backlog, read from the board: every open ticket by its state (in progress, next, and the states the host's
labels name), P1 first within each, for every "what's next" and a release captain's board.

    configure(states=('in progress', 'next', 'waiting', 'parked'), label_states={'waiting': 'waiting'},
              tail=r'\\s*\\(B\\d+\\)\\s*$', id_field='b#', needs_field='needs')
    got = read_open(run, cfg)      # [{id, item, priority, owner, state, needs, where}], or None when GitHub cannot answer
    print(render({'open': got, 'done': []}))

A card's state is its column's (a column of the pipeline's moving roles is `in progress`), else a label the host maps
to a state, else `next`. The board is read once with GitHub's own filter (open issues, the shipped column left out on
GitHub's side), through chuang_sdlc.tickets' board_items, so a read is a point a hundred cards, never the whole board.
The board, its runner and its card fields are chuang_sdlc.tickets'; the command line and any other list the host keeps
(a done table, an older index) stay the host's.
"""
import re

from . import tickets as _tickets

_SET = {
    'states': ('in progress', 'next', 'waiting', 'parked'),   # the states in the order a reader wants them
    'label_states': {},     # {label: state}: a card in no moving column waits in the state its first such label names
    'moving': ('shipyard', 'dock', 'dev', 'shipping', 'failed'),   # the roles whose columns are `in progress`
    'column_states': {},    # {role: state}: a card in that role's column, when it is not a moving one, is in this state
    'default_state': 'next',   # the state of a card no column or label gives one
    'tail': None,           # a regex for a tail a title may carry that no reader needs (an older backlog's number)
    'id_field': '',         # a card field written after the ticket's number in its id (`#213 B4`), '' for none
    'needs_field': '',      # the card field read as what an item needs, '' for none
}

IN_PROGRESS, NEXT = 'in progress', 'next'


def configure(**settings) -> None:
    """The host's settings: `states`, `label_states`, `moving` (roles), `column_states` ({role: state}),
    `default_state`, `tail`, `id_field`, `needs_field` (the module's _SET says what each is). Unknown names are
    refused."""
    for name, value in settings.items():
        if name not in _SET:
            raise TypeError(f'backlog.configure() takes no {name!r}')
        _SET[name] = value


def setting(name: str):
    """One of the host's settings (configure)."""
    return _SET[name]


def moving(cfg: dict = None) -> tuple:
    """The columns whose cards are in progress: the `moving` roles' columns on the board."""
    cfg = _tickets._cfg(cfg)
    return tuple(_tickets.stage(r, cfg) for r in _SET['moving'] if r in cfg['stages'])


def state_of(status: str, labels, cfg: dict = None) -> str:
    """A ticket's backlog state from its Status and labels: a moving column is in progress; otherwise the state its
    column's role has in `column_states`, else the state its first label in `label_states` names, else
    `default_state` (next unless the host says otherwise)."""
    cfg = _tickets._cfg(cfg)
    col = _tickets.column(status, cfg)
    if col in moving(cfg):
        return IN_PROGRESS
    for role, state in _SET['column_states'].items():
        if role in cfg['stages'] and _tickets.stage(role, cfg) == col:
            return state
    for label in labels or ():
        if label in _SET['label_states']:
            return _SET['label_states'][label]
    return _SET['default_state']


def board_filter(cfg: dict = None) -> str:
    """GitHub's own filter for the open backlog: open issues, the shipped column left out (and with it the closed
    tickets and the drafts)."""
    cfg = _tickets._cfg(cfg)
    return f'is:open is:issue -{_tickets.filter_columns([_tickets.stage("shipped", cfg)])}'


def read_open(run=None, cfg: dict = None):
    """The board's open tickets, each {id (`#213`, then the id_field's value), item (the title without its tail),
    priority, owner, state, needs, where (its url)}, sorted P1 first and an unset priority last (the order within each
    state); None when GitHub cannot answer (a host then reads another list). A shipped card or a pull request is no
    item."""
    run, cfg = _tickets._run(run), _tickets._cfg(cfg)
    items = _tickets.board_items(board_filter(cfg), run, cfg)
    if items is None:
        return None
    shipped = _tickets.stage('shipped', cfg)
    out = []
    for it in items:
        content = it.get('content') or {}
        if _tickets.column(it.get('status') or '', cfg) == shipped or content.get('type') != 'Issue':
            continue
        title = it.get('title') or ''
        if _SET['tail']:
            title = re.sub(_SET['tail'], '', title)
        extra = (it.get(_SET['id_field']) or '') if _SET['id_field'] else ''
        needs = (it.get(_SET['needs_field']) or '') if _SET['needs_field'] else ''
        out.append({'id': f"#{content.get('number')}" + (f' {extra}' if extra else ''), 'item': title,
                    'priority': it.get('priority') or '', 'owner': it.get('owner') or '',
                    'state': state_of(it.get('status') or '', it.get('labels'), cfg),
                    'needs': '' if needs.strip() in ('', '—') else needs, 'where': content.get('url', '')})
    out.sort(key=lambda r: r['priority'] or 'P9')
    return out


def short(text: str, width: int = 150) -> str:
    """Text cut to `width` with an ellipsis."""
    return text if len(text) <= width else text[:width - 1].rstrip() + '…'


def render(backlog: dict, owner: str = '', show_done: bool = False, states=None) -> str:
    """The open items by state (`states`, else the configured order), each `P1 #12  Owner: item — needs`, a state's
    count on its heading, then the done ones ({id, item, closed}) when asked."""
    rows = [r for r in backlog['open'] if not owner or r['owner'].lower() == owner.lower()]
    lines = []
    for state in states or _SET['states']:
        group = [r for r in rows if r['state'] == state]
        if not group:
            continue
        lines.append(f"{state} ({len(group)})")
        for r in group:
            needs = f" — {short(r['needs'], 90)}" if r['needs'] else ''
            prio = f"{r['priority']} " if r.get('priority') else ''
            lines.append(f"  {prio}{r['id']:<4} {r['owner']}: {short(r['item'])}{needs}")
    if show_done:
        lines.append(f"done and closed ({len(backlog.get('done') or [])})")
        lines += [f"  {r['id']:<4} {short(r['item'])} — {short(r['closed'], 90)}" for r in backlog.get('done') or []]
    return '\n'.join(lines)
