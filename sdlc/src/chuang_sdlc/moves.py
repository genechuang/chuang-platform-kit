"""A Project board's item helpers: one card read by itself, its fields written in one request, and the rules a host's
webhook receiver and sweeps apply to a move (SMADPickleBot #1682; that project's ticket_moves, its names taken out).

A board move is an event that carries its state: GitHub's `projects_v2_item` delivery names the item that changed and
the field, never the issue or its labels, so the receiver reads that one item once (`read_item`) and its event carries
the node: the issue number, title, labels, Status (the column it moved to), Light and Owner. No listener reads the board
back. This module is the interface chuang_sdlc.listeners and webhooks ask of a host's board helpers
(`listeners.configure(moves=chuang_sdlc.moves)`), in these groups:

  the item and its fields   read_item, column, column_now, light, number, estimate_minutes, labels, owner, title
  the board's names         stage, rank, named, stage_role, column_phase, newly_in, light_for, light_fix
  the writes                set_fields, set_field, set_light
  the fixed cards           is_ship_card, is_placeholder, placeholder_role, right_placeholder
  the put-backs             shipped_live, put_back_to, guarded, put_back_echo, LazyStore, pipeline_marked, put_back
  an issue's card           issue_node, item_for, unblock, to_dock
  an alert's card           alert_key, place_alert
  a closed ticket's card    closed_key, is_unplanned, to_unplanned, restore, unplanned_line, closed_card, is_finished,
                            to_shipped, closed_question, closed_to, closed_letter, wake_letter

Nothing here names a project. The host says its names once (`configure`), each read when a call needs it, never at
import: the board (else chuang_sdlc.tickets' own, so a tenant's layer over tickets reaches here too), the people by
role, the labels, the letter senders, the record collections and the words of every line and letter. A per-call `cfg`
overrides the board. A name a call needs and the host left out raises tickets.NotConfigured; a rule whose label the
host named none of is simply not applied. The names a host's code reads as constants (SENDER, STARTS, BOARD_MOVES,
SCRUM_SENDER, CAPTAIN, LEAD, ...) are module attributes answered from the same settings at each read.

GitHub is reached through chuang_platform_kit.github's throttled client (imported when first used, or the object
`configure(github=)` names), with the caller's `opener`.
"""
import re
import time

from . import board as _board_kit
from . import tenant as _tenant
from .tickets import NotConfigured

# The one item query: a receiver's read of the card an event names.
ITEM_QUERY = ('query($id: ID!) { node(id: $id) { ... on ProjectV2Item { id '
              'content { ... on Issue { number title url state stateReason labels(first: 20) { nodes { name } } } ... on DraftIssue { title } } '
              'status: fieldValueByName(name: "Status") { ... on ProjectV2ItemFieldSingleSelectValue { name updatedAt creator { login } } } '
              'owner: fieldValueByName(name: "Owner") { ... on ProjectV2ItemFieldSingleSelectValue { name } } '
              'light: fieldValueByName(name: "Light") { ... on ProjectV2ItemFieldSingleSelectValue { name } } '
              'estimate: fieldValueByName(name: "Estimate") { ... on ProjectV2ItemFieldNumberValue { number } } } } }')
STATUS_FIELD = 'Status'
# The card's state within its column: two roles may share a column told apart by it, so a Light edit is a move too.
LIGHT_FIELD = 'Light'
MOVE_FIELDS = (STATUS_FIELD, LIGHT_FIELD)
# The fields whose edit is a move event at all: a Priority, Owner or ETA edit reaches the scrum master alone; the
# move's other listeners take MOVE_FIELDS' edits only.
EVENT_FIELDS = MOVE_FIELDS + ('Priority', 'Owner', 'ETA')
KIND_FIELD, OWNER_FIELD = 'Kind', 'Owner'
ALERT_KEY_FIELD = 'Alert key'   # the card's own copy of the alert it was filed for, when the board has the field
TIMEOUT = 10
# A Project text field keeps 1,020 characters and refuses 2,000 (measured 11/4/26): a longer value is cut here, the
# caller adding its own "more" pointer inside the limit.
TEXT_MAX = 1000
WORKING_ROLES = ('shipyard', 'dock', 'dev', 'shipping')   # a ticket in these roles' columns is being worked on
PUT_BACK_SECONDS = 120          # a Status delivery into `to` this soon after the put-back is its own write's echo
PIPELINE_MARK_SECONDS = 600     # a pipeline's mark of its own move into the ship column counts this long

# The words of every line and letter, each a str.format template a host may replace (configure(words={...})).
WORDS = {
    'board_name': 'the board',          # the board as a letter names it
    'ship_card': 'the ship card',       # the ship card as a put-back's reason names it
    'ship_card_word': 'SHIP IT',        # what a ship card's title carries when the board's config names no word
    'only_ship_card': 'only {ship_card} goes into {column}',
    'status_card_stays': 'a status card stays in {column}',
    'only_change_order': 'only a change order goes into {column}',
    'not_shipped': '#{number} is not shipped live yet (its ticket closes when its release passes its production smoke)',
    'put_back_line': '{what} was moved into {column} by {by}; put back in {to}: {why}',
    'put_back_mail': 'Board: {line}. If the move was meant, tell {captain} what it should prove.',
    'queued_left': ('#{number}: {light} in {column}, its owner not building it yet; left there (a commit naming it '
                    'landed: split that step into its own ticket before it lands)'),
    'unplanned': ("#{number} was closed as {why}, so its card was archived off the board: nothing shipped, nothing to "
                  "confirm (the board's Archived items can restore it). {title}\n{url}"),
    'unplanned_subject': 'Closed as {why}: #{number}',
    'closed_question': ('#{number} was closed while in {column}: if it is done, verified and closed, answer '
                        '`Scrum: done`; otherwise reopen it.'),
    'closed_yours': ' It has {owner_state} on the board, so it is yours to settle.',
    'closed_subject': '#{number} closed in {column}',
    'wake_head': '{by} moved #{number} to {column} on {board_name}: {title} ({url}).',
    'wake_unowned': ' It has {owner_state} on the board: take it, or set its Owner and tell them.',
    'wake_admin': (' It carries the {label} label, so it is yours to build now, whatever its Priority, with no approval '
                   'asked first.{start}'),
    'wake_owner': (' It is yours to build now (its Owner on the board); a P1 at once, otherwise say in one line it is '
                   'next.{start}'),
    'wake_start': '',                   # the sentence naming the host's start command, {number} in it; '' says none
    'wake_subject': '#{number} to {column}',
}

