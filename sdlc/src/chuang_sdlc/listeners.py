"""The board's event listeners: what a host's webhook receiver does with one board or ticket event, once, off the
board's own clock. Each event carries its state (the moved card's node, the closed ticket's number and reason, the
commit's tickets), so no listener reads the whole board.

  ticket_committed   a commit's working time on each ticket it names: the host's LOE record and the card's Actual
  board_wake         the owner of a card newly in the shipyard column told by one letter, once per move
  board_light        a dragged card's Light made its column's (cosmetic: a refusal is a line, never a retry)
  dock_landed        a push's tickets to the dock column (forward only), the moment it lands
  ticket_closed      a closed ticket ends in the ship or the failed column: an alert closer's close to the ship
                     column, a close as not planned to the failed one with no Light, any other close asked about
  scrum_nudge        the scrum master run on one card the moment an event names it (never its own echo), or its job
  triage_sweep       a `Triage:` line the receiver could not sweep at once: swept here, or its job started

Every claim is a create-only record in the host's claims collection (`configure(claims=)`), expiring
`claim_ttl_days` after it is taken, so a redelivered event is told once. A listener that wants its event delivered
again raises the host's retry exception (`configure(retry=)`, else this module's `Retry`), with its line as the
message; a refusal that a redelivery would only repeat is a WARNING line instead.

The host passes in what reaches its world: the letter (`mail(to, text, sender, subject=)`), the job a listener falls
back to (`start(args)`), the sweep it runs here first (`sweep()`), and its board helpers (`configure(moves=)`), an
object read at call time (a module, so a host's suites may replace one of its functions) that answers:

  number(node), column(node), stage(role), newly_in(data, role), light_fix(data), set_light(item, value, token),
  wake_letter(node, by) -> (to, text, subject), SENDER, STARTS, STARTED_MINUTES, to_dock(numbers, token) -> lines,
  closed_card(n, token) -> card, is_finished(col), is_unplanned(reason), closed_key(n, closed_at), BOARD_MOVES,
  to_unplanned(item, token), unplanned_line(card, reason), to_shipped(item, token), closed_letter(card), SCRUM_SENDER,
  item_for(n, token, opener=), read_item(item, token, opener), estimate_minutes(node), set_field(item, field, value,
  token, opener=)

Nothing has a default that reaches a real service: unwired, a listener that needs the moves or the claims raises
tickets.NotConfigured. Standard library only.
"""
from datetime import datetime, timedelta, timezone

from .tickets import NotConfigured


class Retry(Exception):
    """The delivery should come again (the bus's retry): the message says why."""


_SET = {
    'head': 'listeners',    # every line opens `<head>: ` (after a WARNING: when it is one)
    'claims': '',           # the record collection a listener claims an event's telling in
    'claim_ttl_days': 7,    # a claim's expires_at, this many days after it is taken (the collection's TTL reads it)
    'moves': None,          # the host's board helpers (the module docstring's list), read at call time
    'retry': None,          # the exception class a listener raises for a redelivery; None: Retry here
    'scrum_state': 'scrum',     # the scrum master's record of each card, with `acted_at` when it last acted
    'acted_seconds': 90,    # an event within this of the scrum master's own act on the card is its own echo
    'loe_record': None,     # (data, store, estimate_of, write_actual) -> lines: the host's LOE record of a commit
    'actual_field': 'Actual',   # the card's field the working time is written to, in hours
    'closer': None,         # (title, newest comment) -> whether an alert closer closed it; None: alerts.closed_by_closer
}


def configure(**settings) -> None:
    """The host's settings (the module's _SET says what each is). Unknown names are refused."""
    for name, value in settings.items():
        if name not in _SET:
            raise TypeError(f'listeners.configure() takes no {name!r}')
        _SET[name] = value


def setting(name: str):
    """One of the host's settings (configure)."""
    return _SET[name]


def _h() -> str:
    return _SET['head']


def _retry(text: str) -> Exception:
    return (_SET['retry'] or Retry)(text)


def _moves():
    if _SET['moves'] is None:
        raise NotConfigured('no board helpers: listeners.configure(moves=...)')
    return _SET['moves']


def claims() -> str:
    """The claims collection; NotConfigured when the host named none."""
    if not _SET['claims']:
        raise NotConfigured('no claims collection: listeners.configure(claims=...)')
    return _SET['claims']


def _now(now=None) -> datetime:
    return now or datetime.now(timezone.utc)   # utc-ok: a claim's instant, never a day


def _claim(now) -> dict:
    return {'at': now, 'expires_at': now + timedelta(days=_SET['claim_ttl_days'])}


