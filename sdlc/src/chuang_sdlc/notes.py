"""A ship's release notes: every ticket the shipped commits name, by its full title, grouped New (the readers' Area's
features), Fixed (its bugs) and Behind the scenes (every other Area, each by its own title, never a count), each ticket
once with its number; written as a Slack message (its text and its blocks), as the GitHub Release's body, as Markdown
for a reply and as the ship pull request's body; the Release itself, its tag, and the ship's change-order ticket.

    got = groups(commits)                                    {'new': [(n, title)], 'fixed': ..., 'behind': ..., ...}
    slack_text(head, when(at), got, url)                     the notification text; slack_blocks the laid-out post
    release(head, base, at, got, token)                      the GitHub Release at head, its URL

`got` may carry `change_order` (the ship's change-order ticket, change_order_number) and `post_ship` (the release's
run Post-Ship: steps, chuang_sdlc.post_ship), which each rendering names. The tickets are read through
chuang_sdlc.titles (its repository, Area and labels); the words, the sections and the zone are configure()'s. The
commits come from GitHub's compare (commits); reading them from a local git is the host's. Posting to Slack is the
host's (the channel and the token are its own). Standard library and the kit's github and clock only.
"""
from datetime import datetime, timezone

from . import post_ship as PS
from . import ship_pr as P
from . import tickets as T
from . import titles as TT

_SET = {
    'sections': (('new', 'New'), ('fixed', 'Fixed'), ('behind', 'Behind the scenes')),
    'emoji': {'new': '✨', 'fixed': '🔧', 'behind': '🛠️'},   # each section's, in the blocks
    'tz': None,             # the zone the go-live instant and the tag are written in; the kit's clock.zone() when None
    'tag_prefix': 'ship-',  # the Release's tag: <prefix>YYYYMMDD-HHMM-<sha8>
    'not_allowed': PS.NOT_ALLOWED,   # a Post-Ship: step off the host's allow-list, as its line says it
    'none': 'No ticket named by these commits.',
    'unread': 'Not read from GitHub: ',
    'retitle': "still carries the member's words, not the work that was built",
    'close_note': 'These tickets close when the production smoke passes, not when this merges.',
}
SLACK_TEXT_LIMIT = 3000   # a section block's text holds 3,000 characters


def configure(**settings) -> None:
    """The host's settings: `sections` (((key, name), ...)), `emoji` ({key: emoji}), `tz`, `tag_prefix`,
    `not_allowed`, and the words `none`, `unread`, `retitle`, `close_note`. Unknown names are refused."""
    for name, value in settings.items():
        if name not in _SET:
            raise TypeError(f'notes.configure() takes no {name!r}')
        _SET[name] = value


def setting(name: str):
    """One of the host's settings (configure)."""
    return _SET[name]


def groups(commits: list, mapped: dict = None, get=None) -> dict:
    """{'new': [(number, title)], 'fixed': [...], 'behind': [...], 'unknown': [numbers], 'member_titles': [numbers]}
    for the tickets `commits` name, each once, in the order its first commit came: the readers' Area's bugs under
    fixed, its other tickets under new, every other Area's under behind; a ticket still titled in a member's words
    (titles.member_label) also in member_titles. A ticket GitHub says does not exist is left out; one GitHub did not
    answer for is in 'unknown'. `get(number)` is the test seam for titles.fetch."""
    out = {'new': [], 'fixed': [], 'behind': [], 'unknown': [], 'member_titles': []}
    want, member, bug = TT.area(), TT.member_label(), TT.bug_label()
    for number, t in TT.read(commits, mapped, get):
        if t is None:
            out['unknown'].append(number)
            continue
        if t.get('missing') or not t.get('title'):
            continue
        if member and member in t['labels']:
            out['member_titles'].append(number)
        if want not in t['labels']:
            out['behind'].append((number, TT.title(t)))
        else:
            out['fixed' if bug in t['labels'] else 'new'].append((number, TT.title(t)))
    return out


def compare_url(base: str, head: str) -> str:
    """GitHub's compare page of the commits after `base` up to `head`."""
    return f'https://github.com/{TT.repo()}/compare/{base[:12]}...{head[:12]}'


def ship_time(seconds) -> str:
    """'85 s from ship to live', seconds alone as every stage time; '' when the release had no ship word to time it
    from (None, '', or not a number)."""
    try:
        return f'{int(seconds)} s from ship to live' if seconds not in (None, '') else ''
    except (TypeError, ValueError):
        return ''