_SET = _tenant.Layered('moves', {
    'board': None,          # () -> the board's config dict; None: chuang_sdlc.tickets' own (configure(board=) there)
    'people': {},           # {captain, lead}: a role left out is tickets.person(role)
    'labels': {},           # {feedback, admin_ask, change_order, blocked}: the host's label for each rule
    'senders': {},          # {board, scrum}: who a move's letter and the scrum master's are from
    'collections': {},      # {starts, put_backs, board_moves, closed_claims}: the record collection of each
    'started_minutes': 20,  # a move within this of a session's own start is its own, told to nobody
    'alert_kind': '',       # the Kind option an alert issue's card is filed under
    'words': {},            # WORDS' templates the host replaces
    'github': None,         # the GitHub client (graphql, project_set_fields, project_add_item, GitHubError,
                            # RateLimited); None: chuang_platform_kit.github, imported when first used
})


def configure(**names) -> None:
    """The host's names (the module's _SET says what each is). Unknown names are refused."""
    for name, value in names.items():
        if not dict.__contains__(_SET, name):
            raise TypeError(f'moves.configure() takes no {name!r}')
        _SET[name] = value


def setting(name: str):
    """One of the host's names (configure)."""
    return _SET[name]


def _named(table: str, role: str, need: bool = True) -> str:
    value = (_SET[table] or {}).get(role, '')
    if not value and need:
        raise NotConfigured(f'no {role} in {table}: moves.configure({table}={{{role!r}: ...}})')
    return value or ''


def label(role: str) -> str:
    """The host's label for a rule (`feedback`, `admin_ask`, `change_order`, `blocked`); '' when it named none."""
    return _named('labels', role, need=False)


def sender(role: str) -> str:
    """Who a letter is from (`board`, `scrum`); NotConfigured when the host named none."""
    return _named('senders', role)


def collection(role: str) -> str:
    """The record collection of a role (`starts`, `put_backs`, `board_moves`, `closed_claims`); NotConfigured when the
    host named none."""
    return _named('collections', role)


def word(key: str, **values) -> str:
    """One of the module's words (WORDS, the host's own where it gave one), its template filled."""
    text = (_SET['words'] or {}).get(key)
    text = WORDS[key] if text is None else text
    return text.format(**values) if values else text


def board() -> dict:
    """The board's config in force: the host's (configure(board=)), else chuang_sdlc.tickets' own, which a tenant's
    layer over tickets replaces."""
    if _SET['board'] is not None:
        return _SET['board']()
    from . import tickets
    return tickets._board()


def person(role: str) -> str:
    """The name the host gave a role (`captain`, `lead`): configure(people=), else tickets.person(role); '' for none."""
    name = (_SET['people'] or {}).get(role, '')
    if name:
        return name
    from . import tickets
    return tickets.person(role)


def captain() -> str:
    """The release captain, who takes a card with no Owner or the lead's; NotConfigured when the host named none."""
    name = person('captain')
    if not name:
        raise NotConfigured("no captain: moves.configure(people={'captain': ...})")
    return name


def lead() -> str:
    """The lead, whose cards the captain settles ('' when the host named none: then no Owner is the lead)."""
    return person('lead')


# The names a host's code reads as constants, answered from the settings at each read (PEP 562), never frozen at import.
_CONSTANTS = {
    'SENDER': lambda: sender('board'), 'SCRUM_SENDER': lambda: sender('scrum'),
    'STARTS': lambda: collection('starts'), 'PUT_BACKS': lambda: collection('put_backs'),
    'BOARD_MOVES': lambda: collection('board_moves'), 'CLOSED_CLAIMS': lambda: collection('closed_claims'),
    'STARTED_MINUTES': lambda: _SET['started_minutes'], 'ALERT_KIND': lambda: _SET['alert_kind'],
    'FEEDBACK_LABEL': lambda: label('feedback'), 'ADMIN_ASK': lambda: label('admin_ask'),
    'CHANGE_ORDER_LABEL': lambda: label('change_order'), 'BLOCKED_LABEL': lambda: label('blocked'),
    'CAPTAIN': captain, 'LEAD': lead,
}


def __getattr__(name: str):
    if name in _CONSTANTS:
        return _CONSTANTS[name]()
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


# ------------------------------------------------------------------ the item and its fields
def read_item(item_id: str, token: str, opener=None) -> dict:
    """One Project item's node by its id over GitHub's GraphQL API ({} for none); RuntimeError with the refusal."""
    node = (_graphql(ITEM_QUERY, {'id': item_id}, token, opener).get('node')) or {}
    return node if node.get('id') else {}


def column(node: dict) -> str:
    """The column the item is in ('' when its Status is unset)."""
    return ((node or {}).get('status') or {}).get('name') or ''


def _renamed(raw: str, cfg: dict) -> str:
    return cfg.get('renamed', {}).get(raw) or cfg.get('merged', {}).get(raw) or raw


def column_now(node: dict, cfg: dict = None) -> str:
    """The item's column with an old name read as its new one (the board's `renamed` and `merged`): the board says an
    old name until its options are renamed, so a forward-only move must not take a shipped card for an earlier one."""
    return _renamed(column(node), cfg or board())


def light(node: dict) -> str:
    """The card's Light ('' when unset): its state within its column."""
    return ((node or {}).get('light') or {}).get('name') or ''


def number(node: dict):
    """The item's issue number (None for a draft such as the ship card)."""
    return ((node or {}).get('content') or {}).get('number')


def estimate_minutes(node: dict):
    """The card's Estimate field (hours) as minutes; None when it is unset."""
    hours = ((node or {}).get('estimate') or {}).get('number')
    return int(round(float(hours) * 60)) if hours else None


def labels(node: dict) -> set:
    """The issue's label names."""
    return {(l or {}).get('name') or '' for l in ((((node or {}).get('content') or {}).get('labels') or {}).get('nodes') or [])}


