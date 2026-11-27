"""The scrum master: a sweep of the board's shipyard column (every few minutes, and on each event naming one of its
cards) that finds who is building what, what is blocked and by whom, and what waits on the project's lead, from what
the board and each card's comments say, and asks each owner only what the board cannot.

An owner answers on the ticket with one line, the newest one by the repository's owner counting:

  Scrum: building | eta <when>        the card is being built and lands about then (parse_eta, in the kit's zone)
  Scrum: next | after #N              it is the owner's next, after #N
  Scrum: blocked by #N | <who>        #N blocks it; <who> (a session, or the lead) can unblock it
  Scrum: blocked on <what> | eta <when>  it waits on something outside the board until about then (0.22.0); the
                                      board's blocked Light, if it has one, shows either block
  Scrum: scheduled | <what> | eta <when>  the same wait by its better name (0.63.0, SMADPickleBot #2200): on a board
                                      with a scheduled Light, a wait that names and links no ticket shows that
                                      Light and carries no blocked label, whichever of the two words wrote it
  Scrum: depends on #N                it cannot be finished until #N is: back to the next column
  Scrum: needs <lead> | <the ask>     it waits on the lead's decision, written in full
  Scrum: paused | <why>               its owner set it down: back to the next column, its light off
  Scrum: done                         on a closed card: done, verified and closed: to the ship column

`plan` is the rules for one shipyard card, pure; `closed_plan` the same for a closed card outside the finished
columns; `sweep` reads the column with the board's own filter, applies each plan, keeps one comment a card edited in
place, and sends each owner one letter of the sweep's new questions. The host wires it once (`configure`): the lead's
name (its `needs <lead>` line and its Light), the record store, the letter, and the hooks the host shares with its own
listeners (a closed card's question and who is asked, whether its close was told, an alert closer's close, the ship
record a sweep reads to bring the ship card home). The board, its runner and the people are chuang_sdlc.tickets'.
Nothing has a default that reaches a real service: unwired, a sweep with no store raises NotConfigured.
"""
import hashlib
import re
from datetime import datetime, timedelta, timezone

from . import signature as _signature
from . import tenant as _tenant
from . import tickets as _tickets


MARKER = '**Scrum master**'   # the sweep's own comments open with it, and no line in one counts as an owner's


SENDER = 'Scrum master'       # the letters' sender


BLOCKED_LABEL = 'blocked'


STALE_HOURS = 24


NEEDS_EVERY_HOURS = 24


ASK_AGAIN_HOURS = 24   # an unanswered question is asked again after this
# ... but a card in the working column that nobody has started is asked about again this often (0.26.0; SMADPickleBot
# #1450: a card the lead dragged in at 9:22 AM sat unstarted until 1:14 PM, its owner having missed the one letter,
# while every 10-minute sweep wrote "no Light" and asked nobody).
UNSTARTED_AGAIN_MINUTES = 30


# 0.40.0 (SMADPickleBot #2066; the lead, 11/20/26 1:25 PM PT, after an owner sat idle 7.9 of 13.4 hours with over 40
# cards queued: the "Scrum master who is suppose to sweep and remind you of items assigned to you in Shipyard that you
# are not working on and to keep you not idle"). An owner with a card it can work and nothing being built is told to
# start one every full sweep (idle_owners, letter), and so is an owner whose building cards no start or commit has
# named for this long. A Queued card is never sent back to the next column for its owner's idleness any more (the
# 30-minute QUEUED_IDLE_MINUTES rule of 0.x, SMADPickleBot #697): it stays where the lead queued it.
STALE_BUILD_MINUTES = 90


IDLE_KEY = 'idle-{owner}'   # an owner's idle record in the sweep's state collection: {since, sweeps, ticket, kind}


CLOSED_EVERY_HOURS = 24   # a closed card outside the finished columns is asked about at most once a day


# 0.20.0 (SMADPickleBot #1337; Gene, 11/10/26 10:10 AM PT: "nobody is sweeping these tickets and figuring out if these
# are tickets that need to be re-shipped, or closed as won't-fix"): an open card in the failed column is asked about
# once it has sat there this long, and again each this long after, until its owner re-ships or drops it.
WRECKED_HOURS = 24


PRIORITY_ASK = 'set its Priority on the board (P1 now, P2 a bug, P3 a feature, P4 someday)'


LIGHT_CLEAR = 'light {n} clear'   # the host's command that clears a card's Light, named where a Needs-you has no ask


WORDS = ('MARKER', 'SENDER', 'BLOCKED_LABEL', 'PRIORITY_ASK', 'LIGHT_CLEAR')


CARD_FIELDS = ('id state stateReason closedAt blockedBy(first: 20) { nodes { id number state closedAt } } '
               'comments(last: 60) { nodes { databaseId body createdAt author { login } } }')
CARD_QUERY = ('query($owner: String!, $repo: String!, $n: Int!) { repository(owner: $owner, name: $repo) { '
              f'issue(number: $n) {{ {CARD_FIELDS} }} }} }}')
PREFETCH_CHUNK = 50   # issues a prefetch reads in one aliased query (#1207)


LINK_MUTATION = 'mutation($i: ID!, $b: ID!) { addBlockedBy(input: {issueId: $i, blockingIssueId: $b}) { issue { number } } }'


_SET = _tenant.Layered('scrum', {   # a tenant's settings layered over it (tenant.using, 0.30.0)
    'lead': 'lead',        # the project's lead: the `needs <lead>` line, the Light for their decision, the assignee
    'store': None,          # () -> the record store (get and set on (collection, id)) a sweep keeps its state in
    'mail': None,           # (to, text, subject, sender=) -> the line said; an owner's letter
    'state': 'scrum',       # the record collection of each card's sweep state
    'finished': None,       # (column, cfg) -> whether a closed card is where it belongs; default: shipped, failed, shipment
    'unplanned': None,      # (state reason) -> whether a close was as not planned (to the failed column, no question)
    'closed_question': None,    # (number, column) -> the question a closed card's owner is asked
    'closed_to': None,      # (owner) -> who is asked about a closed card; default: its owner, the captain for none or the lead
    'closed_told': None,    # (number, closed_at, store) -> whether the host's own listener told this close already
    'closer': None,         # (title, newest comment) -> whether an alert closer closed it (to the ship column, no question)
    'ship_run_of': None,    # (store) -> a reader of the host's ship record, for the ship card's keeper; None: the host's
    'tz': None,             # the zone an ETA is read in and a stamp written; None: chuang_platform_kit.clock's
    'eta_command': '',      # 0.16.0: the host's command that sets a card's ETA field, `{n}` its number; '' says none
    'start_command': '',    # 0.20.0: the host's command that picks a ticket up (tickets.start), `{n}` its number
    'warm_state': None,     # 0.29.0: (store) -> (alive, note): whether the host's dock warm is running now, and its
                            # last word; None: no such record, the Warming limit alone decides
    'release_train': True,  # 0.31.0: False for a board with no dock, warm or ship card: sweep() skips those parts
})


def eta_ask(n) -> str:
    """How an owner gives a card's ETA, in the sweep's asks: the host's command for the board's ETA field (0.16.0,
    configure(eta_command=)) and the `Scrum: building | eta <when>` line, which the sweep copies into it."""
    command = (_SET['eta_command'] or '').format(n=n)
    return (f'`{command}` or ' if command else '') + '`Scrum: building | eta <when>`'


def _line_re(lead: str):
    """An owner's `Scrum:` line and its kind, the lead's own `needs <lead>` among the kinds."""
    return re.compile(r'^\s*Scrum:\s*(building|next|blocked by|blocked on|scheduled|depends on|needs ' + re.escape(lead.lower())
                      + r'|paused|done)\b(.*)$', re.I | re.M)


SCRUM_RE = _line_re(_SET['lead'])   # the one regex of an owner's line: a host's listener reads lines with it too
NEEDS_KIND = f"needs {_SET['lead'].lower()}"


def configure(**settings) -> None:
    """The host's settings: `lead` (a name), `store`, `mail`, `finished`, `unplanned`, `closed_question`, `closed_to`,
    `closed_told`, `closer`, `ship_run_of`, `warm_state` (callables), `state` (a collection), `tz` (a zone), and `words` ({name: text}
    for this module's worded constants, WORDS). SCRUM_RE and NEEDS_KIND are made again for the lead. Unknown names are
    refused."""
    global SCRUM_RE, NEEDS_KIND
    words = settings.pop('words', None) or {}
    for name, value in settings.items():
        if name not in _SET:
            raise TypeError(f'scrum.configure() takes no {name!r}')
        _SET[name] = value
    for name, value in words.items():
        if name not in WORDS:
            raise TypeError(f'scrum.configure(words=) takes no {name!r}')
        globals()[name] = value
    SCRUM_RE = _line_re(_SET['lead'])
    NEEDS_KIND = f"needs {_SET['lead'].lower()}"


def setting(name: str):
    """One of the host's settings (configure)."""
    return _SET[name]


def lead() -> str:
    """The project's lead, by the name the board's Owner field gives them."""
    return _SET['lead']


def captain() -> str:
    """The release captain (chuang_sdlc.tickets' people): a card with no Owner is theirs to take or assign."""
    return _tickets.person('captain')


def _zone():
    from chuang_platform_kit import clock
    return _SET['tz'] or clock.zone()


def now_utc() -> datetime:
    """The instant now (a stamp, never a day)."""
    return datetime.now(timezone.utc)   # utc-ok: an instant


def pt(when: datetime) -> str:
    """A stamp for people in the kit's zone, `10/29/26 12:40 PM PT`: the kit's clock.human_time without the weekday."""
    from chuang_platform_kit import clock
    return clock.human_time(when, weekday=False, tz_word=True, tz=_SET['tz'])


def scrum_line(comments, login: str, skip=()) -> dict:
    """The newest `Scrum:` line in a comment by `login` (the repository's owner: every session writes as it, and
    nobody else's line counts): {kind, ref, who, eta, ask, at}; {} when none. `skip` names kinds read past (an open
    card's `done`, which only a closed card takes)."""
    for c in sorted(comments or [], key=lambda c: c.get('createdAt') or '', reverse=True):
        if (c.get('author') or {}).get('login') != login or MARKER in (c.get('body') or ''):
            continue
        found = [m for m in SCRUM_RE.finditer(c.get('body') or '') if m.group(1).lower() not in skip]
        if not found:
            continue
        m = found[-1]
        kind, rest = m.group(1).lower(), m.group(2)
        if kind == 'scheduled':
            # 0.63.0: `scheduled | <what> | eta <when>` (or `scheduled <what> | eta <when>`) is `blocked on <what>`
            # by another word, so every rule below reads one kind.
            kind, rest = 'blocked on', rest.lstrip().lstrip('|:').lstrip() if rest.lstrip()[:1] in ('|', ':') else rest
        # A full reference keeps its repository (`owner/repo#N`, SMADPickleBot #2006: the ChuangFinance session's
        # "after genechuang/ChuangFinance#26" on a SMAD card was read as SMAD's #26); a bare #N is the card's own.
        ref = re.search(r'(?:\b([\w.-]+/[\w.-]+))?#(\d+)', rest)
        parts = [p.strip() for p in rest.split('|')]
        eta = next((p[3:].strip() for p in parts if p.lower().startswith('eta')), '')
        who = parts[1] if kind == 'blocked by' and len(parts) > 1 else ''
        ask = rest.split('|', 1)[1].strip() if kind in (NEEDS_KIND, 'paused') and '|' in rest else ''
        if kind == 'blocked on':
            # 0.22.0: what it waits on is the text before its first `|` (an "ETA" or a "#N" inside it is part of the
            # wait, never the line's ETA or ref); the ETA only from a later part.
            ask, ref = parts[0], None
            eta = next((p[3:].strip() for p in parts[1:] if p.lower().startswith('eta')), '')
        return {'kind': kind, 'ref': int(ref.group(2)) if ref else None, 'ref_repo': (ref.group(1) or '') if ref else '',
                'who': who, 'eta': eta, 'ask': ask, 'at': c.get('createdAt')}
    return {}


