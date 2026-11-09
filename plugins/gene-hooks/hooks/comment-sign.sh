#!/usr/bin/env bash
# PreToolUse on Bash and PowerShell (comment-sign.py): a GitHub comment a session writes carries its signature line
# (1.23.0). Its launcher: whichever Python this box has (`python` first: on Windows `python3` can be the Store's empty
# alias). No Python: the call goes ahead unchecked, never trapped.
here="$(dirname "${BASH_SOURCE[0]}")"   # not $0: boot.sh sources this file
for py in python python3; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import json' >/dev/null 2>&1; then
    exec "$py" "$here/comment-sign.py"
  fi
done
exit 0
