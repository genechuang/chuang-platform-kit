#!/usr/bin/env python3
"""plugins/gene-hooks/hooks/evidence-gate.py and its call from stamp-reply.py (1.10.0; Gene, 10/29/26). Standard
library only; the positive case first. Session names here are made up: the hook names no one."""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, '..', 'plugins', 'gene-hooks', 'hooks')


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HOOKS, file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = load('evidence_gate', 'evidence-gate.py')
S = load('stamp_reply', 'stamp-reply.py')
FAILS = []
NAMES = ['Red Fox', 'Blue Owl']


def check(label, got, want):
    print(('PASS  ' if got == want else 'FAIL  ') + label + ('' if got == want else f'\n        got  {got!r}\n        want {want!r}'))
    if got != want:
        FAILS.append(label)


def tool(name, **inp):
    return json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': name, 'input': inp}]}})


def said(text):
    return json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': text}]}})


def asked(text='why'):
    return json.dumps({'type': 'user', 'message': {'content': text}})


def result():
    return json.dumps({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'content': 'ok'}]}})


def block(reply, lines, names=NAMES):
    return (G.check(reply, lines, names) or {}).get('decision')


owner = "The court booking job is Red Fox's, so I'll file it for them."
check("the positive case: naming a session's work with no git log this turn holds it",
      block(owner, [asked(), tool('Bash', command='gcloud logging read x'), result(), said(owner)]), 'block')
check('the same after a git log of the file goes through',
      block(owner, [asked(), tool('Bash', command='git log --format=%h -8 -- booking.py'), result(), said(owner)]), None)
check('a git log in an earlier turn does not count: the turn starts at the last typed prompt',
      block(owner, [asked(), tool('Bash', command='git log -3'), result(), said('ok'), asked('who built it'), said(owner)]), 'block')
check("each ownership form is caught; a session's note, review or receipt is not",
      ([bool(block(t, [asked()])) for t in ("Red Fox's booking job", 'built by Blue Owl', 'Red Fox owns the triage',
                                           'it belongs to Blue Owl', "the runner is Blue Owl's")],
       [bool(block(t, [asked()])) for t in ("Red Fox's landing note", "Blue Owl's review came back",
                                           'I sent Red Fox the note', "Blue Owl's receipt")]),
      ([True] * 5, [False] * 4))
check('no session names (outside a repo): the ownership check is off', block(owner, [asked(), said(owner)], names=[]), None)
live = '#553 is live on web and iPhone.'
check('a production claim with nothing read this turn holds it', block(live, [asked(), said(live)]), 'block')
check('the same after any read (Bash, Read, an MCP call) goes through',
      [block(live, [asked(), tool(t, command='gh run view 1'), result(), said(live)]) for t in ('Bash', 'Read', 'mcp__x__search')],
      [None, None, None])
check('a plain reply passes', block('Committed and pushed to dock.', [asked(), said('Committed and pushed to dock.')]), None)
check("the session names are the repo's Session trailers, longest first; git failing is none",
      (G.session_names(run=lambda *a, **k: type('R', (), {'returncode': 0, 'stdout': 'Red Fox\n\nBlue Owl\nRed Fox\n'})()),
       G.session_names(run=lambda *a, **k: type('R', (), {'returncode': 128, 'stdout': ''})())),
      (['Blue Owl', 'Red Fox'], []))
stamped = f"10/29/26 1:40 AM PT. {live}\n\n— 10/29/26 1:40 AM PT"
check('through stamp-reply.py: a stamped reply with an unread production claim is held; read, it passes',
      ((S.evidence(stamped, [asked(), said(stamped)], names=NAMES) or {}).get('decision'),
       S.evidence(stamped, [asked(), tool('Bash', command='gh run view'), result(), said(stamped)], names=NAMES)),
      ('block', None))

print(f'{len(FAILS)} failure(s)')
sys.exit(1 if FAILS else 0)
