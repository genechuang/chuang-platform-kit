#!/usr/bin/env python3
"""Stop: a reply that reminds Gene of pending items must spell them out (Gene, 10/27/26 4:04 PM PT: "how do we codify
this, in a exit hook maybe, so that you are required to list out manual instructions or decision details whenever you
remind me of an item that first was listed way back up the feed?"; the claude-shared rule of 10/26/26 6:29 PM PT, "a
reminder of a manual task restates the whole of it", broken that afternoon by "the three open decisions" with none
listed).

The turn's final reply (what Gene reads) is checked for a phrase that points at pending items without saying them:
"the three decisions", "your two questions", "the installer above", "as listed earlier", "see above", "still waiting
on you". When one is there and the reply carries no numbered list of at least two items, the turn is held once
(decision: block) with what to do: restate each item in full, with its steps and where they go. A second stop in the
same turn (stop_hook_active) lets go, so it never loops. Anything unreadable lets the reply through.
Standard library only; hooks/test_reminder_gate.py.
"""
import json
import re
import sys

COUNT = r'(?:two|three|four|five|six|seven|eight|nine|ten|\d+)'
POINTERS = [
    re.compile(rf'\b(?:the|your|my|those|these)\s+{COUNT}\s+(?:open\s+|pending\s+|remaining\s+)?'
               rf'(?:decisions?|questions?|steps?|items?|answers?|things|actions?)\b', re.I),
    re.compile(r'\b(?:as|see|listed|shown|given|described)\s+(?:above|earlier|before)\b', re.I),
    re.compile(r'\b(?:the|that)\s+(?:installer|command|steps?|instructions?|decisions?)\s+(?:above|earlier|from before)\b', re.I),
    re.compile(r'\bstill\s+(?:waiting|open)\s+(?:on|for)\s+(?:you|your)\b', re.I),
]
NUMBERED = re.compile(r'^\s*(?:\*\*)?\d+[.)]\s+\S', re.M)


def pointer(text: str) -> str:
    """The first phrase in `text` that points at pending items, or ''."""
    for p in POINTERS:
        m = p.search(text or '')
        if m:
            return m.group(0)
    return ''


def spelled_out(text: str) -> bool:
    """Whether `text` carries a numbered list of at least two items."""
    return len(NUMBERED.findall(text or '')) >= 2


def final_reply(lines: list) -> str:
    """The text of the turn's last assistant message, from transcript JSONL lines (newest last)."""
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get('type') != 'assistant':
            continue
        content = (entry.get('message') or {}).get('content')
        texts = [c.get('text', '') for c in content if isinstance(c, dict) and c.get('type') == 'text'] if isinstance(content, list) else []
        if any(t.strip() for t in texts):
            return '\n'.join(texts)
    return ''


def decide(event: dict, read=None) -> dict:
    """The Stop hook's answer: {} to let the reply through, or a block with the reason."""
    if event.get('stop_hook_active'):
        return {}
    path = event.get('transcript_path') or ''
    try:
        lines = (read or (lambda p: open(p, encoding='utf-8').read().splitlines()[-400:]))(path) if path else []
    except OSError:
        return {}
    text = final_reply(lines)
    hit = pointer(text)
    if not hit or spelled_out(text):
        return {}
    return {'decision': 'block', 'reason': (
        f'reminder-gate: your reply points at pending items ("{hit}") without listing them. Gene reads only this reply '
        '(claude-shared CLAUDE.md, Replies to Gene: a reminder of a manual task restates the whole of it; 10/27/26: never '
        'remind him of a step you are not ready to present). Your reply has already reached Gene: do NOT send it again '
        '(Gene, 11/5/26: a full rewrite arrives as a second copy). Send ONE short follow-up holding only the items, each '
        'a numbered entry: what it is, what you need from him, and the full steps with where each goes; or one line '
        'saying the reminder was not meant.')}


def main(stdin=sys.stdin, out=sys.stdout) -> int:
    try:
        event = json.load(stdin)
    except ValueError:
        return 0
    answer = decide(event)
    if answer:
        out.write(json.dumps(answer) + '\n')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:   # noqa: BLE001 - a hook blocks only on purpose (claude-shared CLAUDE.md, Hooks)
        sys.exit(0)