ANSWER_MAX = 600


ANY_SCRUM = re.compile(r'^\s*Scrum:\s*(.+)$', re.I | re.M)


def lead_answer(comments, login: str, since: str) -> dict:
    """The lead's reply to a Needs-you ask: the newest comment by `login` after `since` (the ask's time) that is an
    answer, not a session's own line: a `Scrum:` line in any word but the owners' kinds (`Scrum: Yes`), or a plain
    comment of at most ANSWER_MAX characters with no scrum master marker, Triage line or owner's `Scrum:` line. Every
    session writes as the same login, so the card's state (asked, and assigned to the lead) is what makes it theirs; and
    a signed comment (chuang_sdlc.signature: a session's or the pipeline's, 0.15.0) is never the lead's. {text, at}; {}
    when none."""
    for c in sorted(comments or [], key=lambda c: c.get('createdAt') or '', reverse=True):
        body = (c.get('body') or '').strip()
        if (c.get('author') or {}).get('login') != login or (c.get('createdAt') or '') <= (since or ''):
            continue
        if _signature.signed_by(body):
            continue
        owners = [m for m in SCRUM_RE.finditer(body) if m.group(1).lower() != 'done']   # `done` is a closed card's
        if MARKER in body or re.search(r'^[\s*_>]*Triage:', body, re.I | re.M) or owners:
            continue
        other = ANY_SCRUM.search(body)
        if other:
            return {'text': other.group(1).strip(), 'at': c.get('createdAt')}
        if body and len(body) <= ANSWER_MAX:
            return {'text': body, 'at': c.get('createdAt')}
    return {}


def gh_stamp(when: str) -> str:
    """A stamp in GitHub's own `2026-10-30T00:49:54Z` form, so it compares as text with a comment's createdAt (the
    sweep's own stamps are Python's isoformat, `+00:00`)."""
    try:
        return datetime.fromisoformat(when.replace('Z', '+00:00')).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    except ValueError:
        return when


WEEKDAYS = ('MONDAY', 'TUESDAY', 'WEDNESDAY', 'THURSDAY', 'FRIDAY', 'SATURDAY', 'SUNDAY')


ETA_DAY = re.compile(r'^([A-Z]{3,9})[,.]?\s+(.*)$')


ETA_ZONE = re.compile(r'\s*\b(?:PT|PST|PDT)\.?$')


def localize(naive: datetime, tz) -> datetime:
    """A wall-clock time in `tz`, standard time where the wall clock is ambiguous or skipped (the hour a clock change
    repeats or leaves out), as a stored stamp is never: an ETA is a future wall-clock time."""
    first, second = naive.replace(tzinfo=tz, fold=0), naive.replace(tzinfo=tz, fold=1)
    return min((first, second), key=lambda d: d.dst() or timedelta(0))


def _later(when: datetime, days: int) -> datetime:
    """`when` moved on by whole days of 24 hours, as an instant, written in its own zone."""
    return (when.astimezone(timezone.utc) + timedelta(days=days)).astimezone(when.tzinfo)


def parse_eta(text: str, now: datetime):
    """An ETA's instant in the kit's zone: `10/29/26 5:00 PM`, `10/29 5 PM`, `5:00 PM`, `5 PM` (today, or tomorrow once
    past), or ISO; None when it cannot be read (asked again after STALE_HOURS like any quiet card). A leading weekday,
    short or full, and a trailing PT, PST or PDT are read past (a reply stamp's own form); a weekday before a bare time
    names the next such day, today while the time is still ahead."""
    tz = _zone()
    t = ETA_ZONE.sub('', (text or '').strip().upper()).strip()
    day = None
    m = ETA_DAY.match(t)
    if m:
        named = [i for i, w in enumerate(WEEKDAYS) if w.startswith(m.group(1))]
        if named:
            day, t = named[0], m.group(2).strip()
    if not t:
        return None
    try:
        got = datetime.fromisoformat(t.replace('Z', '+00:00'))
        return got if got.tzinfo else localize(got, tz)
    except ValueError:
        pass
    local = now.astimezone(tz)
    floor = now - timedelta(minutes=1)
    for fmt in ('%m/%d/%y %I:%M %p', '%m/%d/%y %I %p', '%m/%d %I:%M %p', '%m/%d %I %p', '%I:%M %p', '%I %p', '%I:%M%p', '%I%p'):
        try:
            got = datetime.strptime(t, fmt)
        except ValueError:
            continue
        if '%y' not in fmt:
            got = got.replace(year=local.year)
        if '%m' not in fmt:
            got = got.replace(month=local.month, day=local.day)
            when = localize(got, tz)
            if day is not None:
                when = localize(got + timedelta(days=(day - local.weekday()) % 7), tz)
                return when if when > floor else localize(got + timedelta(days=7), tz)
            return when if when > floor else _later(when, 1)
        return localize(got, tz)
    return None


ETA_DATES = ('%m/%d/%y', '%m/%d/%Y', '%m/%d', '%Y-%m-%d')


def field_eta(day: str):
    """The instant a board's ETA field means (0.16.0, SMADPickleBot #1274): its day's end, 11:59 PM in the kit's zone,
    since a Date field holds no time; None when it holds none."""
    try:
        d = datetime.strptime((day or '').strip(), '%Y-%m-%d')
    except ValueError:
        return None
    return localize(d.replace(hour=23, minute=59), _zone())


def eta_day(text: str, now: datetime) -> str:
    """The day, YYYY-MM-DD, an owner's ETA names for the board's field: a date alone (`11/10/26`, `11/10`, `2026-11-10`,
    a leading weekday and a trailing PT read past) or any ETA parse_eta reads, its day in the kit's zone; '' when none."""
    tz = _zone()
    t = ETA_ZONE.sub('', (text or '').strip().upper()).strip()
    m = ETA_DAY.match(t)
    if m and any(w.startswith(m.group(1)) for w in WEEKDAYS):
        t = m.group(2).strip()
    local = now.astimezone(tz)
    for fmt in ETA_DATES:
        if '%y' in fmt.lower():
            try:
                return datetime.strptime(t, fmt).strftime('%Y-%m-%d')
            except ValueError:
                continue
        # A month/day is the next one not yet past: this year's, else a later one's (parsed with its year, so 2/29 reads
        # in a leap year, the next one up to four years on; strptime's own default year is 1900, no leap year).
        for year in range(local.year, local.year + 5):
            try:
                got = datetime.strptime(f'{t}/{year}', fmt + '/%Y')
            except ValueError:
                continue
            if localize(got.replace(hour=23, minute=59), tz) >= now:
                return got.strftime('%Y-%m-%d')
    when = parse_eta(text, now)
    return when.astimezone(tz).strftime('%Y-%m-%d') if when else ''


def card_eta(card: dict, line: dict, now: datetime, state: dict = None):
    """(the ETA the sweep decides on, the day to write into the board's field or ''), 0.16.0: the field's, unless the
    owner's `Scrum: building | eta` line is newer than the field and readable, which then names it (to the minute when
    the line gives one) and is copied into the field when its day differs. A line already copied (the card's record's
    `eta_line_at`, its `at`) is never newer than the field the copy wrote: it keeps its minute while the field holds its
    day, and the field rules once the owner has changed or cleared it since (`tickets eta N none` stays cleared)."""
    day = (card.get('eta') or '').strip()
    field = field_eta(day)
    building = line.get('kind') in ('building', 'blocked on')   # 0.22.0: a blocked card's ETA is its wait's end
    said = parse_eta(line.get('eta'), now) if building else None
    said_day = eta_day(line.get('eta'), now) if building else ''
    if not said_day:
        return field, ''
    said = said or field_eta(said_day)   # a date alone: the field takes it, its end the ETA
    if line.get('at') and line.get('at') == (state or {}).get('eta_line_at'):
        return (said, '') if said_day == day else (field, '')
    if not field or (line.get('at') or '') > (card.get('eta_at') or ''):
        return said, (said_day if said_day != day else '')
    return field, ''