def is_feedback(node: dict) -> bool:
    """Whether the item is a feedback ticket: an issue carrying the host's `feedback` label (never, when it named
    none)."""
    return bool(number(node)) and bool(label('feedback')) and label('feedback') in labels(node)


def owner(node: dict) -> str:
    """The card's Owner ('' when unset)."""
    return ((node or {}).get('owner') or {}).get('name') or ''


def title(node: dict) -> str:
    """The item's title ('' when unread): an issue's, or a draft's (the ship card's, a placeholder's)."""
    return ((node or {}).get('content') or {}).get('title') or ''


# ------------------------------------------------------------------ the board's names
def stage(role: str, cfg: dict = None) -> str:
    """A column's name on the board by its role (board.stage over the board in force)."""
    return _board_kit.stage(role, cfg or board())


def rank(col: str, light: str = '', raw: str = '', cfg: dict = None):
    """A card's place along the flow as a role's index: its column and, where two roles share one, its Light; a card in
    a merged column is that column's role. None off the flow (board.rank)."""
    return _board_kit.rank(col, light, raw, cfg or board())


def named(status: str, cfg: dict = None) -> str:
    """A Status as the board names it now: an old name read as its new one, a role as its column (board.named)."""
    return _board_kit.named(status, cfg or board())


def stage_role(status: str, light: str = '', cfg: dict = None) -> dict:
    """{stage, stage_name}: the card's role along the flow (where two roles share a column, by its Light), `failed` for
    the wreck column, '' off the flow; and its column's name as the board says it."""
    cfg = cfg or board()
    name = named(status, cfg)
    at = rank(name, light, status, cfg)
    role = cfg['flow'][at] if at is not None else ('failed' if name == stage('failed', cfg) else '')
    return {'stage': role, 'stage_name': name}


def column_phase(status: str, cfg: dict = None) -> str:
    """The phase an open ticket in `status` is in: 'working' in a working column (WORKING_ROLES), else 'filed' (none, a
    ticket off the board, is filed)."""
    cfg = cfg or board()
    working = {stage(r, cfg) for r in WORKING_ROLES if r in cfg['stages']}
    return 'working' if status and named(status, cfg) in working else 'filed'


def newly_in(data: dict, role: str, cfg: dict = None) -> bool:
    """Whether this move event is a Status move INTO `role`'s column from another (`from`, GitHub's previous value),
    never a Light edit nor a move within the column."""
    cfg = cfg or board()
    col = stage(role, cfg)
    return ((data or {}).get('field_name') or STATUS_FIELD) == STATUS_FIELD and column((data or {}).get('node')) == col \
        and ((data or {}).get('from') or '') != col


def light_for(col: str, cfg: dict = None):
    """The Light a card dragged into this column should show: its one role's light, '' for a column with none; None
    when two roles share it (which of them is the pipeline's to say) or the column is not the board's."""
    cfg = cfg or board()
    roles = [r for r, name in cfg['stages'].items() if name == col]
    return cfg.get('lights', {}).get(roles[0], '') if len(roles) == 1 else None


def light_fix(data: dict, cfg: dict = None):
    """The Light this move should leave on its card, or None to leave it: a dragged card takes its column's light (none
    where the column has none: a working light is a session's own start, never a drag), a shared column is left to the
    pipeline, the `needs` light is always kept, and in the shipyard column a session's `working`, the scrum master's
    `queued` and `blocked` and a push's `pushing` are kept (the start's move arrives after it set the light). A change
    order's Light (the `shipment` column) is the ship's to say. A draft (the ship card, a status card, a placeholder)
    is no ticket and is left alone."""
    cfg = cfg or board()
    node = (data or {}).get('node') or {}
    col, have = column(node), light(node)
    if col and col == cfg['stages'].get('shipment'):
        return None
    want = light_for(col, cfg) if col and number(node) else None
    lights = cfg.get('lights', {})
    if want is None or have == want or have == lights.get('needs'):
        return None
    if col == stage('shipyard', cfg) and have and have in (lights.get('working'), lights.get('queued'), lights.get('pushing'),
                                                           lights.get('blocked'), lights.get('scheduled')):   # 0.63.1
        return None
    return want


# ------------------------------------------------------------------ the writes
def _github():
    if _SET['github'] is not None:
        return _SET['github']
    from chuang_platform_kit import github
    return github


def _kit(call, token: str):
    """One call of the throttled GitHub client (`call(github)`: a call is refused locally once the token's quota is
    under its floor or this process is over its cap, so a loop here never reaches GitHub); its answer, RuntimeError
    with the refusal: `HTTP <status>` for a refused call, the exception's name for no answer, GitHub's message for a
    GraphQL error, the client's words for a spent quota."""
    if not token:
        raise RuntimeError('no GitHub token')
    github = _github()
    try:
        return call(github)
    except github.RateLimited as e:
        raise RuntimeError(e.message) from None
    except github.GitHubError as e:
        if e.status == 0:
            raise RuntimeError((e.message or 'no answer').split(':')[0]) from None
        if e.status >= 400:
            raise RuntimeError(f'HTTP {e.status}') from None
        raise RuntimeError(e.message or 'GraphQL error') from None


def _graphql(query: str, variables: dict, token: str, opener=None) -> dict:
    """One GitHub GraphQL query through the client (_kit); its `data`. The Project mutations are the client's own verbs
    (project_add_item, project_set_fields), never a document written here: the one copy of GitHub's shapes."""
    return _kit(lambda github: github.graphql(token, query, variables, opener=opener, timeout=TIMEOUT), token)


def set_fields(item_id: str, values: dict, token: str, cfg: dict = None, opener=None) -> None:
    """Several fields of the card ({field: value}, '' clears one) in ONE GraphQL request: an aliased update (or clear)
    per field in one mutation document, so a card is placed in two calls, the add and this. A single-select takes its
    option's name (one the board lacks yet is written as its `light_fallback`), a `number` field a number, a `text`
    field its first TEXT_MAX characters. RuntimeError with the refusal; GitHub applies the aliases in order and still
    sends one project-item webhook per field."""
    cfg = cfg or board()
    if not values:
        return
    ids = {}
    for field, value in values.items():
        fld = cfg['fields'][field]
        if fld.get('type') == 'number':
            ids[fld['id']] = {'number': float(value)} if str(value).strip() != '' else None
            continue
        if fld.get('type') == 'text':
            ids[fld['id']] = {'text': str(value)[:TEXT_MAX]} if str(value) != '' else None
            continue
        if value and value not in fld['options']:
            value = cfg.get('light_fallback', {}).get(value, value)
        ids[fld['id']] = fld['options'][value] if value else None
    _kit(lambda github: github.project_set_fields(token, cfg['id'], item_id, ids, opener=opener), token)


