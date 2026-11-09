"""An alert's incident as a GitHub issue, filed once per opening and closed once the alert watcher says it is fixed, and
the create-only claim a triage pass takes on an issue so two passes never investigate it at once.

An incident is the host's alert watcher's record of one failing thing: {open, held, first_at, last_at, closed_at,
count, kind, label, subject, body, lines, failed_steps, execution, link}. Each one the watcher tells (open, not held,
with a first failure) is filed as an issue whose body ends in a hidden marker line naming the incident's key and its
first failure (`marker`), so the issue list tells which openings have an issue; a claim per opening (`claim_key`,
taken through the host's `claim(id)` or `claim_opening` on its store) makes the file once-only whatever GitHub's
search lags. An open marked issue is closed once its incident has closed, opened again since, or is gone (`plan`),
except an issue of a kind that waits for a person (`person_closes`), and except an issue its triage made a bug ticket
(the bug label): that one is told once that its alert cleared (`plan_cleared`) and stays open for its fix.

    configure(repo='owner/name', title_prefix='Alert: ', marker_tag='alert', collections={'claims': ..., 'triage': ...})
    lines = act(key, incident, GitHub(token), claim)

The words (the title prefix, the marker's tag, the closers' marks, the body's opening, the notes) and the repository
and collections are configure()'s; the zone a stamp is written in is the kit's clock. Standard library and the kit's
github, redact and clock only.
"""
import re
import urllib.parse
from datetime import datetime

from . import signature as _signature
from . import tickets as _tickets

_SET = {
    'repo': '',             # 'owner/name', or a callable answering it; '' for the board's own `repo`
    'title_prefix': 'Alert: ',      # every alert issue's title opens with it (the issue search reads it too)
    'max_title': 120,
    'marker_tag': 'alert',  # the hidden marker line: <!-- <tag>: <key> @ <first failure> -->
    'subject_head': '',     # a regex for a head the watcher's subject carries that the title drops ('' none)
    'closer_marks': ('Closed by the alert watcher:',),   # how an alert closer's closing comment opens
    'alert_titles': (),     # the title prefixes of every alert issue (this module's own is always among them)
    'person_closes': {},    # {incident kind: why}: kinds whose issue the watcher never closes; they wait for a person
    'bug_label': 'bug',     # the label a triaged alert issue (a bug ticket in place) carries
    'cleared_label': 'alert-cleared',   # marks a triaged issue told its alert cleared, so it is told once
    'intro': 'The alert watcher filed this the moment it told the incident, so its investigator hears at once.',
    'closes_itself': ('Closes itself when the watcher says "Fixed", unless its Triage line has made it a bug ticket: '
                      'then it stays open for its fix.'),
    'close_word': 'Closed by the alert watcher:',   # an issue the watcher closes: `<close_word> <why>.`
    'cleared_note': ('The alert has cleared ({why}), and this ticket stays open for its fix: its Triage line made it a '
                     'bug ticket, which the release that ships the fix closes.'),
    'collections': {},      # {'claims': an opening's claims, 'triage': the triage passes' claims}
    'claim_ttl_days': 30,   # an opening's claim expires after this many days (the collection's TTL reads expires_at)
    'filed': None,          # (incident) -> whether to file it; None for is_filed's own rule (a host whose watcher
                            # shares the rule without this package passes its one copy)
}


def configure(**settings) -> None:
    """The host's settings (the module's _SET says what each is); `collections` is merged over what is set. Unknown
    names are refused."""
    for name, value in settings.items():
        if name not in _SET:
            raise TypeError(f'alerts.configure() takes no {name!r}')
        _SET[name] = dict(_SET[name], **value) if name == 'collections' else value


def setting(name: str):
    """One of the host's settings (configure)."""
    return _SET[name]


def repo() -> str:
    """The repository alert issues are filed in: configure(repo=) (a name, or a callable answering one), else the
    board's `repo`."""
    got = _SET['repo']() if callable(_SET['repo']) else _SET['repo']
    return got or _tickets._board()['repo']


def collection(role: str) -> str:
    """The record collection of a role (`claims`, `triage`); NotConfigured when the host named none."""
    value = (_SET['collections'] or {}).get(role, '')
    if not value:
        raise _tickets.NotConfigured(f'no {role} collection: alerts.configure(collections={{{role!r}: ...}})')
    return value


