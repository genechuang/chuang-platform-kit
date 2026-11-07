"""A ticket's level of effort, estimated and actual: the working-time rule, the calibration of a raw estimate, the record
writer and the words a ticket's comment says.

The actual is the session's working time, never the wall clock: a hook appends one line per tool call to a local
activity log, "<epoch> <session> <tickets>" (the tickets the session's stamp names, `none` when it names none), and
"<epoch> <session> stop" when a turn ends. `active_minutes` charges each gap between two lines to the earlier line's
tickets, at most GAP_CAP_SECONDS, and nothing after a `stop` (the turn waits on a person) or a `none` (other work).
A commit's event carries those minutes; `record` writes them into the ticket's record in the host's store, beside the
elapsed time from its first start, and the board's Actual through the host's writer. A raw estimate is calibrated by
the median actual / raw estimate of the shipped tickets of its own span (`factor`, `calibrate`), so estimates learn
from what the work took. Standard library and the kit's clock only.
"""
import os

EVENT = 'ticket.committed'
# The host names its two collections (record's `records` and `starts`); the kit names none. A record holds
# {estimate_min, estimate_raw_min, actual_min, active_min, elapsed_min, started_epoch, first_commit_epoch,
# committed_epoch, commits, last_key, reported_epoch}; a start {at_epoch, first_epoch}.
LOG_ENV = 'TICKET_ACTIVITY_LOG'
# A gap between two tool calls longer than this is not all work: thinking, a long command or a wait, counted as this much.
GAP_CAP_SECONDS = 300


def log_path() -> str:
    """The activity log: TICKET_ACTIVITY_LOG, else ~/.claude/ticket-activity.log."""
    return os.environ.get(LOG_ENV) or os.path.join(os.path.expanduser('~'), '.claude', 'ticket-activity.log')


def parse(text: str) -> list:
    """The log's lines as (epoch, session, what): what is 'stop', 'none' or a tuple of ticket numbers; a malformed
    line is skipped."""
    out = []
    for raw in (text or '').splitlines():
        parts = raw.split()
        if len(parts) != 3 or not parts[0].isdigit():
            continue
        what = parts[2]
        if what not in ('stop', 'none'):
            nums = tuple(int(x) for x in what.split(',') if x.isdigit())
            if not nums:
                continue
            what = nums
        out.append((int(parts[0]), parts[1], what))
    return out


def read_log(path: str = None) -> list:
    """parse() of the log file; [] when there is none."""
    try:
        with open(path or log_path(), encoding='utf-8') as f:
            return parse(f.read())
    except OSError:
        return []


def active_minutes(lines, numbers, cap: int = GAP_CAP_SECONDS) -> dict:
    """{n: minutes} of working time on each ticket in `numbers`, None for one the log never names: per session, each
    gap between two consecutive lines is charged to the earlier line's tickets, split evenly between them, at most
    `cap` seconds; a gap after a `stop` or a `none` line counts nothing, so a question waiting on a person and another
    ticket's work are never this ticket's."""
    wanted = {int(n) for n in numbers}
    seconds, named = {n: 0.0 for n in wanted}, set()
    by_session = {}
    for epoch, session, what in lines:
        by_session.setdefault(session, []).append((epoch, what))
    for events in by_session.values():
        events.sort(key=lambda e: e[0])
        for (t0, what), (t1, _) in zip(events, events[1:] + [(None, None)]):
            if not isinstance(what, tuple):
                continue
            named.update(n for n in what if n in wanted)
            if t1 is None:
                continue
            share = min(max(t1 - t0, 0), cap) / len(what)
            for n in what:
                if n in wanted:
                    seconds[n] += share
    return {n: (int(round(seconds[n] / 60)) if n in named else None) for n in wanted}


MIN_RECORDS = 5        # a factor from fewer shipped tickets than this is no factor
CALIBRATE_STEP = 5     # a calibrated estimate rounds to 5 minutes, and is never under 5


# The spans a raw estimate is calibrated within: an hour or less, over an hour to four, over four.
BANDS = ((60, 'up to 1h'), (240, '1h to 4h'), (None, 'over 4h'))


def band(raw_min) -> str:
    """The span a raw estimate in minutes falls in (BANDS)."""
    return next(name for top, name in BANDS if top is None or float(raw_min) <= top)


def factor(records, raw_min=None) -> tuple:
    """(the median actual / raw estimate, how many tickets it is from, the span it is of) over the records with both:
    with `raw_min`, the median of that estimate's own span (BANDS), the whole set's when the span has fewer than
    MIN_RECORDS. The raw is the record's `estimate_raw_min` (the filer's own estimate, filed calibrated), else its
    `estimate_min` (a record from before calibration), so a calibrated estimate never feeds the factor. (None, n, '')
    under MIN_RECORDS in all."""
    pairs = [(float(r.get('estimate_raw_min') or r['estimate_min']), float(r['actual_min']))
             for r in records or [] if r.get('actual_min') and (r.get('estimate_raw_min') or r.get('estimate_min'))]

    def median(ps):
        ratios = sorted(a / e for e, a in ps)
        mid = len(ratios) // 2
        return ratios[mid] if len(ratios) % 2 else (ratios[mid - 1] + ratios[mid]) / 2
    if raw_min is not None:
        span = band(raw_min)
        mine = [p for p in pairs if band(p[0]) == span]
        if len(mine) >= MIN_RECORDS:
            return median(mine), len(mine), span
    if len(pairs) < MIN_RECORDS:
        return None, len(pairs), ''
    return median(pairs), len(pairs), 'all spans'