def set_field(item_id: str, field: str, value: str, token: str, cfg: dict = None, opener=None) -> None:
    """One field of the card set to `value` ('' clears it); RuntimeError with the refusal. One field of set_fields; a
    caller with several writes them in one call."""
    set_fields(item_id, {field: value}, token, cfg, opener)


def set_light(item_id: str, value: str, token: str, cfg: dict = None, opener=None) -> None:
    """The card's Light set to `value` ('' clears it); RuntimeError with the refusal."""
    set_field(item_id, LIGHT_FIELD, value, token, cfg, opener)


# ------------------------------------------------------------------ the fixed cards: the ship card, the placeholders
# GitHub's board stops taking a drop into a column that emptied under an open page by a move that was not the viewer's
# own drag, so each flow column that can empty keeps one placeholder draft card (the board's `placeholders` by role).
# GitHub pins no card, so the card is self-righting: every reader of the board skips it and the receiver puts a moved
# one straight back (right_placeholder), publishing nothing.
def is_ship_card(node: dict, cfg: dict = None) -> bool:
    """Whether a read item is the ship card: a draft whose title carries the board's `ship_card` `word` (else the
    module's `ship_card_word`) and is no placeholder."""
    cfg = cfg or board()
    mark = ((cfg.get('ship_card') or {}).get('word') or word('ship_card_word')).upper()
    t = title(node)
    return bool(node) and not number(node) and mark in t.upper() and not is_placeholder(t, cfg)


def is_placeholder(text: str, cfg: dict = None) -> bool:
    """Whether a card's title is a column's placeholder: one of the board's `placeholders`, or any `— … —` (an older
    wording of one still counts, so a rename never leaves a stray); board.is_placeholder."""
    return _board_kit.is_placeholder(text, cfg or board())


def placeholder_role(text: str, cfg: dict = None) -> str:
    """The role whose column a placeholder card belongs in: by its title in `placeholders`, else by the column its
    title names; '' for a title that is no placeholder or names no placeholder column (board.placeholder_role)."""
    return _board_kit.placeholder_role(text, cfg or board())


def right_placeholder(node: dict, token: str, cfg: dict = None, opener=None) -> str:
    """A placeholder card put back in its own column the moment anyone moves it: its role from its title, its Status
    set back when its column is not that role's (set_field, the one write), nothing when it is where it belongs (its
    own set-back comes round as a move too, and settles here); a card that is no placeholder, or whose title names no
    column, is left. The line said ('' for a card that is none of ours); RuntimeError with GitHub's refusal."""
    cfg = cfg or board()
    t = title(node)
    role = placeholder_role(t, cfg)
    if not role:
        return ''
    home, col = stage(role, cfg), column_now(node, cfg)
    if col == home:
        return f'placeholder "{t}" is in {home}; left'
    set_field((node or {}).get('id') or '', STATUS_FIELD, home, token, cfg, opener)
    return f'placeholder "{t}" was moved to {col or "no column"}; put back in {home}'


# 0.62.0 (SMADPickleBot #2194; Gene, 11/22/26 1:23 PM PT: "keep all static description card at the top and not
# moveable ... this applies to all boards"): GitHub puts a card that enters a column at its top, and the sweep's keeper
# (tickets.card_order_keep) put the label card back only every 30 minutes, in three columns. label_first is the
# receiver's own keeper: called for the column of every Status move and every reorder, it reads that column's first
# cards and writes only when its label card is not first (so its own write's echo settles on the read).
COLUMN_ITEMS = ('query($p: ID!, $q: String!, $n: Int!) { node(id: $p) { ... on ProjectV2 { items(first: $n, query: $q) '
                '{ nodes { id content { ... on Issue { number title } ... on DraftIssue { title } } } } } } }')
ITEM_AFTER = ('mutation($p: ID!, $i: ID!, $a: ID!) { updateProjectV2ItemPosition(input: {projectId: $p, itemId: $i, '
              'afterId: $a}) { items(first: 1) { nodes { id } } } }')


def _column_items(q: str, n: int, token: str, cfg: dict, opener=None) -> list:
    """The first `n` items of the board matching GitHub's own filter `q`, in the board's order."""
    got = _graphql(COLUMN_ITEMS, {'p': cfg['id'], 'q': q, 'n': n}, token, opener) or {}
    return [x for x in (((got.get('node') or {}).get('items') or {}).get('nodes') or []) if x]


def label_first(col: str, token: str, cfg: dict = None, opener=None) -> str:
    """The column `col`'s label card (its placeholder) put first when it is not, and in the ship card's home column the
    ship card put second when it is there and is not. One read of the column's first cards when all is in order, a
    second (its drafts) and one or two position writes when it is not; the top write is the client's own verb
    (project_item_to_top), the ship card's a document here since the client has no "after" verb. The line said, ''
    for a column that has no label card in the board's `placeholders`; RuntimeError with GitHub's refusal."""
    cfg = cfg or board()
    role = next((r for r in (cfg.get('placeholders') or {}) if (cfg.get('stages') or {}).get(r) == col), '')
    if not col or not role:
        return ''
    home = (cfg.get('stages') or {}).get((cfg.get('ship_card') or {}).get('home') or 'dock') == col

    def is_label(n):
        return not number(n) and placeholder_role(title(n), cfg) == role
    top = _column_items(f'status:"{col}"', 3, token, cfg, opener)
    first = bool(top) and is_label(top[0])
    if first and (not home or (len(top) > 1 and is_ship_card(top[1], cfg))):
        return f'{col}: its label card is first; left'
    drafts = _column_items(f'is:draft status:"{col}"', 20, token, cfg, opener)
    label = top[0] if first else next((n for n in drafts if is_label(n)), None)
    if label is None:
        return f'{col}: no label card to put first'
    ship = next((n for n in drafts if is_ship_card(n, cfg)), None) if home else None
    said = []
    if not first:
        _kit(lambda github: github.project_item_to_top(token, cfg['id'], label['id'], opener=opener), token)
        said.append('its label card put first')
    if ship is not None:
        _graphql(ITEM_AFTER, {'p': cfg['id'], 'i': ship['id'], 'a': label['id']}, token, opener)
        said.append('the ship card put second')
    return f'{col}: ' + (', '.join(said) if said else 'its label card is first; left')


