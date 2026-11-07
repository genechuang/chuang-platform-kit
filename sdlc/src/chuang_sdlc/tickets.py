"""A project's tickets on a GitHub Project (v2) board: their fields and moves along the board's flow, filing (with the
estimate stage every new ticket passes, however it is filed), the triage line and its sweep, the shipped stage, the
change order a ship files, and the board kept the way its config says.

The board is a dict of `board.py`'s shape. Its fields are a schema every board using this module has: Status (the
columns), Light (a card's state within its column; optional), Owner, Priority (P1 to P4), Kind (Feature, Bug, Task,
Action item), Area, and the optional number fields Estimate, LOE original and Actual (hours) and the text field
Release. `labels` names each Kind's issue label.

The host wires it once (`configure`): where the board's config comes from, the GitHub runner (a `Client`, or a suite's
fake with the same methods), the record store (get, set, merge and create on (collection, id), Firestore's shape), the
people by role, and the hooks (mail a session or person, mark a move into the ship column before it is written). Each
provider is called when it is used, so a host's own names stay the seam its suites patch. Nothing has a default that
reaches a real service: unwired, a call that needs the board or the runner raises NotConfigured, and a missing store
or hook is a step not done, said in the caller's line.

Every function that talks to GitHub takes `run=` (the runner) and `cfg=` (the board) and answers None, False or a
line on a refusal, never raising: a move never stops its caller.
"""
import logging
import os
import re
import sys

from . import board as B
from . import loe as L


class NotConfigured(RuntimeError):
    """The host has not wired what this call needs (configure)."""


_HOST = {
    'board': None,          # () -> the board's config dict
    'runner': None,         # () -> the GitHub runner (Client)
    'store': None,          # () -> the record store, or None when none is at hand (a suite's offline run)
    'raw_store': None,      # () -> the store keep_record merges the raw estimate into; the record store when unset
    'loe_records': None,    # () -> every LOE record (a list of dicts)
    'factor_of': None,      # (raw_min) -> (factor, n, span); default: loe_factor over loe_records
    'mail': None,           # (to, text, subject) -> the line said; a letter to a ticket's owner
    'before_ship_move': None,   # (numbers, run) -> None: the pipeline's own move into the ship column, marked first
    'decide_letter': None,  # (number, choice, title, url, priority, owner, held, ranked, target) -> (subject, text)
    'people': {},           # {'triager', 'default_owner', 'captain'}: session or person names on the Owner field
    'labels': {},           # {'triage', 'member_title', 'admin_ask', 'change_order'}: the host's label names, none by default
    'collections': {},      # {'loe', 'starts'}: the host's record collections, none by default
    'default_area': '',     # the Area of a bug whose feature has none, and of a triaged alert naming none
    'change_order_area': '',  # the Area a ship's change order is filed under
    'card_fields': {},      # {key: field name}: more fields board_items reads onto each card
    'start_hint': '',       # the host's command that records a ticket's start, named in an unmeasured LOE
    'calibrated_since': None,   # epoch seconds: before it a record's estimate_min was the raw (raw_on_record); None never
}


def configure(**wiring) -> None:
    """Wire the module to the host (module docstring): `board`, `runner`, `store`, `raw_store`, `loe_records`,
    `factor_of`, `mail`,
    `before_ship_move`, `decide_letter` (callables), `people`, `labels`, `collections`, `card_fields` (dicts merged over
    the defaults), `default_area`, `change_order_area`, `start_hint` (text), `calibrated_since` (epoch seconds), and `words` ({name: text} for this module's
    worded constants: NO_BODY, LOE_RULE, REUSE_RULE, PRIORITY_RULE, P1_RULE, PLACEHOLDER_BODY, CHANGE_ORDER_NOTE,
    ESTIMATE_COMMAND).
    Unknown names are refused."""
    words = wiring.pop('words', None) or {}
    for name, value in wiring.items():
        if name not in _HOST:
            raise TypeError(f'configure() takes no {name!r}')
        _HOST[name] = dict(_HOST[name], **value) if isinstance(_HOST[name], dict) and isinstance(value, dict) else value
    for name, value in words.items():
        if name not in WORDS:
            raise TypeError(f'configure(words=) takes no {name!r}')
        globals()[name] = value


def _board() -> dict:
    if _HOST['board'] is None:
        raise NotConfigured('no board: configure(board=)')
    return _HOST['board']()


def _runner():
    if _HOST['runner'] is None:
        raise NotConfigured('no GitHub runner: configure(runner=)')
    return _HOST['runner']()


def is_host_runner(run) -> bool:
    """Whether `run` is the host's own runner (configure(runner=)), not a suite's fake passed in."""
    return _HOST['runner'] is not None and run is _HOST['runner']()


def host_store():
    """The host's record store, or None (none wired, or the host has none at hand)."""
    return _HOST['store']() if _HOST['store'] is not None else None


def person(role: str) -> str:
    """The name the host gave a role (`triager`, `default_owner`, `captain`); '' when it gave none."""
    return (_HOST['people'] or {}).get(role, '')


def label(role: str, required: bool = True) -> str:
    """The label the host gave a role (`triage`, `member_title`, `admin_ask`, `change_order`); NotConfigured when it
    gave none and the caller needs one, '' when `required` is False (a label a board may simply not use)."""
    value = (_HOST['labels'] or {}).get(role, '')
    if not value and required:
        raise NotConfigured(f'no {role} label: configure(labels={{{role!r}: ...}})')
    return value


def collection(role: str) -> str:
    """The record collection of a role (`loe`, `starts`); NotConfigured when the host named none."""
    value = (_HOST['collections'] or {}).get(role, '')
    if not value:
        raise NotConfigured(f'no {role} collection: configure(collections={{{role!r}: ...}})')
    return value


KINDS = {'feature': 'Feature', 'bug': 'Bug', 'task': 'Task', 'action-item': 'Action item'}


NO_BODY = ("tickets: not filed: a ticket's body is its plan: give --body \"<the plan>\" or --body-file <path>, never "
           "the title alone")


LOE_RULE = ("tickets: not filed: --loe <estimate> is required, the filer's own level-of-effort estimate for the scoped plan, "
            "in session time, never person-days: 30m, 2h, 1d (a day is 8 hours of session work)")


# The whole rest of the line, for loe_minutes to read: `2 hours` and `1h 30m` are one estimate each.
LOE_LINE = re.compile(r'^LOE estimate:[ \t]*([^\n]*?)[ \t\r]*$', re.M)


def body_loe(body: str):
    """The text of a body's `LOE estimate:` line, the whole rest of the line (`2 hours`, `1h 30m`), or None when it has
    none; loe_minutes reads it."""
    m = LOE_LINE.search(body or '')
    return m.group(1) if m else None


REUSE_RULE = ("tickets: not filed: --reuse \"<what>\" is required (or a `Reuse:` line in the body): what the plan reuses "
              "from the shared code's registry, the module or function, or `none (why)`")
REUSE_LINE = re.compile(r'^Reuse:\s*\S', re.M)


P1_RULE = 'P1 means now: its owner drops other work for it.'


PRIORITY_RULE = ("tickets: --priority is required, the filer's own judgement by the guidelines: P1 now (production broken "
                 "for its users or money, someone who cannot sign in or use it, or the lead's own \"now\"), P2 a bug, P3 a "
                 "feature or an improvement, P4 a parked idea")


PLACEHOLDER_BODY = ("A placeholder, so this column is never empty: GitHub's board stops taking a drop into a column that "
                    "emptied under an open page. Moved, it goes straight back; deleted, the next ship re-makes it. Leave it be.")


CHANGE_ORDER_NOTE = ''   # a host's sentence after the change order's Light line (where else the ship is named)


ESTIMATE_COMMAND = 'estimate'   # the host's command that re-estimates a ticket, named where an estimate was not written


WORDS = ('NO_BODY', 'LOE_RULE', 'REUSE_RULE', 'PRIORITY_RULE', 'P1_RULE', 'PLACEHOLDER_BODY', 'CHANGE_ORDER_NOTE',
         'ESTIMATE_COMMAND')


ORIGINAL_FIELD = 'LOE original'   # the raw estimate on the board, in hours


ESTIMATE_QUERY = ('query($id: ID!) { node(id: $id) { ... on ProjectV2Item { id '
                  'estimate: fieldValueByName(name: "Estimate") { ... on ProjectV2ItemFieldNumberValue { number } } } } }')


