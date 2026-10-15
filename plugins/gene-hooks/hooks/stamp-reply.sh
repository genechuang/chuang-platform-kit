#!/usr/bin/env bash
# The Stop hook's launcher: stamp-reply.py on whichever Python this box has (`python` first: on Windows
# `python3` can be the Store's empty alias). No Python: the turn ends unchecked, never trapped.
here="$(dirname "${BASH_SOURCE[0]}")"   # not $0: boot.sh sources this file
for py in python python3; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import json' >/dev/null 2>&1; then
    exec "$py" "$here/stamp-reply.py"
  fi
done
exit 0