# ------------------------------------------------------------------ the board's put-backs
# A ticket is never dragged into the ship column without proof it shipped live, no card but a change order goes into the
# shipment column, and the app-status column takes the ship card alone and lets none of its status cards out. A move
# that breaks one is put back where it came from, sent nowhere, and its Owner told in one line.
def shipped_live(node: dict) -> bool:
    """Whether a read item's issue is closed as completed: the board's proof the work is live, where a ticket closes
    only once its release passes. An open issue, or one closed as not planned or a duplicate, is no proof."""
    content = (node or {}).get('content') or {}
    return (str(content.get('state') or '').upper() == 'CLOSED'
            and str(content.get('stateReason') or 'COMPLETED').upper() == 'COMPLETED')


def _guards(cfg: dict) -> tuple:
    """(ship, shipment, app status): the guarded columns, '' for a role the board has none of."""
    stages = cfg.get('stages') or {}
    return stages.get('shipping', ''), stages.get('shipment', ''), stages.get('app_status', '')


def put_back_to(data: dict, cfg: dict = None, marked=None) -> tuple:
    """(column, why) when a read move breaks one of the board's rules, ('', '') when it stands. `data` is the move
    event's data with its `node`; only a Status move of a read item counts, from a column it names (the event's `from`,
    an old name read as its new one; the backlog column when it names none). `marked(number)` says whether the pipeline
    marked the ticket's move into the ship column lately (pipeline_marked).
      - the ship column: a ticket not shipped_live, not a change order and not marked goes back where it came from.
      - the shipment column: any card but a change order goes back.
      - the app-status column: a card moved in from another column goes back, the ship card excepted; a status card (a
        draft that is neither the ship card nor a placeholder) moved out of it goes back into it.
    A change order is an issue carrying the host's `change_order` label; a column the board has no role for guards
    nothing."""
    node = (data or {}).get('node') or {}
    if not node or (data.get('field_name') or STATUS_FIELD) != STATUS_FIELD:
        return '', ''
    cfg = cfg or board()
    col, was = column_now(node, cfg), _renamed(data.get('from') or '', cfg)
    if not col or col == was or is_placeholder(title(node), cfg):
        return '', ''
    ship, shipment, apps = _guards(cfg)
    back, n, ship_card = was or stage('backlog', cfg), number(node), is_ship_card(node, cfg)
    co = bool(label('change_order')) and label('change_order') in labels(node)
    if apps and col == apps and was and not ship_card:
        return back, word('only_ship_card', ship_card=word('ship_card'), column=apps)
    if apps and was == apps and not n and not ship_card:
        return apps, word('status_card_stays', column=apps)
    if shipment and col == shipment and not co:
        return back, word('only_change_order', column=shipment)
    if ship and col == ship and n and not co and not shipped_live(node) and not (marked and marked(n)):
        return back, word('not_shipped', number=n)
    return '', ''


def guarded(data: dict, cfg: dict = None) -> bool:
    """Whether a read Status move touches a column the board's rules guard: into or out of the ship, shipment or
    app-status column. Only such a move can break a rule or be a put-back's echo, so only it costs a record read."""
    node = (data or {}).get('node') or {}
    if not node or (data.get('field_name') or STATUS_FIELD) != STATUS_FIELD:
        return False
    cfg = cfg or board()
    kept = set(_guards(cfg)) - {''}
    return column_now(node, cfg) in kept or _renamed(data.get('from') or '', cfg) in kept


def put_back_echo(data: dict, store, now=None, cfg: dict = None) -> bool:
    """Whether this Status delivery is the echo of the board's own put-back: the item was put back into this column
    within PUT_BACK_SECONDS (the `put_backs` collection's record of the item). An unreadable mark is no echo."""
    node = (data or {}).get('node') or {}
    if not node or not store or (data.get('field_name') or STATUS_FIELD) != STATUS_FIELD:
        return False
    # A put-back moves a card out of a guarded column (from it), or a status card back into the app-status column;
    # nothing else can be its echo, so nothing else reads the mark.
    cfg = cfg or board()
    was = _renamed(data.get('from') or '', cfg)
    apps = _guards(cfg)[2]
    kept = set(_guards(cfg)) - {''}
    col = column_now(node, cfg)
    status_card = bool(apps) and col == apps and not number(node) and not is_ship_card(node, cfg)
    if was not in kept and not status_card:
        return False
    marks = collection('put_backs')
    try:
        got = store.get(marks, data.get('item') or node.get('id') or '') or {}
    except Exception:   # noqa: BLE001 - an unread mark only means the move is told as before
        return False
    return (got.get('to') == col and bool(got.get('at_epoch'))
            and (now or time.time)() - float(got['at_epoch']) < PUT_BACK_SECONDS)


class LazyStore:
    """A record store made only on its first get or set: most guarded moves stand without a read, and making a store
    may cost a token. `make()` returns the store; a failure raises on that call, which every reader here takes as
    "unread" (nothing undone)."""

    def __init__(self, make):
        self._make, self._store = make, None

    def _real(self):
        if self._store is None:
            self._store = self._make()
        return self._store

    def get(self, *a, **k):
        """The store's own get, the store made first if this is its first use."""
        return self._real().get(*a, **k)

    def set(self, *a, **k):
        """The store's own set, the store made first if this is its first use."""
        return self._real().set(*a, **k)


