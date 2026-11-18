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


os.environ['PROMISE_JUDGE_OFF'] = '1'   # the regex cases never reach a real model; the judge's cases fake it
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
    "Still open: #1791, built and on dev but not checked or pushed to the dock; that's my next step.",   # Black Widow, 3:55 AM PT
    'My next step is the dock push.',
    "I'm going to build the gate.",
    "That saves about 139 s. I'm filing it as a ticket now and will build it next.",   # Snow White, 4:41 AM PT (#1815)
    'The fix is clear, and will build it next.',
    "I'm sending Black Widow the finding now.",
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
    'Examples: "now I\'ll start #N", "moving on to #N", "that\'s my next step".',   # 1.30.3: the gate's own report
    'It holds replies like `Starting #1801 now.` once.',
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


def test_a_stamp_hold_still_carries_the_promise():
    """a reply held for its stamp would be answered by one short line and the turn would end: the hold's reason carries the promise too"""
    reply = "No stamp here. Now I'll start #1801."
    line = json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': reply}]}})
    event = {'transcript_path': 'x', 'stop_hook_active': False}
    out = S.with_promise({'decision': 'block', 'reason': 'Your reply did not open with the Pacific clock stamp.'}, event,
                         lambda p: [line])
    check('carried', ('Now I\'ll start #1801' in out['reason'], out['reason'].startswith('Your reply did not open')), (True, True))
    check('no promise, no change', S.with_promise({'decision': 'block', 'reason': 'r'}, event,
                                                  lambda p: [json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'Done.'}]}})])['reason'], 'r')
    check('nothing held stays nothing', S.with_promise(None, event, lambda p: [line]), None)


class Ran:
    """subprocess.run's fake: records the call, answers `out` (or raises `boom`)."""
    def __init__(self, out='NONE', code=0, boom=None):
        self.out, self.code, self.boom, self.calls = out, code, boom, []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if self.boom:
            raise self.boom
        return type('R', (), {'returncode': self.code, 'stdout': self.out, 'stderr': ''})()


def judged(reply, run):
    os.environ.pop('PROMISE_JUDGE_OFF', None)
    try:
        return G.check(reply, run=run)
    finally:
        os.environ['PROMISE_JUDGE_OFF'] = '1'


def test_haiku_holds_what_the_regex_misses():
    """the positive case (1.32.0, #1816): a phrasing no pattern knows, judged a promise by Haiku, is held with its words"""
    reply = 'The finding is clear. Onto the warm fix right after this, then the roster sort.'
    run = Ran('PROMISE: Onto the warm fix right after this, then the roster sort.')
    got = judged(reply, run)
    check('held', (got or {}).get('decision'), 'block')
    check('its words quoted', 'Onto the warm fix' in got['reason'], True)
    cmd, kw = run.calls[0]
    check('Haiku, one turn, no settings, the text on stdin', ('haiku' in cmd, '--setting-sources' in cmd, '-p' in cmd,
                                                              'Onto the warm fix' in kw['input'], kw['timeout']),
          (True, True, True, True, G.JUDGE_LIMIT))
    check('the child turns its own judge off', kw['env'].get('PROMISE_JUDGE_OFF'), '1')


def test_haiku_none_or_failure_lets_it_go():
    """NONE, a non-zero exit, an odd answer, a timeout or a missing claude never holds the reply"""
    reply = 'Building passed next to the warm.'
    for label, run in [('NONE', Ran('NONE')), ('exit 1', Ran('PROMISE: x', code=1)), ('odd answer', Ran('maybe?')),
                       ('timeout', Ran(boom=TimeoutError())), ('no claude', Ran(boom=FileNotFoundError())),
                       ('PROMISE: NONE (1.32.1)', Ran('PROMISE: NONE')), ('PROMISE: none.', Ran('PROMISE: none.')),
                       ('a sentence not in the reply (1.32.1)', Ran('PROMISE: I will start #12 now.'))]:
        check(f'let go: {label}', judged(reply, run), None)
    wait = "Building passed next to the warm. The profile follows once the smoke lands; I'll report it then."
    check('let go: a wait Haiku called a promise',
          judged(wait, Ran("PROMISE: The profile follows once the smoke lands; I'll report it then.")), None)


def test_no_candidate_word_calls_no_model():
    """a plain report with none of the candidate words costs no model call"""
    run = Ran('PROMISE: x')
    check('let go', judged('#1814 is filed. The suite passed.', run), None)
    check('no call', run.calls, [])


def test_the_regex_answers_first():
    """a promise the regex holds never waits on the model"""
    run = Ran('NONE')
    check('held by the regex', (judged("Now I'll start #1801.", run) or {}).get('decision'), 'block')
    check('no call', run.calls, [])


def test_quotes_and_the_closing_stamp_are_not_judged():
    """quoted words and the closing stamp line are left out of what Haiku reads"""
    run = Ran('NONE')
    judged('Examples: "now I will start it". Done for now.\n\n— 11/18/26 7:41 AM PT · ticket: #1816', run)
    text = run.calls[0][1]['input'] if run.calls else ''
    check('quote blanked, stamp dropped', ('now I will start it' in text, '7:41 AM PT' in text), (False, False))


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            fn()
    print(f'{len(FAILS)} failure(s)' + (': ' + '; '.join(FAILS) if FAILS else ''))
    sys.exit(1 if FAILS else 0)