def plan(card: dict, facts: dict, state: dict, now: datetime, cfg: dict) -> dict:
    """What the sweep does for one shipyard card: {state (what it found), question (to its owner, '' for none),
    actions [(kind, value)], change (a line for a new comment, '' for none)}. Pure: `facts` is {line, blockers
    [(number, state)], last_commit (datetime or None), closed, answer, owner_building, after}, `state` the card's
    record."""
    lights = cfg['lights']
    who_leads = lead()
    back = _tickets.stage('next', cfg)
    n, light, prio = card['number'], card.get('light') or '', card.get('priority') or ''
    line, blockers = facts.get('line') or {}, facts.get('blockers') or []
    labels = card.get('labels') or []
    out = {'state': '', 'question': '', 'actions': [], 'change': ''}
    open_blockers = [b for b, s in blockers if s == 'OPEN']
    if state.get('queued_idle_since') and light != lights.get('queued'):
        out['actions'].append(('forget', 'queued_idle_since'))   # the clock belongs to one Queued spell

    if line.get('kind') == 'depends on' and line.get('ref') and line['ref'] not in facts.get('closed', ()):
        out['state'] = f"depends on #{line['ref']}, still open"
        out['actions'] += [('move', 'next'), ('link', line['ref'])]
        out['change'] = f"Moved back to {back}: it depends on #{line['ref']}, which is not finished."
        return out

    if line.get('kind') == 'paused' and state.get('shipyard_at') and (line.get('at') or '') < state['shipyard_at']:
        # Put in the shipyard after the pause was written: the pause is spent, and the card waits Queued behind what its
        # owner is building, as a `next` line would have it.
        busy = [b for b in facts.get('owner_building') or () if b != n]
        line = {'kind': 'next', 'ref': busy[0] if busy else None, 'at': state['shipyard_at']}
        facts = dict(facts, line=line)
    if line.get('kind') == 'paused':
        # Its owner set it down: the shipyard holds what is being built, so it waits in the next column, its light off,
        # until a new `Scrum: building` line.
        why = line.get('ask') or 'no reason given'
        out['state'] = f'paused ({why}): back to {back}'
        out['actions'].append(('move', 'next'))
        out['change'] = f'Moved back to {back}: its owner paused it ({why}); the {_tickets.stage("shipyard", cfg)} holds what is being built.'
        return out

    blocked = lights.get('blocked') or ''   # 0.22.0 (SMADPickleBot #1336): a board with no blocked Light is unchanged
    scheduled = lights.get('scheduled') or ''   # 0.63.0 (SMADPickleBot #2200): a board with none shows a wait as blocked
    # The blocked light comes from the owner's own line alone, and never replaces Needs you (the lead's ask and
    # assignment are its) or the host's pushing light (a push under way): the review of #1336.
    relight = blocked and light != blocked and light not in (lights.get('needs'), lights.get('pushing'))
    unblocked_q = f"#{n} is unblocked: pick it back up (`Scrum: building | eta <when>`), or say `Scrum: next | after #N`."
    if line.get('kind') == 'blocked on':
        # 0.22.0: a wait outside the board (an email, a daily cycle, Apple's review), its ETA when the wait should end;
        # the card stays in the shipyard, its light the blocked one and its label on, and once the ETA passes its owner
        # is asked whether the wait is over (again ASK_AGAIN_HOURS later while it is not answered).
        what = line.get('ask') or 'something outside the board'
        # 0.43.0 (SMADPickleBot, Gene 11/21/26 10:56 AM PT: "again a scrum master job that he is not doing"): a wait whose
        # words name tickets ("Gene's pick on #2079") is linked to them like `blocked by`, and once every ticket it names
        # or links is closed the card is unblocked in the same pass. #1829 sat Blocked 11 hours after #2079 closed, and
        # #230 18 minutes after its three linked blockers closed, since a `blocked on` line returned here before the
        # every-blocker-closed rule below was reached.
        named = [int(m) for m in re.findall(r'#(\d+)', what) if int(m) != n]
        linked_now = [b for b, _s in blockers]
        # 0.63.0 (SMADPickleBot #2200; Gene, 11/22/26: "maybe blocked is not a good label, we also need Scheduled for
        # ticket that is blocked until a time/event?", with 13 cards Blocked and three of them blocked on anybody): a
        # wait that names and links no ticket is a date or an event, and on a board with a scheduled Light it shows
        # that Light and carries no blocked label. A wait on tickets stays blocked, as `blocked by` is.
        sched = bool(scheduled) and not named and not linked_now
        wait_role, wait_light = ('scheduled', scheduled) if sched else ('blocked', blocked)
        word = 'scheduled for' if sched else 'blocked on'
        missing = [r for r in named if r not in linked_now]
        # The wait's own ETA only: a line with none never borrows the field's, which may still hold a build's date.
        eta, write = card_eta(card, line, now, state) if line.get('eta') else (None, '')
        # 0.46.0 (SMADPickleBot #2146): the tickets a wait names or links end it only when the last of them closed after
        # the wait was written (the wait was on them: #1829's #2079, #230's three links), or once the wait's ETA has
        # passed or when it has none. A ticket that closed before the wait is context or an old link: #2106 and #2027
        # through old links, #1573 ("since #1572 shipped") and #2028 through tickets named as context, were unblocked by
        # 0.43.0 with their ETAs days ahead. 0.45.0 applied this to unnamed waits alone.
        closed_since = (facts.get('blockers_closed') or '') > (line.get('at') or '')
        decides = bool(named or linked_now) and (closed_since or not (eta and eta > now))
        if decides and not missing and not open_blockers:
            closed = sorted(set(named) | set(linked_now))
            out['state'] = 'every ticket its wait names or links is closed'
            if BLOCKED_LABEL in labels:
                out['actions'].append(('unlabel', BLOCKED_LABEL))
            if light and light in (blocked, scheduled):
                out['actions'].append(('light', 'shipyard'))
            out['change'] = 'Unblocked: every ticket its wait names or links is closed (' + ', '.join(f'#{r}' for r in closed) + ').'
            out['question'] = unblocked_q
            return out
        for r in missing:
            out['actions'].append(('link', r))
        if wait_light and light != wait_light and light not in (lights.get('needs'), lights.get('pushing')):
            out['actions'].append(('light', wait_role))
            out['change'] = f'Switched to {wait_light}: its owner says it waits on {what}.'
        if sched and BLOCKED_LABEL in labels:
            out['actions'].append(('unlabel', BLOCKED_LABEL))   # a date is no block: the label is for a person or a ticket
        elif not sched and BLOCKED_LABEL not in labels:
            out['actions'].append(('label', BLOCKED_LABEL))
        if write and 'ETA' in (cfg.get('fields') or {}):
            out['actions'].append(('eta', (write, line.get('at') or '')))
        out['state'] = f'{word} {what}' + (f', ETA {pt(eta)}' if eta else ', no ETA')
        if eta and eta <= now:
            out['question'] = (f"#{n} is {word} {what}, and its ETA ({pt(eta)}) has passed: is the wait over? "
                               f"`Scrum: building | eta <when>` to pick it up, or `Scrum: blocked on {what} | eta <when>` "
                               "with the new end of the wait.")
        elif not eta:
            out['question'] = (f"#{n} is {word} {what} with no ETA: when should the wait be over? "
                               f"`Scrum: blocked on {what} | eta <when>`.")
        return out

    linked = [b for b, _s in blockers]
    ref = line.get('ref') if line.get('kind') == 'blocked by' else None
    if ref and not (ref in linked and not open_blockers):   # a named blocker closed with no other open: the rule below
        who = line.get('who') or ''
        if relight:
            out['actions'].append(('light', 'blocked'))
        if BLOCKED_LABEL not in labels:
            out['actions'].append(('label', BLOCKED_LABEL))
            out['change'] = f"Blocked by #{ref}" + (f"; {who} can unblock it." if who else '.')
            if who.lower() == who_leads.lower():
                out['actions'].append(('needs_on', ref))
            elif who in cfg['fields']['Owner']['options']:
                out['actions'].append(('owner_on', (ref, who)))
        if ref not in linked:
            out['actions'].append(('link', ref))
        out['state'] = f"blocked by #{ref}" + (f" ({who} to unblock)" if who else '')
        return out

    if BLOCKED_LABEL in labels and blockers and not open_blockers:
        out['state'] = 'every blocker closed'
        out['actions'].append(('unlabel', BLOCKED_LABEL))
        if blocked and light == blocked:   # the blocked light off in the same pass, and the one question below
            out['actions'].append(('light', 'shipyard'))
        out['change'] = 'Unblocked: every ticket blocking it is closed.'
        out['question'] = unblocked_q
        return out
    if open_blockers:
        # A linked blocker alone keeps the label (as before 0.22.0) but never the light: the owner's own line says
        # whether it is building beside it.
        out['state'] = 'blocked by ' + ', '.join(f'#{b}' for b in open_blockers)
        if BLOCKED_LABEL not in labels:
            out['actions'].append(('label', BLOCKED_LABEL))
        return out

    stale_label = BLOCKED_LABEL in labels and not blockers   # a `blocked on` label its line no longer backs
    if light and light in (blocked, scheduled):
        # 0.22.0: blocked no more (its owner's newest line is no block, and no linked ticket blocks it): `building` turns
        # it to the working light; anything else takes the blocked light off and asks what is next, in the words of the
        # unblock above, so a card asked there is not asked again.
        if BLOCKED_LABEL in labels:
            out['actions'].append(('unlabel', BLOCKED_LABEL))
        if line.get('kind') == 'building':
            out['actions'].append(('light', 'working'))
            out['state'] = f"the owner says building: {lights['working']} set"
            out['change'] = f"Switched to {lights['working']}, as its owner said: the wait is over."
            return out
        out['actions'].append(('light', 'shipyard'))
        out['state'] = 'no block on record: the ' + ('scheduled' if light == scheduled else 'blocked') + ' light off'
        out['question'] = unblocked_q
        return out
    if stale_label:
        out['actions'].append(('unlabel', BLOCKED_LABEL))   # and the card goes on to its other rules below

    if light == lights['needs']:
        # A Needs-you card always says what it needs. A card the sweep itself flagged (needs_at) before asks were kept
        # was flagged for its Priority.
        ask = ((line.get('ask') if line.get('kind') == NEEDS_KIND else '') or state.get('needs_ask')
               or (PRIORITY_ASK if (not prio or state.get('needs_at')) else ''))
        if ask == PRIORITY_ASK and prio:
            out['actions'].append(('clear_light', n))
            out['state'] = f"{who_leads} set its Priority ({prio}): {lights['needs']} cleared"
            out['change'] = f'Priority {prio} set, so it no longer needs {who_leads}; back to its owner.'
            out['question'] = f'#{n} has its Priority ({prio}) now: is it your next? `Scrum: next | after #N` or `Scrum: building | eta <when>`.'
            return out
        answer = facts.get('answer') or {}
        if ask and answer:
            # The lead answered on the card; the light off, they are unassigned, and the owner is handed their words.
            out['actions'].append(('clear_light', n))
            out['state'] = f'{who_leads} answered: “{answer["text"]}”'
            out['change'] = f'{who_leads} answered the ask (“{answer["text"]}”), so it no longer needs them; handed back to its owner.'
            out['question'] = (f'{who_leads} answered #{n}\'s ask ({ask}): “{answer["text"]}”. Act on it, then say where it '
                               'stands: `Scrum: building | eta <when>` or `Scrum: next | after #N`.')
            return out
        if ask:
            out['state'] = f"{lights['needs']}, {who_leads}: {ask}"
            if not state.get('assigned'):   # on the lead's plate, their "Assigned to me" view, once the ask is written
                out['actions'].append(('assign', n))
            return out
        out['state'] = f"{lights['needs']}, but no ask on record"
        out['question'] = (f"#{n} is {lights['needs']} with no ask on record, so {who_leads} cannot tell what it needs: write it on the "
                           f"ticket in full as `Scrum: needs {who_leads} | <the decision or the steps, and where each goes>`, or "
                           f"clear the light (`{LIGHT_CLEAR.format(n=n)}`) if nothing is pending.")
        return out

    if not light and not prio:   # the Light is one field: Needs you never replaces a working one
        last = state.get('needs_at')
        if not last or (now - datetime.fromisoformat(last)).total_seconds() >= NEEDS_EVERY_HOURS * 3600:
            out['actions'].append(('needs', n))
        out['state'] = f"no Light and no Priority: {lights['needs']}, {who_leads}: {PRIORITY_ASK}"
        return out

    if lights.get('pushing') and light == lights['pushing']:
        # 0.21.0 (SMADPickleBot #1344): its work is committed and its dock push is running; the push's landing moves it
        # on, and a failed push puts the working light back. Nothing to ask.
        out['state'] = f"{lights['pushing']}: its dock push is running"
        return out

    building = [b for b in facts.get('owner_building') or () if b != n]
    working = lights['working']
    if light == working:
        # A working card with no Priority puts the lead on it once (their "Assigned to me" view); the Priority set takes
        # them off.
        if not prio and not state.get('prio_assigned'):
            out['actions'].append(('prio_assign', n))
        elif prio and state.get('prio_assigned'):
            out['actions'].append(('prio_unassign', n))
        last = facts.get('last_commit')
        said = datetime.fromisoformat(line['at'].replace('Z', '+00:00')) if line.get('at') else None
        if line.get('kind') == 'next' and (last is None or (said and said > last)):
            # The owner said it is next, not being built, and no commit named it since: Queued, in the shipyard.
            out['actions'].append(('light', 'queued'))
            after = f", after #{line['ref']}" if line.get('ref') else ''
            out['state'] = f"its owner says next{after}: {lights['queued']}"
            out['change'] = f"Switched to {lights['queued']}: its owner says it is next{after}, not being built yet."
            return out
        eta, write = card_eta(card, line, now, state)   # 0.16.0: the board's ETA field, a newer line copied into it
        if write and 'ETA' in (cfg.get('fields') or {}):
            out['actions'].append(('eta', (write, line.get('at') or '')))
        if building and not (eta and eta > now):
            # An owner may build several cards at once, so a card with its own live ETA is handled as a lone one below,
            # and one without is asked about itself.
            others = ', '.join(f'#{b}' for b in building)
            out['state'] = f'building with no ETA it can read, beside {others}'
            out['question'] = (f"#{n} is {working}, as are {others} of yours: what's its ETA? {eta_ask(n)}, or "
                               f"`Scrum: next | after #N` if it is only waiting its turn ({lights['queued']}, "
                               f"in the {_tickets.stage('shipyard', cfg)}).")
            return out
        last = facts.get('last_commit')
        quiet = last is None or (now - last).total_seconds() >= STALE_HOURS * 3600
        if eta and eta > now:
            out['state'] = f'building, ETA {pt(eta)}'
            if quiet:
                out['state'] += f'; no commit in {STALE_HOURS} h'
                out['question'] = (f"#{n} is {working} with an ETA of {pt(eta)}, but no commit has named it in "
                                   f"{STALE_HOURS} hours: still on it? {eta_ask(n)} or `Scrum: next | after #N`.")
            return out
        if eta:
            out['state'] = f'building, its ETA {pt(eta)} has passed'
            out['question'] = f"#{n} is {working} and its ETA ({pt(eta)}) has passed: what's the new one? {eta_ask(n)}"
            return out
        if line.get('kind') == 'building' and not quiet and not eta_day(line.get('eta'), now):   # unreadable, not cleared
            # An ETA the sweep cannot read is asked about once, as a missing one is: a word in its place usually means
            # the card is not being built, and `paused` is the word for that.
            out['state'] = 'building, no ETA it can read: ' + (line.get('eta') or 'none given')
            out['question'] = (f"#{n} is {working}, but its ETA (“{line.get('eta') or 'none given'}”) is not a time the "
                               f"sweep can read: say it as a date and time, {eta_ask(n)}, or, if it waits on "
                               f"something, `Scrum: paused | <why>`, and it goes back to {back} until you pick it up again.")
            return out
        out['state'] = 'building, no ETA'
        out['question'] = f"#{n} is {working}: what's its ETA? {eta_ask(n)}."
        return out

    if lights.get('queued') and light == lights['queued']:
        # A card waiting its turn stays in the shipyard, unasked while its owner builds; back to the next column only
        # once it has been Queued QUEUED_IDLE_MINUTES with nothing of its owner's being built (the clock in its record).
        if line.get('kind') == 'building':
            out['actions'].append(('light', 'working'))
            out['state'] = f'the owner says building: {working} set'
            out['change'] = f'Switched to {working}, as its owner said.'
            return out
        if building:
            out['state'] = f"{lights['queued']}, behind #{building[0]}, which its owner is building"
            if state.get('queued_idle_since'):
                out['actions'].append(('forget', 'queued_idle_since'))
            after = facts.get('after') or {}
            if line.get('ref') and after.get('status') in ('closed', 'landed') and line['ref'] not in building:
                # The wait its owner named is over while it builds another card: the owner is asked once what it waits
                # on now.
                out['question'] = (f"#{n} waits on #{line['ref']}, which has {'closed' if after['status'] == 'closed' else 'landed'}, "
                                   f"while you build #{building[0]}: start #{n} (`Scrum: building | eta <when>`) or say what "
                                   f"it waits on now (`Scrum: next | after #{building[0]}`).")
            return out
        # 0.40.0 (SMADPickleBot #2066): it stays where the lead queued it. Its owner's idleness is the owner's to end, and
        # the sweep tells them to start a card every pass (idle_owners), where this used to send the card back.
        if state.get('queued_idle_since'):
            out['actions'].append(('forget', 'queued_idle_since'))   # a clock from before 0.40.0
        out['state'] = f"{lights['queued']}, and nothing of its owner's is being built: its owner is told to start a card"
        return out

    if not light:
        if line.get('kind') == 'building':
            out['actions'].append(('light', 'working'))
            out['state'] = f'the owner says building: {working} set'
            out['change'] = f'Switched to {working}, as its owner said.'
            return out
        if line.get('kind') == 'next':
            ref = line.get('ref')
            out['state'] = 'next' + (f", after #{ref}" if ref else '') + f": {lights['queued']}"
            out['actions'].append(('light', 'queued'))
            out['change'] = f"Switched to {lights['queued']}: its owner says it is next" + (f", after #{ref}." if ref else '.')
            if building:
                # It waits in the shipyard behind its owner's build.
                out['state'] += f', behind #{building[0]}, which its owner is building'
                return out
            after = facts.get('after') or {}
            if after.get('status') in ('closed', 'landed'):
                out['question'] = (f"#{n} waits on #{ref}, which has {'closed' if after['status'] == 'closed' else 'landed'}"
                                   ", so nothing holds it now: start it (`Scrum: building | eta <when>`) or say what it "
                                   "waits on (`Scrum: next | after #N`).")
            elif after.get('status') == 'idle':
                out['question'] = (f"#{n} waits on #{ref}, which nobody is building (it is in {after.get('column') or 'no column'})"
                                   f": start #{ref} or #{n}, or say what it really waits on.")
            elif not ref:
                out['question'] = (f"#{n} is your next, but nothing of yours is being built: start it (`Scrum: building | "
                                   "eta <when>`) or say what comes first (`Scrum: next | after #N`).")
            return out
        out['state'] = 'no Light: nobody has said whether it is being built'
        out['question'] = (f"#{n} is in the {_tickets.stage('shipyard', cfg)} with no Light: is it your next, and does what "
                           "you're on now outrank it? `Scrum: next | after #N`, or `Scrum: building | eta <when>` if you've "
                           "switched to it.")
        out['again_minutes'] = UNSTARTED_AGAIN_MINUTES   # 0.26.0: a card nobody has started is not left for a day
        return out

    out['state'] = light
    return out


