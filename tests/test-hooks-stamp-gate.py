#!/usr/bin/env python3
"""The PreToolUse hook plugins/gene-hooks/hooks/stamp-gate.sh: every line a session writes must open with the Pacific
stamp; a tool call after an unstamped line is refused, up to three times per line (Gene, 10/14/26 12:43 AM PT; the newest-text-since-
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
        self.activity_path = os.path.join(self.dir, 'ticket-activity.log')
        self.sid = f's{next(_ids)}'

    def activity(self):
        """The activity lines this session's gate appended, without their epoch: [(session, tickets)]."""
        if not os.path.exists(self.activity_path):
            return []
        return [tuple(l.split()[1:]) for l in open(self.activity_path, encoding='utf-8').read().splitlines()]

    def gate(self, lines, extra=None, wait='', lands=None):
        """Run the gate over `lines`; `lands` (seconds, line) appends a line while it waits, as Claude Code does."""
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        event = {'hook_event_name': 'PreToolUse', 'tool_name': 'Read', 'transcript_path': self.path,
                 'session_id': self.sid, **(extra or {})}
        # UTF-8 both ways, as Claude Code reads a hook (the refusals name the "·" form; Windows' default is cp1252).
        p = subprocess.Popen([BASH, GATE], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding='utf-8', errors='replace',
                             env=dict(os.environ, TMPDIR=self.dir, TEMP=self.dir, STAMP_GATE_WAIT=wait,
                                      TICKET_ACTIVITY_LOG=self.activity_path))
        if lands:
            p.stdin.write(json.dumps(event))
            p.stdin.close()
            time.sleep(lands[0])
            with open(self.path, 'a', encoding='utf-8') as f:
                f.write(lands[1] + '\n')
            p.wait(timeout=20)
            return p.returncode, p.stderr.read()
        _, err = p.communicate(json.dumps(event), timeout=20)
        return p.returncode, err

    def state(self):
        path = os.path.join(self.dir, 'stamp-gate-' + self.sid)
        return open(path, encoding='utf-8').read() if os.path.exists(path) else ''


def gate(lines, extra=None):
    return Session().gate(lines, extra)


