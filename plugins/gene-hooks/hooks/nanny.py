#!/usr/bin/env python3
"""The session nanny (SMADPickleBot #1214; Gene, 11/8/26 11:04 PM PT: "Yes tell snow to build session nanny in Gene
hook", after a background build sat 311 s on a dead build lock while its first line said "waiting for another Android
build", and Gene had to keep asking what the session was waiting on).

    nanny.py         PostToolUse: record a background Bash start; put a run that waits or went quiet in front of the session
                     Stop (the event's hook_event_name): a flagged run no Stop has shown yet holds the turn once

A background Bash call's result says "Command running in background with ID: <id>. Output is being written to: <path>",
so the start is recorded from the event alone ({id: command, path, started}), in a small state file per session
(NANNY_DIR, ~/.claude/session-nanny/<session>.json). At every later tool call each recorded run still running (its output
file has no "[exited with code" line yet) is read for size, age and the tail of its output, never the transcript and
no process list, and flagged when it:

- prints a wait ("waiting for", "still holds", "queued", "blocked", "lock" in its last lines), or
- has printed nothing in its first QUIET_S (30 s), or
- has not grown for STALL_S (120 s).

Each flag is told once as the tool result's context: the run's id, command, age and last line. A run whose command is
quiet by design (NANNY_QUIET, a regex; by default a `wait --for` mail pull) is never flagged for being quiet. At Stop,
a flagged run still running that no Stop has shown yet holds the turn once (the release-profile gate's way), so the
session explains it or stops it. No model call; a missing or unreadable file is no flag. Standard library only.
"""
import json
import os
import re
import sys
import time

QUIET_S = 30
STALL_S = 120
TAIL_BYTES = 4096
WAIT = re.compile(r'waiting for|still holds|\bqueued\b|\bblocked\b|\block\b', re.I)
STARTED = re.compile(r'running in background with ID: (\S+?)\.? Output is being written to: (.+?\.output)')
EXITED = '[exited with code'
QUIET_OK = re.compile(os.environ.get('NANNY_QUIET') or r'\bwait\s+--for\b')


def state_path(session: str) -> str:
    folder = os.environ.get('NANNY_DIR') or os.path.join(os.path.expanduser('~'), '.claude', 'session-nanny')
    return os.path.join(folder, f"{re.sub(r'[^A-Za-z0-9_.-]', '_', session or 'session')}.json")


def load(path: str) -> dict:
    try:
        with open(path, encoding='utf-8') as f:
            got = json.load(f)
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def save(path: str, state: dict):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(state, f)
        os.replace(path + '.tmp', path)
    except OSError:
        pass


def response_text(resp) -> str:
    """The tool result's text, whatever shape the harness gives it (a string, or a dict of stdout and stderr)."""
    if isinstance(resp, str):
        return resp
    if isinstance(resp, dict):
        return ' '.join(str(resp.get(k) or '') for k in ('stdout', 'stderr', 'output', 'message'))
    return str(resp or '')


def record(event: dict, state: dict, now: float) -> bool:
    """A background Bash start, from the call's result; True when one was recorded."""
    if event.get('tool_name') != 'Bash' or not (event.get('tool_input') or {}).get('run_in_background'):
        return False
    m = STARTED.search(response_text(event.get('tool_response')))
    if not m:
        return False
    state.setdefault('runs', {})[m.group(1)] = {'command': str(event['tool_input'].get('command') or '')[:300],
                                                'path': m.group(2).strip(), 'started': now}
    return True


def look(run: dict, now: float) -> tuple:
    """(running, flag kind or '', last line, age seconds) for one recorded run; an unreadable output is not running."""
    try:
        st = os.stat(run['path'])
        with open(run['path'], 'rb') as f:
            f.seek(max(0, st.st_size - TAIL_BYTES))
            tail = f.read().decode('utf-8', errors='replace')
    except (OSError, KeyError):
        return False, '', '', 0
    if EXITED in tail:
        return False, '', '', 0
    age = now - float(run.get('started') or now)
    lines = [ln.strip() for ln in tail.splitlines() if ln.strip()]
    last = lines[-1] if lines else ''
    quiet_ok = bool(QUIET_OK.search(run.get('command') or ''))
    if any(WAIT.search(ln) for ln in lines[-5:]):
        return True, 'waits', last, age
    if not quiet_ok and st.st_size == 0 and age >= QUIET_S:
        return True, 'silent', last, age
    if not quiet_ok and st.st_size > 0 and now - st.st_mtime >= STALL_S:
        return True, 'stalled', last, age
    return True, '', last, age


WHY = {'waits': 'its output says it is waiting', 'silent': f'it has printed nothing in its first {QUIET_S} s',
       'stalled': f'its output has not grown for {STALL_S} s'}


def line_for(task: str, run: dict, kind: str, last: str, age: float) -> str:
    return (f"background run {task} ({run.get('command', '')[:120]}), {round(age)} s old: {WHY[kind]}; "
            f"last line: {last[:200] or '(none)'}")


def post(event: dict, now: float) -> str:
    """The context for this tool result: each run newly flagged, once per run and kind; '' when none."""
    path = state_path(str(event.get('session_id') or ''))
    state = load(path)
    changed = record(event, state, now)
    told = []
    for task, run in list((state.get('runs') or {}).items()):
        running, kind, last, age = look(run, now)
        if not running:
            del state['runs'][task]
            changed = True
            continue
        if kind and kind not in run.get('told', []):
            run.setdefault('told', []).append(kind)
            run['flagged'] = True
            changed = True
            told.append(line_for(task, run, kind, last, age))
    if changed:
        save(path, state)
    if not told:
        return ''
    return ('Session nanny (gene-hooks): ' + '; '.join(told) + '. Say what it is waiting on and whether to stop it '
            '(TaskStop), or fix what holds it; never leave it running unexplained.')


def stop(event: dict, now: float) -> str:
    """The reason to hold this Stop once: flagged runs still running that no Stop has shown; '' to let it end."""
    if event.get('stop_hook_active'):
        return ''
    path = state_path(str(event.get('session_id') or ''))
    state = load(path)
    held = []
    for task, run in list((state.get('runs') or {}).items()):
        running, kind, last, age = look(run, now)
        if not running:
            del state['runs'][task]
            continue
        kind = kind or (run.get('told') or [''])[-1]
        if run.get('flagged') and not run.get('held') and kind:
            run['held'] = True
            held.append(line_for(task, run, kind, last, age))
    save(path, state)
    if not held:
        return ''
    return ('Session nanny (gene-hooks): before this turn ends, ' + '; '.join(held) + '. Tell Gene in one line what it '
            'waits on and what happens next, or stop it; this holds once.')


def main(argv) -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(event, dict):
        return 0
    # The event names its hook (Stop, else PostToolUse); an argument still wins, for a hand run.
    mode = argv[1] if len(argv) > 1 else ('stop' if event.get('hook_event_name') == 'Stop' else 'post')
    now = time.time()
    try:
        if mode == 'stop':
            reason = stop(event, now)
            if reason:
                print(json.dumps({'decision': 'block', 'reason': reason}))
            return 0
        context = post(event, now)
        if context:
            print(json.dumps({'hookSpecificOutput': {'hookEventName': event.get('hook_event_name') or 'PostToolUse',
                                                     'additionalContext': context}}))
    except Exception as e:   # noqa: BLE001 - a hook that cannot check says so and gets out of the way
        print(json.dumps({'systemMessage': f'session nanny: {type(e).__name__}: {e}; background runs are NOT being watched'}))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
