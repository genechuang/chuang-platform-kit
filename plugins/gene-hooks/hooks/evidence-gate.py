#!/usr/bin/env python3
"""A reply that says who built something, or how production stands, rests on something read in the same turn (Gene,
10/29/26 12:47 AM PT: "You have shown a pattern of bullshitting without verifying the facts first", after a session
named another as the builder of code it had built itself, without a git log, and offered as a fix what the logs on its
screen showed was already so). Run by stamp-reply.py once the stamp is right, so it rides the Stop hook every session
already has and reaches a running session through boot.sh, with no reload.

The turn is everything after the last typed prompt. Two checks on its final reply:
- Ownership: the reply names a session as the builder or owner of something ("<Name>'s booking job", "built by <Name>",
  "<Name> owns ...", "is <Name>'s") while the turn ran no `git log`, `git blame` or `git show`. The session names are
  the working repo's own: the values of its recent commits' `Session:` trailers, so the hook names no one.
- Evidence: the reply states how production stands ("is live", "shipped", "deployed", "booked", "smoke passed", "is
  serving", "no failure") while the turn read nothing at all (no Bash, Read, Grep, Glob, WebFetch or MCP call).
What it cannot see: a wrong conclusion drawn from evidence that was read. Standard library only.
"""
import json
import re
import subprocess

AFTER_OWNER = r'(?!landing|note|review|receipt|message|letter|question|word|answer|reply|turn|session|inbox|ship|call)'
PRODUCTION = re.compile(
    r'\b(?:is|are|went|now)\s+live\b|\bshipped\b|\bdeployed\b|\bis serving\b|\bsmokes? (?:passed|failed|green)\b'
    r'|\bwas booked\b|\bbooking (?:failed|succeeded)\b|\bin production\b|\bno failure\b', re.I)
GIT_HISTORY = re.compile(r'\bgit\b[^|;&\n]*\b(?:log|blame|show)\b')
READ_TOOLS = {'Bash', 'Read', 'Grep', 'Glob', 'WebFetch'}


def session_names(cwd: str = None, run=subprocess.run) -> list:
    """The working repo's session names: its last 300 commits' Session: trailer values, longest first. [] outside a
    repo or when git cannot answer."""
    try:
        got = run(['git', 'log', '-300', '--format=%(trailers:key=Session,valueonly,separator=%x0A)'],
                  cwd=cwd or None, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return []
    if got.returncode != 0:
        return []
    names = {n.strip() for n in (got.stdout or '').splitlines() if n.strip() and len(n.strip()) <= 40}
    return sorted(names, key=lambda n: (-len(n), n))


def ownership_patterns(names: list) -> list:
    """The attribution forms for these session names."""
    if not names:
        return []
    who = '(?:' + '|'.join(re.escape(n) for n in names) + ')'
    return [
        re.compile(rf"\b{who}['’]s\s+{AFTER_OWNER}\w+", re.I),
        re.compile(rf'\b(?:built|written|owned|made)\s+by\s+{who}\b', re.I),
        re.compile(rf'\b{who}\s+(?:built|owns|wrote|made|is the owner of)\b', re.I),
        re.compile(rf'\b(?:belongs to|is owned by)\s+{who}\b', re.I),
        re.compile(rf"\b(?:is|was|are|were)\s+(?:all\s+)?{who}['’]s\b", re.I),
    ]


def _is_prompt(entry):
    content = (entry.get('message') or {}).get('content')
    return isinstance(content, str) or (isinstance(content, list) and any(
        isinstance(c, dict) and c.get('type') == 'text' for c in content))


def turn(lines: list) -> list:
    """The transcript entries after the last typed prompt."""
    entries = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    start = 0
    for i, e in enumerate(entries):
        if e.get('type') == 'user' and _is_prompt(e):
            start = i + 1
    return entries[start:]


def tool_uses(entries: list) -> list:
    """Every tool call the assistant made in `entries`: (name, input)."""
    out = []
    for e in entries:
        if e.get('type') != 'assistant':
            continue
        for c in (e.get('message') or {}).get('content') or []:
            if isinstance(c, dict) and c.get('type') == 'tool_use':
                out.append((c.get('name') or '', c.get('input') or {}))
    return out


def check(reply: str, lines: list, names: list) -> dict:
    """None when the reply stands, else the Stop hook's block with what to do."""
    uses = tool_uses(turn(lines))
    for p in ownership_patterns(names):
        m = p.search(reply or '')
        if m and not any(n == 'Bash' and GIT_HISTORY.search(str(i.get('command') or '')) for n, i in uses):
            return {'decision': 'block', 'reason': (
                f'evidence-gate: your reply says who built or owns something ("{m.group(0)}") but this turn ran no git '
                'log, blame or show. Run git log on the file and name the session its Session trailers show, or drop '
                'the attribution (Gene, 10/29/26: a session called its own code another\'s).')}
    m = PRODUCTION.search(reply or '')
    if m and not any(n in READ_TOOLS or n.startswith('mcp__') for n, _ in uses):
        return {'decision': 'block', 'reason': (
            f'evidence-gate: your reply states how production stands ("{m.group(0)}") but this turn read nothing. '
            'Read the log, run or record that shows it and cite it, or say it is unverified (Gene, 10/29/26: "stop '
            'bullshitting me").')}
    return None
