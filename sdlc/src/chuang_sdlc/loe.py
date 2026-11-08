"""A ticket's level of effort, estimated and actual: the working-time rule, the calibration of a raw estimate, the record
writer and the words a ticket's comment says.

The actual is the session's working time, never the wall clock: a hook appends one line per tool call to a local
activity log, "<epoch> <session> <tickets>" (the tickets the session's stamp names, `none` when it names none), and
"<epoch> <session> stop" when a turn ends. `active_minutes` charges each gap between two lines to the earlier line's
tickets, at most GAP_CAP_SECONDS, and nothing after a `stop` (the turn waits on a person) or a `none` (other work).

The work a session hands to delegates counts too (11/8/26: a ticket built by three agents, 451 s of their own tool
calls, read 0m, since the hook exempted a helper's calls from the log and the session's one call that started them
was one line charged at most the cap). The hook writes two more shapes: "<epoch> <session> <tickets> delegated
<tool_use_id>" for the session's call that starts a delegate, which counts as a ticket line of the session's own, and
"<epoch> <session> agent <agent_id> <tool_use_id>" for each of the delegate's own tool calls. `minutes` gives each
agent the intervals between its consecutive lines, capped as a session's gaps are, and the tickets of the delegated
line that started it (the same tool_use_id), else of the session's newest line before the agent's first, each ticket's
share of an interval 1 / the tickets it names, as a session's gap is split (the 11/8/26 review: an agent on a
two-ticket delegation was credited whole to each, twice its time in all); a ticket's delegated time is, at each
instant, the largest share an agent gives it less the share the session's own line gives it, so two agents in
parallel, or an agent beside the session on the same ticket, count once. The actual is the sum.
A commit's event carries those minutes; `record` writes them into the ticket's record in the host's store, beside the
elapsed time from its first start, and the board's Actual through the host's writer. A raw estimate is calibrated by
the median actual / raw estimate of the shipped tickets of its own span (`factor`, `calibrate`), so estimates learn
from what the work took. Standard library and the kit's clock only.
"""
import os

EVENT = 'ticket.committed'
# The host names its two collections (record's `records` and `starts`); the kit names none. A record holds
# {estimate_min, estimate_raw_min, actual_min, active_min, delegated_min, elapsed_min, started_epoch,
# first_commit_epoch, committed_epoch, commits, last_key, reported_epoch}; a start {at_epoch, first_epoch}.
LOG_ENV = 'TICKET_ACTIVITY_LOG'
# A gap between two tool calls longer than this is not all work: thinking, a long command or a wait, counted as this much.
GAP_CAP_SECONDS = 300


def log_path() -> str:
    """The activity log: TICKET_ACTIVITY_LOG, else ~/.claude/ticket-activity.log."""
    return os.environ.get(LOG_ENV) or os.path.join(os.path.expanduser('~'), '.claude', 'ticket-activity.log')


def _tickets(field: str) -> tuple:
    """A line's ticket field, `12` or `12,14`, as a tuple of numbers; () when it holds none."""
    return tuple(int(x) for x in field.split(',') if x.isdigit())


def parse(text: str) -> list:
    """The log's lines as (epoch, session, what). A three-field line's what is 'stop', 'none' or a tuple of ticket
    numbers, as always. "<epoch> <session> <tickets> delegated <tool_use_id>", the session's call that starts a
    delegate, is ('delegated', tool_use_id, tickets), its tickets a tuple, () when the stamp named none; "<epoch>
    <session> agent <agent_id> <tool_use_id>", a delegate's own tool call, is ('agent', agent_id, tool_use_id), the
    tool_use_id '-' when the hook could not read which call started it. Any other shape is skipped, so an older
    parser, which took three fields or nothing, skips the two new ones the same way."""
    out = []
    for raw in (text or '').splitlines():
        parts = raw.split()
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        if len(parts) == 3:
            what = parts[2]
            if what not in ('stop', 'none'):
                what = _tickets(what)
                if not what:
                    continue
        elif len(parts) == 5 and parts[2] == 'agent':
            what = ('agent', parts[3], parts[4])
        elif len(parts) == 5 and parts[3] == 'delegated':
            tickets = () if parts[2] == 'none' else _tickets(parts[2])
            if parts[2] != 'none' and not tickets:
                continue
            what = ('delegated', parts[4], tickets)
        else:
            continue
        out.append((int(parts[0]), parts[1], what))
    return out


def _kind(what) -> str:
    """A parsed line's shape: 'tickets', 'stop', 'none', 'delegated' or 'agent'."""
    if isinstance(what, tuple):
        return what[0] if what and isinstance(what[0], str) else 'tickets'
    return what


