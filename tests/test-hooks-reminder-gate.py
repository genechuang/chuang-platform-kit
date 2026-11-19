#!/usr/bin/env python3
"""hooks/reminder-gate.py (Gene, 10/27/26 4:04 PM PT). Standard library only; the positive case first."""
import importlib.util
import json
import os
import sys

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'plugins', 'gene-hooks', 'hooks')
spec = importlib.util.spec_from_file_location('reminder_gate', os.path.join(HERE, 'reminder-gate.py'))
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)
FAILS = []


def check(label, got, want):
    print(('PASS  ' if got == want else 'FAIL  ') + label + ('' if got == want else f'\n        got  {got!r}\n        want {want!r}'))
    if got != want:
        FAILS.append(label)


def transcript(*texts):
    return [json.dumps({'type': 'user', 'message': {'content': 'hi'}})] + [
        json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': t}]}}) for t in texts]


def run(text, active=False):
    return G.decide({'transcript_path': 'x', 'stop_hook_active': active}, read=lambda p: transcript('working...', text))


bare = 'Still waiting on you: the ticket-start installer, `ship`, and the three open decisions.'
listed = ('Waiting on your two decisions:\n\n1. **The admin key:** delete it? On yes I remove it.\n'
          '2. **SMPDReport:** create a private repo? On yes I push it.')
check('the positive case: "the three open decisions" with nothing listed holds the turn and names the phrase',
      (run(bare).get('decision'), 'the three open decisions' in run(bare).get('reason', '') or 'Still waiting on you' in run(bare).get('reason', '')),
      ('block', True))
check('the same reminder with the items numbered goes through', run(listed), {})
check('the hold asks for one short follow-up, never the reply again (Gene, 11/5/26: a report arrived twice)',
      ('do NOT send it again' in run(bare).get('reason', ''), 'ONE short follow-up' in run(bare).get('reason', '')),
      (True, True))
check('each pointer phrase is caught; plain replies are not',
      ([bool(G.pointer(t)) for t in ('see above', 'the installer above', 'as listed earlier', 'your 2 questions',
                                      'still open for you')],
       [bool(G.pointer(t)) for t in ('The board shows three columns.', 'I fixed it above the fold', 'Two tests failed.')]),
      ([True] * 5, [False] * 3))
check('a second stop in the turn, an unreadable transcript, or no transcript lets go',
      (run(bare, active=True), G.decide({'transcript_path': 'nope'}, read=lambda p: (_ for _ in ()).throw(OSError())), G.decide({})),
      ({}, {}, {}))
check('only the final reply counts: a pointer in an earlier message of the turn does not hold it',
      G.decide({'transcript_path': 'x'}, read=lambda p: transcript(bare, 'All done; nothing waits on you.')), {})

print(f'{len(FAILS)} failure(s)' + (':\n' + '\n'.join(f'  - {f}' for f in FAILS) if FAILS else ''))
sys.exit(1 if FAILS else 0)