def _unread(got: dict) -> str:
    return _SET['unread'] + ', '.join(f'#{n}' for n in got['unknown'])


def slack_text(sha: str, when: str, got: dict, release_url: str = '', ship_seconds=None) -> str:
    """The Slack message (mrkdwn): a head line with the short sha, the time it went live, the change order, the ship
    time and the GitHub Release's link, then each section's tickets, every title linked to its issue."""
    head = f"*Shipped {sha[:8]}* · live {when}"
    if got.get('change_order'):
        head += f" · <{TT.issue_url(got['change_order'])}|change order #{got['change_order']}>"
    if ship_time(ship_seconds):
        head += f" · {ship_time(ship_seconds)}"
    if release_url:
        head += f" · <{release_url}|release notes>"
    lines = [head]
    for key, name in _SET['sections']:
        if got.get(key):
            lines.append(f"\n*{name}*")
            lines += [f"• <{TT.issue_url(n)}|#{n}> {t}" for n, t in got[key]]
    if got.get('unknown'):
        lines.append(f'\n_{_unread(got)}_')
    if len(lines) == 1:
        lines.append(_SET['none'])
    return '\n'.join(lines)


def markdown_text(sha: str, got: dict) -> str:
    """The same notes as Markdown (a reply's, the ship pull request's): a head line, then each section's tickets, each
    `- [#N](its issue) title`, and the tickets still titled in a member's words named last, to retitle before they
    ship."""
    lines = [f"**Shipping {sha[:8]}**"]
    for key, name in _SET['sections']:
        if got.get(key):
            lines.append(f"\n**{name}**")
            lines += [f"- [#{n}]({TT.issue_url(n)}) {t}" for n, t in got[key]]
    if got.get('unknown'):
        lines.append(f'\n_{_unread(got)}_')
    if len(lines) == 1:
        lines.append(_SET['none'])
    if got.get('member_titles'):
        lines.append('\n**Retitle before it ships:** '
                     + ', '.join(f'[#{n}]({TT.issue_url(n)})' for n in got['member_titles']) + f" {_SET['retitle']}")
    return '\n'.join(lines)


def pr_body(base: str, head: str, commits: list, mapped: dict = None, get=None) -> str:
    """The ship pull request's body: markdown_text of the commits' groups, the compare link and `close_note`, through
    ship_pr.no_closing_keyword, so its merge closes no ticket. `mapped` and `get` are groups' seams."""
    text = markdown_text(head, groups(commits, mapped, get))
    text += f"\n\nCommits: {compare_url(base, head)}\n\n{_SET['close_note']}"
    return P.no_closing_keyword(text)


def slack_blocks(sha: str, when: str, got: dict, release_url: str = '', ship_seconds=None) -> list:
    """The same message in Slack's blocks: a header, one grey line with the go-live time, the ship time and the
    Release, a rule, then each section under its emoji, each row the linked ticket number right after its bullet and
    the title after it, so the links line up down the post; a long section goes on in another block (SLACK_TEXT_LIMIT);
    the unanswered tickets in grey, then the Post-Ship: steps with their results. slack_text stays the notification's
    text."""
    facts = [f'🟢 Live {when}']
    if ship_time(ship_seconds):
        facts.append(f'⏱️ {ship_time(ship_seconds)}')
    if release_url:
        facts.append(f'<{release_url}|📝 Release notes>')
    blocks = [{'type': 'header', 'text': {'type': 'plain_text', 'text': f'🚀 Shipped {sha[:8]}', 'emoji': True}},
              {'type': 'context', 'elements': [{'type': 'mrkdwn', 'text': '   ·   '.join(facts)}]},
              {'type': 'divider'}]
    for key, name in _SET['sections']:
        rows = [f'• <{TT.issue_url(n)}|#{n}>  {t}' for n, t in got.get(key) or []]
        emoji = (_SET['emoji'] or {}).get(key)
        chunk = f'*{emoji} {name}*' if emoji else f'*{name}*'
        for row in rows:
            if len(chunk) + len(row) + 1 > SLACK_TEXT_LIMIT:
                blocks.append({'type': 'section', 'text': {'type': 'mrkdwn', 'text': chunk}})
                chunk = ''
            chunk = f'{chunk}\n{row}' if chunk else row
        if rows:
            blocks.append({'type': 'section', 'text': {'type': 'mrkdwn', 'text': chunk}})
    if not any(got.get(k) for k, _ in _SET['sections']):
        blocks.append({'type': 'section', 'text': {'type': 'mrkdwn', 'text': _SET['none']}})
    if got.get('unknown'):
        blocks.append({'type': 'context', 'elements': [{'type': 'mrkdwn', 'text': _unread(got)}]})
    if got.get('post_ship'):
        blocks.append({'type': 'section', 'text': {'type': 'mrkdwn', 'text': (
            '*🧰 Post-ship steps*\n' + '\n'.join(PS.lines(got['post_ship'], False, _SET['not_allowed'])))[:SLACK_TEXT_LIMIT]}})
    return blocks


