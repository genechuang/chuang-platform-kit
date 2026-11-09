#!/usr/bin/env bash
# PostToolUse and Stop (nanny.py, SMADPickleBot #1214): a background run that waits or goes quiet is put in front of
# its session at the next tool call, and holds the turn's end once. Its launcher: whichever Python this box has
# (`python` first: on Windows `python3` can be the Store's empty alias). No Python: nothing is watched, said, and the
# call goes ahead.
here="$(dirname "${BASH_SOURCE[0]}")"   # not $0: boot.sh sources this file
for py in python python3; do   # the event's hook_event_name says which: Stop, else PostToolUse
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import json' >/dev/null 2>&1; then
    exec "$py" "$here/nanny.py"
  fi
done
printf '{"systemMessage":"session nanny: no Python found; background runs are NOT being watched"}\n'
exit 0
