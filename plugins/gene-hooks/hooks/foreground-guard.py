#!/usr/bin/env python3
"""PreToolUse on Bash and PowerShell: a command that can run over two minutes runs with run_in_background, never in
the foreground, because the user cannot reach the session while it runs (Gene, 10/21/26 12:05 AM PT, after Black
Widow ran app/scripts/ship.py in the foreground twice and could not be reached for 7+ minutes: "why the fuck cannot
you get it in your head to run long running processes in the background?? put it in claude-shared, add it to a hook,
do something about it").

Refused (exit 2, the reason on stderr) when the call is not run_in_background and its command names a known long
runner (LONG, below), or its own timeout is 120000 ms or more. A push to `hopper` is allowed: that branch runs no
gate and takes seconds. Anything unexpected exits 0: a broken hook must never trap a session.
"""
import json
import re
import sys

# (pattern, what it is). Matched against the whole command, case-sensitively except where noted.
LONG = [
    (r'app/scripts/ship\.py', 'the app ship chain'),
    (r'app/scripts/artifact\.py\s+build', 'the app build'),
    (r'app/scripts/sim_smoke\.py', 'the Simulator smoke'),
    (r'app/scripts/desktop_smoke\.py', 'the web smoke'),
    (r'scripts/desktop-tests\.py', 'the desktop test run'),
    (r'scripts/build-images\.py', 'the image builds'),
    (r'scripts/run-tests\.py', 'the full Python suite'),
    (r'\bgh\s+run\s+watch\b', 'a run watch'),
    (r'\bgcloud\s+builds\s+submit\b', 'a Cloud Build'),
    (r'\bterraform\s+(-chdir=\S+\s+)?(apply|plan)\b', 'a Terraform plan or apply'),
    (r'\beas\s+build\b', 'an EAS build'),
    (r'\bnpx\s+expo\s+export\b', 'an Expo export'),
]
# A script in LONG counts only when it is run: at the start of a command segment (after env assignments) or as the
# argument of an interpreter. A grep, cat or Read that merely names the file is not a run (10/21/26 12:25 AM PT: the
# guard refused `grep ... scripts/desktop-tests.py`, and a `docker images | grep test` beside its name).
RUN = r'(?:(?:^|[;&|(]|\$\()\s*(?:\w+=\S*\s+)*|(?<![\w.-])(?:python[0-9.]*|py|node|bash|sh|pwsh|powershell)(?:\.exe)?\s+(?:-\S+\s+)*)["\']?\S*?'
SCRIPT = re.compile(r'\.(py|sh|js)')
# Short forms of a long runner: one suite by name.
SHORT = [re.compile(r'scripts/run-tests\.py\b[^;&|]*--only\b')]
PUSH = re.compile(r'\bgit\s+(-C\s+\S+\s+|-c\s+\S+\s+)*push\b')
TO_HOPPER = re.compile(r'hopper\b')
PYTEST = re.compile(r'\bpython[0-9.]*(\.exe)?\s+-m\s+pytest\b(?P<rest>[^;&|]*)')
JEST = re.compile(r'\bnpx\s+jest\b(?P<rest>[^;&|]*)')
LONG_TIMEOUT_MS = 120000


def reason(event: dict):
    """The refusal's reason, or None to allow."""
    if event.get('tool_name') not in ('Bash', 'PowerShell'):
        return None
    tin = event.get('tool_input') or {}
    if tin.get('run_in_background') is True:
        return None
    cmd = str(tin.get('command') or '').replace('\\', '/')   # a Windows path reads as the patterns' forward slashes
    def runs(pat):
        # A script path must be run (RUN before it); a command pattern (gh run watch, terraform) matches as written.
        return re.search((RUN + pat) if SCRIPT.search(pat) else pat, cmd)
    found = next((what for pat, what in LONG if runs(pat) and not any(s.search(cmd) for s in SHORT)), None)
    if not found and PUSH.search(cmd) and not TO_HOPPER.search(cmd):
        found = 'a git push (the pre-push gate runs the suites and the builds)'
    if not found:
        m = PYTEST.search(cmd)
        if m and not re.search(r'\S+\.py\b', m.group('rest')):
            found = 'pytest over the whole suite'
    if not found:
        m = JEST.search(cmd)
        if m and not re.search(r'\S+\.(test|spec)\.[jt]sx?\b', m.group('rest')):
            found = 'jest over more than one test file'
    if not found:
        try:
            if int(tin.get('timeout') or 0) >= LONG_TIMEOUT_MS:
                found = f"a call with a {int(tin['timeout']) // 1000} s timeout"
        except (TypeError, ValueError):
            pass
    if not found:
        return None
    return (f'Refused: {found} can run over two minutes, and nobody can reach this session while a foreground command '
            f'runs. Rerun it with run_in_background: true and keep answering; you are told when it ends (claude-shared '
            f'CLAUDE.md, Liveness; Gene, 10/21/26).')


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or '{}')
        why = reason(event)
    except Exception:   # noqa: BLE001 - never trap a session
        return 0
    if why:
        print(why, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
