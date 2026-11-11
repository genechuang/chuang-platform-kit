#!/usr/bin/env python3
"""chuang_sdlc.trailers: the plumbing trailers a renderer drops, and a trailer's values.

Pinned (the positive case first): each standard shape is plumbing (Session, the old Claude-Session and a bare session
URL, Ticket with a number or none, Pipeline-Parity, Post-Ship), case ignored; content is kept (a bullet, a claude.ai
page that is not a session, a Session line too long or with a URL, a Ticket line naming neither); a host's own shape
joins the standard ones only when it is passed; shape() escapes its key; values() reads one trailer in order.

Run:  python -B tests/test-sdlc-trailers.py
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))

from chuang_sdlc import trailers as T  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


print('the standard shapes')
check('the positive case: a Session trailer is plumbing', T.is_plumbing('Session: Alice'), True)
check('each standard shape', [T.is_plumbing(ln) for ln in (
    'Claude-Session: https://claude.ai/code/session_abc', 'https://claude.ai/code/session_abc',
    "  session: O'Neil-Smith Jr.", 'Ticket: #42', 'Ticket: #4, #5', 'ticket: none (notes)',
    'Pipeline-Parity: the other runner has it through the shared script', 'Post-Ship: worker refresh')],
      [True] * 8)
check('content stays', [T.is_plumbing(ln) for ln in (
    '- the button reads Save', 'https://claude.ai/code/artifact/abc', 'Session: ' + 'x' * 41,
    'Session: https://example.test/x', 'Ticket: later', 'Phone-Update: ship', '')],
      [False] * 7)

print("a host's own shape")
host = T.matcher(*T.STANDARD, T.shape('Phone-Update', r'\w+'))
check("the positive case: the host's shape is plumbing with it", (T.is_plumbing('Phone-Update: ship', host),
                                                                   T.is_plumbing('phone-update:  ship ', host)), (True, True))
check('and the standard shapes still are', T.is_plumbing('Ticket: #9', host), True)
check('a value off its shape is content', T.is_plumbing('Phone-Update: ship it now', host), False)
check('a raw regex counts as a shape', T.is_plumbing('Note: x', T.matcher(r'^\s*Note:.*$')), True)
check('only the shapes passed', T.is_plumbing('Session: Alice', T.matcher('ticket')), False)
check('shape() escapes its key', T.is_plumbing('AxB: 1', T.matcher(T.shape('A.B'))), False)

print('values')
msg = 'Subject\n\n- body\n\nPost-Ship: worker refresh\npost-ship:   worker rebuild  \nSession: Alice\n'
check('the positive case: every value in order, stripped', T.values(msg, 'Post-Ship'), ['worker refresh', 'worker rebuild'])
check('an empty trailer never borrows the next line', T.values('S\n\nPost-Ship:\nSession: Alice\n', 'Post-Ship'), [])
check('a CR line ending is stripped', T.values('S\r\n\r\nPost-Ship: worker refresh \r\n', 'Post-Ship'), ['worker refresh'])
check('a key it lacks', T.values(msg, 'Ticket'), [])
check('no message', T.values(None, 'Ticket'), [])

print('reverted commits')


def commit(sha, subject, reverts=None):
    return {'sha': sha, 'message': subject + (f'\n\nThis reverts commit {reverts}.\n' if reverts else '\n')}


A, B, C = 'a' * 40, 'b' * 40, 'c' * 40
check('the positive case: a commit a later one reverts is dropped, the revert and the others kept',
      [c['sha'] for c in T.unreverted([commit(A, 'a feature'), commit(B, 'kept'), commit(C, 'Revert "a feature"', A)])], [B, C])
check('a short sha in the revert line, or a short sha in the list, still names it',
      (T.reverted([commit(A, 'x'), commit(C, 'Revert', A[:8])]), T.reverted([commit(A[:8], 'x'), commit(C, 'Revert', A)])),
      ({A}, {A[:8]}))
check('any order: the list newest first, as git log gives it', T.reverted([commit(C, 'Revert', A), commit(A, 'x')]), {A})
check('a revert that is itself reverted takes nothing back: the change is there again',
      T.reverted([commit(A, 'x'), commit(B, 'Revert x', A), commit(C, 'Revert the revert', B)]), {B})
check('a revert of a commit outside the list drops nothing', T.unreverted([commit(B, 'kept'), commit(C, 'Revert', A)]),
      [commit(B, 'kept'), commit(C, 'Revert', A)])
check('no reverts, an empty list, a commit with no message or sha',
      (T.reverted([commit(A, 'x'), commit(B, 'y')]), T.unreverted([]), T.reverted([{'sha': A}, {'message': f'This reverts commit {A}.'}])),
      (set(), [], {A}))
check('a word that only looks like the line, or fewer than seven characters, is no revert',
      T.reverted([commit(A, 'x'), commit(B, 'see: this reverts commits later\n\nThis reverts commit aaaaaa.')]), set())

print(f'\n{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
