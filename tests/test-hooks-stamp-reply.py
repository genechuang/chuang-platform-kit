#!/usr/bin/env python3
"""The Stop hook plugins/gene-hooks/hooks/stamp-reply.py: a turn's last reply must open with the Pacific stamp.

Run:  python tests/test-hooks-stamp-reply.py      (prints PASS/FAIL per case and `N failure(s)`)
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, '..', 'plugins', 'gene-hooks', 'hooks', 'stamp-reply.py')
spec = importlib.util.spec_from_file_location('stamp_reply', PATH)
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)
failures = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ('' if ok else f'\n        got  {got!r}\n        want {want!r}'))
    if not ok:
        failures.append(label)


def line(kind, content, **kw):
    return json.dumps({'type': kind, 'message': {'content': content}, **kw})


PROMPT = line('user', 'what next?')
TOOL_RESULT = line('user', [{'type': 'tool_result', 'content': 'ok'}])
NOON = datetime(2026, 10, 13, 12, 49, tzinfo=timezone.utc)   # 5:49 AM PDT; utc-ok: the hook's clock input is an instant


def tail(lines):
    return lambda path: lines


# the positive case first: a stamped reply lets the turn end
check('the positive case: a reply opening and closing with the stamp ends the turn',
      S.decide({}, tail([PROMPT, line('assistant', [{'type': 'text', 'text': '10/13/26 5:49 AM PT\n\nDone.\n\n— 10/13/26 5:49 AM PT'}])]), NOON), None)
out = S.decide({}, tail([PROMPT, line('assistant', [{'type': 'text', 'text': '10/13/26 5:49 AM PT\n\nDone, pushed.'}])]), NOON)
check('  opening stamp but none at the bottom blocks, asking for the closing line (Gene, 10/14/26 1:54 AM)',
      ((out or {}).get('decision'), 'did not close' in (out or {}).get('reason', ''), '— 10/13/26 5:49 AM PT' in (out or {}).get('reason', '')),
      ('block', True, True))
check('  a one-line stamped reply counts as both (the recovery line ends the loop)',
      S.decide({}, tail([PROMPT, line('assistant', [{'type': 'text', 'text': '10/13/26 5:49 AM PT done, see above'}])]), NOON), None)
check('  the closing stamp may sit anywhere on the last line', S.ends_stamped('text\n\n*— 10/13/26 5:49 AM PT*\n'), True)
out = S.decide({}, tail([PROMPT, line('assistant', [{'type': 'text', 'text': '10/13/26 5:49 AM PT\n\nDone.\n\n— 5:49 AM PT'}])]), NOON)
check('  a closing line with the time alone blocks (Gene, 10/17/26 5:56 AM PT: "— 5:54 AM PT" passed 1.1.0, "still old hook. Why?")',
      ((out or {}).get('decision'), 'did not close' in (out or {}).get('reason', '')), ('block', True))
out = S.decide({}, tail([PROMPT, line('assistant', [{'type': 'text', 'text': 'Done, pushed.'}])]), NOON)
check('  an unstamped reply blocks with the clock now', (out or {}).get('decision'), 'block')
check('  the reason carries the Pacific stamp', '5:49 AM PT' in (out or {}).get('reason', ''), True)
check('  never twice in a row', S.decide({'stop_hook_active': True}, tail([PROMPT, line('assistant', [{'type': 'text', 'text': 'x'}])]), NOON), None)
check('  a turn with no text since the prompt ends', S.decide({}, tail([line('assistant', [{'type': 'text', 'text': 'old'}]), PROMPT,
                                                                      line('assistant', [{'type': 'tool_use', 'name': 'Bash'}]), TOOL_RESULT]), NOON), None)
check('  the last text of the turn is the one read, past tool calls',
      S.decide({}, tail([PROMPT, line('assistant', [{'type': 'text', 'text': '10/13/26 5:48 AM PT checking\n— 10/13/26 5:48 AM PT'}]), TOOL_RESULT,
                         line('assistant', [{'type': 'text', 'text': 'All done.'}])]), NOON)['decision'], 'block')
check('  bold and lower case stamps count', [bool(S.STAMP.match(t)) for t in ('**10/13/26 5:49 AM PT**', '10/13/26 12:01am: x', 'At 10/13/26 5:49 AM', '10/13/26 5:49')],
      [True, True, False, False])
check('  the date is required before the time (Gene, 10/17/26: "add date too mm/dd/yy"), opening and closing alike',
      ([bool(S.STAMP.match(t)) for t in ('10/17/26 5:21 AM PT', '**10/17/26 5:21 AM PT**', '10/17/2026 5:21 am', '10/17/26', '5:21 AM PT')],
       [S.ends_stamped(t) for t in ('done\n\n— 10/17/26 5:21 AM PT', 'done\n\n— 5:21 AM PT', 'done — 10/17/26 5:21 AM PT')]),
      ([True, True, True, False, False], [True, False, True]))
check('  Pacific time across DST: winter is UTC-8', S.pacific_now(datetime(2026, 12, 1, 20, 0, tzinfo=timezone.utc)).hour, 12)  # utc-ok: an instant in, Pacific out
def reply_at(stamp):
    return tail([PROMPT, line('assistant', [{'type': 'text', 'text': f'{stamp} PT\n\nDone.\n\n— {stamp} PT'}])])


# 1.4.0, Gene 10/20/26 12:19 AM PT: "Your time stamp is drifting badly from my iOS time" (a reply said 12:22 at 12:19);
# 12:22 AM: "reject timestamps more than 1 min ahead or behind".
check('  drift: a minute ahead or a minute behind passes',
      [S.decide({}, reply_at(s), NOON) for s in ('10/13/26 5:50 AM', '10/13/26 5:48 AM')], [None, None])
out = S.decide({}, reply_at('10/13/26 5:52 AM'), NOON)
check('  drift: three minutes ahead blocks, naming the clock to copy',
      (out['decision'], '3 minute(s) ahead' in out['reason'], '"— 10/13/26 5:49 AM PT"' in out['reason']), ('block', True, True))
out = S.decide({}, reply_at('10/13/26 5:47 AM'), NOON)
check('  drift: two minutes behind blocks', (out['decision'], '2 minute(s) behind' in out['reason']), ('block', True))
check('  drift: across midnight by the date', S.drift('x\n— 10/12/26 11:59 PM PT', S.pacific_now(
    datetime(2026, 10, 13, 7, 0, tzinfo=timezone.utc))), None)  # utc-ok: 12:00 AM PDT 10/13, a minute after
check('  drift: a four-digit year is not judged', S.drift('x\n— 10/13/2026 9:00 AM PT', S.pacific_now(NOON)), None)

# the real script: any error exits 0 with no output (never traps a session)
r = subprocess.run([sys.executable, PATH], input='not json', capture_output=True, text=True)
check('  bad input: exit 0, nothing printed', (r.returncode, r.stdout), (0, ''))
d = tempfile.mkdtemp()
tp = os.path.join(d, 't.jsonl')
with open(tp, 'w', encoding='utf-8') as f:
    f.write('\n'.join([PROMPT, line('assistant', [{'type': 'text', 'text': 'unstamped'}])]) + '\n')
r = subprocess.run([sys.executable, PATH], input=json.dumps({'transcript_path': tp}), capture_output=True, text=True)
check('  end to end: a real transcript file blocks', json.loads(r.stdout or '{}').get('decision'), 'block')
print(f'\n{len(failures)} failure(s)' + (':' if failures else ''))
for f in failures:
    print(f'  - {f}')
sys.exit(1 if failures else 0)