# ------------------------------------------------------------------ a commit's working time
def ticket_committed(data: dict, store, token: str, opener=None, record=None) -> list:
    """A commit's working time on each ticket it names: the host's LOE record (`record`, else configure's
    `loe_record`), the estimate read from the card (moves.estimate_minutes of its item) and the working time written to
    the card's Actual field. Lines; the retry exception when GitHub or the store refused (every write is idempotent)."""
    M = _moves()
    if not data.get('tickets'):
        return [f'{_h()}: a ticket.committed naming no ticket; nothing to record']
    record = record or _SET['loe_record']
    if record is None:
        raise NotConfigured('no LOE record: listeners.configure(loe_record=...)')
    items = {}

    def item(n):
        if n not in items:
            items[n] = M.item_for(int(n), token, opener=opener)
        return items[n]

    def estimate_of(n):
        it = item(n)
        return M.estimate_minutes(M.read_item(it, token, opener)) if it else None

    def write_actual(n, hours):
        it = item(n)
        if not it:
            return False
        M.set_field(it, _SET['actual_field'], hours, token, opener=opener)
        return True
    try:
        return record(data, store, estimate_of, write_actual)
    except Exception as e:   # noqa: BLE001 - GitHub or the store refused; every write is idempotent
        raise _retry(f"WARNING: {_h()}: the LOE of {data.get('tickets')} not recorded ({type(e).__name__}: "
                     f'{str(e)[:160]}); Pub/Sub delivers it again') from None


# ------------------------------------------------------------------ the board's moves
def board_wake(data: dict, store, now=None, mail=None) -> str:
    """The owner of a card newly in the shipyard column (a Status move into it from another column, moves.newly_in)
    told by one letter (moves.wake_letter, `mail(to, text, sender, subject=)`). A move within moves.STARTED_MINUTES of
    the ticket's own start (the host's moves.STARTS record, {at_epoch}) is the session's own and tells nobody. The drag
    is recorded on the scrum master's record (`shipyard_at`), a newer word than any pause written before it. Claimed
    once per ticket and Status stamp; the retry exception when the letter could not go ('' for a draft or no move in)."""
    M = _moves()
    node = (data or {}).get('node') or {}
    n = M.number(node)
    if not n or not M.newly_in(data, 'shipyard'):
        return ''   # a draft (the ship card is the host's) or no move into the shipyard: nobody to wake, no line
    now = _now(now)
    started = store.get(M.STARTS, str(n)) or {}
    if started.get('at_epoch') and now.timestamp() - float(started['at_epoch']) <= M.STARTED_MINUTES * 60:
        return f'{_h()}: board: #{n} to the shipyard by its own session\'s start; nobody to wake'
    moved_at = (node.get('status') or {}).get('updatedAt') or now.strftime('%Y-%m-%dT%H:%M:%SZ')
    try:
        # The drag is a newer word than any `Scrum: paused` written before it: the scrum master reads this beside the line.
        store.merge(_SET['scrum_state'], str(n), {'shipyard_at': moved_at})
    except Exception:   # noqa: BLE001 - the owner's letter still goes; the pause then stands as before
        pass
    stamp = ((node.get('status') or {}).get('updatedAt') or '').replace(':', '')
    key = f'board-wake-{n}-{stamp or "now"}'
    if mail is None:
        raise NotConfigured('board_wake needs mail=')
    if not store.create(claims(), key, {'number': n, **_claim(now)}):
        return f'{_h()}: board: #{n} to the shipyard was already told'
    to, text, subject = M.wake_letter(node, (data or {}).get('by') or '')
    try:
        sent = mail(to, text, M.SENDER, subject=subject)
    except Exception as e:   # noqa: BLE001 - the bus refused: Pub/Sub delivers the move again
        store.delete(claims(), key)
        raise _retry(f'WARNING: {_h()}: board: #{n}\'s letter to {to} did not go ({type(e).__name__}: '
                     f'{str(e)[:160]}); Pub/Sub delivers it again')
    return f"{_h()}: board: #{n} to the shipyard; {to} told (session mail {(sent or {}).get('id') or '?'})"


def board_light(data: dict, token: str, setter=None) -> str:
    """A dragged card's Light made its column's: moves.light_fix says what, `setter` (else moves.set_light) writes it.
    Its own Light edit comes back as a move whose light already fits, so the loop settles there. A refusal is a WARNING
    line, never a retry: the light is cosmetic and the next move fixes it ('' when nothing is to change)."""
    M = _moves()
    want = M.light_fix(data)
    node = (data or {}).get('node') or {}
    if want is None:
        return ''
    n = M.number(node)
    try:
        (setter or M.set_light)(node.get('id') or (data or {}).get('item') or '', want, token)
    except Exception as e:   # noqa: BLE001 - GitHub refused or the network: the light waits for the next move
        return f'WARNING: {_h()}: board: #{n}\'s Light was not set to {want or "none"} ({str(e)[:160]})'
    return f"{_h()}: board: #{n} in {M.column(node)}: Light {want or 'cleared'}"


