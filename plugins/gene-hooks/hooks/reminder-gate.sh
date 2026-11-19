#!/usr/bin/env bash
# Stop: a reply that reminds Gene of pending items lists them in full (reminder-gate.py; Gene, 10/27/26). In this
# plugin since 1.34.0 (SMADPickleBot #1810); while a box still wires its own copy, this one stands down
# (moved.lib.sh). A hook blocks only on purpose: a missing .py or no Python says so and lets the reply through.
here="$(dirname "${BASH_SOURCE[0]}")"   # not $0: boot.sh sources this file
. "$here/moved.lib.sh" 2>/dev/null && moved_elsewhere reminder-gate.sh && exit 0
py="$here/reminder-gate.py"
warn() { printf '{"systemMessage":"reminder-gate hook: %s; reminders are NOT being checked"}\n' "$1"; exit 0; }
[ -f "$py" ] || warn "$py not found"
case "$OSTYPE" in msys*|cygwin*) command -v python >/dev/null 2>&1 && exec python "$py" ;; esac
if command -v python3 >/dev/null 2>&1 && python3 -c '' >/dev/null 2>&1; then
  exec python3 "$py"
fi
command -v python >/dev/null 2>&1 || warn "no python on this box"
exec python "$py"
