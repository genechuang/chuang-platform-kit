"""A vendor's open findings mirrored as GitHub issues: a Sentry issue or a GitGuardian incident filed once, by the
marker its body carries, and closed once the vendor shows it resolved or ignored.

Both mirrors are the same shape. The vendor's open items are listed, and so is each marked open issue's own item (to
learn whether it has closed since). `plan` puts each open item no issue carries the marker of on the file list, and
each open issue whose item is known and no longer open on the close list. A closed issue counts as handled, so a
reopened item is reopened by hand, never filed twice. The sweep files and closes through the host's issue client
(`chuang_sdlc.alerts.GitHub` or one with its `issues`, `file` and `close`).

The host keeps every word a person reads, its organization and repositories, its resolve command, and the seams its
suites patch: this module takes them as arguments at each call, so a host's module-level name replaced by a suite is
the one used. Nothing here names a project.

Never in an issue: a GitGuardian occurrence's flagged string, its committer or the secret's hash. A Sentry exception's
value and a breadcrumb's text are cut short. Every body goes through the kit's `redact`.
"""
import re
import urllib.error
import urllib.parse

from chuang_platform_kit.redact import redact

_SESSION = re.compile(r'^Session:\s*(.+?)\s*$', re.MULTILINE)
_SHA = re.compile(r'^[0-9a-f]{7,40}$')


# -- shared ------------------------------------------------------------------------------------------------------------

def marker(tag: str, item_id) -> str:
    """The body line that ties an issue to its vendor item, `<!-- <tag>: <id> -->` (never the title, which a person or
    the investigator may retitle)."""
    return f'<!-- {tag}: {item_id} -->'


def marked_id(tag: str, body):
    """The vendor item id an issue body carries under `tag`, or None."""
    m = re.search(r'<!-- ' + re.escape(tag) + r': (\d+) -->', body or '')
    return int(m.group(1)) if m else None


def plan(items, gh_issues, *, tag: str, item_id, is_open):
    """(to_file, to_close). to_file: each open item (`is_open(item)`) no issue, open or closed, carries the marker of.
    to_close: (issue, the item's status) for each OPEN marked issue whose item is known and no longer open. `item_id`
    gives an item's id as the marker holds it (an int); a pull request is never read as an issue."""
    by_id = {}
    for i in items:
        n = item_id(i)
        if n is not None:
            by_id[n] = i
    marked = {}
    for iss in gh_issues:
        if iss.get('pull_request') is not None:
            continue
        n = marked_id(tag, iss.get('body'))
        if n is not None:
            marked.setdefault(n, []).append(iss)
    to_file = [i for i in items if is_open(i) and item_id(i) not in marked]
    to_close = []
    for n, issues_for in marked.items():
        item = by_id.get(n)
        if not item or is_open(item):
            continue
        for iss in issues_for:
            if iss.get('state') == 'open':
                to_close.append((iss, item.get('status')))
    return to_file, to_close


def commit_session(repo, sha, get) -> str:
    """The `Session:` trailer of a commit (who pushed it), read with `get('/repos/<repo>/commits/<sha>')`; '' when the
    commit has none or cannot be read (no token, no such commit, GitHub down: not known)."""
    if not sha or not repo:
        return ''
    try:
        got = get(f'/repos/{repo}/commits/{sha}')
        message = ((got or {}).get('commit') or {}).get('message') or ''
    except Exception:   # noqa: BLE001
        return ''
    m = _SESSION.search(message)
    return m.group(1) if m else ''


def close_all(gh, to_close, *, vendor: str, noun: str, dry: bool, out=print) -> None:
    """Close each (issue, status) with the host's one comment, `Closed by the Workflow Watchdog: the <noun> is <status>
    in <vendor>.`; a dry run prints the plan alone."""
    for iss, status in to_close:
        out(f"  CLOSE  #{iss['number']} ({status} in {vendor})")
        if not dry:
            gh.close(iss['number'], comment=f'Closed by the Workflow Watchdog: the {noun} is {status} in {vendor}.')


# -- Sentry ------------------------------------------------------------------------------------------------------------

SENTRY_OPEN = ('unresolved',)


def sentry_is_open(issue, states=SENTRY_OPEN) -> bool:
    """Whether Sentry still shows the issue as unresolved."""
    return (issue.get('status') or '').lower() in states


def sentry_id(issue):
    """A Sentry issue's numeric id, or None."""
    s = str(issue.get('id') or '')
    return int(s) if s.isdigit() else None


def sentry_title(issue, prefix: str) -> str:
    """`<prefix><short id>: <title, cut to fit an email subject>`."""
    title = ' '.join((issue.get('title') or 'error').split())
    if len(title) > 90:
        title = title[:87].rstrip() + '...'
    return f"{prefix}{issue.get('shortId') or issue.get('id')}: {title}"


