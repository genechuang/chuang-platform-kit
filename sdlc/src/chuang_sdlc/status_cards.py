"""Status cards: draft cards in one board column that say where something stands outside the board (an app's builds
in a store's review, a deploy's rungs), each rewritten only when its words change.

The caller owns every word. It hands sync() the facts for each key, a since mark for each (since()), and a `render`
that turns them into a card's (title, body). sync() keeps one draft card per key in the column whose role it names:
a missing card is created there, a card whose words changed is edited, and the rest are left alone, so an untouched
card stays untouched (each edit is a board event every listener hears). What the caller keeps between reads is
{'since': marks, 'cards': {key: {item, draft, words}}}.
"""
from datetime import datetime, timezone

from chuang_sdlc.tickets import DRAFT_EDIT

ADD = ('mutation($p:ID!,$t:String!,$b:String){addProjectV2DraftIssue(input:{projectId:$p,title:$t,body:$b})'
       '{projectItem{id content{... on DraftIssue{id}}}}}')
EDIT = DRAFT_EDIT
COLUMN = ('mutation($p:ID!,$i:ID!,$f:ID!,$o:String!){updateProjectV2ItemFieldValue(input:{projectId:$p,itemId:$i,'
          'fieldId:$f,value:{singleSelectOptionId:$o}}){projectV2Item{id}}}')


def since(old: dict, facts: dict, now: datetime, sig, waiting_since=None) -> dict:
    """When each card's state began: {key: {sig, at}}. `sig(key, facts)` is what counts as a change; while
    `waiting_since(facts)` gives an instant (a store's own submission time for a review it holds), that is the mark;
    otherwise the old mark is kept while the signature is the same, and `now` starts a new one."""
    out = {}
    for key, f in facts.items():
        s = sig(key, f)
        outside = waiting_since(f) if waiting_since else None
        if outside:
            out[key] = {'sig': s, 'at': outside}
            continue
        was = (old or {}).get(key) or {}
        out[key] = was if was.get('sig') == s and was.get('at') else {'sig': s, 'at': now.isoformat(timespec='seconds')}
    return out


def _instant(iso):
    try:
        at = datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at   # utc-ok: a stored instant with no zone is UTC


def waited(at_iso: str, now: datetime) -> str:
    """'2d 4h', '5h 12m', '7m': how long since `at_iso`; '' for none or an unreadable one."""
    at = _instant(at_iso) if at_iso else None
    if at is None:
        return ''
    minutes = max(0, int((now - at).total_seconds() // 60))
    days, hours, mins = minutes // 1440, minutes % 1440 // 60, minutes % 60
    return f'{days}d {hours}h' if days else f'{hours}h {mins}m' if hours else f'{mins}m'


def when(iso: str, zone) -> str:
    """An instant in people's words in `zone` with its year and zone word ('11/10/26 3:12 PM PT'); '' for none."""
    at = _instant(iso) if iso else None
    if at is None:
        return ''
    from chuang_platform_kit import clock
    return clock.human_time(at, weekday=False, tz_word=True, tz=zone)


def sync(call, cfg: dict, role: str, keys, facts: dict, marks: dict, render, kept: dict) -> dict:
    """The cards made what `facts` says, in `keys`' order. `call(document, variables)` is one GraphQL call answering
    its `data` (None writes nothing); `cfg` the board's config (its `id`, `stages` by role and the Status field's
    `id` and `options`); `render(key, facts, mark)` a card's (title, body). Returns {'since': marks, 'cards': ...,
    'written': n}. No `call`, or a board with no column for `role`, writes nothing and keeps the old cards; a refusal
    of one card leaves the others for the next read."""
    cards = dict((kept or {}).get('cards') or {})
    out = {'since': marks, 'cards': cards, 'written': 0}
    column = (cfg or {}).get('stages', {}).get(role)
    status = (cfg or {}).get('fields', {}).get('Status', {})
    option = (status.get('options') or {}).get(column or '')
    if call is None or not option:
        return out
    for key in keys:
        if key not in facts:
            continue
        title, body = render(key, facts[key], marks.get(key) or {})
        words = title + '\n' + body
        have = cards.get(key) or {}
        if have.get('words') == words:
            continue
        try:
            if have.get('draft'):
                call(EDIT, {'d': have['draft'], 't': title, 'b': body})
                cards[key] = {**have, 'words': words}
            else:
                made = call(ADD, {'p': cfg['id'], 't': title, 'b': body})['addProjectV2DraftIssue']['projectItem']
                call(COLUMN, {'p': cfg['id'], 'i': made['id'], 'f': status['id'], 'o': option})
                cards[key] = {'item': made['id'], 'draft': made['content']['id'], 'words': words}
            out['written'] += 1
        except Exception:   # noqa: BLE001 - the caller logs the count; the next read tries this card again
            continue
    return out
