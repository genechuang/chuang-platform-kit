#!/usr/bin/env bash
# PostToolUse (Bash, Read, Agent) and Stop: a push to the project's main owes a release profile (release-profile-gate.py
# says how and why; Gene, 10/22/26). `release-profile-gate.sh post|stop`. In this plugin since SMADPickleBot #1810;
# while a box still wires its own copy, this one stands down (moved.lib.sh), so no turn is held twice. A missing script
# or no Python lets everything through (a hook blocks only on purpose).
here="$(dirname "${BASH_SOURCE[0]}")"   # not $0: boot.sh sources this file
. "$here/moved.lib.sh" 2>/dev/null && moved_elsewhere release-profile-gate.sh && exit 0
py="$here/release-profile-gate.py"
[ -f "$py" ] || exit 0
case "$OSTYPE" in msys*|cygwin*) command -v python >/dev/null 2>&1 && exec python "$py" "$@" ;; esac
if command -v python3 >/dev/null 2>&1 && python3 -c '' >/dev/null 2>&1; then
  exec python3 "$py" "$@"
fi
command -v python >/dev/null 2>&1 || exit 0
exec python "$py" "$@"