def _names(what):
    """The tickets a line names: a ticket line's tuple, a delegated line's; None for a stop, a none or an agent's."""
    kind = _kind(what)
    return what if kind == 'tickets' else what[2] if kind == 'delegated' else None


def read_log(path: str = None) -> list:
    """parse() of the log file; [] when there is none."""
    try:
        with open(path or log_path(), encoding='utf-8') as f:
            return parse(f.read())
    except OSError:
        return []


def _excess(theirs, ours) -> float:
    """The seconds of `theirs` beyond `ours`: each a list of weighted half-open intervals (start, end, weight), the
    weight the share of that time one ticket gets (1 / the tickets its line names). At each instant a list's rate is
    the largest weight among its intervals covering it, never their sum, so two intervals in parallel count once; the
    result is the sum over time of theirs less ours where theirs is larger. One sorted sweep of the ends and starts."""
    events = []
    for group, spans in ((0, theirs), (1, ours)):
        for start, end, weight in spans:
            if end > start:
                events.append((start, 1, group, weight))
                events.append((end, -1, group, weight))
    events.sort(key=lambda e: (e[0], e[1]))   # at one instant an end goes before a start: the intervals are half-open
    active, total, last = ({}, {}), 0.0, None
    for t, delta, group, weight in events:
        if last is not None and t > last:
            theirs_rate = max((w for w, c in active[0].items() if c > 0), default=0.0)
            ours_rate = max((w for w, c in active[1].items() if c > 0), default=0.0)
            if theirs_rate > ours_rate:
                total += (theirs_rate - ours_rate) * (t - last)
        active[group][weight] = active[group].get(weight, 0) + delta
        last = t
    return total


def _agent_tickets(session: str, first: int, tool_use_id: str, delegated: list, by_session: dict):
    """The tickets an agent's time goes to: its delegated line's (the same tool_use_id, any session; the newest at or
    before the agent's first line, else any), else what the newest line of its own session at or before its first
    line names; () when that line is a stop or a none, when the delegated line named none, or when there is no line."""
    if tool_use_id != '-':
        mine = [d for d in delegated if d[1] == tool_use_id]
        if mine:
            before = [d for d in mine if d[0] <= first]
            return max(before or mine, key=lambda d: d[0])[2]
    earlier = [e for e in by_session.get(session, []) if e[0] <= first]
    if not earlier:
        return ()
    return _names(max(earlier, key=lambda e: e[0])[1]) or ()


def minutes(lines, numbers, cap: int = GAP_CAP_SECONDS) -> dict:
    """{n: {'active': minutes, 'delegated': minutes}} of working time on each ticket in `numbers`, None for one the
    log never names, a delegated line or an agent's included. The session's own time: per session, each gap between
    two consecutive lines of its own (a delegated line among them, naming its tickets; an agent's lines are the
    agent's) is charged to the earlier line's tickets, split evenly between them, at most `cap` seconds; a gap after a
    `stop` or a `none` line counts nothing, so a question waiting on a person and another ticket's work are never this
    ticket's. The delegates' time: each agent's lines, by agent_id, open the intervals [t, min(next t, t + cap))
    between consecutive lines (its last opens none), on the tickets `_agent_tickets` resolves, each ticket's share
    1 / those tickets, as a session's gap is split (the 11/8/26 review: an agent on a two-ticket delegation was counted
    whole on each); a ticket's delegated seconds are, at each instant, the largest share an agent's interval gives it
    less the share the session's own interval charged to it gives it ([t0, min(t1, t0 + cap)) of each line naming it,
    at 1 / its tickets), summed over time (`_excess`), so two agents in parallel, and an agent beside the session's own
    work on the ticket, count once. 'active' is the sum."""
    wanted = {int(n) for n in numbers}
    seconds, spans, named = {n: 0.0 for n in wanted}, {n: [] for n in wanted}, set()
    by_session, agents, delegated = {}, {}, []
    for epoch, session, what in lines:
        if _kind(what) == 'agent':
            agents.setdefault(what[1], []).append((epoch, session, what[2]))
            continue
        by_session.setdefault(session, []).append((epoch, what))
        if _kind(what) == 'delegated':
            delegated.append((epoch, what[1], what[2]))
    for events in by_session.values():
        events.sort(key=lambda e: e[0])
        for (t0, what), (t1, _) in zip(events, events[1:] + [(None, None)]):
            tickets = _names(what)
            if tickets is None:
                continue
            named.update(n for n in tickets if n in wanted)
            if t1 is None or not tickets:
                continue
            charged = min(max(t1 - t0, 0), cap)
            share = charged / len(tickets)
            for n in tickets:
                if n in wanted:
                    seconds[n] += share
                    if charged:
                        spans[n].append((t0, t0 + charged, 1 / len(tickets)))
    by_agent = {n: [] for n in wanted}
    for calls in agents.values():
        calls.sort(key=lambda c: c[0])
        tool_use_id = next((c[2] for c in calls if c[2] != '-'), '-')
        all_tickets = _agent_tickets(calls[0][1], calls[0][0], tool_use_id, delegated, by_session)
        tickets = [n for n in all_tickets if n in wanted]
        if not tickets:
            continue
        named.update(tickets)
        for (t0, _, _), (t1, _, _) in zip(calls, calls[1:]):
            end = t0 + min(max(t1 - t0, 0), cap)
            if end > t0:
                for n in tickets:
                    by_agent[n].append((t0, end, 1 / len(all_tickets)))
    out = {}
    for n in wanted:
        if n not in named:
            out[n] = None
            continue
        delegated_s = _excess(by_agent[n], spans[n])
        out[n] = {'active': int(round((seconds[n] + delegated_s) / 60)), 'delegated': int(round(delegated_s / 60))}
    return out