def main():
    check('a stamped line before the call passes', gate([prompt(), text('10/17/26 12:45 AM PT · ticket: #671 Checking the deploy now:'), tool_use()])[0], 0)
    code, err = gate([prompt(), text('Checking the deploy now:'), tool_use()])
    # 1.13.1: the refusal is ASCII, the separator named as "the middle dot (U+00B7)": a session reading the hook's bytes under
    # a non-UTF-8 code page (GBK read C2 B7 as 路) copied the mangled dot into every stamp (Gene, 11/2/26 12:41 PM PT).
    check('an unstamped line is refused (exit 2) with the clock to copy, in ASCII', (code, 'Stamp first' in err, ' PT", the middle dot (U+00B7) and "ticket: #N"' in err,
                                                                                    all(ord(c) < 128 for c in err)), (2, True, True, True))
    check('  bold and leading blank lines still count as a stamp', gate([prompt(), text('\n\n**10/17/26 9:05 PM PT · ticket: #671** next:'), tool_use()])[0], 0)
    check('  a lowercase am/pm counts', gate([prompt(), text('10/17/26 9:05 pm PT · ticket: none: next'), tool_use()])[0], 0)
    check('  no space before AM counts', gate([prompt(), text('10/17/26 7:05AM PT | ticket: #12 go'), tool_use()])[0], 0)
    check('  the date is required before the time (Gene, 10/17/26: "add date too mm/dd/yy"; 5:56 AM: a dateless stamp "still old hook")',
          [gate([prompt(), text(t), tool_use()])[0] for t in ('10/17/26 5:21 AM PT · ticket: #1 next', '**10/17/2026 5:21 pm PT** · ticket: none go',
                                                              '10/17/26 next', '5:21 AM PT · ticket: #1 next')],
          [0, 0, 2, 2])
    dated_err = gate([prompt(), text('no stamp here'), tool_use()])[1]
    check('  the refusal names the dated form', bool(re.search(r'starts "\d{1,2}/\d{1,2}/\d{2} \d{1,2}:\d{2} [AP]M PT", the middle dot', dated_err)), True)

    # 1.13.0 (SMADPickleBot #671; Gene, 10/31/26 11:45 AM PT: "also write the ticket # you're working on - if none, then
    # say ticket: none"): the stamp's line names the ticket.
    def raw(t):   # Claude Code writes its transcript with the middle dot as itself, json.dumps as ·: both count
        return json.dumps({'type': 'assistant', 'uuid': f'u-{next(_ids)}', 'message': {'content': [{'type': 'text', 'text': t}]}},
                          separators=(',', ':'), ensure_ascii=False)
    check('#671: a stamp naming its ticket passes, one, several or none, the dot written raw or escaped',
          [gate([prompt(), line, tool_use()])[0] for line in (
              text('10/31/26 11:45 AM PT · ticket: #667: next'), raw('10/31/26 11:45 AM PT · ticket: #667: next'),
              text('10/31/26 11:45 AM PT · ticket: #669, #244: both'), raw('10/31/26 11:45 AM PT · ticket: none: a question'),
              text('**10/31/26 11:45 AM PT — Ticket: #667**'))],
          [0, 0, 0, 0, 0])
    # 1.14.0 (SMADPickleBot #858; Gene, 11/3/26 5:53 AM PT): each passing call appends "<epoch> <session> <tickets>"
    # to the activity log, the tickets the stamp names and none of the ones later on its line; a refusal appends nothing.
    s = Session()
    for t in ('11/03/26 5:55 AM PT · ticket: #858: and #850 later on the line', '11/03/26 5:55 AM PT · ticket: #857, #858: both',
              '11/03/26 5:55 AM PT · ticket: none: a question', 'no stamp'):
        s.gate([prompt(), text(t), tool_use()])
    before = time.time()
    s.gate([prompt(), text('11/03/26 5:55 AM PT · ticket: #12 go'), tool_use()])
    epoch = int(open(s.activity_path, encoding='utf-8').read().splitlines()[-1].split()[0])
    check('#858: the activity line per passing call, the stamp\'s tickets alone, a refusal none, the epoch now',
          (s.activity(), before - 2 <= epoch <= time.time() + 2),
          ([(s.sid, '858'), (s.sid, '857,858'), (s.sid, 'none'), (s.sid, '12')], True))
    code, err = gate([prompt(), text('10/31/26 11:45 AM PT: next'), tool_use()])
    check('  a stamp with no ticket is refused, naming the form with the clock',
          (code, 'Name the ticket' in err, bool(re.search(r'starts "\d{1,2}/\d{1,2}/\d{2} \d{1,2}:\d{2} [AP]M PT", the middle dot \(U\+00B7\) and "ticket: #N"', err)),
           all(ord(c) < 128 for c in err)),
          (2, True, True, True))
    check('  the ticket is on the stamp\'s own line, and is a number or none',
          [gate([prompt(), text(t), tool_use()])[0] for t in ('10/31/26 11:45 AM PT\nticket: #667', '10/31/26 11:45 AM PT · ticket: TBD',
                                                              '10/31/26 11:45 AM PT · the ticket is #667')],
          [2, 2, 2])
    s = Session()
    bare = [prompt(), text('10/31/26 11:45 AM PT next'), tool_use()]
    check('  refused three times, then let through, as a missing stamp is', [s.gate(bare)[0] for _ in range(4)], [2, 2, 2, 0])

    # The line just written is often not on disk yet: the previous step's unstamped line is caught one call later.
    s = Session()
    late = [prompt(), text('Now the Jokes screen.'), tool_use(), TOOL_RESULT, tool_use()]
    check('an unstamped line from an earlier step of this turn is refused at the next call', s.gate(late)[0], 2)
    check('  and again, and a third time: a retry without a stamped line is still refused (1.3.0; once let it through)',
          [s.gate(late)[0], s.gate(late)[0]], [2, 2])
    check('  but not a fourth: the cap keeps a stuck session moving', s.gate(late)[0], 0)
    check('  a new unstamped line after it is refused again', s.gate(late + [TOOL_RESULT, text('31 of 31 pass.'), tool_use()])[0], 2)
    check('the newest text decides: stamped after an unstamped one passes',
          gate([prompt(), text('unstamped'), TOOL_RESULT, text('10/17/26 12:46 AM PT · ticket: #2 stamped now'), tool_use()])[0], 0)
    check('a real prompt starts over: unstamped text from before it is not checked',
          gate([text('old unstamped words'), TOOL_RESULT, prompt('next please'), tool_use()])[0], 0)
    check('an isMeta injection is not a prompt: the unstamped line before it is still refused',
          gate([prompt(), text('unstamped'), tool_use(), TOOL_RESULT, meta(), tool_use()])[0], 2)
    # 1.5.0: the newest line lands after the gate starts; a miss is read again before it is refused.
    s = Session()
    behind = [prompt(), text('unstamped'), tool_use(), TOOL_RESULT, tool_use()]
    # The line lands 0.5 s in, after the gate's first read even on a loaded box (its start alone took 375 ms with two
    # builds running, 11/1/26, and a 0.2 s landing beat the first read), and before the first 0.8 s wait ends.
    code, _ = s.gate(behind, wait='0.8 0.8', lands=(0.5, text('10/20/26 1:56 PM PT · ticket: #3 stamped, still landing')))
    check('a stamped line that lands while the gate waits passes, and is counted as a rescue',
          (code, s.state().startswith('rescued ')), (0, True))
    t0 = time.perf_counter()
    code, _ = Session().gate(behind, wait='0.2 0.2')
    check('  a miss nothing rescues is still refused, after the wait', (code, time.perf_counter() - t0 >= 0.4), (2, True))
    check('  a stamped line passes at once, with no wait', Session().gate([prompt(), text('10/20/26 1:56 PM PT · ticket: none ok'), tool_use()], wait='5')[0], 0)
    check('a turn with no text yet passes', gate([prompt(), tool_use()])[0], 0)
    check('a helper (agent_id in the event) is exempt', gate([prompt(), text('unstamped'), tool_use()], {'agent_id': 'a1'})[0], 0)
    s = Session()
    os.remove(s.path) if os.path.exists(s.path) else None
    missing = {'hook_event_name': 'PreToolUse', 'transcript_path': os.path.join(s.dir, 'none.jsonl')}
    r = subprocess.run([BASH, GATE], input=json.dumps(missing), capture_output=True, text=True, timeout=20)
    check('a missing transcript passes', r.returncode, 0)

    # 1.4.0, Gene 10/20/26 12:19 AM PT: "Your time stamp is drifting badly from my iOS time" (12:22 written at 12:19).
    def at(minutes):
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)  # utc-ok: converted to Pacific by the DST rule below
        y = now.year
        march = datetime(y, 3, 8, 10, tzinfo=timezone.utc)  # utc-ok: the DST switch's instant, compared in UTC
        nov = datetime(y, 11, 1, 9, tzinfo=timezone.utc)  # utc-ok: the DST switch's instant, compared in UTC
        dst = march + timedelta(days=(6 - march.weekday()) % 7) <= now < nov + timedelta(days=(6 - nov.weekday()) % 7)
        t = now + timedelta(hours=-7 if dst else -8, minutes=minutes)
        return f"{t.month}/{t.day}/{t:%y} " + t.strftime('%I:%M %p').lstrip('0')
    check('drift: the clock now passes', gate([prompt(), text(f'{at(0)} PT · ticket: #671 ok'), tool_use()])[0], 0)
    check('  an older stamp passes here (the line before a call is often not on disk yet; Stop judges behind)',
          gate([prompt(), text(f'{at(-40)} PT · ticket: #671 ok'), tool_use()])[0], 0)
    code, err = gate([prompt(), text(f'**{at(5)} PT** next'), tool_use()])
    check('  five minutes ahead is refused, naming the stamp and the clock',
          (code, 'Stamp ahead of the clock' in err, at(5) in err), (2, True, True))

    s = Session()
    lines = [prompt(), text('10/17/26 12:45 AM PT · ticket: #671 ok'), tool_use()]
    t0 = time.perf_counter()
    for _ in range(3):
        s.gate(lines)
    print(f'  (each run with the harness: {(time.perf_counter() - t0) / 3 * 1000:.0f} ms)')
    print(f'\n{len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
