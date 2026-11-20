#!/usr/bin/env python3
"""UserPromptSubmit: Gene telling a session to work a ticket moves it to In shipyard at once (Gene, 10/27/26 3:53 PM PT:
"I just told BW to do 466 - shouldn't she immediately change the status of the ticket to In Shipyard and it gets
reflected on the board?"). Before this a ticket moved only at its first commit, so the board showed Backlog for as
long as a session worked before committing.

A prompt that says to do, build, fix, work on, start, take, tackle or implement one or more ticket numbers (`do 466`,
`fix #440 and #443`, `Do 440 443 432`) starts `python scripts/tickets.py start <numbers>` in the project the session
works in (the first of the prompt's folder and its parents with that script), detached and hidden, so the prompt never
waits on GitHub; the move is forward only, so a ticket already further along stays. The prompt is always let through:
this hook never blocks (exit 0), says what it started as context, and says so when it could not start it.

Only the user's own prompts reach UserPromptSubmit, never another session's message, so only Gene's word moves a card.
In this plugin since 1.38.0 (SMADPickleBot #1810; claude-shared's until then): the ticket tool is the one the project
names in `.claude/gene-hooks.toml`, else scripts/tickets.py as before. Tested by tests/test-hooks-ticket-start.py.
"""
import json
import os
import re
import subprocess
import sys

VERBS = r'(?:do|build|fix|work\s+on|start(?:\s+on)?|take|tackle|implement)'
NUMS = r'#?\d{2,5}(?:(?:\s*,\s*|\s+and\s+|\s+)#?\d{2,5})*'
ASK = re.compile(rf'\b({VERBS})\s+(ticket\s+)?({NUMS})\b', re.I)
# A message another session sent arrives as a prompt too (10/28/26 9:30 AM PT: Black Widow's "start store build 19"
# moved closed #19 back to Shipyard, twice), and so does a subagent's hand-back (10/30/26 11:02 PM PT, SMADPickleBot
# #650: a research agent's "cold start 48 s" moved closed #48 to the Shipyard, twice): only Gene's own words move a card.
PEER = re.compile(r'<cross-session-message\b|^\s*Another Claude session sent a message|<agent-message\b|\[Subagent hand-back\]', re.I)
# A number wearing a unit is a measurement, not a ticket ("cold start 48 s", "take 250 MB", "do 95%").
UNIT = re.compile(r'\s*(?:(?:s|ms|secs?|seconds?|min|minutes?|h|hours?|[KMG]i?B|x)\b|%)', re.I)


def numbers(prompt: str) -> list:
    """The ticket numbers a prompt tells a session to work, in the order said, each once ([] for none). "build" is
    also a noun ("store build 19", the iPhone's build number), so after it only `#N` or `ticket N` names a ticket;
    a number followed by a unit is a measurement; a peer session's message or a subagent's hand-back names none."""
    if PEER.search(prompt or ''):
        return []
    out = []
    for m in ASK.finditer(prompt or ''):
        if m.group(1).lower() == 'build' and not m.group(2) and not m.group(3).lstrip().startswith('#'):
            continue
        if UNIT.match(prompt, m.end()) and not m.group(3).lstrip().startswith('#'):
            continue
        for n in re.findall(r'\d{2,5}', m.group(3)):
            if n not in out:
                out.append(n)
    return out


CONVENTION = os.path.join('scripts', 'tickets.py')


def ticket_tool(cwd: str) -> tuple:
    """(the project's root, its ticket tool) for the folder a prompt came from: the script the project names in
    `.claude/gene-hooks.toml` (`[tickets] script`, project_config.py; 1.38.0), else, for a project that names none,
    the first of `cwd` and its parents holding scripts/tickets.py, which is what this hook looked for before it moved
    here; ('', '') for a project with neither."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import project_config
        root, tool = project_config.script(cwd, 'tickets')
        if tool:
            return root, tool
    except Exception:   # noqa: BLE001 - no config reader beside this file: the convention below
        pass
    here = os.path.abspath(cwd or '.')
    while True:
        if os.path.isfile(os.path.join(here, CONVENTION)):
            return here, os.path.join(here, CONVENTION)
        parent = os.path.dirname(here)
        if parent == here:
            return '', ''
        here = parent


def project_root(cwd: str) -> str:
    """The root of the project `cwd` is in when it has a ticket tool, else ''."""
    return ticket_tool(cwd)[0]


def start(root: str, nums: list, popen=subprocess.Popen, tool: str = '') -> bool:
    """The ticket tool's `start` detached and hidden in `root`; True when it was started."""
    tool = tool or os.path.join(root, CONVENTION)
    flags = {}
    if os.name == 'nt':
        flags['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x200)
    else:
        flags['start_new_session'] = True
    log = open(os.path.join(os.environ.get('TEMP') or '/tmp', 'ticket-start.log'), 'a', encoding='utf-8')
    try:
        popen([sys.executable, tool, 'start', *nums], cwd=root,
              stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **flags)
        return True
    except OSError:
        return False


def main(stdin=sys.stdin, popen=subprocess.Popen, out=sys.stdout) -> int:
    try:
        event = json.load(stdin)
    except ValueError:
        return 0
    nums = numbers(str(event.get('prompt') or ''))
    if not nums:
        return 0
    root, tool = ticket_tool(str(event.get('cwd') or ''))
    if not root:
        return 0
    ok = start(root, nums, popen, tool)
    tickets = ', '.join(f'#{n}' for n in nums)
    said = os.path.relpath(tool, root).replace(os.sep, '/')
    note = (f'ticket-start: {tickets} moving to In shipyard on the board (Gene asked for the work; forward only)' if ok else
            f'ticket-start: could not start the move of {tickets}; run python {said} start {" ".join(nums)}')
    out.write(json.dumps({'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': note}}) + '\n')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:   # noqa: BLE001 - a hook blocks only on purpose (claude-shared CLAUDE.md, Hooks)
        sys.exit(0)
