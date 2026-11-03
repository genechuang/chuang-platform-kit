#!/usr/bin/env bash
# The Stop hook's launcher: stamp-reply.py on whichever Python this box has (`python` first: on Windows
# `python3` can be the Store's empty alias). No Python: the turn ends unchecked, never trapped.
here="$(dirname "${BASH_SOURCE[0]}")"   # not $0: boot.sh sources this file
event=""
[ -t 0 ] || IFS= read -r -d '' -t 1 event 2>/dev/null
# The turn's end on the activity log (1.14.0, SMADPickleBot #858): the time from here to the next prompt is waiting on
# the person, never work on a ticket. A helper's Stop (an event naming agent_id) is not the session's.
case "$event" in *'"agent_id"'*) ;; *)
  sid="${event#*\"session_id\"}"
  if [ "$sid" != "$event" ]; then sid="${sid#*\"}"; sid="${sid%%\"*}"; else sid=default; fi
  printf -v now '%(%s)T' -1 2>/dev/null && \
    printf '%s %s stop\n' "$now" "${sid//[^A-Za-z0-9-]/}" >> "${TICKET_ACTIVITY_LOG:-$HOME/.claude/ticket-activity.log}" 2>/dev/null ;;
esac
for py in python python3; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import json' >/dev/null 2>&1; then
    exec "$py" "$here/stamp-reply.py" <<<"$event"
  fi
done
exit 0