# ------------------------------------------------------------------ GitHub
class Client:
    """GitHub for the ticket tools, through chuang_platform_kit.github's throttled client: the one seam every function
    here takes as `run=`, and the thing a suite's fake stands in for. `tok` is the token or a callable answering it,
    read at the first call (None: no token, every call refused); `cfg` the board's config or a callable answering it
    (the repository and the Project are the board's). Every method answers None or False when GitHub refuses (a
    GitHubError, the throttle's RateLimited, no token), never raises, and keeps the refusal's text in `error`. Issues are
    read in gh's `--json` shape (state OPEN or CLOSED, stateReason, labels [{name}], createdAt, comments [{author
    {login}, body}])."""

    def __init__(self, tok=None, cfg=None, opener=None):
        self._tok, self._cfg, self.opener, self.error = tok, cfg, opener, ''
        self._nodes = {}   # issue number -> GraphQL node id, read once a process (item_id asks for every move)

    @property
    def cfg(self) -> dict:
        """The board's config: the one given, else the host's (configure(board=))."""
        cfg = self._cfg if self._cfg is not None else _board
        return cfg() if callable(cfg) else cfg

    @property
    def repo(self) -> str:
        """The board's repository, owner/name."""
        return self.cfg['repo']

    @property
    def token(self) -> str:
        """The token, a callable's answer read once; '' when there is none."""
        if callable(self._tok):
            self._tok = self._tok() or ''
        return self._tok or ''

    def auth_ok(self) -> bool:
        """Whether a token is at hand (without one, a commit check reads the form alone)."""
        return bool(self.token)

    def _refused(self) -> bool:
        self.error = ''
        if not self.token:
            self.error = 'no GitHub token (GH_TOKEN, GITHUB_TOKEN or a signed-in gh)'
            return True
        return False

    def _call(self, verb, *args, **kw):
        """One kit verb with the token and the repository, its answer; None on a refusal, the reason kept in `error`."""
        from chuang_platform_kit import github
        if self._refused():
            return None
        try:
            return getattr(github, verb)(self.token, self.repo, *args, opener=self.opener, **kw)
        except github.GitHubError as e:
            self.error = str(e)
            return None

    def graphql(self, query: str, variables: dict = None):
        """The `data` of one GraphQL call; None on a refusal."""
        from chuang_platform_kit import github
        if self._refused():
            return None
        try:
            return github.graphql(self.token, query, variables or {}, opener=self.opener)
        except github.GitHubError as e:
            self.error = str(e)
            return None

    @staticmethod
    def _shape(got: dict) -> dict:
        """A REST issue (github.issue) in gh's `--json` shape: number, title, body, state OPEN/CLOSED, stateReason,
        labels [{name}], createdAt, url, pr, and `id` (GitHub's numeric id) and `node` (the GraphQL id) beside."""
        reason = (got.get('state_reason') or '').upper()
        return {'number': got['number'], 'title': got['title'], 'body': got.get('body') or '', 'state': (got.get('state') or '').upper(),
                'stateReason': reason, 'labels': [{'name': n} for n in got.get('label_names') or []],
                'createdAt': got.get('created_at') or '', 'url': got.get('html_url') or '', 'pr': bool(got.get('pull_request')),
                'id': got.get('id'), 'node': got.get('node_id') or ''}

    def issue(self, number: int):
        """One issue in gh's shape (its labels, body, state and ids); None when GitHub does not answer or has none."""
        got = self._call('issue', int(number))
        if got is None:
            return None
        if got.get('node_id'):
            self._nodes[int(number)] = got['node_id']
        return self._shape(got)

    def node_id(self, number: int) -> str:
        """An issue's GraphQL node id, read once a process; '' when GitHub does not answer."""
        if int(number) not in self._nodes:
            self.issue(number)
        return self._nodes.get(int(number), '')

    def issues(self, state: str = 'open', labels=(), search: str = '', limit: int = 100, comments: int = 0):
        """The repository's issues in gh's shape (github.issues: a list, or GitHub's search when `search` is given,
        with the last `comments` of each when asked); None when GitHub does not answer."""
        return self._call('issues', state, list(labels), search, limit, comments)

    def create_issue(self, title: str, body: str, labels=()):
        """A new issue; its number, or None."""
        got = self._call('create_issue', title, body, list(labels))
        if not got or not got.get('number'):
            return None
        if got.get('node_id'):
            self._nodes[int(got['number'])] = got['node_id']
        return int(got['number'])

    def comment(self, number: int, body: str) -> bool:
        """A comment on the issue."""
        return self._call('comment', int(number), body) is not None

    def edit_comment(self, comment_id: int, body: str) -> bool:
        """An issue comment's body replaced."""
        return self._call('edit_comment', int(comment_id), body) is not None

    def add_labels(self, number: int, labels) -> bool:
        """Labels added; False when GitHub refused (a label the repository lacks is a 422: create_label, then again)."""
        return self._call('add_labels', int(number), list(labels)) is not None

    def remove_label(self, number: int, label: str) -> bool:
        """A label taken off; True whether or not the issue carried it, False only on a refusal."""
        return self._call('remove_label', int(number), label) is not None

    def create_label(self, name: str, color: str, description: str = '') -> bool:
        """A repository label made, or one of that name given this color and description."""
        return self._call('create_label', name, color, description) is not None

    def assign(self, number: int, login: str, on: bool = True) -> bool:
        """`login` assigned to the issue, or unassigned."""
        return self._call('add_assignees' if on else 'remove_assignees', int(number), [login]) is not None

    def close_issue(self, number: int, reason: str = 'completed', comment: str = '') -> bool:
        """The issue closed with its reason, the comment posted first when given."""
        return self._call('close_issue', int(number), reason, comment) is not None

    def add_sub_issue(self, parent: int, child_id) -> bool:
        """#child (by its numeric id) a sub-issue of #parent."""
        return self._call('add_sub_issue', int(parent), int(child_id)) is not None

    def _project(self, verb, *args, **kw):
        """One of the kit's Projects v2 mutations on the board (its `id`); None on a refusal."""
        from chuang_platform_kit import github
        if self._refused():
            return None
        try:
            return getattr(github, verb)(self.token, self.cfg['id'], *args, opener=self.opener, **kw)
        except github.GitHubError as e:
            self.error = str(e)
            return None

    def item_add(self, node: str):
        """The Project item of an issue (by its node id), added when it is not on the board (GitHub's add is
        idempotent); None when GitHub does not answer."""
        return self._project('project_add_item', node) or None

    def item_field(self, item: str, field_id: str, option_id: str = None, text: str = None, clear: bool = False) -> bool:
        """A Project item's field: a single-select by its option's id, a text by `text`, or emptied with `clear`."""
        if clear:
            return bool(self._project('project_clear_field', item, field_id))
        return bool(self._project('project_set_field', item, field_id, option_id=option_id, text=text))

    def item_fields(self, item: str, fields: dict) -> bool:
        """Several fields of one Project item in ONE request (the kit's project_set_fields): {field_id: an option's id,
        {'text': …}, {'number': …}, or None to clear}."""
        return bool(self._project('project_set_fields', item, fields))

    def draft_add(self, title: str, body: str):
        """A draft card on the Project; its item id, or None."""
        return self._project('project_add_draft', title, body) or None

    def item_top(self, item: str) -> bool:
        """A Project item put at the top of its column."""
        return bool(self._project('project_item_to_top', item))


def _run(run):
    return run if run is not None else _runner()


def _cfg(cfg):
    return cfg if cfg is not None else _board()


def issue(number: int, run=None, repo: str = None):
    """{state ('open' or 'closed'), title, url, pr} of #number, or None when GitHub does not answer (or there is no such
    issue). `repo` is kept for older callers: the runner's repository is the board's."""
    got = _run(run).issue(int(number))
    if not got:
        return None
    return {'state': (got.get('state') or '').lower(), 'title': got.get('title') or '', 'url': got.get('url') or '',
            'pr': bool(got.get('pr'))}


def repo_owner(cfg: dict = None) -> str:
    """The repository's owner, lowercased: the one account whose Triage lines count. Never the board's `owner`, which
    names the Project's organization or user."""
    return _cfg(cfg)['repo'].split('/')[0].lower()


# ------------------------------------------------------------------ the estimate
def estimate_hours(minutes) -> str:
    """Minutes as a board field's hours: 30 -> '0.5', 120 -> '2', 100 -> '1.67' (loe.hours)."""
    return L.hours(minutes)


LOE_UNITS = {'': 1, 'm': 1, 'min': 1, 'mins': 1, 'minute': 1, 'minutes': 1, 'h': 60, 'hr': 60, 'hrs': 60, 'hour': 60,
             'hours': 60, 'd': 480, 'day': 480, 'days': 480}   # a day is 8 hours of session work
_LOE_PART = r'(\d+(?:\.\d+)?)\s*(minutes?|mins?|m|hours?|hrs?|h|days?|d)?'


def loe_minutes(text: str):
    """The minutes an LOE estimate means: `30m`, `2h`, `1.5h`, `1d` (8 hours), a bare number of minutes, or the
    compound and spelled-out forms a triager writes and loe.words prints: `1h 30m`, `1h30m`, `2 hours`, `30 min`,
    `1d 2h`, a sentence's closing period dropped (`1h.`); None when it reads as none of those (a bare number only
    alone: `1 30` is no estimate)."""
    s = str(text or '').strip().lower()
    s = s[:-1].rstrip() if s.endswith('.') else s
    if not s or not re.fullmatch(rf'(?:{_LOE_PART}\s*)+', s):
        return None
    parts = re.findall(_LOE_PART, s)
    if len(parts) > 1 and any(not unit for _n, unit in parts):
        return None
    return int(round(sum(float(n) * LOE_UNITS[unit] for n, unit in parts)))


def loe_words(minutes) -> str:
    """Minutes as the words a comment says: `45m`, `1h 10m`, `2h`, `1d 2h` (loe.words)."""
    return L.words(minutes)


def loe_text(rec: dict) -> str:
    """One LOE record as words (loe.text, the host's start command named when the actual is unmeasured)."""
    return L.text(rec, start_hint=_HOST['start_hint'])


def ticket_estimate(number: int, run=None, cfg: dict = None):
    """A ticket's estimate in minutes: the board's Estimate field first, then its body's `LOE estimate:` line; None
    when it has neither or GitHub does not answer."""
    run, cfg = _run(run), _cfg(cfg)
    item = item_id(number, run, cfg) if 'Estimate' in cfg['fields'] else None
    data = run.graphql(ESTIMATE_QUERY, {'id': item}) if item else None
    node = data.get('node') if isinstance(data, dict) else None
    hours = ((node or {}).get('estimate') or {}).get('number') if isinstance(node, dict) else None
    if hours:
        return int(round(float(hours) * 60))
    got = run.issue(int(number)) or {}
    return loe_minutes(body_loe(got.get('body')))


def loe_factor(raw_min=None, read=None) -> tuple:
    """(the median actual / raw estimate, how many tickets, the span) from every LOE record (`read()`, else the host's
    loe_records), of `raw_min`'s own span (loe.factor); (None, 0, '') when there is no reader or it fails, so the raw
    estimate is filed, and said."""
    read = read or _HOST['loe_records']
    if read is None:
        return None, 0, ''
    try:
        return L.factor(read(), raw_min)
    except Exception:   # noqa: BLE001 - unread: the raw estimate is filed, and said
        return None, 0, ''


def calibrated(raw_text: str, factor_of=None) -> tuple:
    """(the estimate to file, its raw minutes, the note said beside it) for a raw estimate: the raw times its span's
    factor, `10m`, `(raw 4h x 0.04, 1h to 4h: the median actual/estimate of 23 tickets)`; the raw as it is, with a note,
    when there is no factor. In minutes alone (`70m`), the form loe_minutes reads back whole."""
    raw = loe_minutes(raw_text)
    factor_of = factor_of or _HOST['factor_of'] or loe_factor
    by, n, span = factor_of(raw) if raw is not None else (None, 0, '')
    if raw is None or by is None:
        return raw_text, raw, f'(raw, uncalibrated: {n} ticket(s) with an actual)' if raw is not None else ''
    cal = L.calibrate(raw, by)
    return f'{cal}m', raw, f'(raw {loe_words(raw)} x {by:.2f}, {span}: the median actual/estimate of {n} tickets)'


