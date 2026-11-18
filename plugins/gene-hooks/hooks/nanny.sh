#!/usr/bin/env bash
# PostToolUse and Stop (nanny.py, SMADPickleBot #1214): a background run that waits or goes quiet is put in front of
# its session at the next tool call, and holds the turn's end once. Its launcher: whichever Python this box has
# (`python` first: on Windows `python3` can be the Store's empty alias). No Python: nothing is watched, said, and the
# call goes ahead.
here="$(dirname "${BASH_SOURCE[0]}")"   # not $0: boot.sh sources this file
input=$(cat)
# Nothing to watch, nothing to start (1.31.0, SMADPickleBot #1807: two Python starts, the probe and nanny.py, were
# ~200 ms of every tool call): no background start in this call and no run recorded for the session is answered in
# bash before any Python.
case "$input" in
  *'"run_in_background":true'*|*'"run_in_background": true'*) ;;
  *)
    sid=""
    [[ "$input" =~ \"session_id\"[[:space:]]*:[[:space:]]*\"([^\"]+)\" ]] && sid="${BASH_REMATCH[1]}"
    if [ -n "$sid" ]; then
      state="${NANNY_DIR:-$HOME/.claude/session-nanny}/${sid//[^A-Za-z0-9_.-]/_}.json"
      [ -f "$state" ] || exit 0
      body=$(<"$state")
      case "$body" in *'"started"'*|*'"unmatched"'*) ;; *) exit 0 ;; esac
    fi
    ;;
esac
for py in python python3; do   # the event's hook_event_name says which: Stop, else PostToolUse
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import json' >/dev/null 2>&1; then
    printf '%s' "$input" | "$py" "$here/nanny.py"
    exit 0
  fi
done
printf '{"systemMessage":"session nanny: no Python found; background runs are NOT being watched"}\n'
exit 0
