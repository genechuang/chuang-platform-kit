"""Who and what broke production, for a Sentry alarm: the commit and the code an error summary should point to, and
who shipped it.

From a Sentry issue's latest event and Sentry's API (its GitHub integration and code mappings):

- the code: the top frame of the host's own code in the stack trace (the last in-app frame, where it was raised),
  linked to that line on GitHub at the release's commit (Sentry's stacktrace-link, the project's code mappings);
- the release: the commit the failing code was deployed from, and who shipped it (its head commit's trailer);
- the likely cause: Sentry's suspect commits for the event (its committers API), when it has them, each with who made
  it: the commit's `Session:` trailer (the working session that wrote it), else its author. When Sentry has none (a
  release that carries no commits), the caller may name the commit that last changed the failing line instead
  (`line_change`, from GitHub's blame).

`get` is the caller's own Sentry read, `get(path) -> the decoded answer` (falsy when it could not be read), so the
caller holds the token; `org` is the Sentry organization's slug and `repo` the GitHub repository (`owner/name`) the
commit links point at. Standard library only; every read is optional, and a missing piece is left out rather than
guessed.
"""
import re
import urllib.parse

_SHA = re.compile(r'^[0-9a-f]{7,40}$')
_SESSION = re.compile(r'^Session:\s*(.+?)\s*$', re.M)


def _source(frame: dict) -> bool:
    """Whether a frame names a source file a person wrote: not `<anonymous>`, not a phone's unmapped bundle."""
    name = str(frame.get('filename') or frame.get('absPath') or '')
    return bool(name) and not name.startswith('<') and not name.endswith(('.jsbundle', '.bundle', '.hbc'))


def top_frame(event: dict) -> dict:
    """{file, line, function} of the frame that raised: the last in-app source frame of the last exception's stack,
    else its last source frame; {} when the event has no stack trace or no frame names a source file."""
    frames = []
    for entry in (event or {}).get('entries') or []:
        if entry.get('type') == 'exception':
            for value in (entry.get('data') or {}).get('values') or []:
                frames = (value.get('stacktrace') or {}).get('frames') or frames
    frames = [f for f in frames if _source(f)]
    if not frames:
        return {}
    ours = [f for f in frames if f.get('inApp')]
    f = (ours or frames)[-1]
    return {'file': f.get('filename') or f.get('absPath') or '', 'line': f.get('lineNo') or f.get('lineno'),   # API, SDK spelling
            'function': f.get('function') or ''}


def repo_path(code_url: str) -> str:
    """The repository path of a GitHub blob link ('src/app/queue.py'), or ''."""
    m = re.search(r'/blob/[^/]+/([^#?]+)', code_url or '')
    return urllib.parse.unquote(m.group(1)) if m else ''


def release_sha(event: dict) -> str:
    """The commit an event's release names ('1abb2227…', or an app's 'com.example.app@1.0.0+<sha>'), or ''."""
    rel = (event or {}).get('release')
    version = (rel or {}).get('version') if isinstance(rel, dict) else rel
    tail = re.split(r'[@+]', str(version or ''))[-1].lower()
    return tail if _SHA.match(tail) else ''


def session_of(message: str) -> str:
    """The `Session:` trailer of a commit message (the working session that made it), or ''."""
    found = _SESSION.findall(message or '')
    return found[-1] if found else ''


def commit_url(repo: str, sha: str) -> str:
    """A commit's page on GitHub, or '' without a repository or a sha."""
    return f'https://github.com/{repo}/commit/{sha}' if repo and sha else ''


def code_link(get, project: str, frame: dict, sha: str, org: str) -> str:
    """The failing line on GitHub, at `sha` when known (else the mapped default branch), '#L<line>' appended; '' when
    Sentry's code mappings cannot place the file."""
    if not frame.get('file'):
        return ''
    query = {'file': frame['file'], 'lineNo': frame.get('line') or ''}
    if sha:
        query['commitId'] = sha
    got = get(f'/projects/{org}/{project}/stacktrace-link/?{urllib.parse.urlencode(query)}') or {}
    url = got.get('sourceUrl') or ''
    return f"{url}#L{frame['line']}" if url and frame.get('line') else url