def keep_record(number, raw_min, write=None) -> None:
    """The raw estimate on the ticket's LOE record (`estimate_raw_min`), so the factor is always actual over raw:
    `write(n, doc)`, else a merge into the host's raw store (configure(raw_store=), the record store when unset);
    nothing when neither is at hand. Best effort, a WARNING on a refusal."""
    try:
        if write is None:
            st = _HOST['raw_store']() if _HOST['raw_store'] is not None else host_store()
            if st is None:
                return
            write = lambda n, doc: st.merge(collection('loe'), str(n), doc)  # noqa: E731
        write(number, {'estimate_raw_min': int(raw_min)})
    except Exception as e:   # noqa: BLE001
        print(f'WARNING: tickets: #{number} raw estimate not kept ({type(e).__name__})', file=sys.stderr)


def estimate_stage(number: int, raw, run=None, cfg: dict = None, factor_of=None, record: bool = True, write=None,
                   item: str = None, raw_unfactored: bool = True) -> str:
    """The one estimate stage every new ticket passes, however it was filed (by hand, by a triage line's `LOE:`, by a
    backfill): the raw (`30m`, `2h`, `1d`, or minutes) calibrated (calibrated), the board's Estimate and LOE original
    written in one request, and the raw kept on the LOE record unless `record` is False (a retroactive estimate, kept
    off the records so the calibration stays on estimates made before the work). A raw of 0 or less is no estimate (it
    would file the 5-minute floor and feed a calibrated value to the factor). `item`, the card's Project item when the
    caller holds it, spares the add-to-board lookup. With no calibration factor (the LOE records unread or too few) the
    raw is filed as the Estimate and said, for a person at a terminal; with `raw_unfactored` False (a sweep, whose line
    only a log reads) the Estimate is left empty, LOE original and the record's raw are still written, and a WARNING
    says so, since no later sweep revisits the card. The line."""
    raw_min = raw if isinstance(raw, int) and not isinstance(raw, bool) else loe_minutes(raw)
    if raw_min is None or raw_min <= 0:
        return f'tickets: #{number} no estimate written ({raw!r} is no LOE: 30m, 2h, 1d)'
    cfg, run = _cfg(cfg), _run(run)
    got = (factor_of or _HOST['factor_of'] or loe_factor)(raw_min)   # read once: the calibration below reuses it
    text, _raw, note = calibrated(f'{raw_min}m', lambda _r: got)
    left = got[0] is None and not raw_unfactored
    values = {k: v for k, v in (('Estimate', None if left else estimate_hours(loe_minutes(text))),
                                (ORIGINAL_FIELD, estimate_hours(raw_min)))
              if k in cfg['fields'] and v is not None}
    item = item or item_id(int(number), run, cfg)
    if not (item and set_fields(item, values, run, cfg)):
        return f'tickets: #{number} estimate not written (the board did not take it)'
    if record:
        keep_record(number, raw_min, write)
    if left:
        line = (f"tickets: #{number} LOE original {values.get(ORIGINAL_FIELD)} h; Estimate left empty (no calibration "
                f"factor: {got[1]} ticket(s) with an actual, the LOE records unread or too few; "
                f"`{ESTIMATE_COMMAND} #{number} {loe_words(raw_min)}` writes it)")
        logging.getLogger(__name__).warning(line)
        return line
    return (f"tickets: #{number} Estimate {values.get('Estimate')} h, LOE original {values.get(ORIGINAL_FIELD)} h "
            f"{note}{'' if record else ' (retroactive: kept off the LOE records)'}").rstrip()


def loe_event(numbers, now=None, lines=None) -> dict:
    """One commit's loe.EVENT: {tickets, at, key, active}, the working minutes the local activity log shows for each
    ticket (loe.active_minutes; None for one it never names), for the host to publish and record."""
    import time as _time
    at = int(now if now is not None else _time.time())
    active = L.active_minutes(L.read_log() if lines is None else lines, numbers)
    return {'tickets': [int(n) for n in numbers], 'at': at, 'key': f'{at}-{"-".join(str(n) for n in numbers)}',
            'active': {str(n): m for n, m in active.items()}}


def record_loe(numbers, store=None, run=None, now=None, cfg: dict = None, data: dict = None) -> list:
    """The LOE of each ticket so far, written here: loe.record over this commit's event (`data`, else loe_event of
    `numbers`) into the store (the host's when none is given), the estimate from ticket_estimate and the Actual through
    set_field. Lines; best effort, a refusal is a word, never a failed commit."""
    run = _run(run)
    cfg_ = _cfg(cfg)
    data = data or loe_event(numbers, now)
    try:
        st = store if store is not None else host_store()
        if st is None:
            raise NotConfigured('no record store')
    except Exception as e:   # noqa: BLE001
        return [f'tickets: LOE not recorded (no record store: {type(e).__name__})']

    def write_actual(n, hours):
        item = item_id(int(n), run, cfg_) if 'Actual' in cfg_['fields'] else None
        return bool(item) and set_field(item, 'Actual', hours, run, cfg_)
    try:
        return L.record(data, st, lambda n: ticket_estimate(int(n), run, cfg_), write_actual,
                        records=collection('loe'), starts=collection('starts'), start_hint=_HOST['start_hint'])
    except Exception as e:   # noqa: BLE001 - GitHub or the store refused: said, never a failed commit
        return [f'tickets: LOE not recorded ({type(e).__name__})']


def report_loe(numbers, store=None, run=None, now=None) -> list:
    """The one LOE comment of each ticket, at its ship: `LOE: ` and loe_text of its record, once (the record's
    reported_epoch). A ticket with no record, or one reported already, is skipped. Lines; best effort."""
    import time as _time
    run = _run(run)
    at = int(now if now is not None else _time.time())
    try:
        st = store if store is not None else host_store()
        if st is None:
            raise NotConfigured('no record store')
    except Exception as e:   # noqa: BLE001
        return [f'tickets: LOE not reported (no record store: {type(e).__name__})']
    lines = []
    for n in numbers:
        try:
            rec = st.get(collection('loe'), str(n)) or {}
            if not rec or rec.get('reported_epoch'):
                continue
            text = 'LOE: ' + loe_text(rec)
            if run.comment(int(n), text):
                st.set(collection('loe'), str(n), dict(rec, reported_epoch=at))
                lines.append(f'tickets: #{n} {text}')
        except Exception as e:   # noqa: BLE001
            lines.append(f'tickets: #{n} LOE not reported ({type(e).__name__})')
    return lines


# ------------------------------------------------------------------ the board's fields and columns
def item_id(number: int, run=None, cfg: dict = None):
    """The Project item of #number, adding the issue to the Project when it is not on it (GitHub's add is idempotent:
    addProjectV2ItemById answers the item already there); the issue's node id read once a process. None when GitHub
    does not answer."""
    run = _run(run)
    node = run.node_id(int(number))
    return run.item_add(node) if node else None


def _field_value(item: str, field: str, value: str, run, cfg: dict):
    """(field id, the value as the kit takes it: an option's id, {'text': …}, {'number': …}, or None to clear) for one
    field of set_field / set_fields; a Status write into the ship column marked first (the host's before_ship_move,
    for its own runner only); False for an option the board lacks or a number that is none."""
    spec = cfg['fields'][field]
    if spec.get('type') == 'number':   # hours; '' empties it
        if str(value).strip() == '':
            return spec['id'], None
        try:
            return spec['id'], {'number': float(value)}
        except ValueError:
            return spec['id'], False
    if field == 'Status':
        value = column(value, cfg)   # a role, a new name or an old one: the column the board means
        if value == stage('shipping', cfg) and _HOST['before_ship_move'] is not None and is_host_runner(run):
            n = _item_number(item, run, cfg)
            if n:
                _HOST['before_ship_move']([n], run)
    if 'options' in spec and value == '':
        return spec['id'], None   # a single-select emptied (the Light of a column with none)
    if 'options' in spec:
        if value not in spec['options']:
            value = cfg.get('light_fallback', {}).get(value, value)   # an option the board lacks yet
        if value not in spec['options']:
            return spec['id'], False
        return spec['id'], spec['options'][value]
    return spec['id'], {'text': value}


def set_field(item: str, field: str, value: str, run=None, cfg: dict = None) -> bool:
    """Set a Project field on an item: a single-select by its option's name, a text field by its text, a number field
    by its value (through set_fields' one request)."""
    run, cfg = _run(run), _cfg(cfg)
    fid, got = _field_value(item, field, value, run, cfg)
    if got is False:
        return False
    if got is None:
        return run.item_field(item, fid, clear=True)
    if isinstance(got, dict) and 'number' in got:
        return set_fields(item, {field: value}, run, cfg)
    if isinstance(got, dict):
        return run.item_field(item, fid, text=got['text'])
    return run.item_field(item, fid, option_id=got)


def set_fields(item: str, values: dict, run=None, cfg: dict = None) -> bool:
    """Several Project fields of one item in ONE request ({field: value}, each as set_field takes it): the kit's
    project_set_fields through the runner's item_fields. A runner with no item_fields (an older fake) writes them one
    by one. False when any value is an option the board lacks (nothing written) or the write failed."""
    run, cfg = _run(run), _cfg(cfg)
    fields = {}
    for field, value in values.items():
        fid, got = _field_value(item, field, value, run, cfg)
        if got is False:
            return False
        fields[fid] = got
    if not fields:
        return True
    if not hasattr(run, 'item_fields'):
        ok = True
        for fid, got in fields.items():
            ok = (run.item_field(item, fid, clear=True) if got is None
                  else run.item_field(item, fid, text=str(got.get('text', got.get('number'))))
                  if isinstance(got, dict) else run.item_field(item, fid, option_id=got)) and ok
        return bool(ok)
    return bool(run.item_fields(item, fields))