# ------------------------------------------------------------------ closed cards
def finished(col: str, cfg: dict) -> bool:
    """Whether a closed card's column is one it belongs in (configure(finished=), else the shipped and failed columns
    and a `shipment` column the board may have); an old name is read as its new one."""
    if _SET['finished'] is not None:
        return _SET['finished'](col, cfg)
    col = _tickets.column(col, cfg)
    return col in (_tickets.stage('shipped', cfg), _tickets.stage('failed', cfg), cfg.get('stages', {}).get('shipment', ''))


def unplanned(reason) -> bool:
    """Whether a close was as not planned or a duplicate (configure(unplanned=), else GitHub's two reasons)."""
    if _SET['unplanned'] is not None:
        return _SET['unplanned'](reason)
    return str(reason or '').lower() in ('not_planned', 'duplicate')


def closed_question(number, col: str) -> str:
    """The question a closed card's owner is asked (configure(closed_question=), else these words)."""
    if _SET['closed_question'] is not None:
        return _SET['closed_question'](number, col)
    return (f"#{number} was closed while in {col or 'no column'}: if it is done, verified and closed, answer `Scrum: done`; "
            "otherwise reopen it.")


def closed_to(who: str) -> str:
    """Who is asked about a closed card (configure(closed_to=), else its Owner, the captain for none or the lead)."""
    if _SET['closed_to'] is not None:
        return _SET['closed_to'](who)
    return who if who and who != lead() else captain()


def closed_filter(cfg: dict) -> str:
    """GitHub's own filter for the closed cards outside the shipped and failed columns (and a `shipment` column the
    board may have): a point a hundred cards, never the whole board."""
    out = f'is:closed is:issue -status:"{_tickets.stage("shipped", cfg)}" -status:"{_tickets.stage("failed", cfg)}"'
    shipment = cfg.get('stages', {}).get('shipment')
    return out + (f' -status:"{shipment}"' if shipment else '')


def closed_plan(card: dict, comments, login: str, state: dict, told: bool, now: datetime, cfg: dict) -> dict:
    """What the sweep does for one closed card: {action, why}. Pure. Nothing ('') for a card with no column or in a
    finished one (an old column name read as its new one); `archive` for a close as not planned or a duplicate (0.20.0,
    SMADPickleBot #1337: off the board, never into the failed column a release's failures keep); `ship`
    when its owner's newest `Scrum:` line is `done`, or an alert closer closed it (configure(closer=): the newest
    comment and the title); otherwise `ask`, at most once every CLOSED_EVERY_HOURS (`told`, the host's own listener's
    letter for this close, counting as the day's; `wait` until then)."""
    col = _tickets.column(card.get('status') or '', cfg)
    if not col or finished(col, cfg):
        return {'action': '', 'why': f'in {col or "no column"}'}
    if unplanned(card.get('reason')):
        return {'action': 'archive', 'why': f"closed as {'a duplicate' if card.get('reason') == 'duplicate' else 'not planned'}"}
    if scrum_line(comments, login).get('kind') == 'done':
        return {'action': 'ship', 'why': 'its owner answered `Scrum: done`'}
    newest = sorted(comments or [], key=lambda c: c.get('createdAt') or '')
    closer = _SET['closer']
    if closer is not None and closer(card.get('title') or '', (newest[-1].get('body') or '') if newest else ''):
        return {'action': 'ship', 'why': 'an alert closer closed it'}
    asked = state.get('closed_asked_at')
    if told and not asked:
        return {'action': 'wait', 'why': 'its owner was asked when it closed', 'remember': now.isoformat()}
    if asked and (now - datetime.fromisoformat(asked)).total_seconds() < CLOSED_EVERY_HOURS * 3600:
        return {'action': 'wait', 'why': f'its owner was asked at {pt(datetime.fromisoformat(asked))}'}
    return {'action': 'ask', 'why': f'closed while in {col}', 'question': closed_question(card['number'], col)}


def _told(n, closed_at, store) -> bool:
    return bool(_SET['closed_told'](n, closed_at, store)) if _SET['closed_told'] is not None else False


def closed_sweep(cfg: dict, gh, store, now: datetime, dry: bool, letters: dict, only=None, skip=()) -> list:
    """The catch for a close the host's own listener missed: the closed cards outside the finished columns, read with
    closed_filter, each settled by closed_plan: to the ship column with its shipped Light, archived off the board, or its
    question added to its owner's letter (closed_to). A card asked within the day is not read again; `only` is one card
    (an event named it: its owner's `Scrum: done`), read whatever its record says; `skip` the shipyard's own closed
    cards, which the column's sweep has just moved. Lines."""
    cards = _tickets.board_items(closed_filter(cfg), gh.run, cfg)
    if cards is None:
        return ['scrum: closed cards: the board did not answer; next sweep']
    cards = [c for c in cards if not _tickets.is_draft(c) and not _tickets.is_placeholder(c.get('title'), cfg)]
    gh.remember(cards)   # #1207: the items kept and the issues read in one aliased query
    gh.prefetch([(c.get('content') or {}).get('number') for c in cards
                 if (c.get('content') or {}).get('number') not in skip and (only is None or (c.get('content') or {}).get('number') == only)])
    login = _tickets.comment_login(cfg)
    lines = []
    for c in cards:
        n = (c.get('content') or {}).get('number')
        if not n or n in skip or (only is not None and n != only):
            continue
        state = store.get(_SET['state'], str(n)) or {}
        asked = state.get('closed_asked_at')
        if only is None and asked and (now - datetime.fromisoformat(asked)).total_seconds() < CLOSED_EVERY_HOURS * 3600:
            continue   # asked today: nothing to read until tomorrow's ask
        issue = gh.card(n)
        if not issue or issue.get('state') != 'CLOSED':
            continue   # unreadable, or reopened since GitHub's filter saw it
        comments = (issue.get('comments') or {}).get('nodes') or []
        told = _told(n, issue.get('closedAt'), store)
        p = closed_plan(dict(c, number=n, reason=str(issue.get('stateReason') or '').lower()), comments, login, state, told, now, cfg)
        if p['action'] == 'archive':
            ok = gh.archive(n)
            lines.append(f"scrum: closed #{n}: {p['why']}: " + ('archived off the board' if ok
                                                                 else 'the archive was refused; next sweep tries again'))
            continue
        if p['action'] == 'ship':
            ok = gh.ship(n)
            lines.append(f"scrum: closed #{n}: {p['why']}: " + (f"moved to {_tickets.stage('shipped', cfg)}, Shipped" if ok
                                                                 else f"the move to {_tickets.stage('shipped', cfg)} was refused; next sweep tries again"))
            continue
        if p['action'] == 'ask':
            to = closed_to(c.get('owner') or '')
            letters.setdefault(to, []).append((n, c.get('title') or '', p['question'], (c.get('content') or {}).get('url') or ''))
            state['closed_asked_at'] = now.isoformat()
            lines.append(f"scrum: closed #{n} {p['why']}: asked {to}")
        elif p['action'] == 'wait':
            if p.get('remember'):
                state['closed_asked_at'] = p['remember']
            lines.append(f"scrum: closed #{n}: {p['why']}")
        else:
            continue
        if not dry:
            store.set(_SET['state'], str(n), state)
    return lines