def event_tags(event) -> dict:
    """An event's tags as a dict."""
    return {t.get('key'): t.get('value') for t in (event.get('tags') or []) if t.get('key')}


def event_exception(event, max_value: int = 300, max_frames: int = 6):
    """(type, value, frames) of the last exception in the event, the value cut to `max_value`, the innermost
    `max_frames` frames as `file:line in function`; the event's message when it has no exception."""
    for entry in event.get('entries') or []:
        if entry.get('type') != 'exception':
            continue
        values = ((entry.get('data') or {}).get('values') or [])
        if not values:
            continue
        v = values[-1]
        frames = ((v.get('stacktrace') or {}).get('frames') or [])[-max_frames:]
        return (v.get('type') or '', (v.get('value') or '')[:max_value],
                [f"{f.get('filename') or '?'}:{f.get('lineNo') or f.get('lineno') or '?'} in {f.get('function') or '?'}"
                 for f in frames])   # the API's lineNo; lineno is the SDK's spelling
    return ('', (event.get('message') or '')[:max_value], [])


def event_crumbs(event, max_crumbs: int = 6) -> list:
    """The event's last `max_crumbs` breadcrumbs as `level: text`, each text cut to 160."""
    for entry in event.get('entries') or []:
        if entry.get('type') == 'breadcrumbs':
            values = ((entry.get('data') or {}).get('values') or [])[-max_crumbs:]
            return [f"{(c.get('level') or 'info')}: {(c.get('message') or c.get('category') or '')[:160]}" for c in values]
    return []


def event_release(event) -> str:
    """The event's release version, '' when it has none."""
    r = event.get('release')
    return (r.get('version') if isinstance(r, dict) else r) or ''


def release_commit(event, app_prefix: str = 'com.') -> str:
    """The commit an event's code came from: the release when it is a sha, else the sha after a `+` in a release that
    is not an app's (`20261014-2118+cf8b3c8`), else the `build` tag (an app's release is its version, and it tags the
    bundle's commit as `build`), else ''. A version string is never a commit."""
    release = event_release(event)
    if _SHA.match(release):
        return release
    tail = release.rsplit('+', 1)[-1] if '+' in release else ''
    if _SHA.match(tail) and not release.startswith(app_prefix):
        return tail
    build = (event_tags(event).get('build') or '').strip()
    return build if _SHA.match(build) else ''


BLAME_QUERY = ('query($owner:String!,$name:String!,$expr:String!,$path:String!){repository(owner:$owner,name:$name)'
               '{object(expression:$expr){... on Commit{blame(path:$path){ranges{startingLine endingLine '
               'commit{oid message author{name}}}}}}}}')


def line_change(repo, sha, path, line, graphql, session_of) -> dict:
    """The commit that last changed `path`:`line` as of `sha` ({sha, subject, who, url}, `who` its session by
    `session_of(message)`, else its author), from GitHub's blame through `graphql(query, variables)`; {} when any of it
    is unknown or GitHub refuses."""
    if not (repo and sha and path and line):
        return {}
    owner, _, name = repo.partition('/')
    try:
        got = graphql(BLAME_QUERY, {'owner': owner, 'name': name, 'expr': sha, 'path': path})
        ranges = got['repository']['object']['blame']['ranges']
    except Exception:   # noqa: BLE001 - no token, a refusal, no such file at that commit: not known
        return {}
    for r in ranges:
        if r['startingLine'] <= int(line) <= r['endingLine']:
            c = r['commit']
            message = c.get('message') or ''
            return {'sha': c['oid'], 'subject': message.split('\n', 1)[0][:120],
                    'who': session_of(message) or (c.get('author') or {}).get('name') or 'unknown',
                    'url': f"https://github.com/{repo}/commit/{c['oid']}"}
    return {}