def set_stage(item: str, role: str, run=None, cfg: dict = None) -> bool:
    """A card to a role's column and its Light (each column a phase, the Light its state within it), one request; a
    column named by its name is its first role. A board with no Light field sets the column alone."""
    run, cfg = _run(run), _cfg(cfg)
    if role not in cfg['stages']:
        role = next((r for r in cfg['stages'] if stage(r, cfg) == column(role, cfg)), role)
    values = {'Status': role}
    if 'Light' in cfg['fields'] and role in cfg.get('lights', {}):
        values['Light'] = cfg['lights'][role]
    return set_fields(item, values, run, cfg)


def stage(role: str, cfg: dict = None) -> str:
    """A column's name on the board by its role (board.stage)."""
    return B.stage(role, _cfg(cfg))


def column(name: str, cfg: dict = None) -> str:
    """A Status as the board names it now, an old name read as its new one and a role as its column (board.named)."""
    return B.named(name, _cfg(cfg))


def flow(cfg: dict = None) -> tuple:
    """The pipeline's columns in order (board.flow)."""
    return B.flow(_cfg(cfg))


def rank(col: str, light: str = '', raw: str = '', cfg: dict = None):
    """A card's place along the flow as a role's index (board.rank); None off the flow."""
    return B.rank(col, light, raw, _cfg(cfg))


def light_for(col: str, cfg: dict = None):
    """The Light a card moved into this column should show (board.light_for)."""
    return B.light_for(col, _cfg(cfg))


def is_placeholder(title: str, cfg: dict = None) -> bool:
    """Whether a card's title is a column's placeholder (board.is_placeholder)."""
    return B.is_placeholder(title, _cfg(cfg))


def start(numbers, run=None, cfg: dict = None) -> list:
    """A session picks these tickets up, planning included: each to the shipyard column (never a step back) and, while
    it is there, its Light the `working` one. A card already past the shipyard keeps its light, and so does `needs`.
    [(number, ok)]."""
    run, cfg = _run(run), _cfg(cfg)
    lights = cfg.get('lights', {})
    target = cfg['flow'].index('shipyard')
    out = []
    for n in numbers:
        got = ticket_place(n, run, cfg)
        if got is None:
            out.append((n, False))
            continue
        item, now, light, raw = got
        at = rank(now, light, raw, cfg)
        if at is not None and at > target:
            out.append((n, True))
            continue
        item = item or item_id(n, run, cfg)
        values = {} if at == target else {'Status': 'shipyard'}
        if 'Light' in cfg['fields'] and light not in (lights.get('working'), lights.get('needs')):
            values['Light'] = lights['working']
        ok = bool(item) and set_fields(item, values, run, cfg)
        out.append((n, ok))
    return out


OPTIONS_QUERY = ('query($id: ID!) { node(id: $id) { ... on ProjectV2SingleSelectField { options { id name color description } } } }')


RENAME_MUTATION = ('mutation($id: ID!, $options: [ProjectV2SingleSelectFieldOptionInput!]) { updateProjectV2Field(input: '
                   '{fieldId: $id, singleSelectOptions: $options}) { projectV2Field { ... on ProjectV2SingleSelectField { '
                   'options { id name } } } } }')


def _status_options(run, cfg) -> list:
    """The Status field's options on the board [{id, name, color, description}]; [] when GitHub does not answer."""
    data = run.graphql(OPTIONS_QUERY, {'id': cfg['fields']['Status']['id']})
    try:
        return list(data['node']['options']) if data else []
    except (KeyError, TypeError):
        return []


COLUMNS_UNREAD = "WARNING: tickets: the board's columns could not be read; nothing renamed"


def sync_columns(run=None, cfg: dict = None, tmp: str = None, merge: bool = False) -> str:
    """The board made what its config says: its column labels (_sync_options), then its placeholder cards
    (placeholders), on one line, a WARNING from either first; a board whose columns could not be read is written
    nothing. `tmp` is kept for older callers."""
    run, cfg = _run(run), _cfg(cfg)
    line = _sync_options(run, cfg, merge)
    if line == COLUMNS_UNREAD:
        return line
    made = placeholders(run, cfg)
    if made.startswith('WARNING: ') and not line.startswith(('WARNING', 'ERROR')):
        line = 'WARNING: ' + line
    return f"{line}; {made.replace('WARNING: ', '', 1).replace('tickets: ', '', 1)}"


def _sync_options(run, cfg: dict, merge: bool = False) -> str:
    """The board's column labels made what the config says: an option whose name is a key of `renamed` is renamed to
    its value, every option sent back with its own id, color and description, so each keeps its id and every ticket
    keeps its column (a save without the ids makes new options and clears the field on every ticket); then read again,
    and any id that changed is said as an ERROR. The line said."""
    have = _status_options(run, cfg)
    if not have:
        return COLUMNS_UNREAD
    renamed, merged = cfg.get('renamed', {}), cfg.get('merged', {})
    todo = [o['name'] for o in have if o['name'] in renamed]
    # A label neither a column of the config nor an old name of one (a hand rename on the board) is drift the other way.
    unknown = [o['name'] for o in have
               if o['name'] not in renamed and o['name'] not in merged and o['name'] not in cfg['stages'].values()]
    if unknown:
        return (f"WARNING: tickets: the board has column(s) the config does not name: {', '.join(unknown)}; "
                f"the board config's `stages` is the one home of the names, so add or rename them there")
    # A merged column is deleted only when asked and only while no card is in it: deleting an option clears the
    # Status of every card in it.
    drop = [o['name'] for o in have if o['name'] in merged] if merge else []
    if drop:
        cards = board_items(filter_columns(drop), run, cfg)
        if cards is None:
            return "WARNING: tickets: the board was not read, so no merged column was deleted"
        held = sorted(it['content']['number'] or 0 for it in cards if it['status'] in drop)
        if held:
            return f"WARNING: tickets: {', '.join(drop)} still hold(s) {', '.join(f'#{n}' for n in held)}; move them first"
    # Each column's description is the config's too, so the words under a column name cannot drift from what it means.
    descs = {stage(r, cfg): text for r, text in cfg.get('descriptions', {}).items()}

    def desc(o):
        return descs.get(renamed.get(o['name'], o['name']), o.get('description') or '')
    redesc = [renamed.get(o['name'], o['name']) for o in have
              if o['name'] not in drop and desc(o) != (o.get('description') or '')]
    if not todo and not drop and not redesc:
        return "tickets: the board's columns already match the config"
    options = [{'id': o['id'], 'name': renamed.get(o['name'], o['name']), 'color': o['color'],
                'description': desc(o)} for o in have if o['name'] not in drop]
    sent = run.graphql(RENAME_MUTATION, {'id': cfg['fields']['Status']['id'], 'options': options})
    if sent is None:
        return f"WARNING: tickets: the board's columns were not renamed ({(getattr(run, 'error', '') or '').strip()[:160]})"
    after = {o['id']: o['name'] for o in _status_options(run, cfg)}
    lost = [o['id'] for o in have if o['id'] not in after and o['name'] not in drop]
    if lost:
        return f"ERROR: tickets: renaming the board's columns changed option ids {lost}: every ticket in them lost its column"
    parts = ((['renamed: ' + ', '.join(f'{old} is {renamed[old]}' for old in todo)] if todo else [])
             + (['merged and deleted: ' + ', '.join(f'{d} into {merged[d]}' for d in drop)] if drop else [])
             + (['described: ' + ', '.join(redesc)] if redesc else []))
    return "tickets: the board's columns " + '; '.join(parts)


_TEXT = '... on ProjectV2ItemFieldTextValue { text } ... on ProjectV2ItemFieldSingleSelectValue { name }'
CARD_FIELDS = (('status', 'Status'), ('light', 'Light'), ('owner', 'Owner'), ('priority', 'Priority'), ('area', 'Area'))


def _items_query(cfg: dict) -> str:
    """The board's items query: each card's issue (number, title, url, labels) or draft (title), and its fields
    (CARD_FIELDS, then the host's card_fields), under the Project's organization or user (`owner_type`)."""
    fields = list(CARD_FIELDS) + [(k, v) for k, v in (_HOST['card_fields'] or {}).items() if k not in dict(CARD_FIELDS)]
    owner = 'user' if cfg.get('owner_type') == 'user' else 'organization'
    return (f'query($owner: String!, $number: Int!, $q: String!, $after: String) {{ {owner}(login: $owner) {{ '
            'projectV2(number: $number) { items(first: 100, after: $after, query: $q) { '
            'pageInfo { hasNextPage endCursor } nodes { id '
            'content { __typename ... on Issue { number title url labels(first: 20) { nodes { name } } } '
            '... on DraftIssue { title } } '
            + ' '.join(f'{_alias(key)}: fieldValueByName(name: "{name}") {{ {_TEXT} }}' for key, name in fields)
            + ' } } } } }')


def _alias(key: str) -> str:
    """A card key as a GraphQL alias (letters, digits and underscores): `b#` is `bnum`."""
    return re.sub(r'\W', '_', key.replace('#', 'num'))


def filter_columns(names) -> str:
    """A board filter's Status term for these columns, each name quoted for the ones with spaces."""
    return 'status:' + ','.join(f'"{n}"' for n in names)


def board_items(query: str, run=None, cfg: dict = None):
    """The board's cards that match GitHub's own filter (`is:open is:issue`, a column's `filter_columns`), each in `gh
    project item-list`'s shape (title, status, light, owner, priority, area and the host's card_fields, labels, content
    {type, number, url}) with its item `id`; None when GitHub does not answer. The filter runs on GitHub's side, so a
    read costs a point a hundred cards it returns, never the whole board's hundreds."""
    run, cfg = _run(run), _cfg(cfg)
    keys = [k for k, _n in CARD_FIELDS] + [k for k in (_HOST['card_fields'] or {}) if k not in dict(CARD_FIELDS)]
    owner = 'user' if cfg.get('owner_type') == 'user' else 'organization'
    query_text = _items_query(cfg)
    out, after = [], None
    while True:
        data = run.graphql(query_text, {'owner': cfg['owner'], 'number': int(cfg['number']), 'q': query, 'after': after})
        if data is None:
            return None
        try:
            page = data[owner]['projectV2']['items']
        except (KeyError, TypeError):
            return None
        if not isinstance(page, dict):
            return None
        for node in page.get('nodes') or []:
            content = node.get('content') or {}

            def val(k):
                v = node.get(_alias(k)) or {}
                return v.get('name') or v.get('text') or ''
            card = {'id': node.get('id'), 'title': content.get('title') or ''}
            card.update({k: val(k) for k in keys})
            card['labels'] = [n['name'] for n in (content.get('labels') or {}).get('nodes') or []]
            card['content'] = {'type': content.get('__typename') or 'DraftIssue', 'number': content.get('number'),
                               'url': content.get('url') or ''}
            out.append(card)
        info = page.get('pageInfo') or {}
        if not info.get('hasNextPage'):
            return out
        after = info.get('endCursor')


