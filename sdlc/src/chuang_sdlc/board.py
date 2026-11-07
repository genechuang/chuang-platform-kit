"""A ticket board's names in one place: its columns by role, the flow they make, a card's place along it, the
placeholder cards that keep a column from emptying, and the `Ticket:` trailers a commit names its work by.

The board is a GitHub Project (v2) the host describes once as a dict, usually a JSON file (`load`):

    {"repo": "owner/name", "owner": "<the Project's org or user>", "owner_type": "organization" | "user",
     "number": 1, "id": "PVT_...",
     "fields": {"Status": {"id": "...", "options": {"Backlog": "<option id>", ...}},
                "Estimate": {"id": "...", "type": "number"}, "Release": {"id": "..."}, ...},
     "stages": {"backlog": "Backlog", "shipyard": "Building", "dock": "Ready", ...},   # a column's name by its role
     "flow": ["backlog", "next", "shipyard", "dock", "dev", "shipping", "shipped"],      # the roles, in order
     "renamed": {"<old column name>": "<its name now>"}, "merged": {"<deleted column>": "<the column it joined>"},
     "lights": {"<role>": "<the Light option a card in it shows>", "working": ..., "needs": ...},
     "light_fallback": {"<a Light the board lacks yet>": "<the one written instead>"},
     "placeholders": {"<role>": "— <Column>: what a card here is —"}, "descriptions": {"<role>": "..."},
     "labels": {"Feature": "feature", "Bug": "bug", ...}}

Two roles may share one column (a column's cards told apart by their Light), and a rename is the dict's `renamed` and
the board's own option, never a hunt through code: every caller names a column by its role. Standard library only, so
a webhook that reads one card's column needs nothing else installed.
"""
import json
import re


def load(path: str) -> dict:
    """The board's config from a JSON file."""
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def stage(role: str, cfg: dict) -> str:
    """A column's name on the board by its role (`stages`)."""
    return cfg['stages'][role]


def named(status: str, cfg: dict) -> str:
    """A Status as the board names it now: a role as its column, an old name (`renamed`, `merged`) as its new one,
    anything else as it is."""
    return cfg['stages'].get(status) or cfg.get('renamed', {}).get(status) or cfg.get('merged', {}).get(status, status)


def flow(cfg: dict) -> tuple:
    """The pipeline's columns in order (`flow`, by role)."""
    return tuple(cfg['stages'][r] for r in cfg['flow'])


def rank(col: str, light: str = '', raw: str = '', cfg: dict = None):
    """A card's place along the flow as a role's index: its column and, where two roles share one, its Light (the
    role's light, or that light's fallback); a card in a merged column (`raw` its old name) is the role its column
    merged into, or `dev` for one merged into dev's column. None off the flow."""
    roles = cfg['flow']
    merged_into = cfg.get('merged', {}).get(raw)
    if merged_into and 'dev' in cfg['stages'] and merged_into == stage('dev', cfg) and 'dev' in roles:
        return roles.index('dev')
    if merged_into:
        col = merged_into
    places = [i for i, r in enumerate(roles) if stage(r, cfg) == col]
    if not places:
        return None
    fb = cfg.get('light_fallback', {})
    lit = [i for i in places if cfg.get('lights', {}).get(roles[i])
           and light in (cfg['lights'][roles[i]], fb.get(cfg['lights'][roles[i]]))]
    return lit[0] if lit else places[0]


def light_for(col: str, cfg: dict):
    """The Light a card moved into this column should show: its one role's light, '' for a column with none; None when
    two roles share the column (which of them is the pipeline's to say) or the column is not the board's."""
    roles = [r for r in cfg['stages'] if stage(r, cfg) == named(col, cfg)]
    if len(roles) != 1:
        return None
    return cfg.get('lights', {}).get(roles[0], '')


PLACEHOLDER_EDGE = '—'   # the em dash a placeholder's title opens and closes with


def is_placeholder(text: str, cfg: dict) -> bool:
    """Whether a card's title is a column's placeholder: one of `placeholders`, or any `— … —` (an older wording still
    counts, so a rename never leaves a stray)."""
    t = (text or '').strip()
    if not t:
        return False
    if t in set((cfg.get('placeholders') or {}).values()):
        return True
    return len(t) > 4 and t.startswith(PLACEHOLDER_EDGE + ' ') and t.endswith(' ' + PLACEHOLDER_EDGE)


def placeholder_role(text: str, cfg: dict) -> str:
    """The role whose column a placeholder card belongs in: by its title in `placeholders`, else by the column its
    title names (`— Dock: …`, an old name read as its new one); '' for a title that is no placeholder or names no
    placeholder column."""
    t = (text or '').strip()
    want = cfg.get('placeholders') or {}
    for role, title in want.items():
        if t == title:
            return role
    if not is_placeholder(t, cfg):
        return ''
    name = t[2:-2].split(':', 1)[0].strip()
    col = cfg.get('renamed', {}).get(name) or cfg.get('merged', {}).get(name) or name
    return next((r for r in want if cfg['stages'].get(r) == col), '')


# A commit names its work by its `Ticket:` trailers: `Ticket: #12, #14`, or `Ticket: none (why)` for a commit no work
# item owns. A `none (...)` reason names no ticket, whatever numbers its words carry.
TRAILER = re.compile(r'^\s*Ticket:\s*(.+?)\s*$', re.I | re.M)
NONE = re.compile(r'^none\s*\((.{3,})\)$', re.I)
TICKET_NUMBER = re.compile(r'#(\d+)')


def ticket_trailers(message: str) -> tuple:
    """(numbers, none_reason) from a commit message's `Ticket:` trailers: ([12, 14], '') or ([], 'session notes'),
    the numbers sorted and each once; ([], None) when it names none."""
    numbers, reason = set(), None
    for value in TRAILER.findall(message or ''):
        m = NONE.match(value)
        if m:
            reason = m.group(1).strip()
            continue
        numbers.update(int(n) for n in TICKET_NUMBER.findall(value))
    return sorted(numbers), ('' if numbers else reason)


def ticket_numbers(message: str) -> list:
    """The tickets a commit message's `Ticket:` trailers name, sorted ([] for `Ticket: none (why)` or no trailer)."""
    return ticket_trailers(message)[0]