def sentry_body(issue, event, *, repo: str, session, cause: list, footer: str, tag: str, pt,
                max_value: int = 300, max_frames: int = 6, max_crumbs: int = 6) -> str:
    """What the investigator needs to find and judge the failure: the Sentry link, counts and times (`pt(stamp)`, the
    host's reading), the release as a commit link, who raised it (`session`, the release commit's), what broke it
    (`cause`, the host's lines), the latest event's exception, stack head and breadcrumbs, the host's `footer` (how to
    resolve it) and the marker. Redacted."""
    event = event or {}
    tags = event_tags(event)
    exc_type, exc_value, frames = event_exception(event, max_value, max_frames)
    release = event_release(event)
    sha = release_commit(event)
    if sha:
        where = 'https://github.com/' + repo + '/commit/' + sha + ('' if sha == release else f' (release `{release}`)')
    else:
        where = f'`{release}` (not a commit)' if release else 'n/a'
    lines = [
        f"Sentry holds an unresolved issue in **{(issue.get('project') or {}).get('slug') or '?'}**: "
        f"{' '.join((issue.get('title') or '').split())[:200]}",
        '',
        f"- Sentry: {issue.get('permalink') or 'n/a'}",
        f"- Events: {issue.get('count') or '?'}; users: {issue.get('userCount') or 0}; level: {issue.get('level') or tags.get('level') or '?'}",
        f"- First seen: {pt(issue.get('firstSeen'))}; last seen: {pt(issue.get('lastSeen'))}",
        f"- Release: {where}",
        f"- Raised by: {session or ('not known (no Session trailer on the release commit)' if sha else 'not known (no commit for this release)')}",
        f"- Where: logger `{tags.get('logger') or '?'}`, function `{tags.get('function') or tags.get('transaction') or '?'}`, "
        f"service `{tags.get('service') or '?'}`, host `{tags.get('server_name') or '?'}`",
        '',
    ]
    if cause:
        lines += ['**What broke it**', ''] + [f'- {c}' for c in cause] + ['']
    if exc_type or exc_value:
        lines += [f"**{exc_type or 'Error'}**: {exc_value or '(no message)'}", '']
    if frames:
        lines += ['Stack (innermost last):', ''] + [f"- `{f}`" for f in frames] + ['']
    crumbs = event_crumbs(event, max_crumbs)
    if crumbs:
        lines += ['Breadcrumbs (last few):', ''] + [f"- {c}" for c in crumbs] + ['']
    lines += [footer, '', marker(tag, issue.get('id'))]
    return redact('\n'.join(lines))


def sentry_sweep(*, gh, issues, gh_issues, tag: str, latest_event, is_alarm, title, body, misses: int, dry: bool,
                 out=print) -> int:
    """File and close one pass: the plan over `issues` and `gh_issues`, each item to file read for its latest event
    (`latest_event(id)`: {} for none, None when it could not be read, and then nothing is filed for it), kept only when
    `is_alarm(issue, event)` says (alarm, why), filed as `title(issue)` / `body(issue, event)`; after `misses` failed
    reads the rest wait for the next pass. Returns 0."""
    to_file, to_close = plan(issues, gh_issues, tag=tag, item_id=sentry_id, is_open=sentry_is_open)
    out(f'Sentry: {sum(1 for i in issues if sentry_is_open(i))} unresolved issue(s), {len(to_file)} to file, '
        f'{len(to_close)} to close')
    missed = 0
    for s in to_file:
        t = title(s)
        if missed >= misses:
            out(f'  WAIT   {t}: Sentry is not answering ({missed} reads failed this run); asked again next hour')
            continue
        event = latest_event(s['id'])
        if event is None:
            missed += 1
            out(f'  WAIT   {t}: its latest event could not be read; asked again next hour')
            continue
        alarm, why = is_alarm(s, event)
        if not alarm:
            out(f'  SKIP   {t}: {why}')
            continue
        out(f'  FILE   {t}')
        if not dry:
            gh.file(t, body(s, event))
    close_all(gh, to_close, vendor='Sentry', noun='issue', dry=dry, out=out)
    return 0


# -- GitGuardian -------------------------------------------------------------------------------------------------------

GITGUARDIAN_OPEN = ('TRIGGERED', 'ASSIGNED')


def gg_is_open(incident, states=GITGUARDIAN_OPEN) -> bool:
    """Whether the incident still waits on someone: TRIGGERED or ASSIGNED."""
    return (incident.get('status') or '').upper() in states


def gg_title(incident, *, prefix: str, home: str, detector, files) -> str:
    """`<prefix><id>: <detector> in <first file> (+N more)`; the file is `<repo>/<file>` when the incident is in a repo
    other than `home` (`incident['_repo']`), so another repo's fixture is not read as a leak in this one."""
    found = files(incident)
    where = found[0] if found else 'an unknown file'
    origin = incident.get('_repo') or home
    if origin.lower() != home.lower() and found:
        where = f"{origin.split('/')[-1]}/{where}"
    if len(found) > 1:
        where += f' (+{len(found) - 1} more)'
    return f"{prefix}{incident['id']}: {detector(incident)} in {where}"


def in_head(path, *, repo: str, local: bool, isfile, get, not_found) -> str:
    """Whether `path` is on `repo`'s default branch now: 'yes', 'no', or 'not measured'. With `local` (a checkout of
    that repo) `isfile(path)` answers; otherwise `get('/repos/<repo>/contents/<path>')`, `not_found(e)` telling a 404
    from any other refusal. Anything that is not a clear answer is 'not measured', never 'no': an occurrence's own
    `presence` describes its commit, not today's tree."""
    if not path:
        return 'not measured'
    if local:
        return 'yes' if isfile(path) else 'no'
    try:
        get(f"/repos/{repo}/contents/{urllib.parse.quote(path, safe='/')}")
        return 'yes'
    except Exception as e:   # noqa: BLE001 - no token, no answer: unknown, never a false 'no'
        return 'no' if not_found(e) else 'not measured'


