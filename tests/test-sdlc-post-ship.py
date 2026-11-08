#!/usr/bin/env python3
"""chuang_sdlc.post_ship: the Post-Ship: steps a release's commits ask for, and their lines in the notes.

Pinned (the positive case first): an allowed step is read with its sha and words; a step off the allow-list is kept,
not allowed; the same step twice is once, in commit order; a trailer repeating the job's own script is read without
it; other lines are ignored; the label; a line a step for Slack and for markdown, linked when it has a url, the host's
words for a step not allowed; failed() is everything that did not pass.

Run:  python -B tests/test-sdlc-post-ship.py
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))

from chuang_sdlc import post_ship as S  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


ALLOWED = {('worker', ('refresh',)): 'the cache built again', ('worker', ('rebuild', '--all')): 'every index again'}
COMMITS = [
    {'sha': '1a2b3c4d5e6f', 'message': 'One\n\n- body\n\nPost-Ship: worker refresh\nTicket: #42'},
    {'sha': '9f8e7d6c5b4a', 'message': 'Two\n\nPost-Ship: worker -m run.py rebuild --all\nPost-Ship: worker refresh\n'
                                       'post-ship: other wipe'},
]
found = S.steps(COMMITS, ALLOWED, script_of=lambda job: ('-m', 'run.py') if job == 'worker' else ())

print('steps')
check('the positive case: an allowed step with its sha and words', found[0],
      {'job': 'worker', 'args': ['refresh'], 'sha': '1a2b3c4d5e6f', 'what': 'the cache built again', 'allowed': True})
check("the job's own script dropped, the same step once, the unlisted kept",
      [(s['job'], s['args'], s['allowed']) for s in found],
      [('worker', ['refresh'], True), ('worker', ['rebuild', '--all'], True), ('other', ['wipe'], False)])
check('no script_of reads the arguments as written',
      S.steps(COMMITS[1:], ALLOWED)[0]['args'], ['-m', 'run.py', 'rebuild', '--all'])
check('no commits', S.steps(None, ALLOWED), [])
check('the label', S.label(found[1]), 'worker rebuild --all')

print('lines and failed')
done = [dict(found[0], result='passed', seconds=74, url='https://console.example.test/e1'),
        dict(found[2], result='refused', seconds=None, url='')]
check('the positive case: a line a step for Slack', S.lines(done),
      ['• <https://console.example.test/e1|worker refresh>: ✅ passed in 74 s, the cache built again (1a2b3c4d)',
       '• `other wipe`: 🚫 refused, not on the allow-list, so not run (9f8e7d6c)'])
check('markdown, and the host\'s words for a step not allowed',
      S.lines(done, markdown=True, not_allowed='not listed'),
      ['- [`worker refresh`](https://console.example.test/e1): ✅ passed in 74 s, the cache built again (1a2b3c4d)',
       '- `other wipe`: 🚫 refused, not listed (9f8e7d6c)'])
check('an unknown result is red', S.lines([dict(found[0], result='did not start (x)', seconds=None)])[0].split(': ')[1][:1],
      '🔴')
check("an allowed step with no words is never called off the allow-list",
      S.lines([dict(found[0], what='', result='passed', seconds=3, url='')]),
      ['• `worker refresh`: ✅ passed in 3 s (1a2b3c4d)'])
check('failed: everything not passed', [s['job'] for s in S.failed(done)], ['other'])

print(f'\n{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
