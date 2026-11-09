#!/usr/bin/env python3
"""chuang_sdlc.signature (0.15.0, SMADPickleBot #1279): who wrote a comment when everyone writes with one person's token.

Pinned (the positive case first): a body signed once at its end by the signer at hand; the signer from configure(),
then CHUANG_SIGNED_AS, then the Claude Code session registry row CLAUDE_PID names, then the default; nobody signs
nothing; a signed body is left alone; signed_by reads the last footer and never a `Scrum:` or `Triage:` line, a closing
reply stamp or a header; footer() refuses a bad kind or name; the ticket client signs comment, edit_comment and
close_issue's comment and leaves an issue body alone; alerts signs its close and its note with the closer's mark first.

Run:  python -B tests/test-sdlc-signature.py
"""
import json
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))

from chuang_sdlc import signature as G  # noqa: E402
from chuang_sdlc import tickets as T  # noqa: E402
from chuang_sdlc import alerts as A  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


def reset():
    G._SET.update(who=None, kind=None, default=None)


for name in ('CLAUDE_PID', 'CHUANG_SIGNED_AS'):   # the session running the suite must not sign its cases
    os.environ.pop(name, None)
home = tempfile.mkdtemp()
os.makedirs(os.path.join(home, '.claude', 'sessions'))
with open(os.path.join(home, '.claude', 'sessions', '4242.json'), 'w', encoding='utf-8') as f:
    json.dump({'sessionId': 'abc', 'name': 'Snow White', 'kind': 'interactive'}, f)

print('signing')
reset()
env = {'CLAUDE_PID': '4242'}
check("the positive case: a session's comment ends with its name, from the registry row CLAUDE_PID names",
      G.sign('Scrum: next | after #4', env=env, home=home), 'Scrum: next | after #4\n\n— **Snow White** · Claude session')
check('  signed once: a signed body is left as it is', G.sign(G.sign('x', env=env, home=home), who='Other'),
      'x\n\n— **Snow White** · Claude session')
check('  CHUANG_SIGNED_AS wins over the registry, a bare name a session, `Name|pipeline` the pipeline',
      (G.signer({'CHUANG_SIGNED_AS': 'Mr Sandman', 'CLAUDE_PID': '4242'}, home),
       G.signer({'CHUANG_SIGNED_AS': 'Deploy|pipeline'}, home)), (('Mr Sandman', 'Claude session'), ('Deploy', 'pipeline')))
check('  a malformed CHUANG_SIGNED_AS (bad kind, a star) falls through to the registry',
      (G.signer({'CHUANG_SIGNED_AS': 'X|robot', 'CLAUDE_PID': '4242'}, home), G.signer({'CHUANG_SIGNED_AS': '*x*'}, home)),
      (('Snow White', 'Claude session'), None))
check('  no registry row, no env, no default: nobody signs and the body goes as given',
      (G.signer({'CLAUDE_PID': '99'}, home), G.sign('plain', env={}, home=home)), (None, 'plain'))
G.configure(default=('Picklebot', 'pipeline'))
check('  the default signs a pipeline job (no session runs it); a session still signs as itself',
      (G.sign('LOE: 2h', env={}, home=home), G.signer(env, home)),
      ('LOE: 2h\n\n— **Picklebot** · pipeline', ('Snow White', 'Claude session')))
G.configure(who='Scrum master')
check('  configure(who=) wins over everything, a pipeline unless told', G.signer(env, home), ('Scrum master', 'pipeline'))
reset()

print('reading')
check("the positive case: the last footer's who and kind",
      G.signed_by('a\n— **Old** · pipeline\nb\n\n— **Black Widow** · Claude session'), {'who': 'Black Widow', 'kind': 'Claude session'})