def dock_landed(data: dict, token: str, mover=None) -> str:
    """A push landed on the dock branch: its tickets to the dock column at once (`mover`, else moves.to_dock: forward
    only, one line a ticket). A ticket GitHub refused is a WARNING line, never a retry: the warm's own result moves it
    on, and a redelivery would only repeat the same refusal."""
    M = _moves()
    numbers = [int(n) for n in (data or {}).get('tickets') or []]
    sha = str((data or {}).get('sha') or '')[:7]
    if not numbers:
        return f'{_h()}: dock: {sha or "a push"} names no ticket; no card moved'
    lines = (mover or M.to_dock)(numbers, token)
    bad = [line for line in lines if 'not moved' in line or 'not found' in line]
    return (('WARNING: ' if bad else '') + f'{_h()}: dock: {sha} landed: ' + '; '.join(lines))


def ticket_closed(data: dict, store, token: str, now=None, read=None, ship=None, mail=None, also=None) -> str:
    """A closed ticket ends in the ship or the failed column: the event {number, state_reason, by}, and this reads that
    one card (`read`, else moves.closed_card). Off the board, or in a finished column already: nothing. Closed as not
    planned or a duplicate (moves.is_unplanned): the failed column with no Light, the move marked first in the host's
    moves.BOARD_MOVES record, its owner told in one line once. Closed by an alert closer (configure's `closer`, else
    chuang_sdlc.alerts.closed_by_closer): the ship column (`ship`, else moves.to_shipped), marked first. Any other
    close: one letter (moves.closed_letter) asking its owner, once per close (moves.closed_key). `also(n, card,
    reason)` is the host's own word on the close (its alert tracker's), added to the lines it names. GitHub's refusal
    is a WARNING, never a retry: the scrum master's sweep catches a close this missed."""
    M = _moves()
    n = str((data or {}).get('number') or '')
    if not n.isdigit():
        return f'{_h()}: a ticket.closed with no number; nothing to do'
    if mail is None:
        raise NotConfigured('ticket_closed needs mail=')
    n, now = int(n), _now(now)
    try:
        card = (read or M.closed_card)(n, token)
    except RuntimeError as e:
        return f'WARNING: {_h()}: closed #{n} could not be read ({e}); the scrum master\'s sweep catches it'
    extra = also(n, card, (data or {}).get('state_reason')) if also else ''
    if not card or not card.get('column'):
        return f'{_h()}: closed #{n} is not on the board; nothing to do' + extra
    col = card['column']
    if M.is_finished(col):
        return f'{_h()}: closed #{n} is in {col} already; nothing to do' + extra
    reason = str((data or {}).get('state_reason') or card.get('reason') or '').lower()
    if M.is_unplanned(reason):
        # Closed as not planned or a duplicate: nothing shipped and nothing to confirm, so the failed column with no
        # Light, the move marked first, and the owner told once in one line (the same claim a close's letter takes).
        key = M.closed_key(n, card.get('closed_at'))
        if not store.create(claims(), key, {'number': n, **_claim(now)}):
            return f'{_h()}: closed #{n} ({reason}) in {col} was already settled'
        try:
            store.set(M.BOARD_MOVES, str(n), {'at_epoch': int(now.timestamp())})
            M.to_unplanned(card['item'], token)
        except Exception as e:   # noqa: BLE001 - GitHub or the store refused: the claim and the mark freed, the sweep catches it
            store.delete(claims(), key)
            store.delete(M.BOARD_MOVES, str(n))   # a mark left behind would read a later hand drag as the pipeline's
            return f'WARNING: {_h()}: closed #{n} ({reason}) was not moved to Shipwreck ({str(e)[:160]}); the scrum master\'s sweep catches it'
        to, text, subject = M.unplanned_line(card, reason)
        try:
            mail(to, text, M.SCRUM_SENDER, subject=subject)
        except Exception as e:   # noqa: BLE001 - the card is placed; the word to the owner is best effort
            return f'{_h()}: closed #{n} ({reason}): moved from {col} to Shipwreck, no Light; {to} not told ({str(e)[:120]})'
        return f'{_h()}: closed #{n} ({reason}): moved from {col} to Shipwreck, no Light; {to} told' + extra
    closer = _SET['closer']
    if closer is None:
        from . import alerts
        closer = alerts.closed_by_closer
    if closer(card.get('title'), card.get('comment')):
        try:
            store.set(M.BOARD_MOVES, str(n), {'at_epoch': int(now.timestamp())})
            (ship or M.to_shipped)(card['item'], token)
        except Exception as e:   # noqa: BLE001 - GitHub or the store refused: the sweep catches it
            return f'WARNING: {_h()}: closed #{n} was not moved to Ship ({str(e)[:160]}); the scrum master\'s sweep catches it'
        return f'{_h()}: closed #{n} by an alert closer: moved from {col} to Ship, Shipped'
    key = M.closed_key(n, card.get('closed_at'))
    if not store.create(claims(), key, {'number': n, **_claim(now)}):
        return f'{_h()}: closed #{n} in {col} was already told'
    to, text, subject = M.closed_letter(card)
    try:
        mail(to, text, M.SCRUM_SENDER, subject=subject)
    except Exception as e:   # noqa: BLE001 - the bus refused: the claim freed, so the sweep's catch asks
        store.delete(claims(), key)
        return f'WARNING: {_h()}: closed #{n}\'s letter to {to} did not go ({str(e)[:160]}); the scrum master\'s sweep asks'
    return f'{_h()}: closed #{n} while in {col}; {to} asked to confirm it done or reopen it'


