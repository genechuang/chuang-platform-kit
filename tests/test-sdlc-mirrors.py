#!/usr/bin/env python3
"""chuang_sdlc.mirrors: a vendor's open findings (Sentry issues, GitGuardian incidents) mirrored as GitHub issues.

Pinned (the positive case first, on fakes, never GitHub or a vendor): a new open item is filed once with its marker
and a resolved one's open issue is closed; a closed issue counts as handled; a pull request is never read; the
commit's Session trailer; Sentry's release commit (a sha, a `+sha` tail, an app's `build` tag, never a version), the
exception cut short, the blame line, the body redacted with the host's footer; the sweep waits on an unread event and
stops after its misses; GitGuardian's title names another repo's file, in_head's three answers, the body never
carrying the match, a vendor 5xx raised as VendorDown, a repo it does not watch skipped, a second run's filing skipped.

Run:  python -B tests/test-sdlc-mirrors.py
"""
import os
import sys
import urllib.error

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))

from chuang_sdlc import mirrors as M  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(('  PASS  ' if ok else '  FAIL  ') + label)
    if not ok:
        print(f'        got  {got!r}\n        want {want!r}')
        fails.append(label)


class Issues:
    def __init__(self):
        self.calls = []

    def file(self, title, body):
        self.calls.append(('file', title, body))

    def close(self, number, comment=''):
        self.calls.append(('close', number, comment))


TOKEN = 'ghp_' + 'c' * 36
OPEN = {'id': '11', 'shortId': 'APP-1', 'status': 'unresolved', 'title': 'Boom', 'project': {'slug': 'app'}}
DONE = {'id': '12', 'shortId': 'APP-2', 'status': 'resolved', 'title': 'Old'}
GH = [{'number': 5, 'state': 'open', 'body': M.marker('sentry-issue', 12)},
      {'number': 6, 'state': 'closed', 'body': M.marker('sentry-issue', 13)},
      {'number': 7, 'state': 'open', 'body': M.marker('sentry-issue', 11), 'pull_request': {}}]

# -- the positive case ---------------------------------------------------------------------------------------------
to_file, to_close = M.plan([OPEN, DONE, dict(DONE, id='13', status='unresolved')], GH, tag='sentry-issue',
                           item_id=M.sentry_id, is_open=M.sentry_is_open)
check('the positive case: the new open issue is filed and the resolved one\'s open issue closed; a closed issue is '
      'handled, a pull request never read',
      ([i['id'] for i in to_file], [(i['number'], s) for i, s in to_close]), (['11'], [(5, 'resolved')]))

gh = Issues()
out = []
M.sentry_sweep(gh=gh, issues=[OPEN, DONE], gh_issues=GH, tag='sentry-issue', latest_event=lambda n: {},
               is_alarm=lambda i, e: (True, ''), title=lambda i: M.sentry_title(i, 'Sentry issue '),
               body=lambda i, e: 'b', misses=3, dry=False, out=out.append)
check('the sweep files the alarm and closes with the one comment',
      gh.calls, [('file', 'Sentry issue APP-1: Boom', 'b'),
                 ('close', 5, 'Closed by the Workflow Watchdog: the issue is resolved in Sentry.')])

gh = Issues()
M.sentry_sweep(gh=gh, issues=[OPEN, dict(OPEN, id='14'), dict(OPEN, id='15')], gh_issues=[], tag='sentry-issue',
               latest_event=lambda n: None, is_alarm=lambda i, e: (True, ''), title=lambda i: i['id'],
               body=lambda i, e: 'b', misses=2, dry=False, out=out.append)
check('an unread event files nothing, and after the misses the rest wait',
      (gh.calls, [line.split(':')[0] for line in out[-3:]]), ([], ['  WAIT   11', '  WAIT   14', '  WAIT   15']))

gh = Issues()
M.sentry_sweep(gh=gh, issues=[OPEN], gh_issues=[], tag='sentry-issue', latest_event=lambda n: {},
               is_alarm=lambda i, e: (False, 'a developer\'s machine'), title=lambda i: i['id'],
               body=lambda i, e: 'b', misses=3, dry=False, out=out.append)
check('a non-alarm is skipped with its reason', (gh.calls, out[-1]), ([], "  SKIP   11: a developer's machine"))

check('the commit session', (M.commit_session('o/r', 'abc', lambda p: {'commit': {'message': 's\n\nSession: Snow White\n'}}),
                             M.commit_session('o/r', 'abc', lambda p: (_ for _ in ()).throw(OSError())),
                             M.commit_session('', 'abc', None)), ('Snow White', '', ''))

sha = '5007117' + 'a' * 33
check('the release commit: a sha, a +sha tail, an app\'s build tag, never a version',
      (M.release_commit({'release': {'version': sha}}), M.release_commit({'release': '20261014-2118+cf8b3c8'}),
       M.release_commit({'release': 'com.x.app@1.0+9', 'tags': [{'key': 'build', 'value': 'abcdef1'}]}),
       M.release_commit({'release': 'com.x.app@1.0+abcdef1'})), (sha, 'cf8b3c8', 'abcdef1', ''))