def _marker_re():
    return re.compile(r'<!-- ' + re.escape(_SET['marker_tag']) + r': (\S+) @ (\S+) -->')


def _labels(iss: dict) -> set:
    return {(lb.get('name') if isinstance(lb, dict) else str(lb)) for lb in iss.get('labels') or []}


def triaged(iss: dict) -> bool:
    """Whether the alert issue has become a bug ticket: its Triage line swept, the bug label on it."""
    return _SET['bug_label'] in _labels(iss)


def closed_by_closer(title: str, comment: str) -> bool:
    """Whether an issue was closed by an alert closer: its newest comment opens with a closer's mark, or its title is
    an alert issue's, as the closers file and close them."""
    said = (comment or '').lstrip()
    titles = (_SET['title_prefix'],) + tuple(_SET['alert_titles'] or ())
    return any(said.startswith(m) for m in _SET['closer_marks']) or (title or '').startswith(titles)


def marker(key, first_at):
    """The hidden line an issue's body carries: its incident's key and first failure."""
    return f"<!-- {_SET['marker_tag']}: {key} @ {first_at} -->"


def marked(body):
    """(key, first_at) of the incident an issue body was filed for, or None."""
    m = _marker_re().search(body or '')
    return (m.group(1), m.group(2)) if m else None


def is_filed(inc: dict) -> bool:
    """An incident to file (configure(filed=), the host's own rule, when it gave one): open, told (not held for a
    schedule's retry) and with a first failure to key it on."""
    if _SET['filed'] is not None:
        return bool(_SET['filed'](inc))
    return bool(inc.get('open')) and not inc.get('held') and bool(inc.get('first_at'))


def pt(stamp) -> str:
    """An incident's ISO instant as `Fri 10/23/26 1:30 AM PT` in the kit's zone; the text itself when it cannot be
    read."""
    from chuang_platform_kit import clock
    try:
        dt = datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return str(stamp)
    return clock.human_time(dt, weekday=True, tz_word=True)


def issue_title(key: str, inc: dict) -> str:
    """The watcher's own subject (its `subject_head` dropped) after the title prefix; a plain one for an incident told
    before subjects were kept; cut to `max_title`."""
    said = str(inc.get('subject') or '')
    if _SET['subject_head']:
        said = re.sub(_SET['subject_head'], '', said)
    said = said.strip()
    if not said:
        said = f"{inc.get('label') or key} ({inc.get('kind') or 'alert'}) failing since {pt(inc.get('first_at'))}"
    title = _SET['title_prefix'] + said
    top = int(_SET['max_title'])
    return title if len(title) <= top else title[:top - 1].rstrip() + '…'


def issue_body(key: str, inc: dict) -> str:
    """The watcher's message as it was told, then the incident's facts and the marker; every line redacted."""
    from chuang_platform_kit.redact import redact
    lines = [_SET['intro'], '']
    body = str(inc.get('body') or '').strip()
    if body:
        lines += [body, '']
    else:
        lines += [f"What broke: {inc.get('label') or key}", ''] + [f" - {line}" for line in inc.get('lines') or []] + ['']
    lines.append(f"- **Incident:** `{key}` ({inc.get('kind') or '?'})")
    lines.append(f"- **First failure:** {pt(inc.get('first_at'))}; latest {pt(inc.get('last_at'))}; "
                 f"{inc.get('count', 1)} in all")
    if inc.get('failed_steps'):
        lines.append(f"- **Failed steps:** {', '.join(inc['failed_steps'])}")
    if inc.get('execution'):
        lines.append(f"- **Execution:** `{inc['execution']}`")
    if inc.get('link'):
        lines.append(f"- **Log:** {inc['link']}")
    closes = (_SET['person_closes'] or {}).get(inc.get('kind'), _SET['closes_itself'])
    lines += ['', closes, '', marker(key, inc['first_at'])]
    return redact('\n'.join(lines))


def _waits_for_person(inc) -> bool:
    return inc is not None and inc.get('kind') in (_SET['person_closes'] or {})