# ------------------------------------------------------------------ the two sweeps, here first, the job after
def scrum_nudge(number, store, now=None, start=None, why: str = '', sweep=None) -> str:
    """The scrum master run on one card the moment an event names it: `sweep()` here (its lines), and `start(args)`
    (the host's job, `scrum-sweep --ticket N`) only when that failed. Never its own trigger: an event within
    `acted_seconds` of the scrum master's last act on the card (its record's `acted_at`, its own Light and comment edits
    coming back) is skipped, and one run a card a minute is claimed (`scrum-<n>-<minute>`). A refused start is a
    WARNING: the scrum master's own sweep catches it. '' for no card."""
    if not number:
        return ''
    now = _now(now)
    rec = store.get(_SET['scrum_state'], str(number)) or {}
    try:
        acted = datetime.fromisoformat(str(rec.get('acted_at') or ''))
    except ValueError:
        acted = None
    secs = _SET['acted_seconds']
    if acted and (now - acted).total_seconds() < secs:
        return f'{_h()}: scrum: #{number}: its own edit ({why or "an event"}) within {secs} s; not run again'
    if not store.create(claims(), f"scrum-{number}-{now.strftime('%Y%m%d%H%M')}", _claim(now)):
        return f'{_h()}: scrum: #{number} already started this minute'
    try:   # the card's sweep run here; the job only when that failed
        if sweep is None:
            raise NotConfigured('scrum_nudge needs sweep=')
        lines = sweep()
        return '\n'.join([f'{_h()}: scrum: #{number} run here ({why or "an event"})'] + [f'{_h()}: {line}' for line in lines or []])
    except Exception as e:   # noqa: BLE001 - no token, GitHub or the store refusing: the job, idempotent, runs it
        here = f'{type(e).__name__}: {str(e)[:120]}'
    try:
        if start is None:
            raise NotConfigured('scrum_nudge needs start=')
        start(['scrum-sweep', '--ticket', str(number)])
    except Exception as e:   # noqa: BLE001 - the scrum master's own sweep is the catch-all
        return (f'WARNING: {_h()}: scrum: #{number} failed here ({here}) and was not started ({type(e).__name__}: '
                f'{str(e)[:160]}); the sweep catches it')
    return f'WARNING: {_h()}: scrum: #{number} not run here ({here}); started ({why or "an event"})'


def triage_sweep(data: dict, start=None, sweep=None) -> str:
    """A `Triage:` line the receiver could not sweep at once: the ticket sweep run here (`sweep()`, its lines), and
    `start(args)` (the host's job, `tickets-sweep`) only when that failed; the retry exception while the start is
    refused, so the bus keeps trying and, after its deliveries, the dead letter is an alert. The sweep itself is
    idempotent (it applies each line once)."""
    n = (data or {}).get('number')
    try:
        if sweep is None:
            raise NotConfigured('triage_sweep needs sweep=')
        lines = sweep()
        return '\n'.join([f"{_h()}: #{n}'s Triage line: swept here"] + [f'{_h()}: {line}' for line in lines or []])
    except Exception as e:   # noqa: BLE001 - no token, GitHub or the store refusing: the job, idempotent, sweeps it
        why = f'{type(e).__name__}: {str(e)[:160]}'
    try:
        if start is None:
            raise NotConfigured('triage_sweep needs start=')
        got = start(['tickets-sweep'])
    except Exception as e:   # noqa: BLE001 - the job would not start or the network: the bus delivers it again
        raise _retry(f'WARNING: {_h()}: #{n}\'s ticket sweep failed here ({why}) and did not start '
                     f'({type(e).__name__}: {str(e)[:200]}); Pub/Sub delivers it again')
    return (f"WARNING: {_h()}: #{n}'s Triage line: not swept here ({why}); the sweep started "
            f"({got.get('execution') or 'an execution'})")