def dock_catch(cfg: dict, gh, dry: bool = False) -> list:
    """The catch for a dock landing nothing told (0.11.0; SMADPickleBot #1203, split from #1202: GitHub never delivered
    a dock push, so no card moved): the tickets the dock branch's commits since the main branch name in their `Ticket:`
    trailers (one compare through the runner, cfg's `main_branch` and `dock_branch`, else main and dock), every card's
    place in one aliased read (tickets.places), and each card still before the dock column moved forward to it in one
    aliased write (tickets.move_many, forward only). Lines, one a card moved; a compare GitHub does not answer is one."""
    main, dock = cfg.get('main_branch', 'main'), cfg.get('dock_branch', 'dock')
    if not hasattr(gh.run, 'compare'):
        return []   # 0.11.1: a runner with no compare (an older host's, a suite's fake) has nothing to read; no line
    commits = gh.run.compare(main, dock)
    if commits is None:
        return [f'scrum: {main}...{dock} could not be read; the dock catch runs next sweep']
    from . import board as _board
    numbers = sorted({n for c in commits for n in _board.ticket_numbers((c or {}).get('message') or '')})
    if not numbers:
        return []
    got = _tickets.places(numbers, gh.run, cfg)
    target = cfg['flow'].index('dock')
    behind = []
    for n in numbers:
        if n not in got:
            continue
        _item, now, light, raw = got[n]
        at = _tickets.rank(now, light, raw, cfg)
        if at is not None and at < target:
            behind.append(n)
    if not behind:
        return []
    if dry:
        return [f"scrum: #{n} is on {dock} but not in {_tickets.stage('dock', cfg)}; it would move there (dry run)" for n in behind]
    moved = dict(_tickets.move_many(behind, 'dock', gh.run, cfg))
    return [f"scrum: #{n} is on {dock} but was not in {_tickets.stage('dock', cfg)}: " +
            ('moved there' if moved.get(n) else 'the move was refused; next sweep tries again') for n in behind]


def closed_needs_filter(cfg: dict) -> str:
    """GitHub's own filter for the closed cards still carrying the needs-the-lead Light, in any column."""
    return f'is:closed is:issue light:"{cfg["lights"]["needs"]}"'


def closed_needs_sweep(cfg: dict, gh, dry: bool = False) -> list:
    """A closed ticket never keeps the needs-the-lead Light (0.8.2; SMADPickleBot #1133: a Needs you set on a shipped
    card sat there three days, outside every column the other sweeps read): each closed card carrying it, read with
    closed_needs_filter in any column, has the Light cleared and the lead unassigned; the issue is read once more first,
    so a card reopened since GitHub's filter saw it keeps its Light. Lines."""
    cards = _tickets.board_items(closed_needs_filter(cfg), gh.run, cfg)
    if cards is None:
        return ['scrum: closed cards with a needs light: the board did not answer; next sweep']
    gh.remember(cards)   # #1207
    gh.prefetch([(c.get('content') or {}).get('number') for c in cards if (c.get('light') or '') == cfg['lights']['needs']])
    lines = []
    for c in cards:
        n = (c.get('content') or {}).get('number')
        if not n or _tickets.is_draft(c) or _tickets.is_placeholder(c.get('title'), cfg):
            continue
        if (c.get('light') or '') != cfg['lights']['needs']:
            continue   # the card's own Light, not the filter alone: GitHub's index can lag a clear made this sweep
        issue = gh.card(n)
        if not issue or issue.get('state') != 'CLOSED':
            continue   # unreadable, or reopened since the filter saw it
        ok = gh.field(n, 'Light', '')
        if ok:
            gh.assign(n, on=False)
        lines.append(f"scrum: closed #{n} kept {cfg['lights']['needs']}: " +
                     ('cleared, the lead unassigned' if ok else 'the clear was refused; next sweep tries again'))
    return lines


def question_key(question: str) -> str:
    """A question's short fingerprint ('' for none): a card is asked again only when its question changes."""
    return hashlib.sha256(question.encode()).hexdigest()[:16] if question else ''


def idle_owners(cards, cfg: dict, starts: dict, now: datetime) -> dict:
    """0.40.0 (SMADPickleBot #2066): {owner: {kind, number, title, url, free, minutes}} for each owner of a shipyard
    card who is not working by the board's own evidence. Pure: `cards` the column's open rows, `starts` {number: the
    datetime of its last start or commit, None for none on record}.

      kind 'start'      nothing of theirs carries the working or pushing light, and at least one card can be worked: no
                        Light with a Priority, or the queued light, and no blocked label. The card named is the first
                        by Priority, a queued one before one with no Light, then the lowest number; `free` how many.
      kind 'carry on'   something of theirs is being built, none of it is pushing, and the newest start or commit on
                        any of their building cards is STALE_BUILD_MINUTES old or more (`minutes`). A building card
                        with no start on record says nothing either way.

    The lead is never told (their cards get the needs light), nor is a card with no owner."""
    lights = cfg['lights']
    working, pushing, queued = lights.get('working') or None, lights.get('pushing') or None, lights.get('queued') or None
    who_leads = lead()
    by = {}
    for c in cards:
        n, owner = (c.get('content') or {}).get('number'), c.get('owner') or ''
        if n and owner and owner != who_leads:
            by.setdefault(owner, []).append(c)

    def rank(c):
        return (c.get('priority') or 'P9', 0 if c.get('light') else 1, c['content']['number'])

    def item(kind, c, free=0, minutes=0):
        return {'kind': kind, 'number': c['content']['number'], 'title': c.get('title') or '',
                'url': (c.get('content') or {}).get('url') or '', 'free': free, 'minutes': minutes,
                'column': _tickets.stage('shipyard', cfg), 'light': working or ''}
    out = {}
    for owner, rows in by.items():
        building = [c for c in rows if working and c.get('light') == working]
        if pushing and any(c.get('light') == pushing for c in rows):
            continue   # a dock push of theirs is running
        if not building:
            free = [c for c in rows if BLOCKED_LABEL not in (c.get('labels') or [])
                    and ((queued and c.get('light') == queued) or (not c.get('light') and c.get('priority')))]
            if free:
                out[owner] = item('start', min(free, key=rank), free=len(free))
            continue
        seen = [starts.get(c['content']['number']) for c in building]
        seen = [s for s in seen if s]
        quiet = (now - max(seen)).total_seconds() / 60 if seen else 0
        if quiet >= STALE_BUILD_MINUTES:
            out[owner] = item('carry on', min(building, key=rank), minutes=int(quiet))
    return out


def idle_sweep(cfg: dict, cards, starts: dict, store, now: datetime, dry: bool = False) -> tuple:
    """(lines, {owner: item}) of a full sweep's idle owners (idle_owners), each item carrying `sweeps`, how many full
    sweeps in a row found the owner idle, and `since`, the first of them (the owner's record, IDLE_KEY in the sweep's
    state collection). An owner found working again has the record emptied, so the next spell counts from one."""
    idle = idle_owners(cards, cfg, starts, now)
    lines = []
    owners = {c.get('owner') for c in cards if c.get('owner')} | set(idle)
    for owner in sorted(owners):
        key = IDLE_KEY.format(owner=re.sub(r'[^a-z0-9]+', '-', owner.lower()).strip('-'))
        rec = store.get(_SET['state'], key) or {}
        got = idle.get(owner)
        if not got:
            if rec and not dry:
                store.set(_SET['state'], key, {})
            continue
        got['sweeps'] = int(rec.get('sweeps') or 0) + 1
        got['since'] = rec.get('since') or now.isoformat()
        if not dry:
            store.set(_SET['state'], key, {'since': got['since'], 'sweeps': got['sweeps'], 'ticket': got['number'],
                                           'kind': got['kind']})
        what = (f"nothing being built and {got['free']} card{'s' if got['free'] != 1 else ''} to work: told to start"
                if got['kind'] == 'start' else
                f"no start or commit on a building card in {got['minutes']} minutes: told to carry on with")
        lines.append(f"scrum: {owner} is idle ({what} #{got['number']}; sweep {got['sweeps']} of this spell)")
    return lines, idle


def letter(owner: str, items, idle: dict = None) -> tuple:
    """(subject, text) of one owner's letter: every card of theirs with a new question, and, when the sweep found the
    owner idle (0.40.0, idle_sweep's item), the one card to start or carry on with now, said first and in the
    subject."""
    lines, subject = [], ''
    if idle:
        n, title = idle['number'], idle.get('title') or ''
        cmd = _SET['start_command'].format(n=n) if _SET['start_command'] else ''
        how = f' (`{cmd}`)' if cmd else ''
        if idle['kind'] == 'start':
            subject = f'Scrum master: start #{n} now'
            free = idle.get('free') or 1
            lines.append(f"{owner}, nothing of yours is being built, and {free} of your {idle.get('column') or 'working-column'} "
                         f"card{'s' if free != 1 else ''} can be worked. Start #{n} {title} now{how}, in this turn. A "
                         "full context, a ship, a warm, a run or a reply you are waiting for is no reason to wait: the "
                         "plan is the ticket's body, and the pipeline watches the rest.")
        else:
            subject = f'Scrum master: carry on with #{n} now'
            lines.append(f"{owner}, #{n} {title} is {idle.get('light') or 'being built'}, and no start or commit has named "
                         f"any card you are building for {idle.get('minutes')} minutes. Carry on with it now{how}. If you "
                         "are in the middle of it, carry on and answer nothing.")
        if int(idle.get('sweeps') or 1) > 1:
            lines.append(f"(Told {idle['sweeps']} sweeps running; first at {pt(datetime.fromisoformat(idle['since']))}.)")
        if idle.get('url'):
            lines.append(idle['url'])
    if items:
        word = f"{len(items)} Shipyard card{'s' if len(items) != 1 else ''} need{'s' if len(items) == 1 else ''} a word"
        subject = f'{subject} ({word})' if subject else f'Scrum master: {word}'
        lines.append(f"{'And the' if idle else f'{owner}, the'} scrum master's sweep has a question on each of these. "
                     "Answer on the ticket with one `Scrum:` line:")
        for n, title, q, url in items:
            lines.append(f"- #{n} {title}: {q}\n  {url}")
    return subject, '\n'.join(lines)


def comment_body(when: datetime, found: str, did: str) -> str:
    """The card's one scrum master comment, edited in place each sweep: when, what it found and what it did."""
    return f"{MARKER} · {pt(when)}\n\n- Found: {found or 'nothing to do'}\n- Did: {did or 'nothing'}"