def cleared_why(inc, first: str) -> str:
    """Why an open issue's alert is over, or '' while it still holds: the incident gone, Fixed, or failed again since."""
    if inc is None:
        return 'the alert watcher no longer holds this incident'
    if not inc.get('open'):
        return (f"the alert watcher said Fixed at {pt(inc.get('closed_at'))}" if inc.get('closed_at')
                else 'the alert watcher closed it')
    if str(inc.get('first_at')) != first:
        return f"it was fixed and failed again at {pt(inc.get('first_at'))}, filed as a new issue"
    return ''


def plan(incidents: dict, gh_issues: list):
    """(to_file, to_close). to_file: (key, incident) for each one to file that no GitHub issue, open or closed, carries
    the marker of. to_close: (gh_issue, why) for each OPEN marked issue whose incident has closed, opened again since,
    or is gone; never one of a kind that waits for a person (`person_closes`), nor a triaged bug ticket (plan_cleared
    tells it). Every incident must be in `incidents`: an issue whose key is missing is closed (a sweep reads them all;
    one incident's listener passes only its own issues, `for_key`)."""
    seen = set()
    to_close = []
    for iss in gh_issues:
        if iss.get('pull_request') is not None:
            continue
        m = marked(iss.get('body'))
        if not m:
            continue
        seen.add(m)
        if iss.get('state') != 'open':
            continue
        key, first = m
        inc = incidents.get(key)
        if _waits_for_person(inc):
            continue
        why = cleared_why(inc, first)
        if why and not triaged(iss):
            to_close.append((iss, why))
    to_file = [(k, inc) for k, inc in sorted(incidents.items()) if is_filed(inc) and (k, str(inc['first_at'])) not in seen]
    return to_file, to_close


def plan_cleared(incidents: dict, gh_issues: list) -> list:
    """(gh_issue, note) for each OPEN marked issue that is a bug ticket (`triaged`) whose alert is over and that has not
    been told yet (no cleared label): the note says the alert cleared and the ticket stays open for its fix. A kind that
    waits for a person waits as before."""
    out = []
    for iss in gh_issues:
        if (iss.get('pull_request') is not None or iss.get('state') != 'open' or not triaged(iss)
                or _SET['cleared_label'] in _labels(iss)):
            continue
        m = marked(iss.get('body'))
        if not m:
            continue
        key, first = m
        inc = incidents.get(key)
        if _waits_for_person(inc):
            continue
        why = cleared_why(inc, first)
        if why:
            out.append((iss, _SET['cleared_note'].format(why=why)))
    return out


def for_key(key: str, gh_issues: list) -> list:
    """The issues one incident's plan may touch: those marked with its key (the rest are other incidents')."""
    return [i for i in gh_issues if (marked(i.get('body')) or ('',))[0] == key]


def claim_key(key: str, inc: dict) -> str:
    """The once-only id of an incident's opening."""
    return 'alert-' + re.sub(r'[^A-Za-z0-9_.-]', '-', f"{key}-{inc.get('first_at')}")[:300]


def opening_claimed(store, key: str, inc: dict) -> bool:
    """Whether an incident's opening is claimed already in the host's claims collection (a listener told the same open
    incident again reads this before it reads GitHub)."""
    return bool(store.get(collection('claims'), claim_key(key, inc)))


def claim_opening(store, key: str, cid: str, now) -> bool:
    """An opening's once-only claim, `cid` in the claims collection, created unless held: True when this call took it,
    False when it was already held. It expires `claim_ttl_days` after `now` (a datetime). A write the store refuses
    raises, for the caller to say."""
    from datetime import timedelta
    return store.create(collection('claims'), cid,
                        {'key': key, 'at': now, 'expires_at': now + timedelta(days=int(_SET['claim_ttl_days']))})