event = {'release': sha, 'tags': [{'key': 'logger', 'value': 'core.x'}],
         'entries': [{'type': 'exception', 'data': {'values': [{'type': 'E', 'value': TOKEN + 'x' * 400,
                     'stacktrace': {'frames': [{'filename': 'a.py', 'lineno': 3, 'function': 'f'}]}}]}},
                     {'type': 'breadcrumbs', 'data': {'values': [{'level': 'warning', 'message': 'hi'}]}}]}
body = M.sentry_body(OPEN, event, repo='o/r', session='Black Widow', cause=['the line'], footer='Resolve it.',
                     tag='sentry-issue', pt=lambda s: 'PT')
check('the body: commit link, who, cause, stack, crumbs, the footer and marker; the token redacted, the value cut',
      (f'https://github.com/o/r/commit/{sha}' in body, 'Raised by: Black Widow' in body, '- the line' in body,
       '`a.py:3 in f`' in body, 'warning: hi' in body, body.endswith('Resolve it.\n\n<!-- sentry-issue: 11 -->'),
       TOKEN in body, 'x' * 300 in body), (True, True, True, True, True, True, False, False))

ranges = {'repository': {'object': {'blame': {'ranges': [
    {'startingLine': 1, 'endingLine': 9, 'commit': {'oid': 'f' * 40, 'message': 'Fix it\n\nSession: Mr Sandman',
                                                    'author': {'name': 'Claude'}}}]}}}}
check('the line change: the commit and its session, {} when GitHub refuses',
      (M.line_change('o/r', sha, 'a.py', 3, lambda q, v: ranges, M._SESSION.search and (lambda m: 'Mr Sandman')),
       M.line_change('o/r', sha, 'a.py', 3, lambda q, v: (_ for _ in ()).throw(OSError()), lambda m: '')),
      ({'sha': 'f' * 40, 'subject': 'Fix it', 'who': 'Mr Sandman', 'url': f'https://github.com/o/r/commit/{"f" * 40}'}, {}))

# -- GitGuardian ---------------------------------------------------------------------------------------------------
inc = {'id': 3, 'status': 'TRIGGERED', '_repo': 'o/kit', 'occurrences': [
    {'filepath': 'tests/t.py', 'sha': 'abcdef123', 'matches': [{'match': TOKEN}], 'author_name': 'Someone'}]}
check('the title names another repo\'s file',
      M.gg_title(inc, prefix='GitGuardian incident ', home='o/bot', detector=lambda i: 'Generic Secret',
                 files=lambda i: ['tests/t.py', 'b.py']),
      'GitGuardian incident 3: Generic Secret in kit/tests/t.py (+1 more)')

nf = urllib.error.HTTPError('u', 404, 'nf', {}, None)
check('in_head: local isfile, GitHub yes, a 404 no, anything else not measured',
      (M.in_head('a', repo='o/r', local=True, isfile=lambda p: False, get=None, not_found=None),
       M.in_head('a', repo='o/r', local=False, isfile=None, get=lambda p: {}, not_found=lambda e: False),
       M.in_head('a', repo='o/r', local=False, isfile=None, get=lambda p: (_ for _ in ()).throw(nf),
                 not_found=lambda e: getattr(e, 'code', 0) == 404),
       M.in_head('a', repo='o/r', local=False, isfile=None, get=lambda p: (_ for _ in ()).throw(OSError()),
                 not_found=lambda e: False), M.in_head('', repo='o/r', local=True, isfile=None, get=None, not_found=None)),
      ('no', 'yes', 'no', 'not measured', 'not measured'))

body = M.gg_body(inc, origin='o/kit', session='', head=lambda p, r: 'yes', detector=lambda i: 'Generic Secret',
                 tag='gitguardian-incident', footer='Resolve it there.')
check('the body never carries the match or the author, and ends with the footer and marker',
      (TOKEN in body, 'Someone' in body, '| `tests/t.py` | abcdef1 |' in body, 'Raised by: not known' in body,
       body.endswith('Resolve it there.\n\n<!-- gitguardian-incident: 3 -->')), (False, False, True, True, True))


def down(repo):
    raise urllib.error.HTTPError('u', 500, 'boom', {}, None)


def unwatched(repo):
    raise RuntimeError(f'{repo} is not watched')


raised = None
try:
    M.gg_fetch(['o/a'], [], list_open=down, read_one=None, out=out.append)
except M.VendorDown as e:
    raised = str(e)
got = M.gg_fetch(['o/a', 'o/b'], [9], list_open=lambda r: [] if r == 'o/a' else unwatched(r),
                 read_one=lambda n: {'id': n, 'status': 'RESOLVED'}, out=out.append)
check('a vendor 5xx is VendorDown; an unwatched repo is skipped; a marked id is read on its own',
      (raised, got, out[-1]), ('o/a: HTTP Error 500: boom', [{'id': 9, 'status': 'RESOLVED'}],
                               '::notice::o/b is not watched; skipped'))

gh = Issues()
M.gg_sweep(gh=gh, incidents=[inc, dict(inc, id=4)], gh_issues=[{'number': 2, 'state': 'open',
           'body': M.marker('gitguardian-incident', 8)}], tag='gitguardian-incident', attach=lambda i: i,
           title=lambda i: str(i['id']), body=lambda i: 'b', refiled=lambda n: n == 4, scanned='o/kit', dry=False,
           out=out.append)
check('the sweep files once and skips what another run filed; an unknown marked incident is never closed',
      gh.calls, [('file', '3', 'b')])

print(f'{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
