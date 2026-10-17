#!/usr/bin/env python3
"""The PreToolUse hook plugins/gene-hooks/hooks/stamp-gate.sh: every line a session writes must open with the Pacific
stamp; a tool call after an unstamped line is refused, once per line (Gene, 10/14/26 12:43 AM PT; the newest-text-since-
the-prompt rule after a session found at 1:19 AM that the line just written is often not on disk at PreToolUse).

Run:  python tests/test-hooks-stamp-gate.py      (prints PASS/FAIL per case and `N failure(s)`)
"""
import itertools
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, '..', 'plugins', 'gene-hooks', 'hooks', 'stamp-gate.sh')
BASH = shutil.which('bash') or 'bash'
failures = []
_ids = itertools.count(1)


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ('' if ok else f'\n        got  {got!r}\n        want {want!r}'))
    if not ok:
        failures.append(label)


def line(kind, content, **kw):
    # Compact, as Claude Code writes its transcript (the gate matches "type":"text","text":" with no spaces).
    return json.dumps({'type': kind, 'uuid': f'u-{next(_ids)}', 'message': {'content': content}, **kw}, separators=(',', ':'))


def prompt(t='do it'):
    return line('user', t)


def meta(t='<system-reminder>'):
    return line('user', t, isMeta=True)


TOOL_RESULT = line('user', [{'type': 'tool_result', 'content': 'ok'}])


def tool_use():
    return line('assistant', [{'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'ls'}}])


def text(t):
    return line('assistant', [{'type': 'text', 'text': t}])


class Session:
    """A transcript and a state directory; each gate() call re-reads the transcript as it then is."""
    def __init__(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, 't.jsonl')
        self.sid = f's{next(_ids)}'

    def gate(self, lines, extra=None):
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        event = {'hook_event_name': 'PreToolUse', 'tool_name': 'Read', 'transcript_path': self.path,
                 'session_id': self.sid, **(extra or {})}
        r = subprocess.run([BASH, GATE], input=json.dumps(event), capture_output=True, text=True, timeout=20,
                           env=dict(os.environ, TMPDIR=self.dir, TEMP=self.dir))
        return r.returncode, r.stderr


def gate(lines, extra=None):
    return Session().gate(lines, extra)


def main():
    check('a stamped line before the call passes', gate([prompt(), text('10/17/26 12:45 AM PT Checking the deploy now:'), tool_use()])[0], 0)
    code, err = gate([prompt(), text('Checking the deploy now:'), tool_use()])
    check('an unstamped line is refused (exit 2) with the clock to copy', (code, 'Stamp first' in err, ' PT"' in err), (2, True, True))
    check('  bold and leading blank lines still count as a stamp', gate([prompt(), text('\n\n**10/17/26 9:05 PM PT** next:'), tool_use()])[0], 0)
    check('  a lowercase am/pm counts', gate([prompt(), text('10/17/26 9:05 pm PT: next'), tool_use()])[0], 0)
    check('  no space before AM counts', gate([prompt(), text('10/17/26 7:05AM PT go'), tool_use()])[0], 0)
    check('  the date is required before the time (Gene, 10/17/26: "add date too mm/dd/yy"; 5:56 AM: a dateless stamp "still old hook")',
          [gate([prompt(), text(t), tool_use()])[0] for t in ('10/17/26 5:21 AM PT next', '**10/17/2026 5:21 pm PT** go', '10/17/26 next', '5:21 AM PT next')],
          [0, 0, 2, 2])
    dated_err = gate([prompt(), text('no stamp here'), tool_use()])[1]
    check('  the refusal names the dated form', bool(re.search(r'starts "\d{1,2}/\d{1,2}/\d{2} \d{1,2}:\d{2} [AP]M PT"', dated_err)), True)

    # The line just written is often not on disk yet: the previous step's unstamped line is caught one call later.
    s = Session()
    late = [prompt(), text('Now the Jokes screen.'), tool_use(), TOOL_RESULT, tool_use()]
    check('an unstamped line from an earlier step of this turn is refused at the next call', s.gate(late)[0], 2)
    check('  but only once: the same line never blocks again', s.gate(late)[0], 0)
    check('  a new unstamped line after it is refused again', s.gate(late + [TOOL_RESULT, text('31 of 31 pass.'), tool_use()])[0], 2)
    check('the newest text decides: stamped after an unstamped one passes',
          gate([prompt(), text('unstamped'), TOOL_RESULT, text('10/17/26 12:46 AM PT stamped now'), tool_use()])[0], 0)
    check('a real prompt starts over: unstamped text from before it is not checked',
          gate([text('old unstamped words'), TOOL_RESULT, prompt('next please'), tool_use()])[0], 0)
    check('an isMeta injection is not a prompt: the unstamped line before it is still refused',
          gate([prompt(), text('unstamped'), tool_use(), TOOL_RESULT, meta(), tool_use()])[0], 2)
    check('a turn with no text yet passes', gate([prompt(), tool_use()])[0], 0)
    check('a helper (agent_id in the event) is exempt', gate([prompt(), text('unstamped'), tool_use()], {'agent_id': 'a1'})[0], 0)
    s = Session()
    os.remove(s.path) if os.path.exists(s.path) else None
    missing = {'hook_event_name': 'PreToolUse', 'transcript_path': os.path.join(s.dir, 'none.jsonl')}
    r = subprocess.run([BASH, GATE], input=json.dumps(missing), capture_output=True, text=True, timeout=20)
    check('a missing transcript passes', r.returncode, 0)

    s = Session()
    lines = [prompt(), text('10/17/26 12:45 AM PT ok'), tool_use()]
    t0 = time.perf_counter()
    for _ in range(3):
        s.gate(lines)
    print(f'  (each run with the harness: {(time.perf_counter() - t0) / 3 * 1000:.0f} ms)')
    print(f'\n{len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