class GitHub:
    """The sweep's GitHub reads and writes, through the ticket tools' runner (chuang_sdlc.tickets' configure(runner=)
    unless one is given) and the board helpers. A dry run writes nothing and answers every write True."""

    def __init__(self, cfg: dict, run=None, dry: bool = False):
        self.cfg, self.run, self.dry = cfg, run or _tickets._run(None), dry
        self.owner, self.repo = cfg['repo'].split('/')
        # 0.10.0 (SMADPickleBot #1207; Gene, 11/8/26 9:27 PM PT: "scrum master should be batching graphql calls as much
        # as possible, so we don't get throttled by github"): the cards a sweep reads come from one aliased query
        # (prefetch), their items from the board listing it already holds (remember), and its field writes are queued
        # while `batch` is on and sent in one aliased mutation at its end (flush).
        self._cards, self._items, self._queued, self.batch = {}, {}, [], False

    def remember(self, cards) -> None:
        """The Project items of a board listing's cards, kept so a field write needs no item read of its own."""
        for c in cards or []:
            n = (c.get('content') or {}).get('number')
            if n and c.get('id'):
                self._items[int(n)] = c['id']

    def prefetch(self, numbers) -> int:
        """Every issue's CARD_QUERY fields in ONE aliased query a PREFETCH_CHUNK of issues, kept for card(); the count
        read. A chunk GitHub does not answer leaves its cards to card()'s own read."""
        numbers = [int(n) for n in dict.fromkeys(numbers or []) if n and int(n) not in self._cards]
        got = 0
        for i in range(0, len(numbers), PREFETCH_CHUNK):
            chunk = numbers[i:i + PREFETCH_CHUNK]
            body = ' '.join(f'i{n}: issue(number: {n}) {{ {CARD_FIELDS} }}' for n in chunk)
            try:
                data = self.run.graphql(f'query($owner: String!, $repo: String!) {{ repository(owner: $owner, name: $repo) '
                                        f'{{ {body} }} }}', {'owner': self.owner, 'repo': self.repo})
            except Exception:   # noqa: BLE001 - a client that raises: card() reads each one itself
                data = None
            repo = (data or {}).get('repository') if isinstance(data, dict) else None
            if not isinstance(repo, dict):
                continue
            for n in chunk:
                if isinstance(repo.get(f'i{n}'), dict):
                    self._cards[n] = repo[f'i{n}']
                    got += 1
        return got

    def card(self, n: int, repo: str = '') -> dict:
        """One issue's state, close, blocked-by links and last 60 comments (CARD_QUERY), from prefetch's read when it
        has it; {} when GitHub does not answer. `repo` (owner/name, 0.42.0) reads another repository's issue (a
        `Scrum:` line's full reference, SMADPickleBot #2006); none, or this board's own, reads this board's."""
        owner, name = repo.split('/', 1) if repo and '/' in repo else (self.owner, self.repo)
        foreign = (owner.lower(), name.lower()) != (self.owner.lower(), self.repo.lower())
        if not foreign and int(n) in self._cards:
            return self._cards[int(n)]
        data = self.run.graphql(CARD_QUERY, {'owner': owner, 'repo': name, 'n': int(n)})
        try:
            return (data['repository']['issue'] or {}) if data else {}
        except (KeyError, TypeError):
            return {}

    def flush(self) -> list:
        """The queued field writes in ONE aliased mutation a tickets.MOVE_CHUNK of cards, a card's writes merged (the
        later wins); a WARNING line a card GitHub refused. Nothing queued is nothing sent."""
        merged = {}
        for item, fields in self._queued:
            merged.setdefault(item, {}).update(fields)
        self._queued = []
        writes = list(merged.items())
        took = set()
        for i in range(0, len(writes), _tickets.MOVE_CHUNK):
            took |= _tickets._write_many(writes[i:i + _tickets.MOVE_CHUNK], set(), self.run, self.cfg)
        for item, fields in writes:   # a card the batch did not take, written by itself as before 0.10.0
            if item not in took and getattr(self.run, 'item_fields', None) and self.run.item_fields(item, fields):
                took.add(item)
        number = {item: n for n, item in self._items.items()}
        return [f"WARNING: scrum: #{number.get(item, item)}'s field write was refused; the next sweep tries again"
                for item, _f in writes if item not in took]

    def assign(self, n, on=True):
        """The repository's owner (the lead's login) assigned to the card or not."""
        return True if self.dry else self.run.assign(n, _tickets.comment_login(self.cfg), on=on)

    def label(self, n, name, add=True):
        """A label added to the card, or taken off."""
        return True if self.dry else (self.run.add_labels(n, [name]) if add else self.run.remove_label(n, name))

    def link(self, issue_id: str, blocker_id: str) -> bool:
        """A native blocked-by link (LINK_MUTATION), by both issues' node ids."""
        return True if self.dry else self.run.graphql(LINK_MUTATION, {'i': issue_id, 'b': blocker_id}) is not None

    def issue_id(self, n: int) -> str:
        """An issue's node id ('' when it cannot be read)."""
        return (self.card(n) or {}).get('id') or ''

    def comment(self, n: int, body: str) -> bool:
        """A comment on the card."""
        return True if self.dry else self.run.comment(n, body)

    def edit_comment(self, cid: int, body: str) -> bool:
        """A comment's body replaced."""
        return True if self.dry else self.run.edit_comment(cid, body)

    def field(self, n: int, field: str, value: str) -> bool:
        """One Project field of the card (tickets.set_field); its item from the listing when remember() kept it; queued
        for flush() while `batch` is on, a single-select only (anything else is written at once). True when queued."""
        if self.dry:
            return True
        item = self._items.get(int(n)) or _tickets.item_id(n, self.run, self.cfg)
        if not item:
            return False
        spec = self.cfg['fields'].get(field) or {}
        if self.batch and 'options' in spec:
            fid, opt = _tickets._option(field, _tickets.column(value, self.cfg) if field == 'Status' else value, self.cfg)
            if opt is False:
                return False
            self._queued.append((item, {fid: opt}))
            return True
        return _tickets.set_field(item, field, value, self.run, self.cfg)

    def move(self, n: int, role: str) -> bool:
        """A card back to `role` (the scrum master only ever moves back: a queued or dependent card to the next column);
        True only once the board took it."""
        return True if self.dry else all(ok for _n, ok in _tickets.move([n], role, self.run, self.cfg, back=True))

    def ship(self, n: int) -> bool:
        """A closed card to the ship column with its shipped Light: tickets.move, forward only, the move marked first
        (tickets.mark_ship_move) whatever runner this sweep holds, so the host never takes it for the lead's drag; True
        only once the board took it."""
        if self.dry:
            return True
        _tickets.mark_ship_move([n], self.run)
        return all(ok for _n, ok in _tickets.move([n], 'shipped', self.run, self.cfg))

    def archive(self, n: int) -> bool:
        """A card closed as not planned or a duplicate archived off the board (0.20.0; SMADPickleBot #1337: it went to the
        failed column until then, where twenty such cards hid the release failures the column is for): the pipeline's
        own edit, marked first as a move is, then tickets.archive; True once the board took it."""
        if self.dry:
            return True
        _tickets.mark_ship_move([n], self.run)
        return _tickets.archive(n, self.run, self.cfg)

    def place(self, n: int):
        """(column, light) of a card, or None when the board does not answer."""
        got = _tickets.ticket_place(n, self.run, self.cfg)
        return (got[1], got[2]) if got else None


def _store():
    if _SET['store'] is None:
        raise _tickets.NotConfigured('no record store: scrum.configure(store=)')
    return _SET['store']()