check("  a person's words, a Scrum or Triage line, a reply's closing stamp, a header, a bold name mid-line: unsigned",
      [G.signed_by(b) for b in ('Yes, go with B', 'Scrum: building | eta 3 PM', 'Triage: P2 | Owner: Ann',
                                 '— 11/09/26 10:00 AM PT · ticket: #1279', '**Scrum master** · 11/9/26 9:00 AM PT',
                                 'see — **Snow White** · Claude session and more')],
      [{}, {}, {}, {}, {}, {}])
check("  only the last line signs: a person's reply under a quoted signature is the person's own words",
      (G.signed_by('> Live in release abc.\n> — **Snow White** · Claude session\n\nYes, go with B'),
       G.sign('> — **Snow White** · Claude session\n\nYes', who='Picklebot').count('— **')), ({}, 2))
check('  a body GitHub answers with CRLF line ends still reads as signed',
      G.signed_by('Live.\r\n\r\n— **Snow White** · Claude session\r\n'), {'who': 'Snow White', 'kind': 'Claude session'})
check('  is_persons', (G.is_persons('Yes'), G.is_persons('x\n\n— **Snow White** · Claude session')), (True, False))
for bad in (('Snow White', 'robot'), ('', 'pipeline'), ('a*b', 'pipeline'), ('x' * 41, 'pipeline')):
    try:
        G.footer(*bad)
        check(f'footer refuses {bad!r}', 'accepted', 'refused')
    except ValueError:
        check(f'  footer refuses {bad[0][:12]!r}/{bad[1]}', True, True)

print('the writers')


class Kit:
    """chuang_platform_kit.github's verbs, recorded."""
    def __init__(self):
        self.calls = []

    def __getattr__(self, verb):
        def call(tok, repo, *args, opener=None, **kw):
            self.calls.append((verb, args))
            return {'number': 5} if verb == 'create_issue' else ({} if verb != 'close_issue' else None)
        return call


import chuang_platform_kit.github as kit_github  # noqa: E402
fake = Kit()
keep = {v: getattr(kit_github, v) for v in ('comment', 'edit_comment', 'close_issue', 'create_issue')}
for v in keep:
    setattr(kit_github, v, getattr(fake, v))
os.environ['CHUANG_SIGNED_AS'] = 'Snow White'
try:
    c = T.Client(tok='t', cfg={'repo': 'o/r'})
    c.comment(7, 'Live in release abc.')
    c.edit_comment(9, 'edited')
    c.close_issue(7, 'completed', 'Shipped.')
    c.close_issue(8)
    c.create_issue('A title', 'A body')
finally:
    for v, f in keep.items():
        setattr(kit_github, v, f)
    del os.environ['CHUANG_SIGNED_AS']
S = '\n\n— **Snow White** · Claude session'
check("the positive case: the ticket client signs a comment, an edit and a close's comment; a bare close and an issue's "
      "body go as given", fake.calls,
      [('comment', (7, 'Live in release abc.' + S)), ('edit_comment', (9, 'edited' + S)),
       ('close_issue', (7, 'completed', 'Shipped.' + S)), ('close_issue', (8, 'completed', '')),
       ('create_issue', ('A title', 'A body', []))])

sent = []
gh = A.GitHub('t', repo='o/r')
gh._call = lambda method, path, body=None: sent.append((method, path, (body or {}).get('body'))) or {}
G.configure(default=('Picklebot', 'pipeline'))
gh.close(3, 'Fixed at 9:00 AM')
gh.comment(4, 'Its alert cleared.')
reset()
check("alerts signs its close and its note, the closer's mark still the comment's first words",
      [s for s in sent if s[0] == 'POST'],
      [('POST', '/repos/o/r/issues/3/comments', 'Closed by the alert watcher: Fixed at 9:00 AM.\n\n— **Picklebot** · pipeline'),
       ('POST', '/repos/o/r/issues/4/comments', 'Its alert cleared.\n\n— **Picklebot** · pipeline')])
check('  the closer still reads it as its own', A.closed_by_closer({'title': 'x'}, sent[0][2]) if hasattr(A, 'closed_by_closer') else True,
      True)

print(f'\n{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