def suspects(get, project: str, event_id: str, org: str, repo: str = '') -> list:
    """Sentry's suspect commits for an event, newest first: [{sha, subject, who, url}], `who` the Session trailer, else
    the author's name, `url` the commit in `repo` ('' without one); [] when Sentry has none (a release with no
    commits, no mapping)."""
    if not event_id:
        return []
    got = get(f'/projects/{org}/{project}/events/{event_id}/committers/') or {}
    out = []
    for committer in got.get('committers') or []:
        author = (committer.get('author') or {}).get('name') or 'unknown'
        for c in committer.get('commits') or []:
            sha = str(c.get('id') or '')
            message = str(c.get('message') or '')
            out.append({'sha': sha, 'subject': message.split('\n', 1)[0][:120], 'who': session_of(message) or author,
                        'url': commit_url(repo, sha)})
    return out


def released_by(get, version: str, sha: str, org: str) -> str:
    """Who shipped a release: the `Session:` trailer of its head commit, else that commit's author, from the commits
    the deploy sent Sentry; '' when Sentry holds none for it."""
    if not version or not sha:
        return ''
    got = get(f'/organizations/{org}/releases/{urllib.parse.quote(version, safe="")}/commits/') or []
    for c in got if isinstance(got, list) else []:
        if str(c.get('id') or '').startswith(sha) or sha.startswith(str(c.get('id') or '-')):
            return session_of(str(c.get('message') or '')) or (c.get('author') or {}).get('name') or ''
    return ''


def blame(get, issue: dict, event: dict, org: str, repo: str = '') -> dict:
    """Everything above for one issue: {frame, code_url, release, release_url, released_by, suspects}; each part
    empty when unknown. The caller may add `line_change` ({sha, subject, who, url}: the commit that last changed the
    failing line, from git blame), which `lines` shows when Sentry names no suspect."""
    project = ((issue or {}).get('project') or {}).get('slug') or ''
    frame = top_frame(event)
    sha = release_sha(event)
    rel = (event or {}).get('release')
    version = str((rel or {}).get('version') if isinstance(rel, dict) else rel or '')
    return {'frame': frame, 'code_url': code_link(get, project, frame, sha, org) if project else '',
            'release': sha[:7], 'release_url': commit_url(repo, sha),
            'released_by': released_by(get, version, sha, org),
            'suspects': suspects(get, project, (event or {}).get('eventID') or '', org, repo) if project else []}


def lines(b: dict, link=lambda url, text: f'<{url}|{text}>', esc=lambda s: s) -> list:
    """The alarm's 'what broke it' lines, Slack links by default (`link` makes them Markdown for an issue body; `esc`
    escapes the free text, a commit subject or a function name, for the channel): the failing line, the release and
    who shipped it, and the likely cause (Sentry's suspect commits, else the commit that last changed the failing
    line). A release with no link (no repository named) is its short sha alone."""
    def commit(c):
        return f"{link(c['url'], c['sha'][:7]) if c.get('url') else c['sha'][:7]} {esc(c['subject'])}, by {esc(c['who'])}"
    out = []
    f = b.get('frame') or {}
    if f.get('file'):
        where = esc(f"{f['file'].rsplit('/', 1)[-1]}:{f.get('line') or '?'}{' in ' + f['function'] if f.get('function') else ''}")
        out.append(f"Code: {link(b['code_url'], where) if b.get('code_url') else where}")
    if b.get('release'):
        by = f", shipped by {esc(b['released_by'])}" if b.get('released_by') else ''
        out.append(f"Release: {link(b['release_url'], b['release']) if b.get('release_url') else b['release']}{by}")
    for s in (b.get('suspects') or [])[:2]:
        out.append(f"Likely cause: {commit(s)}")
    if not b.get('suspects') and (b.get('line_change') or {}).get('sha'):
        out.append(f"Line last changed: {commit(b['line_change'])}")
    return out