def is_draft(card: dict) -> bool:
    """Whether a board_items card is a draft (a column's placeholder, a host's own draft cards): no issue behind it,
    so nobody's to ask about, light or move."""
    content = (card or {}).get('content') or {}
    return content.get('type') == 'DraftIssue' or not content.get('number')


def placeholders(run=None, cfg: dict = None) -> str:
    """One placeholder draft card in each flow column that can empty, the config's `placeholders` by role: the board's
    drafts read once with GitHub's own `is:draft` filter; a role with no draft titled for it or its column is made (the
    draft, then its Status; no Light), one in another column is put back, one in place is left, and any other draft is
    never touched. Idempotent. The line said: what was made or put back, a WARNING naming what GitHub refused or that
    the drafts could not be read, or that all are in place."""
    run, cfg = _run(run), _cfg(cfg)
    want = cfg.get('placeholders') or {}
    if not want:
        return 'tickets: placeholders: none configured'
    drafts = board_items('is:draft', run, cfg)
    if drafts is None:
        return "WARNING: tickets: the board's drafts could not be read; no placeholder made"
    have = {}
    for d in drafts:
        role = B.placeholder_role(d.get('title') or '', cfg)
        if role and role not in have:
            have[role] = d
    made, righted, refused = [], [], []
    for role, named in want.items():
        col = stage(role, cfg)
        d = have.get(role)
        if d is None:
            item = run.draft_add(named, PLACEHOLDER_BODY)
            (made if item and set_field(item, 'Status', role, run, cfg) else refused).append(col)
        elif column(d.get('status') or '', cfg) != col:
            (righted if set_field(d['id'], 'Status', role, run, cfg) else refused).append(col)
    if not made and not righted and not refused:
        return f'tickets: placeholders: all {len(want)} in place'
    parts = (([f"made: {', '.join(made)}"] if made else []) + ([f"put back: {', '.join(righted)}"] if righted else [])
             + ([f"refused by GitHub: {', '.join(refused)}"] if refused else []))
    return ('WARNING: ' if refused else '') + 'tickets: placeholders ' + '; '.join(parts)


ITEM_STATUS_QUERY = ('query($id: ID!) { node(id: $id) { ... on ProjectV2Item { id '
                     'status: fieldValueByName(name: "Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } } '
                     'light: fieldValueByName(name: "Light") { ... on ProjectV2ItemFieldSingleSelectValue { name } } } } }')


def ticket_place(number: int, run=None, cfg: dict = None):
    """(item id, column, Light, the column's raw name) of one ticket on the Project ('' for a card with no Status or no
    Light); None when GitHub does not answer. Its item by item_id and the item's own Status, a few GraphQL points a
    ticket: a move reads only the tickets it moves, never the board."""
    run, cfg = _run(run), _cfg(cfg)
    item = item_id(number, run, cfg)
    if not item:
        return None
    data = run.graphql(ITEM_STATUS_QUERY, {'id': item})
    if data is None:
        return None
    node = data.get('node') if isinstance(data, dict) else None
    if not isinstance(node, dict) or node.get('id') != item:
        return None
    raw = (node.get('status') or {}).get('name') or ''
    return item, column(raw, cfg), ((node.get('light') or {}).get('name') or ''), raw


def ticket_column(number: int, run=None, cfg: dict = None):
    """(item id, column) of one ticket (ticket_place's first two); None when GitHub does not answer."""
    got = ticket_place(number, run, cfg)
    return got[:2] if got else None


# The roles whose cards are put at the top of their column as they move in (the newest ship's cards first).
TOP_ROLES = ('shipping',)


def move(numbers, status: str, run=None, cfg: dict = None, back: bool = False) -> list:
    """Every ticket to `status` (a role or a column's name); [(number, ok)]. Along the flow a ticket already at `status`
    or past it stays where it is (said as moved: it is where the move means), so a re-run never steps a ticket back.
    `back` is the one deliberate step back: it moves only a card that is past `status`. Each ticket's own column is read
    (ticket_place, never the whole board); one that cannot be read is not moved ((number, False)), since its column is
    not known. A column off the flow is set without a read. The order is by role: two roles sharing a column are told
    apart by the Light. The pipeline's own move into the ship column is marked first (before_ship_move)."""
    run, cfg = _run(run), _cfg(cfg)
    roles = cfg['flow']
    role = status if status in cfg['stages'] else next((r for r in roles if stage(r, cfg) == column(status, cfg)), status)
    target = roles.index(role) if role in roles else None
    out = []
    for n in numbers:
        item = None
        if target is not None:
            got = ticket_place(n, run, cfg)
            if got is None:
                out.append((n, False))
                continue
            item, now, light, raw = got
            at = rank(now, light, raw, cfg)
            if at is not None and (at <= target if back else at >= target):
                out.append((n, True))
                continue
        item = item or item_id(n, run, cfg)
        if (cfg['stages'].get(role, column(role, cfg)) == stage('shipping', cfg) and _HOST['before_ship_move'] is not None
                and is_host_runner(run)):
            _HOST['before_ship_move']([n], run)
        ok = bool(item) and set_stage(item, role, run, cfg)
        if ok and role in TOP_ROLES:
            to_top(item, run, cfg)
        out.append((n, ok))
    return out


ITEM_NUMBER = 'query($id: ID!) { node(id: $id) { ... on ProjectV2Item { content { ... on Issue { number } } } } }'


def _item_number(item: str, run=None, cfg: dict = None):
    """The issue number of a Project item (None for a draft, or when GitHub does not answer)."""
    data = _run(run).graphql(ITEM_NUMBER, {'id': item})
    try:
        return ((data['node'] or {}).get('content') or {}).get('number') if data else None
    except (KeyError, TypeError, AttributeError):
        return None


def to_top(item: str, run=None, cfg: dict = None) -> bool:
    """A card put at the top of its column (the kit's project_item_to_top); best effort."""
    return _run(run).item_top(item)


# ------------------------------------------------------------------ filing
def new(kind: str, title: str, body: str = '', owner: str = '', status: str = 'backlog', b: str = '', run=None,
        cfg: dict = None, area: str = '', priority: str = '', loe: str = ''):
    """File a ticket and put it on the Project with its Kind, Area, Owner, Priority, Status (and its Light), B# and
    Estimate in ONE field write; its number, or None (also when `area` is none of the board's Areas). A ticket with no
    body, or the title again, is refused (NO_BODY: the body is the plan); an `loe` that reads as no estimate is refused
    (LOE_RULE). The estimate is `loe`, else the body's `LOE estimate:` line."""
    run, cfg = _run(run), _cfg(cfg)
    if area not in cfg['fields']['Area']['options']:
        return None
    if not (body or '').strip() or body.strip() == title.strip():
        raise ValueError(NO_BODY)
    if loe and (loe_minutes(loe) or 0) <= 0:   # a 0 is no estimate either
        raise ValueError(LOE_RULE)
    kind_name = KINDS.get(kind, kind)
    kind_label = cfg['labels'].get(kind_name)
    # The area label at opening: a listener can tell a filed ticket from an alert issue by it.
    labels = ([kind_label] if kind_label else []) + [area_label(area)]
    number = run.create_issue(title, body or title, labels)
    if not number:
        return None
    add_area_label(number, area, run, cfg)
    item = item_id(number, run, cfg)
    if item:
        role = status if status in cfg['stages'] else next((r for r in cfg['stages'] if stage(r, cfg) == column(status, cfg)), status)
        values = {'Kind': kind_name, 'Area': area, 'Status': role}
        if 'Light' in cfg['fields'] and role in cfg.get('lights', {}):
            values['Light'] = cfg['lights'][role]
        if priority:
            values['Priority'] = priority
        if owner:
            values['Owner'] = owner
        if b:
            values['B#'] = b
        est = loe_minutes(loe) if loe else None
        if est is None:
            est = loe_minutes(body_loe(body))
        if est and 'Estimate' in cfg['fields']:
            values['Estimate'] = estimate_hours(est)
        set_fields(item, values, run, cfg)
    return number


STOP_WORDS = frozenset('the a an and or of to in on for with from by at is are be it its this that as when what who '
                       'into not no its their his her our your one two all any each every after before'.split())


def _words(title: str) -> set:
    return {w for w in re.findall(r'[a-z0-9]+', (title or '').lower()) if len(w) > 2 and w not in STOP_WORDS}


def similar(title: str, run=None, cfg: dict = None, share: float = 0.5) -> list:
    """The open issues a new one may duplicate (reuse an issue before filing one): those GitHub finds for the title's
    words whose own title shares at least `share` of them. [(number, title)], closest first; [] when GitHub cannot be
    asked."""
    words = _words(title)
    if not words:
        return []
    query = ' '.join(sorted(words, key=len, reverse=True)[:6])
    rows = _run(run).issues(state='open', search=f'{query} in:title', limit=30) or []
    scored = [(len(words & _words(r.get('title', ''))) / len(words), r['number'], r.get('title', '')) for r in rows]
    return [(n, t) for s, n, t in sorted(scored, key=lambda x: -x[0]) if s >= share]


AREA_PREFIX = 'area:'


def area_label(area: str) -> str:
    """The label for a Project Area: 'Internal tools' is 'area:internal-tools'."""
    return AREA_PREFIX + re.sub(r'\s+', '-', (area or '').strip().lower())