def pipeline_marked(store, now=time.time):
    """The `marked(number)` check put_back_to takes: the `board_moves` collection's record of the ticket written
    within PIPELINE_MARK_SECONDS. An unreadable mark counts as marked, so a move is never undone on a guess."""
    def marked(n) -> bool:
        if not store:
            return True
        marks = collection('board_moves')
        try:
            got = store.get(marks, str(n)) or {}
        except Exception:   # noqa: BLE001 - unread is not a drag
            return True
        return bool(got.get('at_epoch')) and now() - float(got['at_epoch']) < PIPELINE_MARK_SECONDS
    return marked


def put_back(data: dict, token: str, store=None, mail=None, cfg: dict = None, opener=None, now=time.time) -> str:
    """The board's put-back: put_back_to's column, the echo mark written first (the `put_backs` record of the item),
    the Status set back (set_field, the one write), and the card's Owner (the captain for none or the lead) told in one
    line by `mail(to, text, sender)`, best effort. '' when the move stands; RuntimeError with GitHub's refusal."""
    cfg = cfg or board()
    to, why = put_back_to(data, cfg, pipeline_marked(store, now))
    if not to:
        return ''
    node = data.get('node') or {}
    item = data.get('item') or node.get('id') or ''
    if store:
        marks = collection('put_backs')
        try:
            store.set(marks, item, {'at_epoch': now(), 'to': to})
        except Exception:   # noqa: BLE001 - without the mark the echo is told as a move, no worse than before
            pass
    set_field(item, STATUS_FIELD, to, token, cfg, opener)
    n, col = number(node), column_now(node, cfg)
    what = f'#{n}' if n else f'"{title(node)}"'
    line = word('put_back_line', what=what, column=col, by=data.get('by') or '?', to=to, why=why)
    if mail:
        who, text, sent_by = closed_to(owner(node)), word('put_back_mail', line=line, captain=captain()), sender('board')
        try:
            mail(who, text, sent_by)
        except Exception:   # noqa: BLE001 - the notice is best effort; the put-back stands
            pass
    return line


# ------------------------------------------------------------------ an issue's card
# The issue's node: its id, its labels and the issues it is blocked by (a card whose work landed sheds them).
ISSUE_ID = ('query($o: String!, $r: String!, $n: Int!) { repository(owner: $o, name: $r) { issue(number: $n) { id state '
            'labels(first: 20) { nodes { id name } } blockedBy(first: 20) { nodes { id number state } } } } }')
UNBLOCK = 'mutation($i: ID!, $b: ID!) { removeBlockedBy(input: {issueId: $i, blockingIssueId: $b}) { issue { number } } }'
UNLABEL = ('mutation($i: ID!, $l: [ID!]!) { removeLabelsFromLabelable(input: {labelableId: $i, labelIds: $l}) '
           '{ clientMutationId } }')


def issue_node(number: int, token: str, cfg: dict = None, opener=None) -> dict:
    """The ticket's issue node over GitHub's GraphQL API: `id`, `labels` and `blockedBy` ({} when the issue is not
    found); RuntimeError with the refusal."""
    cfg = cfg or board()
    repo_owner, name = cfg['repo'].split('/')
    return ((_graphql(ISSUE_ID, {'o': repo_owner, 'r': name, 'n': int(number)}, token, opener).get('repository') or {})
            .get('issue') or {})


def item_for(number: int, token: str, cfg: dict = None, opener=None, issue: dict = None) -> str:
    """The ticket's item id on the board (adding an issue that is not on it yet; GitHub's add answers the card already
    there), '' when the issue is not found; RuntimeError with the refusal. `issue` is its node when the caller read it."""
    cfg = cfg or board()
    issue_id = (issue if issue is not None else issue_node(number, token, cfg, opener)).get('id')
    if not issue_id:
        return ''
    return _kit(lambda github: github.project_add_item(token, cfg['id'], issue_id, opener=opener), token) or ''


def unblock(issue: dict, token: str, opener=None) -> list:
    """A card whose work has landed has no blocker: every open blocked-by link of the issue node removed, whoever made
    it (GitHub records no author on a link), and the host's `blocked` label taken off (when it named one). The words for
    the card's line ([] for a card with neither); a refusal is a word, never raised."""
    words = []
    for b in (issue.get('blockedBy') or {}).get('nodes') or []:
        if str(b.get('state') or '').upper() == 'CLOSED':
            continue   # a closed blocker shows no mark; its link is history
        try:
            _graphql(UNBLOCK, {'i': issue['id'], 'b': b['id']}, token, opener)
            words.append(f"unblocked from #{b.get('number')}")
        except RuntimeError as e:
            words.append(f"still blocked by #{b.get('number')} ({e})")
    blocked = label('blocked')
    tags = [t['id'] for t in (issue.get('labels') or {}).get('nodes') or [] if blocked and t.get('name') == blocked]
    if tags:
        try:
            _graphql(UNLABEL, {'i': issue['id'], 'l': tags}, token, opener)
            words.append(f'the {blocked} label off')
        except RuntimeError as e:
            words.append(f'the {blocked} label kept ({e})')
    return words


def to_dock(numbers, token: str, cfg: dict = None, opener=None, read=None) -> list:
    """Every ticket to the dock column with its light, the moment its commit lands: one line each. Never a step back: a
    card already past the dock (or in the wreck column) stays, and the `needs` light is kept; a card already in the
    dock column shows the dock's light again, since new work on it landed. A `queued` card stays where it is: its owner
    has said it is next and not being built, so a commit that lands naming it built a part of it at most. A card that
    lands sheds its open blocked-by links and the `blocked` label (unblock), said after the move on its line."""
    cfg = cfg or board()
    dock, warming, needs = stage('dock', cfg), cfg['lights']['dock'], cfg['lights']['needs']
    queued = cfg['lights'].get('queued', '')
    order = [stage(r, cfg) for r in cfg['flow']]
    past = (set(order[order.index(dock) + 1:]) - {dock}) | {stage('failed', cfg)}   # two roles may share the dock column

    def one(n):
        try:
            issue = issue_node(n, token, cfg, opener)
            item = item_for(n, token, cfg, opener, issue=issue)
            if not item:
                return f'#{n}: not found; not moved'
            node = (read or read_item)(item, token, opener)
            col, have = column_now(node, cfg), light(node)
            if col in past:
                return f'#{n}: already in {col}; left there'
            if queued and have == queued:
                return word('queued_left', number=n, light=queued, column=col)
            set_fields(item, {**({STATUS_FIELD: dock} if col != dock else {}), **({LIGHT_FIELD: warming} if have != needs else {})},
                       token, cfg, opener)
            words = unblock(issue, token, opener)
            return (f"#{n}: {dock if col != dock else 'still ' + dock}, {warming if have != needs else needs + ' kept'}"
                    + ('; ' + ', '.join(words) if words else ''))
        except RuntimeError as e:
            return f'#{n}: not moved ({e})'
    # The tickets side by side, each its own card (a landing's moves one after another were seconds of serial GraphQL);
    # the lines in the order the tickets came. Each worker runs in a copy of the caller's context, taken here on the
    # caller's thread, so a tenant's layer holds in it (a pool's thread starts outside any).
    numbers = list(numbers)
    if len(numbers) < 2:
        return [one(n) for n in numbers]
    import contextvars
    from concurrent.futures import ThreadPoolExecutor
    jobs = [(contextvars.copy_context(), n) for n in numbers]
    with ThreadPoolExecutor(max_workers=min(len(numbers), 4)) as pool:
        return list(pool.map(lambda job: job[0].run(one, job[1]), jobs))


