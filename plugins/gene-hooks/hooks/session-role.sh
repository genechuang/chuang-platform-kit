#!/usr/bin/env bash
# SessionStart: the session's role from the project's sessions manifest, as context (session-role.py says how and why;
# Gene, 10/25/26). `session-role.sh start`. In this plugin since SMADPickleBot #1810; while a box still wires its own
# copy, this one stands down (moved.lib.sh), so no session is told its role twice. A missing script or no Python says
# nothing (a hook blocks only on purpose).
here="$(dirname "${BASH_SOURCE[0]}")"   # not $0: boot.sh sources this file
. "$here/moved.lib.sh" 2>/dev/null && moved_elsewhere session-role.sh && exit 0
py="$here/session-role.py"
[ -f "$py" ] || exit 0
case "$OSTYPE" in msys*|cygwin*) command -v python >/dev/null 2>&1 && exec python "$py" "$@" ;; esac
if command -v python3 >/dev/null 2>&1 && python3 -c '' >/dev/null 2>&1; then
  exec python3 "$py" "$@"
fi
command -v python >/dev/null 2>&1 || exit 0
exec python "$py" "$@"
