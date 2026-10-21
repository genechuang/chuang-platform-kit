#!/usr/bin/env bash
# PreToolUse on Bash and PowerShell (foreground-guard.py): a known long runner in the foreground is refused, so the
# session stays reachable (Gene, 10/21/26). Its launcher: whichever Python this box has (`python` first: on Windows
# `python3` can be the Store's empty alias). No Python: the call goes ahead unchecked, never trapped.
here="$(dirname "${BASH_SOURCE[0]}")"   # not $0: boot.sh sources this file
for py in python python3; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import json' >/dev/null 2>&1; then
    exec "$py" "$here/foreground-guard.py"
  fi
done
exit 0
