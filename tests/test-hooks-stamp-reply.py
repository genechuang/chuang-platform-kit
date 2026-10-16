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
      S.decide({}, tail([PROMPT, line('assistant', [{'type': 'text', 'text': '5:49 AM PT\n\nDone.\n\n— 5:49 AM PT'}])]), NOON), None)
out = S.decide({}, tail([PROMPT, line('assistant', [{'type': 'text', 'text': '5:49 AM PT\n\nDone, pushed.'}])]), NOON)
check('  opening stamp but none at the bottom blocks, asking for the closing line (Gene, 10/14/26 1:54 AM)',
      ((out or {}).get('decision'), 'did not close' in (out or {}).get('reason', ''), '— 5:49 AM PT' in (out or {}).get('reason', '')),
      ('block', True, True))
check('  a one-line stamped reply counts as both (the recovery line ends the loop)',
      S.decide({}, tail([PROMPT, line('assistant', [{'type': 'text', 'text': '5:49 AM PT done, see above'}])]), NOON), None)
check('  the closing stamp may sit anywhere on the last line', S.ends_stamped('text\n\n*— 5:49 AM PT*\n'), True)
out = S.decide({}, tail([PROMPT, line('assistant', [{'type': 'text', 'text': 'Done, pushed.'}])]), NOON)
check('  an unstamped reply blocks with the clock now', (out or {}).get('decision'), 'block')
check('  the reason carries the Pacific stamp', '5:49 AM PT' in (out or {}).get('reason', ''), True)
check('  never twice in a row', S.decide({'stop_hook_active': True}, tail([PROMPT, line('assistant', [{'type': 'text', 'text': 'x'}])]), NOON), None)
check('  a turn with no text since the prompt ends', S.decide({}, tail([line('assistant', [{'type': 'text', 'text': 'old'}]), PROMPT,
                                                                      line('assistant', [{'type': 'tool_use', 'name': 'Bash'}]), TOOL_RESULT]), NOON), None)
check('  the last text of the turn is the one read, past tool calls',
      S.decide({}, tail([PROMPT, line('assistant', [{'type': 'text', 'text': '5:48 AM PT checking\n— 5:48 AM PT'}]), TOOL_RESULT,
                         line('assistant', [{'type': 'text', 'text': 'All done.'}])]), NOON)['decision'], 'block')
check('  bold and lower case stamps count', [bool(S.STAMP.match(t)) for t in ('**5:49 AM PT**', '12:01am: x', 'At 5:49 AM', '5:49')],
      [True, True, False, False])
check('  Pacific time across DST: winter is UTC-8', S.pacific_now(datetime(2026, 12, 1, 20, 0, tzinfo=timezone.utc)).hour, 12)  # utc-ok: an instant in, Pacific out
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