def calibrate(raw_min, by) -> int:
    """A raw estimate in minutes times the factor `by`, rounded to CALIBRATE_STEP minutes and at least that."""
    step = CALIBRATE_STEP
    return max(step, int(round(float(raw_min) * float(by) / step)) * step)


def hours(minutes) -> str:
    """Minutes as a board field's hours: 30 -> '0.5', 120 -> '2', 100 -> '1.67'."""
    return f'{round(minutes / 60, 2):g}'


def words(minutes) -> str:
    """Minutes as the words a comment says: `45m`, `1h 10m`, `2h`, `1d 2h` (a day is 8 hours of work)."""
    minutes = int(round(minutes or 0))
    d, rest = divmod(minutes, 480)
    h, m = divmod(rest, 60)
    parts = [f'{d}d' if d else '', f'{h}h' if h else '', f'{m}m' if m or not (d or h) else '']
    return ' '.join(p for p in parts if p)


def stamp(epoch, tz=None) -> str:
    """An epoch as the clock reading a person writes, with its year and zone: `11/2/26 12:50 PM PT` (the kit's zone
    unless `tz` is given)."""
    from datetime import datetime, timezone
    from chuang_platform_kit import clock
    return clock.human_time(datetime.fromtimestamp(int(epoch), timezone.utc), weekday=False, tz_word=True, tz=tz)  # utc-ok: an instant, written in the zone


def text(rec: dict, tz=None, start_hint: str = '') -> str:
    """One record as words: `estimated 2h, actual 1h 10m of work (-42%), 3h 20m elapsed, from the first start 11/2/26
    12:50 PM PT to the last commit 11/2/26 4:10 PM PT (2 commits)`; an estimate, the working time or the start that is
    missing says so (`start_hint`, the host's command that records a start, is named when the actual is unmeasured)."""
    est, act, active, elapsed = rec.get('estimate_min'), rec.get('actual_min'), rec.get('active_min'), rec.get('elapsed_min')
    head = f'estimated {words(est)}' if est else 'no estimate on the ticket'
    if act is None:
        return head + ', actual unmeasured (no working time logged' + (f' and no `{start_hint}` before its first commit)'
                                                                       if start_hint else ' and no start before its first commit)')
    diff = f' ({(act - est) * 100 // est:+d}%)' if est else ''
    out = f'{head}, actual {words(act)}' + (' of work' if active is not None else '') + diff
    if active is not None and elapsed is not None:
        out += f', {words(elapsed)} elapsed'
    n = int(rec.get('commits') or 1)
    span = (f', from the first start {stamp(rec["started_epoch"], tz)} to the last commit {stamp(rec["committed_epoch"], tz)}'
            if rec.get('started_epoch') and rec.get('committed_epoch') else '')
    return f'{out}{span} ({n} commit{"" if n == 1 else "s"})'


def record(data: dict, store, estimate_of, write_actual, records: str = '', starts: str = '', tz=None,
           start_hint: str = '') -> list:
    """Each ticket of one commit's EVENT into the store's `records`/<n> and the board: the estimate (`estimate_of(n)`,
    else the one kept), the working time the event carries (`active`), the elapsed time from the ticket's first start
    (`starts`/<n> first_epoch) to the commit, the actual (the working time, else the elapsed), the first commit and the
    count; then `write_actual(n, hours)` puts the actual on the board. The same commit again (its `key`) is skipped, so
    a redelivery writes nothing twice. `store` has get(collection, id) and set(collection, id, doc). Lines; raises what
    the store or the board raised, for the caller to retry, and ValueError when the host named no collections."""
    if not records or not starts:
        raise ValueError('loe.record: name the host collections, records= and starts=')
    at, key = int(data.get('at') or 0), str(data.get('key') or data.get('at') or '')
    active = {str(k): v for k, v in (data.get('active') or {}).items()}
    lines = []
    for n in data.get('tickets') or []:
        n = int(n)
        rec = store.get(records, str(n)) or {}
        if key and rec.get('last_key') == key:
            lines.append(f'tickets: #{n} LOE recorded already for this commit')
            continue
        started = (store.get(starts, str(n)) or {}).get('first_epoch') or rec.get('started_epoch')
        elapsed = (at - int(started)) // 60 if started and at - int(started) >= 60 else None
        work = active.get(str(n))
        doc = dict(rec, estimate_min=estimate_of(n) or rec.get('estimate_min'), active_min=work, elapsed_min=elapsed,
                   actual_min=work if work is not None else elapsed, started_epoch=started, committed_epoch=at,
                   first_commit_epoch=rec.get('first_commit_epoch') or rec.get('committed_epoch') or at,
                   commits=int(rec.get('commits') or (1 if rec else 0)) + 1, last_key=key)
        store.set(records, str(n), doc)
        board = ''
        if doc['actual_min'] is not None and not write_actual(n, hours(doc['actual_min'])):
            board = ' (Actual not on the board)'
        lines.append(f'tickets: #{n} LOE so far: {text(doc, tz, start_hint)}{board}')
    return lines
