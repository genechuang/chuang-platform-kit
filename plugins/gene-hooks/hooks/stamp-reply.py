#!/usr/bin/env python3
"""The Stop hook: a turn's last reply must open with the Pacific clock stamp (Gene, 10/13/26 5:49 AM PT: "build
stop hook to put in time stamp, make sure it doesn't add overhead to prompt response").

A hook cannot edit what a session wrote; it can only stop the turn from ending. So when the turn's final text
does not start with `h:mm AM|PM` (the stamp the clock hook hands every prompt and tool result), this answers
{"decision": "block"} with the clock now, and the session adds one stamped line. Only the final message of a
turn is checked: the one Gene reads on his phone when the turn ends, and the one replies to a background
agent or a peer message used to miss (no prompt fires there).

Cost: it runs once per turn, after the reply is written, never on a prompt or a tool call; it reads only the
transcript's tail (the last 256 KB). It never blocks twice in a row (`stop_hook_active`), never on a turn with
no text, and any error exits 0: a broken hook must never trap a session.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

STAMP = re.compile(r'^\s*(\*\*)?\d{1,2}:\d{2}\s?(AM|PM|am|pm)\b')
END_STAMP = re.compile(r'\b\d{1,2}:\d{2}\s?(AM|PM|am|pm)\b')
TAIL = 256 * 1024


def pacific_now(now=None):
    """The Pacific wall clock from UTC by the US DST rule (no zone database needed on Windows)."""
    now = now or datetime.now(timezone.utc)  # utc-ok: converted to Pacific below
    y = now.year
    # DST: 2nd Sunday of March 2:00 local (10:00 UTC) to 1st Sunday of November 2:00 local (09:00 UTC).
    march = datetime(y, 3, 8, 10, tzinfo=timezone.utc)
    start = march + timedelta(days=(6 - march.weekday()) % 7)
    nov = datetime(y, 11, 1, 9, tzinfo=timezone.utc)
    end = nov + timedelta(days=(6 - nov.weekday()) % 7)
    return now + timedelta(hours=-7 if start <= now < end else -8)


def last_reply(lines):
    """The text of the last assistant message that has text, from transcript JSONL lines (newest last)."""
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get('type') == 'user' and not entry.get('isMeta') and _is_prompt(entry):
            return None   # the turn's prompt came after any reply: this turn wrote no text
        if entry.get('type') != 'assistant':
            continue
        content = (entry.get('message') or {}).get('content') or []
        texts = [c.get('text', '') for c in content if isinstance(c, dict) and c.get('type') == 'text']
        if any(t.strip() for t in texts):
            return next(t for t in texts if t.strip())
    return None


def _is_prompt(entry):
    """A user entry that is a typed prompt, not a tool result."""
    content = (entry.get('message') or {}).get('content')
    if isinstance(content, str):
        return True
    return isinstance(content, list) and any(isinstance(c, dict) and c.get('type') == 'text' for c in content)


def decide(event, read_tail, now=None):
    """The hook's JSON answer, or None to let the turn end."""
    if event.get('stop_hook_active'):
        return None
    reply = last_reply(read_tail(event.get('transcript_path') or ''))
    if not reply:
        return None
    opens, closes = bool(STAMP.match(reply)), ends_stamped(reply)
    if opens and closes:
        return None
    t = pacific_now(now)
    stamp = t.strftime('%I:%M %p').lstrip('0')
    where = 'open' if not opens else 'close'
    return {'decision': 'block',
            'reason': f'Your reply did not {where} with the Pacific clock stamp. Send one short line, "— {stamp} PT", '
                      f'saying the reply above is complete (do not repeat it). Every final reply opens with the stamp and '
                      f'closes with "— h:mm AM PT" on its last line (Gene, 10/14/26: on the phone a long reply opens '
                      f'scrolled to its end, so the opening stamp is off screen).'}


def ends_stamped(reply: str) -> bool:
    """The reply's last non-empty line carries a time (`— 1:54 AM PT`): Gene, 10/14/26 1:54 AM PT, "Add timestamp at
    bottom too", after his phone showed a long reply scrolled to its end with the opening stamp off screen."""
    lines = [ln for ln in reply.strip().splitlines() if ln.strip()]
    return bool(lines) and bool(END_STAMP.search(lines[-1]))


def _read_tail(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, 'rb') as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - TAIL))
        data = f.read().decode('utf-8', errors='replace')
    lines = data.splitlines()
    return lines[1:] if size > TAIL else lines   # the first line of a tail may be cut


def main():
    try:
        event = json.loads(sys.stdin.read() or '{}')
        out = decide(event, _read_tail)
        if out:
            print(json.dumps(out))
    except Exception:  # noqa: BLE001 - never trap a session
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