def gg_body(incident, *, origin: str, session: str, head, detector, tag: str, footer: str,
            max_occurrences: int = 10) -> str:
    """What the investigator needs to find and judge the finding, and nothing that repeats it: no match, no author, no
    hash. `head(path, repo)` is in_head's answer for each occurrence; `footer` the host's resolve line. Redacted."""
    occ = (incident.get('occurrences') or [])[:max_occurrences]
    lines = [
        f"GitGuardian flagged a **{detector(incident)}** in {origin}.",
        '',
        f"- Dashboard: {incident.get('gitguardian_url') or 'n/a'}",
        f"- Status: {incident.get('status')}; severity: {incident.get('severity') or 'unknown'}; "
        f"validity: {incident.get('validity') or 'unknown'}",
        f"- First seen: {incident.get('date') or 'unknown'}",
        f"- Occurrences: {incident.get('occurrences_count', len(incident.get('occurrences') or []))}",
        f"- Raised by: {session or 'not known (no Session trailer on the flagged commit)'}",
        '',
    ]
    if occ:
        lines.append('| File | Commit | Committed | File in HEAD | Line |')
        lines.append('|---|---|---|---|---|')
        for o in occ:
            sha = (o.get('sha') or '')[:7]
            lines.append(f"| `{o.get('filepath') or '?'}` | {sha or '?'} | {o.get('date') or '?'} "
                         f"| {head(o.get('filepath'), origin)} | {o.get('url') or 'n/a'} |")
        lines.append('')
    lines += [
        'The flagged string is deliberately not reproduced here. Read it at the '
        'commit and line above; never quote it in a comment or a note.',
        '',
        footer,
        '',
        marker(tag, incident['id']),
    ]
    return redact('\n'.join(lines))


class VendorDown(Exception):
    """The vendor itself did not answer a listing (a 5xx, a network error or a timeout): its outage, not the host's, so
    a pass files and closes nothing on a partial read."""


def vendor_down(e) -> bool:
    """A 5xx answer, or no answer at all; a 4xx (a key without its scope) is the host's, never this."""
    if isinstance(e, urllib.error.HTTPError):
        return e.code >= 500
    return isinstance(e, (urllib.error.URLError, TimeoutError, ConnectionError))


def gg_fetch(repos, ids, *, list_open, read_one, down=VendorDown, out=print) -> list:
    """The open incidents of each repo (`list_open(repo)`), each tagged with its repo in `_repo`, plus each id in `ids`
    (`read_one(id)`, a marked open issue's incident). A repo the vendor does not watch (its RuntimeError) is a notice
    and skipped; the vendor not answering raises `down` so the caller files nothing on a partial read; a single read
    that fails is said and left out."""
    found = {}
    for one in ([repos] if isinstance(repos, str) else list(repos)):
        try:
            got = list_open(one)
        except RuntimeError as e:
            out(f'::notice::{e}; skipped')
            continue
        except Exception as e:
            if vendor_down(e):
                raise down(f'{one}: {redact(str(e))}') from e
            raise
        for i in got:
            i['_repo'] = one
            found[i['id']] = i
    for n in ids:
        if n not in found:
            try:
                found[n] = read_one(n)
            except Exception as e:   # noqa: BLE001
                out(f'could not read incident {n}: {redact(str(e))}')
    return list(found.values())


def gg_sweep(*, gh, incidents, gh_issues, tag: str, attach, title, body, refiled, scanned: str, dry: bool,
             out=print) -> int:
    """File and close one pass: the plan over `incidents` and `gh_issues`, each incident to file given its occurrences
    (`attach(incident)`) and filed as `title` / `body` unless `refiled(id)` says another run has filed it since the
    issues were read (a webhook's run and the hourly sweep can overlap). Returns 0."""
    to_file, to_close = plan(incidents, gh_issues, tag=tag, item_id=lambda i: i.get('id'), is_open=gg_is_open)
    to_file = [attach(i) for i in to_file]
    out(f'GitGuardian: {sum(1 for i in incidents if gg_is_open(i))} open incident(s) in {scanned}, '
        f'{len(to_file)} to file, {len(to_close)} to close')
    for inc in to_file:
        t = title(inc)
        if not dry and refiled(inc['id']):
            out(f'  SKIP   {t} (another run filed it a moment ago)')
            continue
        out(f'  FILE   {t}')
        if not dry:
            gh.file(t, body(inc))
    close_all(gh, to_close, vendor='GitGuardian', noun='incident', dry=dry, out=out)
    return 0
