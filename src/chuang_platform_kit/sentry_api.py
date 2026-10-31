"""Sentry's REST API, one client for every project (SMAD PickleBot #569, out of its picklebot_core/sentry_api.py of
#487: "3rd party integration should be a reusable" adapter, every read and write through one module on the stored
token, never Sentry's MCP).

`Client(org, projects, token)`: the issues API over the standard library: look an issue up by its short id or numeric
id, its newest event trimmed to what a triager reads, the unresolved issues of the projects, a search, a note, a
resolve with a note naming the fix, the paged issue list a sweep walks, and `run(action, args)`, the named actions a
script or a listener asks for by name. A refusal is `Refused`, a SystemExit whose line names the call, the status and
Sentry's own words (never the token), so a script that lets it go exits with its line and a listener catches it by
name. The token is the caller's (a user auth token with the scopes its calls need: event:read and event:write for the
issues, project:releases for releases); where it is stored and under which name is the host's business.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API = 'https://sentry.io/api/0'
TIMEOUT = 30


class Refused(SystemExit):
    """Sentry refused or did not answer: the line says the call, the status and Sentry's own words."""


def pt(stamp, zone: str = 'America/Los_Angeles') -> str:
    """Sentry's ISO stamp as a local clock reading, `mm/dd/yy h:mm AM ZONE`; '' when there is none."""
    if not stamp:
        return ''
    try:
        from zoneinfo import ZoneInfo
        d = datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)   # utc-ok: Sentry's stamps are UTC when they carry no zone
        local = d.astimezone(ZoneInfo(zone))
        return local.strftime('%m/%d/%y %I:%M %p %Z').replace(' 0', ' ').lstrip('0')
    except (ValueError, TypeError):
        return str(stamp)


def next_page(link: str) -> str:
    """The URL of the next page from Sentry's Link header, '' when results="false" or there is none."""
    for part in (link or '').split(','):
        if 'rel="next"' in part and 'results="true"' in part:
            m = re.search(r'<([^>]+)>', part)
            return m.group(1) if m else ''
    return ''