def add_area_label(number: int, area: str, run=None, cfg: dict = None) -> bool:
    """#number labelled `area:<area>` beside its Project Area (a token without the Project's scope can read the label);
    any other area: label comes off. False when GitHub refused (a WARNING)."""
    run = _run(run)
    if not area:
        return False
    want = area_label(area)
    got = run.issue(number) or {}
    have = [lb.get('name') or '' for lb in got.get('labels') or []]
    stale = [lb for lb in have if lb.startswith(AREA_PREFIX) and lb != want]
    ok = want in have or run.add_labels(number, [want])
    for lb in stale:
        ok = run.remove_label(number, lb) and ok
    if not ok:
        print(f'WARNING: tickets: #{number} not labelled {want} ({(getattr(run, "error", "") or "").strip()[:160]})', file=sys.stderr)
        return False
    return True


def area_labels(run=None, cfg: dict = None) -> list:
    """Every ticket on the Project labelled with its Area (a backfill, and a repair after an Area is changed on the
    board): the area labels made first; the board's issues read with GitHub's `is:issue` filter. Lines, one per ticket
    labelled."""
    run, cfg = _run(run), _cfg(cfg)
    for area in cfg['fields']['Area']['options']:
        run.create_label(area_label(area), 'c5def5', f'Project Area: {area}')
    lines = []
    for card in board_items('is:issue', run, cfg) or []:
        number, area = (card.get('content') or {}).get('number'), card.get('area') or ''
        if not number or not area or area_label(area) in (card.get('labels') or []):
            continue
        if add_area_label(int(number), area, run, cfg):
            lines.append(f'tickets: #{number} labelled {area_label(area)}')
    return lines


def area_of(number: int, run=None, cfg: dict = None) -> str:
    """#number's Area, from its `area:` label ('' when it has none or GitHub does not answer)."""
    run, cfg = _run(run), _cfg(cfg)
    got = run.issue(number)
    if not got:
        return ''
    names = {area_label(a): a for a in cfg['fields']['Area']['options']}
    return next((names[lb.get('name')] for lb in got.get('labels') or [] if lb.get('name') in names), '')


def bug(feature: int, title: str, body: str = '', owner: str = '', run=None, cfg: dict = None, area: str = '',
        priority: str = 'P2', loe: str = ''):
    """A bug ticket from a feature: Kind Bug, in the Backlog, the feature's Area (the host's default_area when it has
    none), a sub-issue of #feature, its body linking it, and its Priority set at filing (P2 unless the filer says P1)."""
    run, cfg = _run(run), _cfg(cfg)
    if not (body or '').strip():
        raise ValueError(NO_BODY)
    area = area or area_of(feature, run, cfg) or _HOST['default_area']
    number = new('bug', title, body + f'\n\nFound after #{feature} shipped.', owner, 'backlog',
                 run=run, cfg=cfg, area=area, priority=priority, loe=loe)
    if number:
        _link_sub_issue(feature, number, run)
    return number


def _link_sub_issue(parent: int, child: int, run=None) -> bool:
    """#child made a sub-issue of #parent (by the child's numeric id); False when either read or the link was refused."""
    run = _run(run)
    got = run.issue(child) or {}
    return bool(got.get('id')) and run.add_sub_issue(parent, got['id'])


# ------------------------------------------------------------------ triage
# The investigator's verdict on an issue, the last line of its finding:
#   Triage: P1 | Owner: <name> | Area: <area> | Feature: #42 | LOE: 1h      (Owner, Area, Feature, LOE optional)
#   Triage: none (a network drop the next run passed)                       (nothing to fix: no ticket)
#   Triage: backlog                                                         (a waiting ticket parked)
# `LOE:` is the triager's estimate, which the sweep writes through estimate_stage.
TRIAGE = re.compile(r'^[\s*_>]*Triage:\s*(.+?)\s*$', re.I | re.M)


def triage_line(text: str):
    """The last `Triage:` line in `text` as {priority, owner, area, feature}, with `loe` when its `LOE:` reads as an
    estimate above 0 (loe_minutes) and `loe_unread`, its text, when it does not; {'none': why} for `Triage: none
    (why)`, {'backlog': True}, or None when there is none or it does not read."""
    found = TRIAGE.findall(text or '')
    if not found:
        return None
    value = found[-1].strip().strip('*_ ')
    m = B.NONE.match(value)
    if m:
        return {'none': m.group(1).strip()}
    if value.lower().rstrip('.') == 'backlog':
        return {'backlog': True}
    parts = [p.strip() for p in value.split('|')]
    if not re.fullmatch(r'P[1-4]', parts[0].upper()):
        return None
    out = {'priority': parts[0].upper(), 'owner': '', 'area': '', 'feature': 0}
    for p in parts[1:]:
        key, _, val = p.partition(':')
        key, val = key.strip().lower(), val.strip()
        if key == 'owner':
            out['owner'] = val
        elif key == 'area':
            out['area'] = val
        elif key == 'feature':
            n = B.TICKET_NUMBER.search(val)
            out['feature'] = int(n.group(1)) if n else 0
        elif key == 'loe':   # the triager's estimate; one that does not read is kept to say
            if (loe_minutes(val) or 0) > 0:
                out['loe'] = val
            elif val:
                out['loe_unread'] = val
    return out


def triage(number: int, priority: str, owner: str = '', area: str = '', feature: int = 0, run=None, cfg: dict = None) -> str:
    """An alert issue made a bug ticket in place (never a second issue): on the Project with Kind Bug, in the shipyard
    for a P1 (worked at once) and the backlog otherwise, its Priority, Owner (the host's default_owner when none is
    named or the name is no Owner option) and Area (the feature's, else the host's default_area), the Bug label, a
    sub-issue of #feature when one is named, and a comment saying so. The line."""
    run, cfg = _run(run), _cfg(cfg)
    owner = owner if owner in cfg['fields']['Owner']['options'] else person('default_owner')
    area = (area if area in cfg['fields']['Area']['options']
            else (area_of(feature, run, cfg) if feature else '') or _HOST['default_area'])
    item = item_id(number, run, cfg)
    if not item:
        return f'tickets: #{number} not triaged (the Project did not take it)'
    for field, value in (('Kind', 'Bug'), ('Priority', priority), ('Owner', owner), ('Area', area)):
        if value:
            set_field(item, field, value, run, cfg)
    set_stage(item, 'shipyard' if (priority or '').upper() == 'P1' else 'backlog', run, cfg)
    run.add_labels(number, [cfg['labels']['Bug']])
    add_area_label(number, area, run, cfg)
    if feature:
        _link_sub_issue(feature, number, run)
    note = (f'Triaged: a bug ticket on the Project, {priority}, owner {owner}, area {area}'
            + (f', found after #{feature}' if feature else '') + '. The fix commit names it (`Ticket: #'
            + f'{number}`), and the release that ships it closes it.' + (f' {P1_RULE}' if priority == 'P1' else ''))
    run.comment(number, note)
    return f'tickets: #{number} triaged {priority}, {owner}, {area}'


def queue(run=None, cfg: dict = None) -> list:
    """The open tickets labelled with the triage label, newest first: [(number, title, kind label, filed ISO)]."""
    run, cfg = _run(run), _cfg(cfg)
    rows = run.issues(state='open', labels=[label('triage')], limit=50)
    if rows is None:
        return []
    kinds = {v: k for k, v in cfg['labels'].items()}
    out = []
    for i in rows:
        names = [lb.get('name', '') for lb in i.get('labels') or []]
        kind = next((kinds[n] for n in names if n in kinds), '')
        out.append((i['number'], i['title'], kind, i.get('createdAt', '')))
    return sorted(out, key=lambda r: r[3], reverse=True)


def decide_letter(number: int, choice: str, title: str, url: str, priority: str, owner: str, held: str, ranked: bool,
                  target: str) -> tuple:
    """(subject, text) of the letter a triage decision sends its owner, in neutral words (a host passes its own
    `decide_letter` to configure): where the card stayed when it was held, now and at what priority, or the backlog."""
    if held and choice != 'now':
        return (f'#{number} stays in {held}' + (f', {priority}' if ranked else ''),
                f'#{number} stays in {held}, where it already was' + (f', at {priority}' if ranked else '')
                + f': the triage said {target}, and a decision never steps a card back. {title}\n{url}')
    if choice == 'now':
        return (f'New {priority or "ticket"} for you now: #{number}',
                f'#{number} is {"a " + priority if priority else "yours"} and yours: work it now. {title}\n{url}')
    return (f'New in your backlog: #{number}' + (f', {priority}' if ranked else ''),
            f'#{number} is in your backlog' + (f' at {priority}' if ranked else '') + f'. {title}\n{url}')


