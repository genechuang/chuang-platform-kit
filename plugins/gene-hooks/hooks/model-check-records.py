#!/usr/bin/env python3
"""The desktop app's own record of each session's model, read from disk (gene-hooks 1.25.0; SMADPickleBot #1491).

Gene, 11/12/26 11:50 PM PT, after he found Snow White and Black Widow on Fable at 100% and unresponsive while the model
check was held: "Do fucking 1491 and make sure it works, because this is like the 5th patch on model switching". The
check had set its hold on 11/11/26 10:17 PM PT because every session's replies ran on Opus. But the app's record of
those two sessions still said Fable 5.2: the switch back that night changed what each session process ran, not the
record. The app starts a session from its record, so once their processes were restarted both were back on Fable. A
session's tools could not see this, since list_sessions carries no model. So the check now reads the records itself
and passes what they say to the session, instead of leaving it to the session to notice.

The app keeps one JSON record per session at claude-code-sessions/<account uuid>/<folder>/local_<id>.json under its
data folder, with sessionId, title, model and isArchived. config.json beside it names the signed-in account
(lastKnownAccountUuid). The data folder is CLAUDE_MODEL_CHECK_RECORDS when that is set (the suite's), else every
%LOCALAPPDATA%/Claude-Data/claude_* folder on Windows, else ~/Library/Application Support/Claude on a Mac.

    model-check-records.py list          every unarchived session of the signed-in account: title<TAB>model<TAB>id
    model-check-records.py off <family>  only those whose model is not claude-<family>-*

It prints one line per session and exits 0, or exits 3 with one line on stderr when no record can be read (no data
folder, no account, no records), so a caller can tell an empty answer from an unread one. Standard library only, no
network. It reads files and writes none.
"""
import glob
import json
import os
import sys


def roots():
    """The app's data folders, the suite's override first."""
    given = os.environ.get('CLAUDE_MODEL_CHECK_RECORDS')
    if given:
        return [given]
    found = []
    local = os.environ.get('LOCALAPPDATA')
    if local:
        found += sorted(glob.glob(os.path.join(local, 'Claude-Data', 'claude_*')))
    found.append(os.path.join(os.path.expanduser('~'), 'Library', 'Application Support', 'Claude'))
    return found


def records():
    """(title, model, session id) for every unarchived record of the signed-in account, or None when none was read."""
    out, read_any = [], False
    for root in roots():
        try:
            with open(os.path.join(root, 'config.json'), encoding='utf-8') as f:
                account = (json.load(f) or {}).get('lastKnownAccountUuid')
        except (OSError, ValueError):
            continue
        if not account:
            continue
        for path in glob.glob(os.path.join(root, 'claude-code-sessions', account, '*', 'local_*.json')):
            try:
                with open(path, encoding='utf-8') as f:
                    rec = json.load(f)
            except (OSError, ValueError):
                continue
            read_any = True
            if not isinstance(rec, dict) or rec.get('isArchived'):
                continue
            out.append((str(rec.get('title') or rec.get('sessionId') or '?'), str(rec.get('model') or 'unknown'),
                        str(rec.get('sessionId') or os.path.basename(path)[:-5])))
    return sorted(out) if read_any else None


def main(argv):
    if not argv or argv[0] not in ('list', 'off') or (argv[0] == 'off' and len(argv) < 2):
        print('usage: model-check-records.py list | off <family>', file=sys.stderr)
        return 2
    rows = records()
    if rows is None:
        print('model-check-records: no session record of the signed-in account could be read', file=sys.stderr)
        return 3
    if argv[0] == 'off':
        prefix = 'claude-' + argv[1].lower() + '-'
        rows = [r for r in rows if not r[1].lower().startswith(prefix)]
    for row in rows:
        # The hook carries these into a JSON line it builds by hand: plain ASCII, no quote, backslash, percent or tab.
        title, model, sid = (''.join(c for c in v if c.isascii() and c.isprintable() and c not in '"\\%\t').strip() or '?'
                             for v in row)
        print(f'{title}\t{model}\t{sid}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