class GitHub:
    """The issue reads and writes `act` needs, and a host's alert sweeps', through chuang_platform_kit.github's
    throttled client with one token (the issue search draws on GitHub's search quota; the kit refuses a call locally
    once the quota is under its floor). `repo` the repository (configure(repo=)'s, else the board's, when None);
    `prefix` the title prefix `issues` finds, this module's unless a sweep names its own. A refusal is the kit's
    GitHubError (RateLimited for a spent quota), for the caller to acknowledge."""

    def __init__(self, token: str, repo: str = None, opener=None, prefix: str = None):
        self.token, self.repo, self.opener = token, repo or globals()['repo'](), opener
        self.prefix = _SET['title_prefix'] if prefix is None else prefix

    def _call(self, method: str, path: str, body=None):
        from chuang_platform_kit import github
        got = github.request(self.token, method, path, body, opener=self.opener, timeout=20)[1]
        return got if isinstance(got, dict) else {}

    def issues(self, pages: int = 1) -> list:
        """Every issue, open or closed, whose title starts with the prefix, newest first: 100 a page, at most `pages`
        pages."""
        q = urllib.parse.quote(f'repo:{self.repo} is:issue "{self.prefix.rstrip(": ")}" in:title')
        out = []
        for page in range(1, max(1, int(pages)) + 1):
            got = self._call('GET', f'/search/issues?q={q}&sort=created&order=desc&per_page=100'
                             + (f'&page={page}' if page > 1 else ''))
            items = got.get('items') or []
            out += [dict(i, state=i.get('state', '').lower()) for i in items]
            if len(items) < 100:
                break
        return out

    def file(self, title: str, body: str) -> int:
        """A new issue; its number (0 when GitHub answered none)."""
        return self._call('POST', f'/repos/{self.repo}/issues', {'title': title, 'body': body}).get('number') or 0

    def close(self, number: int, why: str = '', comment: str = '') -> None:
        """The issue closed with a comment: `comment` as given, else `<close_word> <why>.`."""
        self._call('POST', f'/repos/{self.repo}/issues/{number}/comments',
                   {'body': _signature.sign(comment or f"{_SET['close_word']} {why}.")})
        self._call('PATCH', f'/repos/{self.repo}/issues/{number}', {'state': 'closed', 'state_reason': 'completed'})

    def comment(self, number: int, text: str) -> None:
        """One comment on the issue (the note that its alert cleared while the ticket stays open), signed
        (chuang_sdlc.signature: the closer's mark stays its first words)."""
        self._call('POST', f'/repos/{self.repo}/issues/{number}/comments', {'body': _signature.sign(text)})

    def label(self, number: int, name: str) -> None:
        """One label added to the issue (the cleared label, so the note is written once)."""
        self._call('POST', f'/repos/{self.repo}/issues/{number}/labels', {'labels': [name]})


def act(key: str, inc: dict, gh, claim) -> list:
    """One incident's issue made right at once: filed (once, through `claim(id) -> bool`) while it is open and told,
    its open issue closed once it is Fixed or opened again, a triaged one told once that its alert cleared. Lines
    said."""
    mine = for_key(key, gh.issues())
    to_file, to_close = plan({key: inc}, mine)
    lines = []
    for iss, why in to_close:
        gh.close(iss['number'], why)
        lines.append(f"alert issues: #{iss['number']} closed ({why})")
    for iss, note in plan_cleared({key: inc}, mine):   # a triaged bug ticket is told once, never closed
        gh.comment(iss['number'], note)
        gh.label(iss['number'], _SET['cleared_label'])
        lines.append(f"alert issues: #{iss['number']} told its alert cleared; the ticket stays open for its fix")
    for k, i in to_file:
        if not claim(claim_key(k, i)):
            lines.append(f'alert issues: {k} already filed for this opening')
            continue
        n = gh.file(issue_title(k, i), issue_body(k, i))
        lines.append(f'alert issues: {k} filed as #{n}')
    return lines or [f'alert issues: {key}: nothing to file or close']


# ------------------------------------------------------------------ a triage pass's claim
def triage_claim(number: int, by: str, store, now) -> tuple:
    """(claimed, line) for one triage pass on an issue: a create-only record in the `triage` collection, so two passes
    never investigate it at once. True and `claimed #n` when this call created it; False and who holds it otherwise.
    `now` a callable answering epoch seconds."""
    if store.create(collection('triage'), str(number), {'by': by, 'at': int(now()), 'issue': number}):
        return True, f'claimed #{number}'
    held = store.get(collection('triage'), str(number)) or {}
    return False, f"#{number} is taken: {held.get('by') or 'another pass'} since {held.get('at') or '?'}"


def triage_release(number: int, store) -> str:
    """The triage claim dropped, so the next pass may take the issue."""
    store.delete(collection('triage'), str(number))
    return f'released #{number}'