def decide(number: int, choice: str, owner: str = '', priority: str = '', run=None, cfg: dict = None, mail=None) -> str:
    """The triager's decision on a waiting ticket: `now` puts it in the shipyard (worked at once) at `priority` (P1
    unless one is given) on `owner` (the host's triager unless an Owner option is named); `backlog` puts it in the
    backlog, keeping the `priority` and `owner` it was given. Either takes the triage label off, comments the decision
    and tells the owner by `mail(to, text, subject)` (none when `mail` is None), in the host's decide_letter. A card
    already at or past the decision's column keeps its column (never a step back), and one whose column cannot be read
    is not decided. The line."""
    run, cfg = _run(run), _cfg(cfg)
    mail = mail or _HOST['mail']   # the host's configured letter, when the caller passes none
    choice = choice.lower()
    if choice not in ('now', 'backlog'):
        return f'tickets: #{number} not decided: say now or backlog'
    named = owner in cfg['fields']['Owner']['options']
    owner = owner if named else person('triager')
    ranked = bool(re.fullmatch(r'P[1-4]', (priority or '').upper()))
    priority = priority.upper() if ranked else 'P1'
    item = item_id(number, run, cfg)
    if not item:
        return f'tickets: #{number} not decided (the Project did not take it)'
    place = ticket_place(number, run, cfg)
    if place is None:
        return f'tickets: #{number} not decided (its column could not be read)'
    target = 'shipyard' if choice == 'now' else 'backlog'
    at = rank(place[1], place[2], place[3], cfg)
    stays = at is not None and at >= cfg['flow'].index(target)
    held = place[1] if stays and at > cfg['flow'].index(target) else ''
    if choice == 'now':
        fields = (('Status', target), ('Priority', priority), ('Owner', owner))
    else:
        fields = (('Status', target),) + ((('Priority', priority),) if ranked else ()) + ((('Owner', owner),) if named else ())
    for field, value in fields:
        if field == 'Status':
            if not stays:
                set_stage(item, value, run, cfg)
        elif value:
            set_field(item, field, value, run, cfg)
    run.remove_label(number, label('triage'))
    kept = ', '.join(v for f, v in fields[1:])
    note = (f'Triage: now. {stage("shipyard", cfg)}, {priority}, {owner} picks it up.' if choice == 'now'
            else 'Triage: backlog' + (f', {kept}' if kept else '') + '.')
    if held:
        note += f' The card stays in {held}, past {stage(target, cfg)}: a decision never steps it back.'
    run.comment(number, note)
    line = f'tickets: #{number} {choice}' + (f', {stage("shipyard", cfg)} {priority} {owner}' if choice == 'now'
                                             else ', Backlog' + (f' {kept.replace(", ", " ")}' if kept else ''))
    if held:
        line += f' (kept in {held})'
    if mail:
        title = ''
        try:
            title = ((run.issue(number) or {}).get('title') or '').strip()
        except Exception:   # noqa: BLE001 - the letter still goes, without the title
            title = ''
        url = f"https://github.com/{cfg['repo']}/issues/{number}"
        subject, text = (_HOST['decide_letter'] or decide_letter)(number, choice, title, url, priority, owner, held, ranked,
                                                                  stage(target, cfg))
        line += '; ' + mail(owner, text, subject)
    return line


def sweep(run=None, cfg: dict = None, mail=None) -> list:
    """Every open issue off the Project whose newest `Triage:` line, in a comment by the repository's owner (nobody
    else's line counts), names a priority: triaged. `Triage: none (why)` and an issue already on the Project (its
    `area:` label) are left, except a ticket still carrying the triage label: its line is the triager's decision,
    applied by `decide`: a P1 is now, P2 to P4 the backlog at that priority and owner, `Triage: backlog` the backlog
    with neither; a ticket carrying the admin-ask label is now whatever its rank. A line's `LOE:` is the ticket's
    estimate stage, said on its line. Lines, one per issue acted on."""
    run, cfg = _run(run), _cfg(cfg)
    issues = run.issues(state='open', limit=100, comments=60)
    if issues is None:
        return [f'tickets: sweep not run (GitHub: {(getattr(run, "error", "") or "").strip()[:120]})']
    lines = []
    for i in issues:
        names = [(lb.get('name') or '') for lb in i.get('labels') or []]
        waiting = label('triage') in names
        if any(n.startswith(AREA_PREFIX) for n in names) and not waiting:
            continue
        mine = '\n'.join(c.get('body') or '' for c in i.get('comments') or []
                         if ((c.get('author') or {}).get('login') or '').lower() == repo_owner(cfg))
        t = triage_line(mine)
        if not t:
            continue
        acted = len(lines)
        if waiting:
            asked = bool(label('admin_ask', required=False)) and label('admin_ask', required=False) in names
            if 'backlog' in t and not asked:
                lines.append(decide(i['number'], 'backlog', run=run, cfg=cfg, mail=mail))
            elif 'priority' in t or asked:
                lines.append(decide(i['number'], 'now' if asked or t.get('priority') == 'P1' else 'backlog',
                                    t.get('owner', ''), t.get('priority', ''), run=run, cfg=cfg, mail=mail))
        elif 'priority' in t:
            lines.append(triage(i['number'], t['priority'], t['owner'], t['area'], t['feature'], run, cfg))
        if len(lines) > acted:
            fix = f"(`{ESTIMATE_COMMAND} #{i['number']} <loe>` writes it)"
            lines[-1] += ('; ' + estimate_stage(i['number'], t['loe'], run, cfg, raw_unfactored=False).removeprefix('tickets: ')
                          if t.get('loe')
                          else f'; LOE "{t["loe_unread"]}" in its Triage line does not read (30m, 2h, 1h 30m, 1d) {fix}'
                          if t.get('loe_unread') else f'; no LOE in its Triage line {fix}')
    return lines


# ------------------------------------------------------------------ the shipped stage
def live_comment(release: str, commits=None) -> str:
    """A shipped ticket's closing comment: the release, then each commit that did its work by short sha and subject
    (GitHub links a sha in an issue comment), so a closed ticket shows the code that shipped it."""
    text = f'Live in release {release[:8] or "?"}: deployed and its production smoke passed.'
    if commits:
        text += '\n\nThe work:\n' + '\n'.join(f'- {sha} {subject}' for sha, subject in commits)
    return text


def release_text(release: str, co=None) -> str:
    """The board's Release for a ship: `CO #12 · 1a2b3c4d`, its change order and its head, or the head alone for a
    ship with no change order."""
    return f'CO #{co} · {release[:8]}' if co else release[:8]


BRANCH_PREFIX = 'ticket/'   # the branch linked to a shipped ticket: ticket/42
LINK_QUERY = ('query($owner: String!, $name: String!, $sha: String!, $ref: String!) { repository(owner: $owner, '
              'name: $name) { id object(expression: $sha) { oid } ref(qualifiedName: $ref) { id target { oid } } } }')
LINK_MUTATION = ('mutation($issue: ID!, $repo: ID!, $oid: GitObjectID!, $name: String!) { createLinkedBranch(input: '
                 '{issueId: $issue, repositoryId: $repo, oid: $oid, name: $name}) { linkedBranch { id } } }')
MOVE_MUTATION = ('mutation($ref: ID!, $oid: GitObjectID!) { updateRef(input: {refId: $ref, oid: $oid, force: false}) '
                 '{ ref { target { oid } } } }')


def link_branch(number: int, sha: str, run=None, cfg: dict = None) -> bool:
    """The issue's Development shows its work: a branch `ticket/<n>` at `sha`, the ticket's last commit, linked to the
    issue (GitHub's createLinkedBranch; no pull request is opened). Idempotent: the branch already at `sha` is done;
    one behind is moved forward to it (updateRef, never forced), so a ticket shipped again shows its later work; one
    GitHub refuses to move as not a fast forward is already past `sha` and is left; it is created only when missing.
    False when GitHub refused or `sha` does not resolve."""
    run, cfg = _run(run), _cfg(cfg)
    owner, name = cfg['repo'].split('/', 1)
    branch = f'{BRANCH_PREFIX}{number}'
    data = run.graphql(LINK_QUERY, {'owner': owner, 'name': name, 'sha': sha, 'ref': f'refs/heads/{branch}'}) if sha else None
    repo = (data or {}).get('repository') or {} if isinstance(data, dict) else {}
    oid = (repo.get('object') or {}).get('oid')
    if not (repo.get('id') and oid):
        return False
    ref = repo.get('ref') or {}
    if ref.get('id'):
        if (ref.get('target') or {}).get('oid') == oid:
            return True
        got = run.graphql(MOVE_MUTATION, {'ref': ref['id'], 'oid': oid})
        if isinstance(got, dict) and ((got.get('updateRef') or {}).get('ref') or {}).get('target'):
            return True
        return 'fast forward' in (getattr(run, 'error', '') or '').lower().replace('-', ' ')
    node = run.node_id(int(number))
    if not node:
        return False
    got = run.graphql(LINK_MUTATION, {'issue': node, 'repo': repo['id'], 'oid': oid, 'name': branch})
    return bool(((got or {}).get('createLinkedBranch') or {}).get('linkedBranch')) if isinstance(got, dict) else False


def _shipped_query(cfg: dict) -> str:
    owner = 'user' if cfg.get('owner_type') == 'user' else 'organization'
    return (f'query($owner: String!, $number: Int!, $q: String!, $after: String) {{ {owner}(login: $owner) {{ '
            'projectV2(number: $number) { items(first: 100, after: $after, query: $q) { '
            'pageInfo { hasNextPage endCursor } nodes { id content { ... on Issue { number title body '
            'labels(first: 20) { nodes { name } } linkedBranches(first: 1) { totalCount } } } '
            'release: fieldValueByName(name: "Release") { ... on ProjectV2ItemFieldTextValue { text } } '
            'estimate: fieldValueByName(name: "Estimate") { ... on ProjectV2ItemFieldNumberValue { number } } '
            f'original: fieldValueByName(name: "{ORIGINAL_FIELD}") {{ ... on ProjectV2ItemFieldNumberValue {{ number }} }} }} }} }} }} }}')


def shipped_cards(run=None, cfg: dict = None):
    """Every shipped ticket on the board (the ship column's issues; change orders left out), each {item, number, title,
    body, release, estimate, original (hours or None), branches (linked branches)}: a backfill's read, with GitHub's own
    filter. None when GitHub does not answer."""
    run, cfg = _run(run), _cfg(cfg)
    owner = 'user' if cfg.get('owner_type') == 'user' else 'organization'
    out, after = [], None
    query = f'status:"{stage("shipping", cfg)}" is:issue'
    while True:
        data = run.graphql(_shipped_query(cfg), {'owner': cfg['owner'], 'number': int(cfg['number']), 'q': query, 'after': after})
        try:
            page = data[owner]['projectV2']['items']
        except (KeyError, TypeError):
            return None
        for node in page.get('nodes') or []:
            c = node.get('content') or {}
            labels = [lb.get('name') for lb in (c.get('labels') or {}).get('nodes') or []]
            if not c.get('number') or (label('change_order', required=False) or None) in labels:
                continue
            out.append({'item': node.get('id'), 'number': c['number'], 'title': c.get('title') or '', 'body': c.get('body') or '',
                        'release': ((node.get('release') or {}).get('text') or '').strip(),
                        'estimate': (node.get('estimate') or {}).get('number'),
                        'original': (node.get('original') or {}).get('number'),
                        'branches': (c.get('linkedBranches') or {}).get('totalCount') or 0})
        info = page.get('pageInfo') or {}
        if not info.get('hasNextPage'):
            return out
        after = info.get('endCursor')


