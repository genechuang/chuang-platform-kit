#!/usr/bin/env python3
"""sentry_blame: what broke production, for a Sentry alarm: the failing line, the release and who shipped it, and the
suspect commits, from a faked Sentry read (nothing reaches sentry.io).

Run:  python -B tests/test-sentry-blame.py
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import sentry_blame as B  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


ORG, REPO = 'acme', 'acme/widgets'
SHA = '1abb2227fa3f0f42ddc9116d09186d9caedaeb88'
SUSPECT = 'a9c50aee32b510370bd9a6b91348fb91ddfb4d40'
ISSUE = {'id': '7785980584', 'shortId': 'WIDGETS-C', 'project': {'slug': 'widgets-api'}}
EVENT = {'eventID': 'ev1', 'release': {'version': SHA},
         'entries': [{'type': 'exception', 'data': {'values': [{'stacktrace': {'frames': [
             {'filename': 'framework/__init__.py', 'lineNo': 100, 'function': 'view_func', 'inApp': False},
             {'filename': 'core/queue.py', 'lineNo': 1074, 'function': 'apply', 'inApp': True},
             {'filename': 'requests/api.py', 'lineNo': 5, 'function': 'get', 'inApp': False}]}}]}}]}
MESSAGE = 'Orders are recorded in the database first\n\n- a bullet\n\nSession: Night Shift\nCo-Authored-By: Someone'


def fake(answers):
    """A Sentry GET that answers by path prefix and records every path asked."""
    asked = []

    def get(path):
        asked.append(path)
        for prefix, answer in answers.items():
            if path.startswith(prefix):
                return answer
        return {}
    get.asked = asked
    return get


LIVE = {
    '/projects/acme/widgets-api/stacktrace-link/': {'sourceUrl': f'https://github.com/acme/widgets/blob/{SHA}/core/queue.py'},
    '/projects/acme/widgets-api/events/ev1/committers/': {'committers': [
        {'author': {'name': 'Pat Author'}, 'commits': [{'id': SUSPECT, 'message': MESSAGE}]}]},
    f'/organizations/acme/releases/{SHA}/commits/': [
        {'id': SHA, 'message': 'The release\n\nSession: Day Shift', 'author': {'name': 'Pat Author'}}],
}

# The positive case: the failing line linked at the release, who shipped it, and the suspect with its session.
get = fake(LIVE)
b = B.blame(get, ISSUE, EVENT, ORG, REPO)
check("the frame that raised is the last in-app one", b['frame'], {'file': 'core/queue.py', 'line': 1074, 'function': 'apply'})
check("the line on GitHub", b['code_url'], f'https://github.com/acme/widgets/blob/{SHA}/core/queue.py#L1074')
check("asked at the release commit", any(f'commitId={SHA}' in p and 'lineNo=1074' in p for p in get.asked), True)
check("the release", (b['release'], b['release_url'], b['released_by']),
      ('1abb222', f'https://github.com/acme/widgets/commit/{SHA}', 'Day Shift'))
check("the suspect", b['suspects'], [{'sha': SUSPECT, 'subject': 'Orders are recorded in the database first',
                                     'who': 'Night Shift', 'url': f'https://github.com/acme/widgets/commit/{SUSPECT}'}])
check("the Slack lines", B.lines(b), [
    f'Code: <https://github.com/acme/widgets/blob/{SHA}/core/queue.py#L1074|queue.py:1074 in apply>',
    f'Release: <https://github.com/acme/widgets/commit/{SHA}|1abb222>, shipped by Day Shift',
    f'Likely cause: <https://github.com/acme/widgets/commit/{SUSPECT}|a9c50ae> Orders are recorded in the database first, by Night Shift'])

# Markdown and escaping.
b = B.blame(fake(LIVE), ISSUE, EVENT, ORG, REPO)
b['suspects'][0]['subject'] = 'a <b> & c'
md = B.lines(b, link=lambda url, text: f'[{text}]({url})')
check("`link` makes Markdown", md[0].startswith('Code: [queue.py:1074 in apply](https://github.com/'), True)
esc = B.lines(b, esc=lambda s: s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))
check("`esc` escapes the text, never the link",
      (esc[2].endswith('a &lt;b&gt; &amp; c, by Night Shift'), esc[2].startswith('Likely cause: <https://')), (True, True))

# No repository named: no commit links, and the release is its short sha alone.
b = B.blame(fake(LIVE), ISSUE, EVENT, ORG)
check("no repository: no commit links", (b['release_url'], b['suspects'][0]['url']), ('', ''))
check("no repository: the release and the suspect stand alone", B.lines(b)[1:],
      ['Release: 1abb222, shipped by Day Shift', 'Likely cause: a9c50ae Orders are recorded in the database first, by Night Shift'])
check("commit_url needs both", (B.commit_url('', SHA), B.commit_url(REPO, ''), B.commit_url(REPO, 'abc')),
      ('', '', 'https://github.com/acme/widgets/commit/abc'))

# No suspects: the caller's line change.
b = B.blame(fake({k: v for k, v in LIVE.items() if 'stacktrace-link' in k}), ISSUE, EVENT, ORG, REPO)
check("nothing guessed", (b['suspects'], b['released_by']), ([], ''))
check("no cause line without a line change", [x.split(':')[0] for x in B.lines(b)], ['Code', 'Release'])
b['line_change'] = {'sha': SUSPECT, 'subject': 'orders first', 'who': 'Night Shift', 'url': 'https://x/commit/a9'}
check("the line change", B.lines(b)[-1], 'Line last changed: <https://x/commit/a9|a9c50ae> orders first, by Night Shift')
b['suspects'] = [{'sha': SHA, 'subject': 's', 'who': 'Swing Shift', 'url': ''}]
check("a suspect wins over the line change, and a link-less sha stands alone",
      [x for x in B.lines(b) if not x.startswith(('Code', 'Release'))], ['Likely cause: 1abb222 s, by Swing Shift'])


def event(*frames):
    return {'entries': [{'type': 'exception', 'data': {'values': [{'stacktrace': {'frames': list(frames)}}]}}]}


check("`<anonymous>` is never the failing line", B.top_frame(event({'filename': '<anonymous>', 'lineNo': 1, 'inApp': True})), {})
check("nor a phone's bundle", B.top_frame(event({'filename': 'app:///main.jsbundle', 'lineNo': 1, 'inApp': True})), {})
check("a source frame under a bundle frame",
      B.top_frame(event({'filename': 'app:///src/lib/api.ts', 'lineNo': 9, 'function': 'call'},
                        {'filename': 'app:///main.jsbundle', 'lineNo': 1, 'inApp': True})),
      {'file': 'app:///src/lib/api.ts', 'line': 9, 'function': 'call'})
check("no in-app frame: the last source frame",
      B.top_frame(event({'filename': 'a.py', 'lineNo': 2}, {'filename': 'b.py', 'lineNo': 3})),
      {'file': 'b.py', 'line': 3, 'function': ''})
check("the SDK's lineno spelling", B.top_frame(event({'filename': 'a.py', 'lineno': 7}))['line'], 7)
check("no stack", (B.top_frame({}), B.top_frame(None), B.lines({})), ({}, {}, []))
check("an unplaced file: the name, no link", B.lines({'frame': {'file': 'x/y.py', 'line': None}}), ['Code: y.py:?'])

check("a release that is a sha", B.release_sha({'release': {'version': SHA}}), SHA)
check("an app's version+sha", B.release_sha({'release': 'com.example.app@0.1.0+7ab05d9'}), '7ab05d9')
check("a version that is no commit", (B.release_sha({'release': 'com.example.app@0.1.0+9'}), B.release_sha({})), ('', ''))
check("the last Session trailer", B.session_of('x\n\nSession: First\n\nSession: Swing Shift\n'), 'Swing Shift')
check("no trailer", (B.session_of('x'), B.session_of(None)), ('', ''))
check("released_by needs both", (B.released_by(fake(LIVE), '', SHA, ORG), B.released_by(fake(LIVE), SHA, '', ORG)), ('', ''))
check("the author without a trailer",
      B.released_by(fake({'/organizations/': [{'id': SHA, 'message': 'x', 'author': {'name': 'Pat'}}]}), SHA, SHA[:7], ORG), 'Pat')
check("a dict answer (a refusal) is no commit list", B.released_by(fake({'/organizations/': {'detail': 'no'}}), SHA, SHA, ORG), '')

get = fake(LIVE)
b = B.blame(get, {}, EVENT, ORG, REPO)
check("an issue with no project: no link, no suspects", (b['code_url'], b['suspects']), ('', []))
check("only the release was asked", [p.split('?')[0] for p in get.asked], [f'/organizations/acme/releases/{SHA}/commits/'])
check("no event id, no committers", B.suspects(get, 'widgets-api', '', ORG, REPO), [])
check("a frame with no file asks nothing", B.code_link(get, 'widgets-api', {}, SHA, ORG), '')
check("a file Sentry cannot place", B.code_link(fake({}), 'widgets-api', {'file': 'a.py', 'line': 3}, '', ORG), '')

check("a blob link's repository path", B.repo_path(f'https://github.com/o/r/blob/{SHA}/core/a%20b.py#L3'), 'core/a b.py')
check("no path in anything else", (B.repo_path(''), B.repo_path('https://github.com/o/r/commit/x')), ('', ''))

print(f"\n{len(fails)} failure(s)")
if fails:
    sys.exit(1)