# ------------------------------------------------------------------ an alert's card
# The hidden lines alert filers write into an issue's body, by the Alert key's kind: the key moves onto the card's
# Alert key field, which no rewrite of the body can lose. Each on a line of its own, as the filers write them: a plan
# that quotes a marker in a sentence or a table is no alert.
ALERT_MARKERS = (('gcp', re.compile(r'^\s*<!-- gcp-alert: (\S+) @ (\S+) -->\s*$', re.M)),
                 ('sentry', re.compile(r'^\s*<!-- sentry-issue: (\d+) -->\s*$', re.M)),
                 ('gitguardian', re.compile(r'^\s*<!-- gitguardian-incident: (\d+) -->\s*$', re.M)),
                 ('workflow', re.compile(r'^\s*<!-- alert-workflow: ([^<>\n]+?) -->\s*$', re.M)))


def alert_key(body: str) -> str:
    """The card's Alert key from an alert issue's body: `gcp:<key>@<first failure>`, `sentry:<id>`,
    `gitguardian:<id>` or `workflow:<name>`, the first marker found; '' for a body with none."""
    for kind, pattern in ALERT_MARKERS:
        m = pattern.search(body or '')
        if m:
            return f'{kind}:' + '@'.join(g.strip() for g in m.groups())
    return ''


def place_alert(number: int, owner: str, token: str, cfg: dict = None, opener=None, read=None, key: str = '') -> str:
    """An alert issue on the board the moment it is filed: added to the Project in the backlog column, its Kind the
    host's `alert_kind` (when it named one), its investigator as Owner, no Priority and no Light, and its Alert key
    (alert_key of its body) when it has one and the board has the field. A card already past the backlog (a reopened
    issue) is left where it is. The words for the caller's line; RuntimeError with GitHub's refusal."""
    cfg = cfg or board()
    item = item_for(number, token, cfg, opener)
    if not item:
        return f'#{number} not found; not on the board'
    col, backlog = column_now((read or read_item)(item, token, opener), cfg), stage('backlog', cfg)
    if col and col != backlog:
        return f'#{number} already in {col}; left there'
    kind = _SET['alert_kind']
    keyed = {ALERT_KEY_FIELD: key} if key and ALERT_KEY_FIELD in cfg.get('fields', {}) else {}
    set_fields(item, {**({STATUS_FIELD: backlog} if col != backlog else {}), **({KIND_FIELD: kind} if kind else {}),
                      OWNER_FIELD: owner, **keyed}, token, cfg, opener)
    return (f'#{number} on the board: {backlog}, ' + (f'{kind}, ' if kind else '') + f'Owner {owner}'
            + (f', Alert key {key}' if keyed else ''))


# ------------------------------------------------------------------ a closed ticket's card
# A closed ticket's card is in the ship column or the wreck column: an alert closer's close goes to the ship column
# (the failing thing passed again, so the fix is live), a close as not planned is archived off the board, any other
# close is asked about by the scrum master. A host's listener and its sweep read and write through these, the one copy.
CLOSED_ISSUE = ('query($o: String!, $r: String!, $n: Int!) { repository(owner: $o, name: $r) { issue(number: $n) { id '
                'title url state stateReason closedAt projectsV2(first: 10) { nodes { id } } comments(last: 1) { nodes { body } } } } }')
# A close that is no ship: GitHub's own reasons, on a close event (`state_reason`) and the issue node (`stateReason`).
UNPLANNED = ('not_planned', 'duplicate')


def closed_key(number, closed_at: str) -> str:
    """The once-only id of one close's telling: the ticket and the close's time, in the `closed_claims` collection. A
    listener claims it; a sweep reads it, so a close already told is not asked about again."""
    return 'ticket-closed-%s-%s' % (number, re.sub(r'[^0-9A-Za-z]+', '', str(closed_at or 'now')))


def is_unplanned(reason) -> bool:
    """Whether a close's reason (GitHub's `not_planned` or `duplicate`, any case) means the ticket will not ship."""
    return str(reason or '').strip().lower() in UNPLANNED


def to_unplanned(item: str, token: str, cfg: dict = None, opener=None) -> None:
    """A card closed as not planned or a duplicate archived off the board, one request (tickets.ARCHIVE_MUTATION), the
    board's Archived items list able to restore it; RuntimeError with the refusal, or when GitHub answers no item. The
    caller marks the edit in the `board_moves` collection first, as to_shipped's does."""
    from .tickets import ARCHIVE_MUTATION
    cfg = cfg or board()
    data = _graphql(ARCHIVE_MUTATION, {'p': cfg['id'], 'i': item}, token, opener)
    if not (((data or {}).get('archiveProjectV2Item') or {}).get('item') or {}).get('id'):
        raise RuntimeError('GitHub archived no item')