RELEASE_TEXT = re.compile(r'^(?:CO #(\d+) · )?([0-9a-f]{7,40})$')


def raw_on_record(rec: dict):
    """The raw estimate in minutes an LOE record holds: its `estimate_raw_min`, else its `estimate_min` when the
    ticket's earliest record (`started_epoch`, else `first_commit_epoch`) is before the host's `calibrated_since`, when
    that was still the raw; None otherwise (a later estimate_min is the calibrated Estimate copied off the board)."""
    rec = rec or {}
    if (rec.get('estimate_raw_min') or 0) > 0:
        return int(rec['estimate_raw_min'])
    since, cutoff = rec.get('started_epoch') or rec.get('first_commit_epoch'), _HOST['calibrated_since']
    if (rec.get('estimate_min') or 0) > 0 and since and cutoff and int(since) < int(cutoff):
        return int(rec['estimate_min'])
    return None


def backfill_plan(card: dict, last_sha: str, head_of, cos: dict, record: dict, retro: dict) -> dict:
    """What a shipped tickets' backfill writes for one card, as the stages would have written it: {'release': text or
    None, 'raw': (minutes, source) or None, 'original': (minutes, source) or None, 'branch': sha or None}. Release: kept
    when it names its change order; a head alone gains its change order (`cos`, {head8: CO number}); empty, the head of
    the first ship that holds `last_sha` (`head_of(sha)`, None when no ship is known to). The estimate (`raw`, for
    estimate_stage), when both fields are empty: the raw on its LOE record (raw_on_record), its body's `LOE estimate:`
    line, else `retro` ({number: '2h'}, made after the work); when only Estimate is empty, from LOE original. When
    Estimate is set and only LOE original is empty, `original` alone, from the record or the body (never a retro
    estimate): an Estimate made before the work is never recalibrated after it. A raw of 0 is none. Development: its
    last commit, when no branch is linked."""
    n = card['number']
    out = {'release': None, 'raw': None, 'original': None, 'branch': None}
    have = RELEASE_TEXT.match(card.get('release') or '')
    if have and not have.group(1) and cos.get(have.group(2)[:8]):
        out['release'] = release_text(have.group(2), cos[have.group(2)[:8]])
    elif not card.get('release'):
        head = head_of(last_sha) if last_sha else None
        if head:
            out['release'] = release_text(head, cos.get(head[:8]))
    if card.get('original') is None:
        kept = raw_on_record(record)
        line_min = loe_minutes(body_loe(card.get('body')))
        retro_min = loe_minutes((retro or {}).get(str(n)) or (retro or {}).get(n))
        if kept:
            found = (kept, 'record')
        elif (line_min or 0) > 0:
            found = (line_min, 'body')
        elif card.get('estimate') is None and (retro_min or 0) > 0:
            found = (retro_min, 'retro')
        else:
            found = None
        out['raw' if card.get('estimate') is None else 'original'] = found if found and found[0] > 0 else None
    elif card.get('estimate') is None and float(card['original']) > 0:
        out['raw'] = (int(round(float(card['original']) * 60)), 'board')
    if not card.get('branches') and last_sha:
        out['branch'] = last_sha
    return out


def live_numbers(numbers, release: str = '', run=None, cfg: dict = None, work: dict = None, co=None) -> str:
    """These tickets to the shipped column, the Release recorded (release_text: the change order `co` and the head),
    the open ones closed with a comment naming their commits when `work` ({number: [(sha, subject)]}) has them, and
    each one's branch linked at its last commit there (link_branch); then, for the host's own runner, each ticket's one
    LOE comment (report_loe). The line."""
    run, cfg = _run(run), _cfg(cfg)
    done, unlinked = [], []
    for n in numbers:
        item = item_id(n, run, cfg)
        if item:
            if _HOST['before_ship_move'] is not None and is_host_runner(run):
                _HOST['before_ship_move']([n], run)
            set_stage(item, 'shipped', run, cfg)
            if release:
                set_field(item, 'Release', release_text(release, co), run, cfg)
        state = issue(n, run)
        if state and state['state'] == 'open':
            run.close_issue(n, 'completed', live_comment(release, (work or {}).get(n)))
        commits = (work or {}).get(n)
        if commits and not link_branch(n, commits[-1][0], run, cfg):
            unlinked.append(n)
        done.append(n)
    if done and is_host_runner(run):
        report_loe(done, run=run)
    return (f'tickets: live {", ".join(f"#{n}" for n in done) or "none"}' + (f' (release {release_text(release, co)})' if release else '')
            + (f'; no branch linked for {", ".join(f"#{n}" for n in unlinked)}' if unlinked else ''))


# ------------------------------------------------------------------ the change order
# One change-order ticket a ship, listing the tickets it carries: filed at the ship's push, its Light the shipping one,
# then shipped and closed, or failed, with the release (change_order_settle). The label is the host's `change_order`.
CHANGE_ORDER_COLOR = '5319E7'


def change_order_title(head: str, at: float, tz=None) -> str:
    """`Ship 11/2/26 1:41 PM PT: 1a2b3c4d`, the push's minute in the kit's zone and the head."""
    from datetime import datetime, timezone
    from chuang_platform_kit import clock
    t = datetime.fromtimestamp(at, timezone.utc)  # utc-ok: an instant, written in the zone
    return f"Ship {clock.human_time(t, weekday=False, tz_word=True, tz=tz)}: {head[:8]}"


def change_order_body(base: str, head: str, numbers, run=None, how: str = '', cfg: dict = None) -> str:
    """The change order's body: every ticket the release carries with its title, then the range, how it was ordered,
    and what its Light will say. The tickets read once each; one GitHub does not answer is listed by number."""
    run, cfg = _run(run), _cfg(cfg)
    rows = []
    for n in numbers:
        got = issue(n, run)
        rows.append(f'- #{n} {got["title"]}' if got else f'- #{n}')
    lights = cfg.get('lights', {})
    return ('The change order for one ship: the tickets it carries, as the release notes will list them.\n\n'
            + ('\n'.join(rows) if rows else '- none named by these commits') +
            f'\n\nHead {head[:12]}, from {base[:12] or "?"} (https://github.com/{cfg["repo"]}/compare/{base[:12]}...{head[:12]}).'
            + (f' Ordered by {how}.' if how else '') +
            f"\n\nIts Light: {lights.get('shipping', '')} at the push, {lights.get('shipped', '')} (and closed) once the "
            f"production smoke passes, {lights.get('failed', '')} with the failure's bug when a component fails."
            + (f' {CHANGE_ORDER_NOTE}' if CHANGE_ORDER_NOTE else ''))


def change_order_for(head: str, run=None):
    """The change order of the ship whose head is `head` (its title ends with the head's 8 characters), open or closed,
    or None."""
    rows = _run(run).issues(state='all', labels=[label('change_order')], search=f'"{head[:8]}" in:title', limit=10) or []
    return next((r['number'] for r in rows if str(r.get('title', '')).endswith(head[:8])), None)


def change_order_settle(release: str, role: str, note: str, run=None, cfg: dict = None) -> str:
    """The change order of the release at `release` to its end: `shipped`, its Light the shipped one and closed with
    `note`; `failed`, its Light the failed one with `note` as a comment. '' when the release has no change order; a
    refused write is a word."""
    run, cfg = _run(run), _cfg(cfg)
    if not release:
        return ''
    n = change_order_for(release, run)
    if not n:
        return ''
    item = item_id(n, run, cfg)
    ok = bool(item) and set_fields(item, {'Light': cfg['lights'][role]}, run, cfg)
    if role == 'shipped':
        run.close_issue(n, 'completed', note)
    else:
        run.comment(n, note)
    return f'; change order #{n} {cfg["lights"][role]}' + ('' if ok else ' (its Light was not set)')


SMOKE_BUG = '{component} failed its smoke'


def open_bug_for(component: str, run=None, cfg: dict = None):
    """The open bug a component's failed smoke already has (its title starts SMOKE_BUG), or None: a failure that
    repeats is a comment on it, never a second issue."""
    head = SMOKE_BUG.format(component=component)
    rows = _run(run).issues(state='open', search=f'"{head}" in:title', limit=10) or []
    return next((r['number'] for r in rows if str(r.get('title', '')).startswith(head)), None)


# ------------------------------------------------------------------ the command line's helpers
def refused_as_duplicate(title: str, argv, run=None, out=None) -> bool:
    """The command line's sweep before `new` or `bug` files anything: open issues like this title (similar()) are listed
    and nothing is filed, unless `--new` says it is none of them. True when refused."""
    if '--new' in argv:
        return False
    found = similar(title, run=run)
    if not found:
        return False
    out = out or sys.stderr
    print('tickets: not filed: these open issues look like the same work; add to one of them (a comment, or its '
          'Ticket: number in your commit), or pass --new if it is truly different:', file=out)
    for n, t in found[:5]:
        print(f'  #{n} {t}', file=out)
    return True


def _opt(argv, name, default=''):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default


# The flags `new` and `bug` take; any other is refused before anything is filed.
NEW_FLAGS = ('--body', '--body-file', '--owner', '--status', '--b', '--area', '--priority', '--new', '--loe', '--reuse')


BUG_FLAGS = ('--body', '--body-file', '--owner', '--priority', '--new', '--loe')


def body_is_path(argv) -> str:
    """--body's text when it is only a file's path, else '': one line naming a file that exists, or one line ending in
    .md or .txt (the plan belongs in the ticket, never a link to a temp file). --body-file reads the file."""
    text = (_opt(argv, '--body') or '').strip()
    if not text or '\n' in text or _opt(argv, '--body-file'):
        return ''
    return text if os.path.isfile(text) or re.search(r'\.(md|txt)$', text, re.I) else ''


def _body(argv) -> str:
    """The ticket's body: --body's text, or the contents of the file --body-file names."""
    path = _opt(argv, '--body-file')
    if path:
        with open(path, encoding='utf-8') as f:
            return f.read()
    return _opt(argv, '--body')


def _unknown_flags(argv, known) -> list:
    """The --flags in `argv` that its command does not take, so a typo or an unsupported flag is said, never ignored."""
    return [a for a in argv if a.startswith('--') and a not in known]