class Client:
    """The issues API for one organization: `projects` are the slugs `unresolved()` walks."""

    def __init__(self, org: str, projects=(), token: str = '', opener=None, timeout: int = TIMEOUT, zone: str = 'America/Los_Angeles'):
        self.org, self.projects, self.token = org, tuple(projects or ()), token
        self.opener, self.timeout, self.zone = opener or urllib.request.urlopen, timeout, zone

    def _tok(self, tok: str = '') -> str:
        tok = (tok or self.token or '').strip()
        if callable(tok):
            tok = tok()
        if not tok:
            raise Refused('no Sentry token: the host passes one to the client')
        return tok

    def call(self, method: str, path: str, body: dict = None, tok: str = '', full_url: str = '', headers: bool = False):
        """One API call; a refusal is Refused, naming the scope a 403 needs. `path` is under the API root, or `full_url`
        is a page link. With `headers`, (body, the Link header) comes back for paging."""
        data = json.dumps(body).encode() if body is not None else None
        url = full_url or f"{API}{path}"
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={'Authorization': f'Bearer {self._tok(tok)}', 'Content-Type': 'application/json',
                                              'Accept': 'application/json'})
        try:
            with self.opener(req, timeout=self.timeout) as r:
                text = r.read().decode()
                got = json.loads(text) if text else {}
                return (got, r.headers.get('Link', '')) if headers else got
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors='replace')[:200]
            if e.code == 403:
                raise Refused(f"Sentry refused {method} {path or full_url} (403): the token lacks the scope this call needs "
                              f"(event:read and event:write for issues); {detail}")
            raise Refused(f"Sentry {method} {path or full_url} failed ({e.code}): {detail}")
        except (OSError, ValueError) as e:
            raise Refused(f"Sentry {method} {path or full_url} did not answer ({type(e).__name__})")

    def pages(self, path: str, tok: str = '', limit_pages: int = 20) -> list:
        """Every row of a paged list, following Sentry's Link header up to `limit_pages` pages."""
        rows, url, n = [], f"{API}{path}", 0
        while url and n < limit_pages:
            got, link = self.call('GET', '', tok=tok, full_url=url, headers=True)
            rows.extend(got or [])
            url, n = next_page(link), n + 1
        return rows

    def lookup(self, short_id: str, tok: str = '') -> dict:
        """The issue behind a short id (PROJ-6) or a numeric issue id: id, status, counts, title, first and last seen
        (the clock readings in the client's zone), project, link."""
        key = str(short_id).strip()
        if key.isdigit():
            g = self.call('GET', f"/organizations/{self.org}/issues/{key}/", tok=tok)
            d = {'groupId': g.get('id'), 'group': g}
        else:
            d = self.call('GET', f"/organizations/{self.org}/shortids/{key.upper()}/", tok=tok)
        g = d.get('group') or {}
        return {'id': str(d.get('groupId') or g.get('id') or ''), 'shortId': g.get('shortId', key.upper()),
                'status': g.get('status'), 'count': g.get('count'), 'userCount': g.get('userCount'),
                'title': g.get('title'), 'firstSeen': g.get('firstSeen'), 'lastSeen': g.get('lastSeen'),
                'firstSeenPT': pt(g.get('firstSeen'), self.zone), 'lastSeenPT': pt(g.get('lastSeen'), self.zone),
                'project': (g.get('project') or {}).get('slug'), 'link': g.get('permalink')}

    def _id(self, short_id: str, tok: str = '') -> dict:
        issue = self.lookup(short_id, tok)
        if not issue['id']:
            raise Refused(f"{short_id}: no such issue in {self.org}")
        return issue

    def resolve(self, short_id: str, commit: str, next_release: bool = False, session: str = '', postmortem: str = '',
                tok: str = '') -> dict:
        """Resolve the issue (or mark it resolved in the next release) and leave a note naming the fix commit, the
        session and the post-mortem when there is one. The issue as it is afterwards."""
        issue = self._id(short_id, tok)
        status = 'resolvedInNextRelease' if next_release else 'resolved'
        self.call('PUT', f"/organizations/{self.org}/issues/{issue['id']}/", {'status': status}, tok=tok)
        note = f"Fixed by {commit}" + (f" ({session})" if session else '') + \
            (" — resolved in the next release, so it stays open until the fix reaches users" if next_release else '') + \
            (f". Post-mortem: {postmortem}" if postmortem else '')
        self.call('POST', f"/organizations/{self.org}/issues/{issue['id']}/comments/", {'text': note}, tok=tok)
        return {**issue, 'status': status, 'note': note}

    def unresolved(self, tok: str = '', period: str = '14d') -> list:
        """Every unresolved issue in the client's projects, newest last seen first."""
        rows = []
        for project in self.projects:
            for g in self.call('GET', f"/projects/{self.org}/{project}/issues/?query=is:unresolved&statsPeriod={period}", tok=tok) or []:
                rows.append({'shortId': g.get('shortId'), 'project': project, 'count': g.get('count'),
                             'lastSeen': g.get('lastSeen'), 'title': (g.get('title') or '')[:90]})
        return sorted(rows, key=lambda r: r.get('lastSeen') or '', reverse=True)

    def issues(self, project: str, query: str = 'is:unresolved', tok: str = '', **params) -> list:
        """A project's issues matching Sentry's own search, every page (the sweep's list)."""
        q = urllib.parse.urlencode({'query': query, **params})
        return self.pages(f"/projects/{self.org}/{project}/issues/?{q}", tok=tok)

    def event(self, issue_id: str, tok: str = '') -> dict:
        """The issue's newest event as Sentry returns it (the caller trims)."""
        return self.call('GET', f"/organizations/{self.org}/issues/{issue_id}/events/latest/", tok=tok) or {}

    def latest_event(self, short_id: str, tok: str = '') -> dict:
        """The issue's newest event, trimmed to what a triager reads: its message, the exception's type, value and the
        innermost frames of its stack, the tags, the release and when. The caller reads it; it never goes on a
        public ticket whole (a person's words or an address may be in it)."""
        issue = self._id(short_id, tok)
        e = self.event(issue['id'], tok)
        frames, exc = [], {}
        for entry in e.get('entries') or []:
            if entry.get('type') == 'exception':
                values = (entry.get('data') or {}).get('values') or []
                if values:
                    v = values[-1]
                    exc = {'type': v.get('type'), 'value': (v.get('value') or '')[:500]}
                    for f in ((v.get('stacktrace') or {}).get('frames') or [])[-8:]:
                        frames.append(f"{f.get('filename') or f.get('module')}:{f.get('lineNo')} {f.get('function')}")
        return {'issue': issue, 'message': (e.get('message') or e.get('title') or '')[:500], 'exception': exc,
                'frames': frames, 'tags': {t.get('key'): t.get('value') for t in e.get('tags') or []},
                'release': (e.get('release') or {}).get('version') if isinstance(e.get('release'), dict) else e.get('release'),
                'when': pt(e.get('dateCreated'), self.zone)}

    def search(self, query: str, limit: int = 25, tok: str = '') -> list:
        """Issues across the organization matching Sentry's own search, newest first."""
        q = urllib.parse.urlencode({'query': query, 'limit': limit, 'sort': 'date'})
        rows = self.call('GET', f"/organizations/{self.org}/issues/?{q}", tok=tok) or []
        return [{'shortId': g.get('shortId'), 'project': (g.get('project') or {}).get('slug'), 'count': g.get('count'),
                 'lastSeenPT': pt(g.get('lastSeen'), self.zone), 'title': (g.get('title') or '')[:120]} for g in rows]

    def comment(self, short_id: str, text: str, tok: str = '') -> dict:
        """A note on the issue (a triager's finding, a fix's commit)."""
        issue = self._id(short_id, tok)
        self.call('POST', f"/organizations/{self.org}/issues/{issue['id']}/comments/", {'text': text}, tok=tok)
        return {'shortId': issue['shortId'], 'commented': True}

    # The actions a direct call or a listener may ask for, by name: what each takes, and whether it writes.
    ACTIONS = {
        'issue': ('lookup', ('id',), False),
        'event': ('latest_event', ('id',), False),
        'search': ('search', (), False),
        'unresolved': ('unresolved', (), False),
        'comment': ('comment', ('id', 'text'), True),
        'resolve': ('resolve', ('id', 'commit'), True),
    }

    def run(self, action: str, args: dict = None, tok: str = ''):
        """One named action with its args ({id, query, text, commit, next_release, session, postmortem}); Refused for an
        unknown action, a missing arg or Sentry's refusal."""
        if action not in self.ACTIONS:
            raise Refused(f"sentry: no action {action!r} (one of {', '.join(self.ACTIONS)})")
        method, needs, _write = self.ACTIONS[action]
        a = args or {}
        missing = [n for n in needs if not a.get(n)]
        if missing:
            raise Refused(f"sentry {action}: needs {', '.join(missing)}")
        if action == 'issue':
            return self.lookup(a['id'], tok)
        if action == 'event':
            return self.latest_event(a['id'], tok)
        if action == 'search':
            return self.search(a.get('query') or 'is:unresolved', tok=tok)
        if action == 'unresolved':
            return self.unresolved(tok)
        if action == 'comment':
            return self.comment(a['id'], a['text'], tok)
        return self.resolve(a['id'], a['commit'], bool(a.get('next_release')), a.get('session') or '',
                            postmortem=a.get('postmortem') or '', tok=tok)
