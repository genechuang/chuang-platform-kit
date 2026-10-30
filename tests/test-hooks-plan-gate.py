#!/usr/bin/env python3
"""plugins/gene-hooks/hooks/plan-gate.py and its call from stamp-gate.sh (1.11.0; Gene, 10/30/26 12:34 AM PT: "update
your steps progress in real time"). Standard library only; the positive case first."""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, '..', 'plugins', 'gene-hooks', 'hooks')
BASH = shutil.which('bash') or 'bash'
spec = importlib.util.spec_from_file_location('plan_gate', os.path.join(HOOKS, 'plan-gate.py'))
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
FAILS = []


def check(label, got, want):
    print(('PASS  ' if got == want else 'FAIL  ') + label + ('' if got == want else f'\n        got  {got!r}\n        want {want!r}'))
    if got != want:
        FAILS.append(label)


def call(name, **inp):
    return json.dumps({'type': 'assistant', 'uuid': f'u{name}{len(inp)}',
                       'message': {'content': [{'type': 'tool_use', 'id': 'x', 'name': name, 'input': inp}]}},
                      separators=(',', ':'))


PLAN = call('mcp__ccd_turn__update_plan', items=[{'content': 'Fix the move', 'status': 'in_progress'},
                                                  {'content': 'Ship it', 'status': 'pending'}])
WORK = [call('Bash', command='ls') for _ in range(P.LIMIT)]
EVENT = {'tool_name': 'Edit', 'session_id': 's1'}

stale = P.verdict(EVENT, [PLAN] + WORK)
check('the positive case: a list with a step open and LIMIT calls since it changed refuses, naming the step',
      (bool(stale), '"Fix the move"' in (stale or '')), (True, True))
check('one call short of LIMIT goes through', P.verdict(EVENT, [PLAN] + WORK[:-2]), None)
check('an advance in between resets the count',
      P.verdict(EVENT, [PLAN] + WORK[:6] + [call('mcp__ccd_turn__update_plan', advance=True)] + WORK[:6]), None)
check('the plan call itself and wrap_up always go through',
      (P.verdict({'tool_name': 'mcp__ccd_turn__update_plan'}, [PLAN] + WORK),
       P.verdict({'tool_name': 'mcp__ccd_turn__wrap_up'}, [PLAN] + WORK)), (None, None))
done = call('mcp__ccd_turn__update_plan', done=[1, 2])
check('a list with every step done is not held', P.verdict(EVENT, [PLAN, done] + WORK), None)
check('a list closed by wrap_up is not held', P.verdict(EVENT, [PLAN, call('mcp__ccd_turn__wrap_up', recap='x')] + WORK), None)
check('no list at all is not held', P.verdict(EVENT, WORK + WORK), None)
check('a helper (an agent_id) is not held', P.verdict(dict(EVENT, agent_id='a'), [PLAN] + WORK), None)
check('after MAX_REFUSALS the same stale run goes through', P.verdict(EVENT, [PLAN] + WORK, refused=P.MAX_REFUSALS), None)
todo = call('TodoWrite', todos=[{'content': 'Read', 'status': 'in_progress'}])
check('TodoWrite lists count the same', bool(P.verdict(EVENT, [todo] + WORK)), True)
check('advance, done and start replay to the right step',
      P.apply([['a', 'in_progress'], ['b', 'pending'], ['c', 'pending']], {'advance': True}, 'x'),
      [['a', 'completed'], ['b', 'in_progress'], ['c', 'pending']])

# Through stamp-gate.sh, as every session runs it: a stamped line, then the stale list.
d = tempfile.mkdtemp()
path = os.path.join(d, 't.jsonl')
stamp = json.dumps({'type': 'assistant', 'uuid': 'st', 'message': {'content': [{'type': 'text', 'text': '1/1/26 12:00 AM PT: go'}]}},
                   separators=(',', ':'))


def gate(lines):
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    ev = {'hook_event_name': 'PreToolUse', 'tool_name': 'Read', 'transcript_path': path, 'session_id': 'sg'}
    r = subprocess.run([BASH, os.path.join(HOOKS, 'stamp-gate.sh')], input=json.dumps(ev), capture_output=True, text=True,
                       timeout=30, env=dict(os.environ, TMPDIR=d, TEMP=d, STAMP_GATE_WAIT='', PLAN_GATE_WAIT='0'))
    return r.returncode, r.stderr


code, err = gate([json.dumps({'type': 'user', 'message': {'content': 'go'}})] + [PLAN] + WORK + [stamp])
check('stamp-gate.sh refuses the stale list with plan-gate\'s reason', (code, 'Task list stale' in err), (2, True))
code, err = gate([json.dumps({'type': 'user', 'message': {'content': 'go'}})] + [PLAN] + WORK[:4] + [stamp])
check('and lets a fresh one through', code, 0)

print(f'{len(FAILS)} failure(s)')
raise SystemExit(1 if FAILS else 0)
