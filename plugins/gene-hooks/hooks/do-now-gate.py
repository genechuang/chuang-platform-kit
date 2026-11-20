#!/usr/bin/env python3
"""A ticket a session was handed to "do now" is started before its turn ends (1.37.0, SMADPickleBot #1917).

Gene, 11/19/26 3:08 PM PT, quoting a session's own reply ("Next for me is #1908 ..."): "But then proceed to do
nothing! Have you been mysteriously interrupted, and didn't we build a un-interruptor monitoring and why isn't it
working?" It had not been interrupted. Its project's push had mailed it the next ticket as "do it now" (the letter that
replaced the promise gate, 1.33.0), the letter arrived while the session was still working, the session read it, and
then ended its turn: a letter can start a turn, and nothing held one open.

This is not the promise gate come back (Gene, 11/18/26: "get rid of all empty-promise hooks"). It never reads the
reply's words. It checks one action the project asked for:

  the record   a project's tool that hands a session a ticket to do now leaves a small file for that session,
               <DO_NOW_DIR>/<session id>.json = {"ticket": "1917", "at": <epoch seconds>} (the id is the tool's own
               CLAUDE_CODE_SESSION_ID; DO_NOW_DIR is ~/.claude/do-now). A project may rewrite it when the session
               starts a different card of its own, so the ticket in it is always the one next.
  the check    at the turn's end, run inside the Stop hook (stamp-reply.py, so boot.sh's newest version brings it with
               no reload): the session's own lines in the ticket-activity log (stamp-gate.sh: one line a tool call,
               "<epoch> <session id> <tickets its stamp names>") must show a tool call on that ticket after the record.
               Since 1.40.0 (SMADPickleBot #1929) the turn's own transcript answers too: the log can charge the first
               call or two after a change of ticket to the ticket before (worked() says why), and a turn that did its
               work was held twice on 11/19/26. A stamp naming the ticket and then a tool call, after the record.
  the hold     none yet: the turn is held ONCE, with the ticket named, and the record is marked so the next Stop lets
               the turn end whatever happened. A record older than MAX_AGE holds nothing and is removed.

Released (the file removed) the moment the ticket has a tool call after the record. Any failure, a missing file, an
unreadable log: the turn ends, never trapped.
"""
import json
import os
import re
import time

MAX_AGE = 6 * 3600   # a record this old is yesterday's news: it holds nothing


def home_dir(env=None) -> str:
    env = os.environ if env is None else env
    return env.get('DO_NOW_DIR') or os.path.join(os.path.expanduser('~'), '.claude', 'do-now')


def log_path(env=None) -> str:
    env = os.environ if env is None else env
    return env.get('TICKET_ACTIVITY_LOG') or os.path.join(os.path.expanduser('~'), '.claude', 'ticket-activity.log')


def record_path(session_id: str, folder: str = None) -> str:
    """The session's record file; the id cut to the characters the activity log keeps."""
    return os.path.join(folder or home_dir(), re.sub(r'[^A-Za-z0-9-]', '', str(session_id or '')) + '.json')


def touched(lines, session_id: str, ticket: str, since: float) -> bool:
    """Whether the session made a tool call on `ticket` after `since`: an activity line of its own, newer than the
    record, whose stamp named the ticket. A "stop" or an agent's line names no ticket."""
    sid = re.sub(r'[^A-Za-z0-9-]', '', str(session_id or ''))
    for line in lines:
        parts = line.split()
        if len(parts) < 3 or parts[1] != sid or parts[2] in ('stop', 'agent'):
            continue
        try:
            at = float(parts[0])
        except ValueError:
            continue
        if at > since and str(ticket) in re.findall(r'\d+', ' '.join(parts[2:])):
            return True
    return False


# A reply's stamp and the tickets it names: "11/20/26 3:07 AM PT · ticket: #1929" or "ticket: #857, #858" (the shape
# stamp-gate.sh requires; "ticket: none" names none).
STAMP = re.compile(r'^\s*(?:\*\*)?\d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}\s?[AaPp][Mm][^A-Za-z0-9#]*(?:PT)?[^A-Za-z0-9#]*'
                   r'ticket:\s*(none\b|(?:#\d+\s*,?\s*)+)', re.I)


def _instant(stamp) -> float:
    """A transcript entry's ISO timestamp as epoch seconds; 0 when it cannot be read."""
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(stamp).replace('Z', '+00:00')).timestamp()
    except (TypeError, ValueError):
        return 0.0


def worked(transcript: str, ticket: str, since: float, size: int = 2_000_000) -> bool:
    """Whether the session's own transcript shows a tool call made under a stamp naming `ticket`, after `since`
    (1.40.0, SMADPickleBot #1929). The activity log can name the ticket of the turn before for the first call or two
    after a change of ticket: Claude Code appends a text entry to the transcript a moment after writing it, so
    stamp-gate.sh, which logs each call under the newest text in the file, read the one before. At the turn's end the
    transcript is whole, so this is the same action read from where it is true: an assistant text whose stamp names
    the ticket, then a tool call. A stamp with no call after it is words, and counts for nothing. False for a
    transcript that cannot be read."""
    if not transcript:
        return False
    named = False
    for line in _tail(transcript, size):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get('type') != 'assistant' or _instant(entry.get('timestamp')) <= since:
            continue
        blocks = (entry.get('message') or {}).get('content')
        for block in blocks if isinstance(blocks, list) else ():
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'text':
                m = STAMP.match(block.get('text') or '')
                if m:   # only a stamped text changes what the calls after it are charged to
                    named = str(ticket) in re.findall(r'\d+', m.group(1))
            elif block.get('type') == 'tool_use' and named:
                return True
    return False


def _tail(path: str, size: int = 65536) -> list:
    try:
        with open(path, 'rb') as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - size))
            return f.read().decode('utf-8', 'replace').splitlines()
    except OSError:
        return []


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def check(session_id: str, now: float = None, folder: str = None, log: str = None, transcript: str = None):
    """The Stop hook's answer for a session with a do-now record: {'decision': 'block', 'reason'} the first time its
    turn ends with no tool call on the ticket, else None. See the module's docstring for each case. `transcript` is
    the turn's own (the Stop event's transcript_path): when the log shows no call on the ticket, the transcript is
    read for one (worked, 1.40.0)."""
    if not session_id:
        return None
    path = record_path(session_id, folder)
    try:
        with open(path, encoding='utf-8') as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return None
    ticket = re.sub(r'\D', '', str((rec or {}).get('ticket') or ''))
    try:
        at = float(rec.get('at') or 0)
    except (TypeError, ValueError):
        at = 0.0
    now = time.time() if now is None else now
    if (not ticket or rec.get('held') or now - at > MAX_AGE or touched(_tail(log or log_path()), session_id, ticket, at)
            or worked(transcript, ticket, at)):
        _remove(path)
        return None
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(dict(rec, held=True), f)
    except OSError:
        _remove(path)
    return {'decision': 'block',
            'reason': f"Your project handed you ticket #{ticket} to do now, and this turn made no tool call on it. Start it "
                      f"in this turn: write a line stamped \"ticket: #{ticket}\" and make its first tool call (reading "
                      f"its body counts). If another of your own queued cards is truly next, start that one with your "
                      f"project's ticket tool instead, or say on #{ticket}'s card why it waits. A turn that reads a "
                      f"\"do it now\" and ends without touching the ticket is the empty promise (Gene, 11/19/26: "
                      f"\"But then proceed to do nothing!\"). You are held once; the next end of turn is yours."}
