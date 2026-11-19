#!/usr/bin/env bash
# PreToolUse on Bash and PowerShell: no file content through the shell (no-shell-writes.py says what it refuses and
# why; Gene, 10/10/26). In this plugin since 1.34.0 (SMADPickleBot #1810); until then a box wired its own copy by
# hand, and while it still does, this one stands down (moved.lib.sh).
#
# A hook blocks only on purpose: Python exits 2 when it cannot open its script, and Claude Code reads a PreToolUse
# exit 2 as a refusal, so a missing .py or no Python at all says so and lets the call through (exit 0).
here="$(dirname "${BASH_SOURCE[0]}")"   # not $0: boot.sh sources this file
. "$here/moved.lib.sh" 2>/dev/null && moved_elsewhere no-shell-writes.sh && exit 0
py="$here/no-shell-writes.py"
warn() { printf '{"systemMessage":"no-shell-writes hook: %s; shell writes are NOT being checked"}\n' "$1"; exit 0; }
[ -f "$py" ] || warn "$py not found"
# Windows (msys): straight to python. Testing python3 first started a whole interpreter on every shell call,
# ~100 ms of this hook's ~340 ms (measured 10/14/26), to learn what $OSTYPE already says.
case "$OSTYPE" in msys*|cygwin*) command -v python >/dev/null 2>&1 && exec python "$py" ;; esac
if command -v python3 >/dev/null 2>&1 && python3 -c '' >/dev/null 2>&1; then
  exec python3 "$py"
fi
command -v python >/dev/null 2>&1 || warn "no python on this box"
exec python "$py"
