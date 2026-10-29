#!/usr/bin/env python3
"""The Stop hook: a turn's last reply must open with the Pacific clock stamp (Gene, 10/13/26 5:49 AM PT: "build
stop hook to put in time stamp, make sure it doesn't add overhead to prompt response").

A hook cannot edit what a session wrote; it can only stop the turn from ending. So when the turn's final text
does not start with `mm/dd/yy h:mm AM|PM` (`10/17/26 5:21 AM PT`, the form since 10/17/26, required since 1.2.0;
the clock hook hands every prompt and tool result the date and the time), or end with it, this answers
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

# The date leads the time, opening and closing (Gene, 10/17/26 5:21 AM PT: "add date too mm/dd/yy"; 5:56 AM, a closing
# "— 5:54 AM PT" passed 1.1.0, which took the date as optional: "still old hook. Why?"). Required since 1.2.0.
DATE_TIME = r'\d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}\s?(AM|PM|am|pm)\b'
STAMP = re.compile(r'^\s*(\*\*)?' + DATE_TIME)
END_STAMP = re.compile(r'(?<![\d/])' + DATE_TIME)
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
    t = pacific_now(now)
    stamp = f"{t.month}/{t.day}/{t:%y} " + t.strftime('%I:%M %p').lstrip('0')
    if opens and closes:
        off = drift(reply, t)
        if off is None:
            return evidence(reply, read_tail(event.get('transcript_path') or ''), event.get('cwd'))
        return {'decision': 'block',
                'reason': f'Your reply\'s stamp is {abs(off)} minute(s) {"ahead of" if off > 0 else "behind"} the clock: it is '
                          f'{stamp} PT now. Send one short line, "— {stamp} PT", saying the reply above is complete (do not '
                          f'repeat it). A stamp is copied from the latest "Pacific clock:" hook line, never advanced or '
                          f'carried by felt time (Gene, 10/20/26: "Your time stamp is drifting badly from my iOS time").'}
    where = 'open' if not opens else 'close'
    return {'decision': 'block',
            'reason': f'Your reply did not {where} with the Pacific clock stamp. Send one short line, "— {stamp} PT", '
                      f'saying the reply above is complete (do not repeat it). Every final reply opens with the stamp, '
                      f'the date and the time, and closes with "— mm/dd/yy h:mm AM PT" on its last line (Gene, 10/14/26: '
                      f'on the phone a long reply opens scrolled to its end, so the opening stamp is off screen; 10/17/26: '
                      f'the date too).'}


def evidence(reply, lines, cwd=None, names=None):
    """Once the stamp is right, evidence-gate.py's check (1.10.0; Gene, 10/29/26): an attribution of work to a session
    with no git log this turn, or a production claim with nothing read this turn, is held. Loaded from this file's own
    folder, so boot.sh's newest version brings both; any failure lets the reply through."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location('evidence_gate', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'evidence-gate.py'))
        gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gate)
        return gate.check(reply, lines, gate.session_names(cwd) if names is None else names)
    except Exception:  # noqa: BLE001 - never trap a session
        return None


AHEAD_OK, BEHIND_OK = 1, 1   # minutes (Gene, 10/20/26 12:22 AM PT: "reject timestamps more than 1 min ahead or behind")


def drift(reply: str, now_pacific):
    """Minutes the reply's closing stamp is off the clock (positive: ahead), or None when it is within a minute either
    way (since 1.4.0: Gene, 10/20/26 12:19 AM PT, a phone screenshot of "12:22 AM PT" at 12:19). The
    closing stamp is the one written last, moments before this hook runs; a stamp it cannot read is not judged."""
    lines = [ln for ln in reply.strip().splitlines() if ln.strip()]
    m = END_STAMP.search(lines[-1]) if lines else None
    if not m:
        return None
    try:
        stamped = datetime.strptime(re.sub(r'\s+', ' ', m.group(0)).upper().replace('AM', ' AM').replace('PM', ' PM').replace('  ', ' '),
                                    '%m/%d/%y %I:%M %p')
    except ValueError:
        return None
    off = round((stamped - now_pacific.replace(tzinfo=None, second=0, microsecond=0)).total_seconds() / 60)
    return None if -BEHIND_OK <= off <= AHEAD_OK else off


def ends_stamped(reply: str) -> bool:
    """The reply's last non-empty line carries the date and the time (`— 10/17/26 1:54 AM PT`): Gene, 10/14/26 1:54 AM PT,
    "Add timestamp at bottom too", after his phone showed a long reply scrolled to its end with the opening stamp off
    screen; 10/17/26, the date on the closing line too."""
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
