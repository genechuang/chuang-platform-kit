#!/usr/bin/env python3
"""A task list is kept live (Gene, 10/28/26 4:44 PM PT: "if you present a task list with live progress status, you
update it and don't keep me in suspense!!"; 10/30/26 12:34 AM PT, after a session left step 1 of 7 spinning while it
built steps 2 to 4: "update your steps progress in real time so I know what you're doing"). A written rule did not
hold, so this refuses the tool call that would make a list stale.

Run by stamp-gate.sh (PreToolUse, every call, every session, through boot.sh with no reload) only once its awk counted
LIMIT tool calls since the newest plan call, so most calls never start Python. It reads the transcript's tail, replays
the plan calls to know whether a step is still open, and refuses the call when one is and LIMIT or more tool calls
have gone by since the list last changed. The plan tools: any tool whose name ends in `update_plan` (the desktop app's
task list) and TodoWrite; a tool whose name ends in `wrap_up` closes the list. What it cannot see: a list advanced on
time but to the wrong step. Standard library only; anything unexpected lets the call through.

  stdin: the PreToolUse event.  exit 2 + stderr: refused.  exit 0: through.
"""
import json
import os
import sys

LIMIT = int(os.environ.get('PLAN_GATE_LIMIT', '12'))
MAX_REFUSALS = 3   # a stuck session is never trapped: the same stale run is refused at most this many times
TAIL_BYTES = 2_000_000
WAIT = float(os.environ.get('PLAN_GATE_WAIT', '0.4'))   # "0" for the tests
FRESH_SECONDS = 60


def is_plan(name: str) -> bool:
    return (name or '').endswith('update_plan') or name == 'TodoWrite'


def is_close(name: str) -> bool:
    return (name or '').endswith('wrap_up')


def apply(steps, inp: dict, name: str):
    """The list after one plan call: a list of [content, status]."""
    if name == 'TodoWrite':
        return [[t.get('content') or t.get('activeForm') or '', t.get('status') or 'pending'] for t in inp.get('todos') or []]
    if inp.get('items') is not None:
        return [[i.get('content') or '', i.get('status') or 'pending'] for i in inp['items']]
    steps = [list(s) for s in steps or []]
    if inp.get('advance'):
        cur = next((i for i, s in enumerate(steps) if s[1] == 'in_progress'), None)
        if cur is not None:
            steps[cur][1] = 'completed'
        nxt = next((i for i, s in enumerate(steps) if s[1] == 'pending'), None)
        if nxt is not None:
            steps[nxt][1] = 'in_progress'
    for d in inp.get('done') or []:
        if 1 <= int(d) <= len(steps):
            steps[int(d) - 1][1] = 'completed'
    if inp.get('start') and 1 <= int(inp['start']) <= len(steps):
        steps[int(inp['start']) - 1][1] = 'in_progress'
    return steps


def replay(lines):
    """(steps, calls since the list last changed, uuid of the last plan call) from transcript lines."""
    steps, since, mark = None, 0, ''
    for raw in lines:
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if entry.get('type') != 'assistant':
            continue
        for c in (entry.get('message') or {}).get('content') or []:
            if not isinstance(c, dict) or c.get('type') != 'tool_use':
                continue
            name = c.get('name') or ''
            if is_plan(name):
                steps, since, mark = apply(steps, c.get('input') or {}, name), 0, entry.get('uuid') or mark
            elif is_close(name):
                steps, since = None, 0
            else:
                since += 1
    return steps, since, mark


def read_tail(path: str) -> list:
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - TAIL_BYTES))
            data = f.read().decode('utf-8', 'replace')
    except OSError:
        return []
    return data.splitlines()[1:] if len(data) >= TAIL_BYTES else data.splitlines()


def verdict(event: dict, lines: list, refused: int = 0):
    """None to let the call through, or the refusal's text."""
    if event.get('agent_id') or is_plan(event.get('tool_name')) or is_close(event.get('tool_name')):
        return None
    steps, since, _mark = replay(lines)
    open_ = [s for s in steps or [] if s[1] in ('pending', 'in_progress')]
    if not open_ or since + 1 < LIMIT or refused >= MAX_REFUSALS:
        return None
    cur = next((s[0] for s in steps if s[1] == 'in_progress'), '') or open_[0][0]
    return (f'Task list stale: it still shows "{cur}" after {since} tool calls with no update. Send the plan call in the '
            'same message as this call: advance it to the step you are on, mark what finished, or send the whole new '
            'list if the work changed. (gene-hooks plan-gate.py; Gene 10/28/26: "if you present a task list with live '
            'progress status, you update it and don\'t keep me in suspense!!")')


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or '{}')
        lines = read_tail((event.get('transcript_path') or '').replace('\\\\', '/'))
        steps, since, mark = replay(lines)
        state = os.path.join(os.environ.get('TMPDIR') or os.environ.get('TEMP') or '/tmp',
                             'plan-gate-' + ''.join(ch for ch in event.get('session_id') or 'default' if ch.isalnum() or ch == '-'))
        fresh = state + '.fresh'
        if is_plan(event.get('tool_name')) or is_close(event.get('tool_name')):
            # 1.11.1: Claude Code writes a message's tool calls to the transcript only after they all run, so a plan
            # call sent beside other calls is not on disk when they are checked (it refused its own fix at 12:46 AM PT
            # 10/30/26). The plan call's own check marks the session fresh, and the gate trusts that for FRESH_SECONDS.
            with open(fresh, 'w', encoding='utf-8') as f:
                f.write('fresh\n')
            return 0
        try:
            import time
            if time.time() - os.path.getmtime(fresh) < FRESH_SECONDS:
                return 0
        except OSError:
            pass
        key = f'{mark}:{since // LIMIT}'
        try:
            with open(state, encoding='utf-8') as f:
                refused = sum(1 for ln in f if ln.strip() == key)
        except OSError:
            refused = 0
        text = verdict(event, lines, refused)
        if text and WAIT:
            # The plan call sent in the same message may not be on disk yet (stamp-gate.sh's lesson, 1.5.0): read again.
            import time
            time.sleep(WAIT)
            lines = read_tail((event.get('transcript_path') or '').replace('\\\\', '/'))
            text = verdict(event, lines, refused)
        if not text:
            return 0
        try:
            with open(state, 'a', encoding='utf-8') as f:
                f.write(key + '\n')
        except OSError:
            pass
        print(text, file=sys.stderr)
        return 2
    except Exception:   # noqa: BLE001 - a broken hook never traps a session
        return 0


if __name__ == '__main__':
    sys.exit(main())