def sweep(cfg=None, gh=None, store=None, mail=None, now=None, dry=False, out=print, only=None) -> list:
    """One pass over the shipyard: every card's plan applied, its comment kept, the owners' letters sent. Lines.
    `only`, a ticket number, runs that one card (an event named it: its Status, Priority, Light or Owner changed, or a
    `Scrum:` line was written); a card not in the shipyard is then settled as a closed card when it is one, else said
    and left. Open and closed cards in the shipyard alike: a closed one goes to the ship column, its light cleared."""
    cfg = cfg or _tickets._board()
    gh = gh or GitHub(cfg, dry=dry)
    now = now or now_utc()
    cards = _tickets.board_items(_tickets.filter_columns([_tickets.stage('shipyard', cfg)]) + ' is:issue', gh.run, cfg)
    if cards is None:
        return ['scrum: the board did not answer; nothing swept']
    # The column's placeholder card, or any draft, is nobody's: never asked about, lit or moved. GitHub's `is:issue`
    # leaves drafts out, and this holds when it does not (a placeholder converted to an issue keeps its title).
    cards = [c for c in cards if not _tickets.is_draft(c) and not _tickets.is_placeholder(c.get('title'), cfg)]
    every = cards   # the whole column, so a one-card run still sees what else its owner is building
    # #1207: the column's items kept, the cards this run reads in one aliased query, and its field writes in one
    # aliased mutation at its end (flush), so a sweep is a few GraphQL calls, never one a card a field.
    gh.remember(cards)
    gh.batch = not dry
    gh.prefetch([(c.get('content') or {}).get('number') for c in cards
                 if only is None or (c.get('content') or {}).get('number') == int(only)])
    if store is None:
        store = _store()
    who_leads = lead()
    letters, lines = {}, []
    open_rows, starts, idling = [], {}, {}   # 0.40.0: what the idle read takes from this pass
    if only is not None:
        cards = [c for c in cards if (c.get('content') or {}).get('number') == int(only)]
        if not cards:
            lines = closed_sweep(cfg, gh, store, now, dry, letters, only=int(only))
            lines += gh.flush()
            gh.batch = False
            return (lines + _send(letters, mail, dry)) or [f'scrum: #{only} is not in the {_tickets.stage("shipyard", cfg)}; nothing to do']
    for c in cards:
        n = (c.get('content') or {}).get('number')
        if not n:
            continue
        issue = gh.card(n)
        if not issue:
            lines.append(f'scrum: #{n} could not be read; next sweep')
            continue
        if issue.get('state') == 'CLOSED':
            # A closed ticket has left the work: the ship column, its light cleared. The move is marked first
            # (tickets.mark_ship_move), as GitHub.ship() marks: set_field does not, and an unmarked move into the ship
            # column reads to the host as the lead's drag, a ship nobody ordered.
            if not dry:
                _tickets.mark_ship_move([n], gh.run)
                gh.field(n, 'Status', _tickets.stage('shipped', cfg))
                gh.field(n, 'Light', '')
                gh.comment(n, f'{MARKER}: closed, so moved out of the {_tickets.stage("shipyard", cfg)} to '
                              f'{_tickets.stage("shipped", cfg)} with its light cleared.')
            lines.append(f'scrum: #{n} is closed: moved to {_tickets.stage("shipped", cfg)}, light cleared')
            continue
        comments = (issue.get('comments') or {}).get('nodes') or []
        blockers = [(b['number'], b['state']) for b in (issue.get('blockedBy') or {}).get('nodes') or []]
        # 0.45.0 (#2146): when the last linked blocker closed, so a wait written after it is not read as waiting on it
        blockers_closed = max([b.get('closedAt') or '' for b in (issue.get('blockedBy') or {}).get('nodes') or []] or [''])
        mark = store.get(_tickets.collection('starts'), str(n)) or {}
        last = datetime.fromtimestamp(mark['at_epoch'], timezone.utc) if mark.get('at_epoch') else None
        open_rows.append(c)
        starts[n] = last
        login = _tickets.comment_login(cfg)   # every session comments as one account (#2145)
        line = scrum_line(comments, login, skip=('done',))   # `done` is a closed card's word, read past here
        closed = ()
        theirs = line.get('ref_repo') or ''   # #2006: a full reference is read in its own repository
        if theirs and theirs.lower() == cfg['repo'].lower():
            theirs = ''
        if line.get('kind') == 'depends on' and line.get('ref'):
            dep = gh.card(line['ref'], repo=theirs)
            closed = (line['ref'],) if (dep or {}).get('state') == 'CLOSED' else ()
        card = dict(c, number=n)
        state = store.get(_SET['state'], str(n)) or {}
        owner_building = [(x.get('content') or {}).get('number') for x in every
                          if (x.get('owner') or '') == (c.get('owner') or '') and x.get('light') == cfg['lights']['working']]
        after = {}
        if line.get('kind') == 'next' and line.get('ref'):
            ref_card = gh.card(line['ref'], repo=theirs) or {}
            # another repository's issue is on another board: judged by its state alone, never this board's columns
            where = gh.place(line['ref']) if ref_card.get('state') == 'OPEN' and not theirs else None
            if ref_card.get('state') == 'CLOSED':
                after = {'status': 'closed'}
            elif theirs and ref_card.get('state') == 'OPEN':
                after = {'status': 'building', 'column': theirs}
            elif where:
                col, lit = where
                past = _tickets.rank(col, lit, col, cfg)
                after = ({'status': 'landed', 'column': col} if past is not None and past >= cfg['flow'].index('dock')
                         else {'status': 'building', 'column': col} if lit == cfg['lights']['working']
                         else {'status': 'idle', 'column': col})
        asked_at = (line.get('at') if line.get('kind') == NEEDS_KIND else '') or state.get('needs_at') or ''
        answer = lead_answer(comments, login, gh_stamp(asked_at)) if asked_at else {}
        p = plan(card, {'line': line, 'blockers': blockers, 'last_commit': last, 'closed': closed, 'answer': answer,
                        'owner_building': owner_building, 'after': after, 'blockers_closed': blockers_closed},
                 state, now, cfg)
        did = []
        needs = cfg['lights']['needs']
        for kind, value in p['actions']:
            if kind == 'label':
                gh.label(n, value)
                did.append(f'the `{value}` label added')
            elif kind == 'unlabel':
                gh.label(n, value, add=False)
                did.append(f'the `{value}` label taken off')
            elif kind == 'link':
                bid = gh.issue_id(value)
                ok = bool(bid) and gh.link(issue['id'], bid)
                did.append(f'linked as blocked by #{value}' if ok else f'the blocked-by link to #{value} was refused')
            elif kind == 'needs':
                gh.field(n, 'Light', needs)
                state['needs_at'], state['needs_ask'] = now.isoformat(), PRIORITY_ASK
                did.append(f'{needs}, {who_leads}: {PRIORITY_ASK}')
            elif kind == 'clear_light':
                gh.field(n, 'Light', '')
                gh.assign(n, False)
                state.pop('needs_ask', None)
                state.pop('assigned', None)
                did.append(f'{needs} cleared, {who_leads} unassigned')
            elif kind == 'assign':
                gh.assign(n, True)
                state['assigned'] = True
                did.append(f'assigned to {who_leads}')
            elif kind == 'needs_on':
                gh.field(value, 'Light', needs)
                did.append(f'{needs} on #{value}, for {who_leads} to unblock')
            elif kind == 'owner_on':
                ref, who = value
                gh.field(ref, 'Owner', who)
                did.append(f'#{ref} handed to {who} to unblock')
            elif kind == 'light':
                to = cfg['lights'].get(value, '')   # 0.22.0: a role the board gives no light clears it, never a KeyError
                gh.field(n, 'Light', to)
                did.append(f'{to} set' if to else 'its light cleared')
            elif kind == 'eta':   # 0.16.0: an owner's `eta` line copied into the board's ETA field, the line remembered
                day, line_at = value
                if gh.field(n, 'ETA', day):
                    state['eta_line_at'] = line_at
                    did.append(f"ETA {day} set from its owner's line")
                else:
                    did.append(f"ETA {day} not set (GitHub refused it)")
            elif kind == 'remember':   # the Queued clock, kept in the card's record
                state[value[0]] = value[1]
            elif kind == 'forget':
                state.pop(value, None)
            elif kind == 'move':
                back = _tickets.stage(value, cfg)
                if gh.move(n, value):
                    did.append(f'moved back to {back}')
                else:   # never said as moved, and the change comment is not posted
                    did.append(f'the move back to {back} was refused; next sweep tries again')
                    p['change'] = ''
            elif kind in ('prio_assign', 'prio_unassign'):
                gh.assign(n, kind == 'prio_assign')
                if kind == 'prio_assign':
                    state['prio_assigned'] = True
                    did.append(f'no Priority: {who_leads} assigned to {PRIORITY_ASK}')
                else:
                    state.pop('prio_assigned', None)
                    did.append(f'Priority set: {who_leads} unassigned')
        key = question_key(p['question'])
        # An unanswered question is asked again once ASK_AGAIN_HOURS pass; one that names its own wait (`again_minutes`:
        # a card in the working column nobody has started, UNSTARTED_AGAIN_MINUTES) once that passes instead.
        again_s = (p.get('again_minutes') or ASK_AGAIN_HOURS * 60) * 60
        stale = bool(key) and key == state.get('asked') and bool(state.get('asked_at')) and (
            now - datetime.fromisoformat(state['asked_at'])).total_seconds() >= again_s
        if key and (key != state.get('asked') or stale):
            # The same question again counts up from its first ask, and says so: an owner who missed the first letter
            # reads how long the card has waited (0.26.0).
            repeat = key == state.get('asked')
            count = int(state.get('asked_count') or 1) + 1 if repeat else 1
            first = (state.get('first_asked_at') or state.get('asked_at')) if repeat else now.isoformat()
            question = p['question']
            if count > 1:
                waited = max(0, round((now - datetime.fromisoformat(first)).total_seconds() / 60))
                question += f' (Asked {count} times now; first asked {waited} minutes ago.)'
            to = card.get('owner') or captain()
            if to == who_leads and answer and p['state'].startswith(f'{who_leads} answered'):
                to = captain()   # the lead's own card, answered by them: the captain takes it, never a Needs you back on them
            if to == who_leads:
                gh.field(n, 'Light', needs)
                did.append(f'{needs}: its owner is {who_leads}')
            else:
                letters.setdefault(to, []).append((n, card.get('title') or '', question, (c.get('content') or {}).get('url') or ''))
                did.append(f'asked {to}' + (f' again ({count} times since {first[:16]})' if count > 1 else ''))
            state['asked'], state['asked_at'] = key, now.isoformat()
            state['asked_count'], state['first_asked_at'] = count, first
        elif not key:
            for name in ('asked', 'asked_count', 'first_asked_at'):
                state.pop(name, None)
        mine = [x for x in comments if (x.get('author') or {}).get('login') == login
                and (x.get('body') or '').startswith(MARKER + ' ·')]
        body = comment_body(now, p['state'], '; '.join(did))
        if mine:
            gh.edit_comment(mine[-1]['databaseId'], body)
        else:
            gh.comment(n, body)
        if p['change']:
            gh.comment(n, f"{MARKER}: {p['change']}")
        if did:   # a host's listener skips this card's own edits for a while after (never its own trigger)
            state['acted_at'] = now.isoformat()
        if not dry:
            store.set(_SET['state'], str(n), state)
        lines.append(f"scrum: #{n} {p['state']}" + (f" ({'; '.join(did)})" if did else ''))
    unplaced = {}
    if only is None:   # the catch for a close the host's own listener missed
        shipyard ={(c.get('content') or {}).get('number') for c in cards}   # its closed cards were just moved above
        lines += closed_sweep(cfg, gh, store, now, dry, letters, skip=shipyard)
        lines += closed_needs_sweep(cfg, gh, dry)
        warming, wrecked = {}, {}
        # 0.31.0 (SMADPickleBot #1652): a board without a release train (configure or tenant.using(release_train=False))
        # skips what reads one, the dock branch and its warm. 0.32.0 (#1688; Gene, 11/17/26: a new board "inherits
        # everything we built"): the ship card and the columns' label cards are the board's own, so such a board keeps
        # them whenever its config names them (`ship_card`, `placeholders`), and since no ship's sync makes its label
        # cards, the sweep makes a missing one.
        train = _SET['release_train']

        def said(ln):
            return ('WARNING: scrum: ' + ln[len('WARNING: tickets: '):]) if ln.startswith('WARNING: ') else f'scrum: {ln}'
        if train:
            lines += dock_catch(cfg, gh, dry)   # #1203: a dock ticket a lost landing never moved
        if train or cfg.get('ship_card'):
            # The sweep's own store reads the ship record too: no second client.
            reader = _SET['ship_run_of'](store) if _SET['ship_run_of'] is not None else None
            lines += ship_card_home(cfg, gh, dry, ship_run=reader)
        if train or cfg.get('placeholders'):
            if not train and not dry:
                made = _tickets.placeholders(gh.run, cfg)
                if ' in place' not in made:
                    lines.append(said(made))
            # 0.14.0 (#1234): each column's placeholder first, the ship card second in its home, after the keeper's move
            lines += [said(ln) for ln in _tickets.card_order_keep(gh.run, cfg, dry)]
        if train:
            lines += warming_sweep(cfg, gh, store, now, warming, dry)   # 0.14.0: a Dock card stuck Warming
        lines += wrecked_sweep(cfg, gh, store, now, wrecked, dry)   # 0.20.0: the failed column kept for failures
        lines += unplaced_sweep(cfg, gh, unplaced, dry)   # 0.38.0: a card with no column is in no other read
        # 0.40.0 (SMADPickleBot #2066): an owner with a card to work and nothing being built is told to start one, every
        # full sweep, in the same letter as any question (the rows as this pass read them: a light this pass set shows
        # at the next).
        idle_lines, idling = idle_sweep(cfg, open_rows, starts, store, now, dry)
        lines += idle_lines
        for owner in idling:
            letters.setdefault(owner, [])
    else:
        warming, wrecked = {}, {}
    lines += gh.flush()   # #1207: every field write this sweep decided, in one aliased mutation
    gh.batch = False
    lines += _send(unplaced, mail, dry, compose=unplaced_letter)
    lines += _send(letters, mail, dry, compose=lambda to, items: letter(to, items, idling.get(to)))
    lines += _send(warming, mail, dry, compose=warming_letter)
    lines += _send(wrecked, mail, dry, compose=wrecked_letter)
    return lines or [f'scrum: the {_tickets.stage("shipyard", cfg)} is empty']


def ship_card_home(cfg, gh, dry: bool = False, ship_run=None, now: float = None) -> list:
    """The ship card back in its home column when it sits anywhere but there or the ship column, or in the ship column
    with no ship under way, and its title made the board config's `ship_card` title: the board's drafts read with its
    own filter (a dozen cards), kept by tickets.ship_card_keep, the one keeper a ship's sync_columns runs too
    (`ship_run` and `now` passed through). Lines; nothing when it is home and titled, or in the ship column while its
    ship runs."""
    lines = _tickets.ship_card_keep(gh.run, cfg, _tickets.board_items('is:draft', gh.run, cfg) or [], dry=dry,
                                    ship_run=ship_run, now=now)
    return [('WARNING: scrum: ' + ln[len('WARNING: '):]) if ln.startswith('WARNING: ') else f'scrum: {ln}' for ln in lines]


# 0.14.0 (SMADPickleBot #1233; Gene, 11/9/26 12:51 AM PT: "scrum master should look at Dock tickets that have been
# Warming for more than 3 minutes and tell owner to investigate"): on 11/9/26 eight Dock cards sat Warming half an hour
# after their warm and dev Deploy passed, and Gene found it before any session did.
WARMING_MINUTES = 3