def active_minutes(lines, numbers, cap: int = GAP_CAP_SECONDS) -> dict:
    """{n: minutes} of working time on each ticket in `numbers`, the session's own and its delegates' together
    (`minutes`), None for one the log never names."""
    return {n: (m['active'] if m else None) for n, m in minutes(lines, numbers, cap).items()}


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
    12:50 PM PT to the last commit 11/2/26 4:10 PM PT (2 commits)`, with `, 45m of it by delegates` after the work when
    the record holds delegated minutes beside the working time (never after an actual that is the elapsed, which no
    delegate is part of); an estimate, the working time or the start that is missing says so
    (`start_hint`, the host's command that records a start, is named when the actual is unmeasured)."""
    est, act, active, elapsed = rec.get('estimate_min'), rec.get('actual_min'), rec.get('active_min'), rec.get('elapsed_min')
    head = f'estimated {words(est)}' if est else 'no estimate on the ticket'
    if act is None:
        return head + ', actual unmeasured (no working time logged' + (f' and no `{start_hint}` before its first commit)'
                                                                       if start_hint else ' and no start before its first commit)')
    diff = f' ({(act - est) * 100 // est:+d}%)' if est else ''
    out = f'{head}, actual {words(act)}' + (' of work' if active is not None else '')
    if active is not None and rec.get('delegated_min'):
        out += f', {words(rec["delegated_min"])} of it by delegates'
    out += diff
    if active is not None and elapsed is not None:
        out += f', {words(elapsed)} elapsed'
    n = int(rec.get('commits') or 1)
    span = (f', from the first start {stamp(rec["started_epoch"], tz)} to the last commit {stamp(rec["committed_epoch"], tz)}'
            if rec.get('started_epoch') and rec.get('committed_epoch') else '')
    return f'{out}{span} ({n} commit{"" if n == 1 else "s"})'


def record(data: dict, store, estimate_of, write_actual, records: str = '', starts: str = '', tz=None,
           start_hint: str = '') -> list:
    """Each ticket of one commit's EVENT into the store's `records`/<n> and the board: the estimate (`estimate_of(n)`,
    else the one kept), the working time the event carries (`active`, and of it the delegates' `delegated`, kept as
    delegated_min, None when the event carries none), the elapsed time from the ticket's first start (`starts`/<n>
    first_epoch) to the commit, the actual (the working time, else the elapsed), the first commit and the count; then
    `write_actual(n, hours)` puts the actual on the board. The same commit again (its `key`) is skipped, so a
    redelivery writes nothing twice. `store` has get(collection, id) and set(collection, id, doc). Lines; raises what
    the store or the board raised, for the caller to retry, and ValueError when the host named no collections."""
    if not records or not starts:
        raise ValueError('loe.record: name the host collections, records= and starts=')
    at, key = int(data.get('at') or 0), str(data.get('key') or data.get('at') or '')
    active = {str(k): v for k, v in (data.get('active') or {}).items()}
    delegated = {str(k): v for k, v in (data.get('delegated') or {}).items()}
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
        doc = dict(rec, estimate_min=estimate_of(n) or rec.get('estimate_min'), active_min=work,
                   delegated_min=delegated.get(str(n)), elapsed_min=elapsed,
                   actual_min=work if work is not None else elapsed, started_epoch=started, committed_epoch=at,
                   first_commit_epoch=rec.get('first_commit_epoch') or rec.get('committed_epoch') or at,
                   commits=int(rec.get('commits') or (1 if rec else 0)) + 1, last_key=key)
        store.set(records, str(n), doc)
        board = ''
        if doc['actual_min'] is not None and not write_actual(n, hours(doc['actual_min'])):
            board = ' (Actual not on the board)'
        lines.append(f'tickets: #{n} LOE so far: {text(doc, tz, start_hint)}{board}')
    return lines
