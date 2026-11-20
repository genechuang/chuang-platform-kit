#!/usr/bin/env bash
# UserPromptSubmit, PostToolUse and SessionStart: the session's unread mail from ~/.claude/session-mail, handed to it as
# context and marked read (session-mail.py says how and why; SMADPickleBot #391, Gene 10/26/26). In this plugin since
# 1.38.0 (SMADPickleBot #1810); while a box still wires its own copy, this one stands down (moved.lib.sh), so no letter
# is handed over twice. A missing script or no Python lets everything through (a hook blocks only on purpose).
here="$(dirname "${BASH_SOURCE[0]}")"   # not $0: boot.sh sources this file
. "$here/moved.lib.sh" 2>/dev/null && moved_elsewhere session-mail.sh && exit 0
py="$here/session-mail.py"
[ -f "$py" ] || exit 0
case "$OSTYPE" in msys*|cygwin*) command -v python >/dev/null 2>&1 && exec python "$py" "$@" ;; esac
if command -v python3 >/dev/null 2>&1 && python3 -c '' >/dev/null 2>&1; then
  exec python3 "$py" "$@"
fi
command -v python >/dev/null 2>&1 || exit 0
exec python "$py" "$@"
