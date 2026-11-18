#!/usr/bin/env python3
"""plugins/gene-hooks/hooks/promise-gate.py and its call from stamp-reply.py (1.30.0; SMADPickleBot #1802, Gene 11/18/26:
a session that says it is starting the next ticket now does not end its turn without starting it). Standard library
only; the positive case first. The replies are the shapes sessions write."""
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


G = load('promise_gate', 'promise-gate.py')
S = load('stamp_reply', 'stamp-reply.py')
FAILS = []


def check(label, got, want):
    print(('PASS  ' if got == want else 'FAIL  ') + label + ('' if got == want else f'\n        got  {got!r}\n        want {want!r}'))
    if got != want:
        FAILS.append(label)


HELD = [
    "#1800 is on the dock. Now I'll start #1801.",
    'Next I will build the PostHog sweep.',
    "I'll now pick up #1791.",
    'Done with #1790, moving on to #1793.',
    'Starting #1801 now.',
    "I'm on #1801 next.",
    "I'm starting the warm fix now.",
    'Next up: #1791, the roster sort.',
    "I'll start on #1793 next.",
    'Next I start #1793 your way: one ticket per kind.',   # Snow White, 11/18/26 3:27 AM PT, then nothing until asked
    "Meanwhile I'm starting #1793.",
]
LET_GO = [
    'The receipt and the profile follow when it lands.',
    "I'll report its exit, the receipt and the profile when they arrive.",
    "#1801 holds until #1794 ships, then I'll bring you the list.",
    'Reply yes and I will make the settings write.',
    "Once you say go, I'll start #1801.",
    "I'll build it after #1794 is live.",
    'Nothing else of mine is queued in the Shipyard.',
    '#1708 stays blocked until 11/25/26.',
    'It waits for your next ship word or the next automatic ship.',
    'Black Widow is on #1791 next.',
]


def test_a_promise_is_held():
    """the positive case: each reply promising work starting now, with no condition, is held, its sentence quoted"""
    for r in HELD:
        got = G.check(r)
        check(f'held: {r}', (got or {}).get('decision'), 'block')
    check('the reason quotes it and says start it', ('Starting #1801 now' in G.check('Starting #1801 now.')['reason'],
                                                     'first tool call' in G.check('Starting #1801 now.')['reason']), (True, True))


def test_a_report_or_a_wait_goes_through():
    """a reply that reports, waits on something named, or says what another session does is never held"""
    for r in LET_GO:
        check(f'let go: {r}', G.check(r), None)


def test_a_long_reply_is_judged_by_its_sentences():
    """a promise anywhere in a long reply is found; a conditional one beside it does not hide it"""
    reply = ('11/18/26 4:00 AM PT · ticket: #1800: #1800 is on the dock.\n\n- Tests passed.\n'
             "The profile follows when it lands. Now I'll start #1801.\n\n— 11/18/26 4:00 AM PT · ticket: #1800")
    check('found in a long reply', (G.check(reply) or {}).get('decision'), 'block')


def test_stamp_reply_runs_it_after_its_own_checks():
    """stamp-reply's promised() loads the gate from its own folder; a held stop's second stop always ends"""
    check('promised', (S.promised("Now I'll start #1801.") or {}).get('decision'), 'block')
    check('nothing promised', S.promised('All done.'), None)
    check('a held turn ends the second time', S.decide({'stop_hook_active': True, 'transcript_path': ''}, lambda p: []), None)


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            fn()
    print(f'{len(FAILS)} failure(s)' + (': ' + '; '.join(FAILS) if FAILS else ''))
    sys.exit(1 if FAILS else 0)