def restore(number: int, token: str, cfg: dict = None, opener=None) -> str:
    """A reopened ticket's card back on the board: its item by item_for, which answers the card GitHub keeps even
    archived and never adds an issue off the board (the issue's own projects first, as closed_card reads them), then
    tickets.UNARCHIVE_MUTATION, which changes nothing on a card that was never archived. The item id restored, '' for
    an issue with no card; RuntimeError with the refusal."""
    from .tickets import UNARCHIVE_MUTATION
    cfg = cfg or board()
    repo_owner, name = cfg['repo'].split('/')
    node = (_graphql(CLOSED_ISSUE, {'o': repo_owner, 'r': name, 'n': int(number)}, token, opener).get('repository') or {}).get('issue') or {}
    if not node.get('id') or cfg['id'] not in {p.get('id') for p in ((node.get('projectsV2') or {}).get('nodes') or [])}:
        return ''
    item = item_for(number, token, cfg, opener, issue=node)
    if not item:
        return ''
    data = _graphql(UNARCHIVE_MUTATION, {'p': cfg['id'], 'i': item}, token, opener)
    if not (((data or {}).get('unarchiveProjectV2Item') or {}).get('item') or {}).get('id'):
        raise RuntimeError('GitHub restored no item')
    return item


def unplanned_line(card: dict, reason: str) -> tuple:
    """(to, text, subject) of the one line a card's owner is told when its ticket closed as not planned or a duplicate:
    what happened to its card and why, no question. `to` as closed_to resolves it."""
    n, name = card.get('number'), (card.get('title') or '').strip()
    why = 'a duplicate' if str(reason).lower() == 'duplicate' else 'not planned'
    to = closed_to(card.get('owner') or '')
    return (to, word('unplanned', number=n, why=why, title=name, url=card.get('url') or '').rstrip(),
            word('unplanned_subject', why=why, number=n))


def closed_card(number: int, token: str, cfg: dict = None, opener=None, read=None) -> dict:
    """One closed ticket's card, read by itself, never the board: {number, title, url, state, reason, closed_at,
    comment (its newest), item, column (an old name read as its new one), light, owner}; {} when GitHub does not have
    the issue or it is not on the board (the issue's own projects say so, so a card is never added to read it).
    RuntimeError with GitHub's refusal."""
    cfg = cfg or board()
    repo_owner, repo = cfg['repo'].split('/')
    issue = ((_graphql(CLOSED_ISSUE, {'o': repo_owner, 'r': repo, 'n': int(number)}, token, opener)
              .get('repository') or {}).get('issue')) or {}
    boards = {(p or {}).get('id') for p in (issue.get('projectsV2') or {}).get('nodes') or []}
    if not issue.get('id') or cfg['id'] not in boards:
        return {}
    item = item_for(number, token, cfg, opener, issue=issue)
    node = (read or read_item)(item, token, opener) if item else {}
    said = (issue.get('comments') or {}).get('nodes') or []
    return {'number': int(number), 'title': issue.get('title') or '', 'url': issue.get('url') or '',
            'state': issue.get('state') or '', 'reason': str(issue.get('stateReason') or '').lower(), 'closed_at': issue.get('closedAt') or '',
            'comment': (said[-1] or {}).get('body') or '' if said else '', 'item': item,
            'column': column_now(node, cfg) if node else '', 'light': light(node), 'owner': owner(node)}


def is_finished(col: str, cfg: dict = None) -> bool:
    """Whether a card's column is one a closed ticket belongs in: the ship column or the wreck column, or the shipment
    column for a ship's change order, which closes there. An old name is read as its new one."""
    cfg = cfg or board()
    col = _renamed(col, cfg)
    return col in (stage('shipped', cfg), stage('failed', cfg), cfg.get('stages', {}).get('shipment', ''))


def to_shipped(item: str, token: str, cfg: dict = None, opener=None) -> None:
    """A card to the ship column with its `shipped` light; RuntimeError with the refusal. The caller marks the move in
    the `board_moves` collection first, so no handler takes it for a person's drag."""
    cfg = cfg or board()
    set_fields(item, {STATUS_FIELD: stage('shipped', cfg), LIGHT_FIELD: cfg['lights']['shipped']}, token, cfg, opener)


def closed_question(number, col: str) -> str:
    """The scrum master's ask about a ticket closed outside the columns a closed ticket belongs in, the one wording of
    a listener's letter and a sweep's."""
    return word('closed_question', number=number, column=col or 'no column')


def closed_to(who: str) -> str:
    """Who is told about a card: its Owner, or the captain for none or the lead."""
    return who if who and who != lead() else captain()


def _owner_state(who: str) -> str:
    return 'no Owner' if not who else f'Owner {who}'


def closed_letter(card: dict) -> tuple:
    """(to, text, subject) of the scrum master's one letter about a ticket closed outside the columns a closed ticket
    belongs in: its Owner, or the captain for none or the lead, asked to say it is done or reopen it."""
    n, who, col = card.get('number'), card.get('owner') or '', card.get('column') or 'no column'
    to = closed_to(who)
    text = (closed_question(n, col) + f" {card.get('title') or ''} ({card.get('url') or ''})"
            + (word('closed_yours', owner_state=_owner_state(who)) if to != who else ''))
    return to, text, word('closed_subject', number=n, column=col)


def wake_letter(node: dict, by: str, cfg: dict = None) -> tuple:
    """(to, text, subject) of the one letter a card newly in the shipyard column sends: its Owner, or the captain for
    none or the lead; who moved it, the ticket and its link, and the host's start sentence (`wake_start`). A card
    carrying the host's `admin_ask` label is its owner's to build at once (`wake_admin`)."""
    cfg = cfg or board()
    content = (node or {}).get('content') or {}
    n, who = content.get('number'), owner(node)
    cap = captain()
    to = closed_to(who)
    yard = stage('shipyard', cfg)
    head = word('wake_head', by=by or 'Someone', number=n, column=yard, board_name=word('board_name'),
                title=content.get('title') or '', url=content.get('url') or '')
    start = word('wake_start')
    start = start.format(number=n) if start else ''
    ask = label('admin_ask')
    if to == cap and who != cap:
        text = head + word('wake_unowned', owner_state=_owner_state(who))
    elif ask and ask in labels(node):
        text = head + word('wake_admin', label=ask, start=start)
    else:
        text = head + word('wake_owner', start=start)
    return to, text, word('wake_subject', number=n, column=yard)