def warming_sweep(cfg: dict, gh, store, now: datetime, letters: dict, dry: bool = False) -> list:
    """Each Dock card whose Light is the dock's Warming light and was set more than WARMING_MINUTES ago (its `light_at`,
    the board's updatedAt) goes into its owner's warming letter (the captain's for none or the lead), once per Warming
    episode: the card's scrum record keeps the `light_at` it was told about, so a card that warms again is told again.
    One read of the Dock column with GitHub's own filter. Lines.

    0.29.0 (SMADPickleBot #1600): a card is stuck only when nothing is warming it. A host that keeps a record of its
    warm (configure(warm_state=)) is asked first: while its warm is running, no card is read and nobody is told (a
    healthy app landing takes longer than the limit, and three letters in 70 minutes on 11/15 and 11/16/26 were all on
    warms that had passed or were passing); once it is not, the limit decides as before, and the warm's last word
    goes in the letter. A record that cannot be read is no record: the limit alone decides."""
    warm = (cfg.get('lights') or {}).get('dock') or ''
    if not warm:
        return []
    alive, note = False, ''
    if _SET['warm_state'] is not None:
        try:
            alive, note = _SET['warm_state'](store)
        except Exception:   # noqa: BLE001 - an unread record never silences the sweep
            alive, note = False, ''
    if alive:
        return [f'scrum: the dock is warming now ({note or "its warm is running"}); no {warm} card is asked about']
    # the option the board shows for it when it lacks the Light's own (light_fallback), as rank() reads it
    lit = {warm, (cfg.get('light_fallback') or {}).get(warm) or warm}
    limit = int(cfg.get('warming_minutes') or WARMING_MINUTES)   # the board config's, else Gene's 3
    col = _tickets.stage('dock', cfg)
    cards = _tickets.board_items(_tickets.filter_columns([col]) + ' is:issue', gh.run, cfg)
    if cards is None:
        return [f'scrum: the {col} column did not answer; Warming cards are read next sweep']
    lines = []
    for c in cards:
        n = (c.get('content') or {}).get('number')
        if not n or _tickets.is_draft(c) or _tickets.is_placeholder(c.get('title'), cfg) or (c.get('light') or '') not in lit:
            continue
        at = c.get('light_at') or ''
        try:
            since = datetime.fromisoformat(at.replace('Z', '+00:00'))
        except ValueError:
            continue   # no time read: never a guess at its age
        minutes = int((now - since).total_seconds() // 60)
        if minutes < limit:
            continue
        state = store.get(_SET['state'], str(n)) or {}
        if state.get('warming_told') == at:
            continue
        to = c.get('owner') or captain()
        to = captain() if to == lead() else to
        letters.setdefault(to, []).append((n, c.get('title') or '', minutes, (c.get('content') or {}).get('url') or '', note))
        state['warming_told'] = at
        if not dry:
            store.set(_SET['state'], str(n), state)
        lines.append(f'scrum: #{n} has been {warm} {minutes} minutes in {col}: {to} told to investigate')
    return lines


def warming_letter(owner: str, items) -> tuple:
    """(subject, text) of one owner's warming letter: every Dock card of theirs Warming past the limit, each item
    (number, title, minutes, url) and, since 0.29.0, the host's warm's last word as an optional fifth, said once."""
    subject = f"Scrum master: {len(items)} Dock card{'s' if len(items) != 1 else ''} stuck Warming"
    lines = [f"{owner}, these Dock cards are still Warming past the board's limit. Look at the warm's own log and stage "
             "line, its dev Deploy run and the board move's line, then put each right (Ready, or the stage that failed):"]
    notes = []
    for item in items:
        n, title, minutes, url = item[:4]
        lines.append(f'- #{n} {title}: Warming {minutes} minutes\n  {url}')
        if len(item) > 4 and item[4] and item[4] not in notes:
            notes.append(item[4])
    lines += [f"No warm is running now. The warm's last word: {note}" for note in notes]
    return subject, '\n'.join(lines)


def wrecked_question(n, col: str, hours: int) -> str:
    """What the owner of a card left in the failed column is asked (0.20.0): re-ship it or drop it, each by the act
    that moves it (the host's start command, else a move to the shipyard; a close as not planned archives it)."""
    command = (_SET['start_command'] or '').format(n=n)
    pick = f'`{command}`' if command else 'move it to the shipyard'
    return (f'#{n} failed its release and has sat in {col} {hours} hours. Re-ship it ({pick}, then fix and dock it as '
            'usual), or drop it (close it as not planned, which archives its card).')


def wrecked_sweep(cfg: dict, gh, store, now: datetime, letters: dict, dry: bool = False) -> list:
    """The failed column kept for what it is for (0.20.0; SMADPickleBot #1337): a closed card there whose close was as
    not planned or a duplicate is archived off the board, and an open card that has sat there WRECKED_HOURS (its
    `status_at`, the board's updatedAt of its Status) goes into its owner's wrecked letter (the captain's for none or
    the lead), again each WRECKED_HOURS after (the card's scrum record keeps `wrecked_asked_at`; a card that leaves the
    column and fails again starts the clock over, since its `status_at` moves). Two reads of the column with GitHub's
    own filter, open and closed. Lines."""
    failed = _tickets.stage('failed', cfg)
    if not failed:
        return []
    col = _tickets.filter_columns([failed]) + ' is:issue'
    lines = []
    closed = _tickets.board_items(col + ' is:closed', gh.run, cfg)
    if closed is None:
        lines.append(f'scrum: the {failed} column did not answer (closed); next sweep')
        closed = []
    closed = [c for c in closed if not _tickets.is_draft(c) and not _tickets.is_placeholder(c.get('title'), cfg)]
    gh.prefetch([(c.get('content') or {}).get('number') for c in closed])
    for c in closed:
        n = (c.get('content') or {}).get('number')
        issue = gh.card(n) if n else None
        if not issue or issue.get('state') != 'CLOSED' or not unplanned(str(issue.get('stateReason') or '').lower()):
            continue
        ok = gh.archive(n)
        lines.append(f'scrum: #{n} in {failed} was closed as not planned: '
                     + ('archived off the board' if ok else 'the archive was refused; next sweep tries again'))
    cards = _tickets.board_items(col + ' is:open', gh.run, cfg)
    if cards is None:
        return lines + [f'scrum: the {failed} column did not answer (open); next sweep']
    for c in cards:
        n = (c.get('content') or {}).get('number')
        if not n or _tickets.is_draft(c) or _tickets.is_placeholder(c.get('title'), cfg):
            continue
        try:
            since = datetime.fromisoformat((c.get('status_at') or '').replace('Z', '+00:00'))
        except ValueError:
            continue   # no time read: never a guess at its age
        hours = int((now - since).total_seconds() // 3600)
        if hours < WRECKED_HOURS:
            continue
        state = store.get(_SET['state'], str(n)) or {}
        asked = state.get('wrecked_asked_at')
        if asked and asked >= since.isoformat() and (now - datetime.fromisoformat(asked)).total_seconds() < WRECKED_HOURS * 3600:
            continue
        to = c.get('owner') or captain()
        to = captain() if to == lead() else to
        letters.setdefault(to, []).append((n, c.get('title') or '', wrecked_question(n, failed, hours),
                                           (c.get('content') or {}).get('url') or ''))
        state['wrecked_asked_at'] = now.isoformat()
        if not dry:
            store.set(_SET['state'], str(n), state)
        lines.append(f'scrum: #{n} has sat in {failed} {hours} hours: {to} asked to re-ship or drop it')
    return lines


UNPLACED_FILTER = 'no:status is:issue'


def unplaced_sweep(cfg: dict, gh, letters: dict, dry: bool = False) -> list:
    """The safety net for a card with no column (0.38.0; SMADPickleBot #1918: two tickets sat on the board with no
    Status for 16 hours, in no column's read and so in no "what's next", after a filing whose one field write was
    refused whole). One read with GitHub's own `no:status` filter; each issue's card is given the backlog column (a
    closed one too: the closed sweep takes it from there by its own rule) and goes into the captain's one letter,
    with the fields it still lacks. Drafts and the fixed cards are left alone. Lines."""
    backlog = _tickets.stage('backlog', cfg)
    if not backlog:
        return []
    cards = _tickets.board_items(UNPLACED_FILTER, gh.run, cfg)
    if cards is None:
        return ['scrum: the cards with no column did not answer; next sweep']
    lines = []
    for c in cards:
        n = (c.get('content') or {}).get('number')
        if not n or c.get('status') or _tickets.is_draft(c) or _tickets.is_placeholder(c.get('title'), cfg):
            continue
        url, repo = (c.get('content') or {}).get('url') or '', cfg.get('repo') or ''
        if url and repo and f'/{repo.lower()}/issues/' not in url.lower():
            # 0.66.2 (SMADPickleBot #1654): an issue of another repository on this board (GitHub's auto-add of a
            # sub-issue whose parent is here, after the ticket moved away) is not this board's to place; a write by
            # its number would act on this repository's issue of that number. Said, never touched.
            lines.append(f'scrum: a card of another repository has no column ({url}); left alone')
            continue
        gh.remember([c])   # the card's item is in the listing: the write needs no read of its own
        ok = gh.field(n, 'Status', backlog)
        lacks = [name for key, name in (('owner', 'Owner'), ('priority', 'Priority'), ('area', 'Area')) if not c.get(key)]
        if ok:
            letters.setdefault(captain(), []).append((n, c.get('title') or '', lacks,
                                                      (c.get('content') or {}).get('url') or ''))
        lines.append(f'scrum: #{n} had no column: ' + (f'put in {backlog}' if ok else f'the move to {backlog} was refused; '
                                                       'next sweep tries again')
                     + (f"; it still has no {', '.join(lacks)}" if lacks else ''))
    return lines


def unplaced_letter(owner: str, items) -> tuple:
    """(subject, text) of the captain's letter for the cards the sweep found with no column and put in the backlog."""
    subject = f"Scrum master: {len(items)} card{'s' if len(items) != 1 else ''} had no column"
    lines = [f'{owner}, each of these was on the board with no column, so no read of the board showed it. '
             f'Each is in the backlog now; give it what it still lacks:']
    for n, title, lacks, url in items:
        lines.append(f"- #{n} {title}: " + (f"no {', '.join(lacks)}" if lacks else 'its other fields are set') + f'\n  {url}')
    return subject, '\n'.join(lines)


def wrecked_letter(owner: str, items) -> tuple:
    """(subject, text) of one owner's wrecked letter: every card of theirs left in the failed column past the limit."""
    subject = f"Scrum master: {len(items)} card{'s' if len(items) != 1 else ''} left in the failed column"
    lines = [f'{owner}, each of these failed its release and nothing has moved it since. Re-ship or drop each one:']
    for n, title, q, url in items:
        lines.append(f'- #{n} {title}: {q}\n  {url}')
    return subject, '\n'.join(lines)


def _send(letters: dict, mail, dry: bool, compose=None) -> list:
    """Each owner's one letter of the sweep's questions (or `compose`'s, the warming letter); the lines."""
    out = []
    send = mail or _SET['mail']
    for to, items in letters.items():
        subject, text = (compose or letter)(to, items)
        if dry:
            said = 'not mailed (dry run)'
        elif send is None:
            said = 'not mailed (no letter wired: scrum.configure(mail=))'
        else:
            said = send(to, text, subject, sender=SENDER)
        out.append(f'scrum: {to}: {said}')
    return out
