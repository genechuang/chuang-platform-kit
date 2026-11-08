#!/usr/bin/env bash
# The model check's hold (SMADPickleBot #1144; Gene, 11/7/26 6:04 PM PT: "Hourly model check - once the switch is made
# from fable to opus, skip the hourly model check and only restart after the weekly session reset happens (or at
# handoff when switching to new fresh sessions)"). model-check.lib.sh reads the file this writes with builtins alone;
# a session runs this script, named by its absolute path in the check's own text, so the state is written by a program
# and never by a shell redirect a session types.
#
#   model-check-hold.sh set <ISO 8601 time>   hold the model check until then (the Fable window's resetsAt)
#   model-check-hold.sh clear                 end the hold (the switch back to Fable, or a handover's fresh sessions)
#   model-check-hold.sh show                  print the hold's epoch, or "no hold"
#
# The file holds one line, the hold's Unix epoch: CLAUDE_MODEL_CHECK_HOLD_FILE, else $HOME/.claude/model-check.hold.
# The time is parsed with `date -d` (GNU), else python3 or python (BSD date has no -d); a time neither can read is
# refused with exit 2 and one line, and nothing is written. No network.
file="${CLAUDE_MODEL_CHECK_HOLD_FILE:-$HOME/.claude/model-check.hold}"

to_epoch() {
  local t="$1" e="" py
  case "$t" in
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*) ;;
    *) return 1 ;;
  esac
  e=$(date -d "$t" +%s 2>/dev/null)
  case "$e" in ''|*[!0-9]*) e="" ;; esac
  if [ -z "$e" ]; then
    for py in python3 python; do
      command -v "$py" >/dev/null 2>&1 || continue
      e=$("$py" -c 'import sys, datetime
s = sys.argv[1].strip()
s = s[:-1] + "+00:00" if s.endswith("Z") else s
print(int(datetime.datetime.fromisoformat(s).timestamp()))' "$t" 2>/dev/null)
      case "$e" in ''|*[!0-9]*) e="" ;; *) break ;; esac
    done
  fi
  [ -n "$e" ] || return 1
  printf '%s\n' "$e"
}

case "$1" in
  set)
    epoch=$(to_epoch "$2") || { echo "model-check-hold: cannot read '$2' as an ISO 8601 time; no hold written" >&2; exit 2; }
    # A weekly window resets within 7 days: a hold past HOLD_MAX_S (8 days) from now is a wrong field or year, refused.
    printf -v now '%(%s)T' -1 2>/dev/null || now=$(date +%s)
    [ "$epoch" -le $((now + ${HOLD_MAX_S:-691200})) ] \
      || { echo "model-check-hold: '$2' is more than 8 days ahead, past any weekly reset; no hold written" >&2; exit 2; }
    mkdir -p "${file%/*}" 2>/dev/null
    printf '%s\n' "$epoch" >"$file" || { echo "model-check-hold: cannot write $file" >&2; exit 1; }
    echo "model-check-hold: held until epoch $epoch ($file)"
    ;;
  clear)
    rm -f "$file"
    echo "model-check-hold: cleared ($file)"
    ;;
  show)
    if [ -f "$file" ]; then
      IFS= read -r epoch <"$file"
      echo "${epoch:-no hold}"
    else
      echo "no hold"
    fi
    ;;
  *)
    echo "usage: model-check-hold.sh set <ISO 8601 time> | clear | show" >&2
    exit 2
    ;;
esac
exit 0