def release_body(sha: str, base: str, got: dict, ship_seconds=None, at: str = '') -> str:
    """The GitHub Release's body (markdown): the change order, the go-live instant and the ship time, the same
    sections, `#N` linking itself, the Post-Ship: steps, and the compare link."""
    first = ' · '.join(p for p in ((f'live {when(at)}' if at else ''), ship_time(ship_seconds)) if p)
    out = [f'**{first}**'] if first else []
    if got.get('change_order'):
        out.insert(0, f"**Change order #{got['change_order']}**")
    for key, name in _SET['sections']:
        if got.get(key):
            out.append(f'## {name}\n' + '\n'.join(f'- #{n} {t}' for n, t in got[key]))
    if got.get('unknown'):
        out.append(_unread(got))
    if got.get('post_ship'):
        out.append('## Post-ship steps\n' + '\n'.join(PS.lines(got['post_ship'], True, _SET['not_allowed'])))
    out.append(f'Commits: {compare_url(base, sha)}')
    return '\n\n'.join(out)


def _instant(at: str) -> datetime:
    t = datetime.fromisoformat(at.replace('Z', '+00:00')) if at else datetime.now(timezone.utc)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)   # utc-ok: an event's `at` is UTC


def _zone():
    if _SET['tz'] is not None:
        return _SET['tz']
    from chuang_platform_kit import clock
    return clock.zone()


def when(at: str = '') -> str:
    """The go-live instant in the host's zone with its year and seconds: '10/25/26 10:52:11 PM PT' for an ISO instant
    (now when empty; a zoneless one read as UTC)."""
    from chuang_platform_kit import clock
    t = _instant(at).astimezone(_zone())
    return t.strftime('%m/%d/%y %I:%M:%S %p ') + clock.tz_label(t, _zone())


def tag_for(head: str, at: str = '') -> str:
    """The Release's tag: <tag_prefix>YYYYMMDD-HHMM (in the host's zone)-<sha8>."""
    return f"{_SET['tag_prefix']}{_instant(at).astimezone(_zone()).strftime('%Y%m%d-%H%M')}-{head[:8]}"


def change_order_number(head: str, token: str, opener=None):
    """The number of the ship's change-order ticket (tickets.change_order files it at the push, its label
    tickets.label('change_order'), its title ending with the head's 8 characters), or None (the host labels none, no
    such ticket, or GitHub not answering)."""
    from chuang_platform_kit import github
    label = T.label('change_order', required=False)
    if not label:
        return None
    try:
        rows = github.issues(token, TT.repo(), 'all', [label], f'"{head[:8]}" in:title', 10, opener=opener) or []
    except Exception:   # noqa: BLE001 - the notes go out without it
        return None
    return next((r.get('number') for r in rows if str(r.get('title') or '').endswith(head[:8])), None)


def commits(base: str, head: str, token: str, opener=None) -> list:
    """[{'sha', 'message'}] after `base` up to `head`, oldest first, from GitHub's compare API (250 at most): the kit's
    github.compare. Its GitHubError (a RuntimeError) when GitHub does not answer."""
    from chuang_platform_kit import github
    return github.compare(token, TT.repo(), base, head, opener)


def release(head: str, base: str, at: str, got: dict, token: str, opener=None, ship_seconds=None) -> str:
    """The ship's GitHub Release, created at `head` with tag_for's tag, named `Ship <when> (<sha8>)`, its body
    release_body (an existing tag's Release reused, so a second run adds nothing): the kit's github.create_release.
    Its URL; GitHubError (a RuntimeError, GitHub's message in it) on a refusal."""
    from chuang_platform_kit import github
    return github.create_release(token, TT.repo(), tag_for(head, at), head, f'Ship {when(at)} ({head[:8]})',
                                 release_body(head, base, got, ship_seconds, at), opener=opener)
