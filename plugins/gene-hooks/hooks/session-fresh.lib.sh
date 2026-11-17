# Sourced by pacific-clock.sh on UserPromptSubmit (1.28.0, SMADPickleBot #1665): two things a session cannot see for
# itself, each handed to it once, on its next prompt.
#
# 1. Stale skills. plugin-version.sh, on SessionStart with source compact or resume, writes a mark for the session
#    (stale-skills.mark under STATE). The next prompt finds the mark, removes it and runs stale-skills.py once on the
#    transcript, which names every re-stated skill whose copy is older than the newest installed one. Why: on 11/16/26 a
#    compacted session followed the account-handover skill at 0.8.18 while 0.8.31 was installed (Gene, 10:09 PM PT:
#    "Should the first job of handover session to load latest skill then???").
#
# 2. An account switch. The signed-in account of the desktop app (config.json's lastKnownAccountUuid, the file
#    model-check-records.py reads) is kept per box with the time it changed (account-switch.box, "<uuid> <epoch>"); a
#    session whose acknowledged account (ack-<session id>, the account it was last told of or first saw) is not the
#    signed-in one while that switch is under a day old is handed the switch note once. Why (Gene, 11/16/26 10:13 PM PT: "Why do I even need to prompt the hibernating sw to read bootstrap.md?"):
#    a hibernating session woken after a switch could not know the account had changed, so Gene had to paste a line.
#    The note's text is the box's own, ACCOUNT_SWITCH_NOTE (default $HOME/.claude/account-switch-note.txt, one line),
#    since the kit names no project; with no note file a generic line is handed. A switch more than a day old is told
#    to no session that never saw it (a session started long after is no woken one).
#
# Builtins only on a prompt with neither mark nor switch: a few file reads and no process. Never fails the hook: any
# miss leaves FRESH_TEXT empty. FRESH_TEXT is JSON-safe (quotes and backslashes escaped, one line).
FRESH_TEXT=""
_fresh_json() {
  local s=$1
  s=${s//$'\r'/}
  s=${s//$'\n'/ }
  s=${s//$'\t'/ }
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  printf '%s' "$s"
}
session_fresh() {
  local state="${CLAUDE_SESSION_FRESH_STATE:-$HOME/.claude/session-fresh}" sid="" re now
  re='"session_id" *: *"([^"]+)"'
  [[ "$input" =~ $re ]] && sid=${BASH_REMATCH[1]}
  case "$sid" in ''|*[!A-Za-z0-9_-]*) return 0 ;; esac
  now=$(printf '%(%s)T' -1)
  local out=""
  # 1. stale skills, once after a compaction or resume
  if [ -f "$state/stale-$sid.mark" ]; then
    rm -f "$state/stale-$sid.mark" 2>/dev/null
    local tp="" py="" p found=""
    re='"transcript_path" *: *"([^"]+)"'
    [[ "$input" =~ $re ]] && tp=${BASH_REMATCH[1]//\\\\/\/}
    local dir="${BASH_SOURCE[0]%/*}"
    [ "$dir" = "${BASH_SOURCE[0]}" ] && dir=.
    for p in python3 python; do
      command -v "$p" >/dev/null 2>&1 && "$p" -c '' >/dev/null 2>&1 && { py="$p"; break; }
    done
    if [ -n "$tp" ] && [ -n "$py" ] && [ -f "$dir/stale-skills.py" ]; then
      found=$("$py" "$dir/stale-skills.py" "$tp" 2>/dev/null)
    fi
    if [ -n "$found" ]; then
      found=${found//$'\n'/; }
      out="Stale skills (SMADPickleBot #1665): your context carries skill text from before a compaction or resume, and ${found}. Before you follow any of these, load it again with the Skill tool; the text your context holds is not the one installed."
    fi
  fi
  # 2. an account switch since this session last saw one
  local roots=() r cfg="" content="" uuid=""
  if [ -n "${CLAUDE_SESSION_FRESH_APP:-}" ]; then
    roots=("$CLAUDE_SESSION_FRESH_APP")
  else
    [ -n "${LOCALAPPDATA:-}" ] && for r in "${LOCALAPPDATA//\\//}"/Claude-Data/claude_*; do roots+=("$r"); done
    roots+=("$HOME/Library/Application Support/Claude")
  fi
  for r in "${roots[@]}"; do [ -f "$r/config.json" ] && { cfg="$r/config.json"; break; }; done
  if [ -n "$cfg" ]; then
    IFS= read -r -d '' content <"$cfg" 2>/dev/null
    re='"lastKnownAccountUuid" *: *"([0-9A-Za-z-]+)"'
    [[ "$content" =~ $re ]] && uuid=${BASH_REMATCH[1]}
  fi
  if [ -n "$uuid" ]; then
    mkdir -p "$state" 2>/dev/null
    local box_uuid="" box_at="" ack=""
    [ -f "$state/account-switch.box" ] && IFS=' ' read -r box_uuid box_at <"$state/account-switch.box" 2>/dev/null
    box_at=${box_at%$'\r'}
    case "$box_at" in ''|*[!0-9]*) box_at=0 ;; esac
    if [ -z "$box_uuid" ]; then
      printf '%s 0\n' "$uuid" >"$state/account-switch.box" 2>/dev/null   # first sight: no switch to tell
      box_at=0
    elif [ "$box_uuid" != "$uuid" ]; then
      box_at=$now
      printf '%s %s\n' "$uuid" "$box_at" >"$state/account-switch.box" 2>/dev/null
    fi
    # the session's acknowledgment is the account it was last told of (or first saw), so two switches in one second
    # are still two
    [ -f "$state/ack-$sid" ] && IFS= read -r ack <"$state/ack-$sid" 2>/dev/null
    ack=${ack%$'\r'}
    if [ "$box_at" -gt 0 ] && [ $((now - box_at)) -le 86400 ]; then
      if [ "$ack" != "$uuid" ]; then
        printf '%s\n' "$uuid" >"$state/ack-$sid" 2>/dev/null
        local note_file="${ACCOUNT_SWITCH_NOTE:-$HOME/.claude/account-switch-note.txt}" note=""
        [ -f "$note_file" ] && IFS= read -r note <"$note_file" 2>/dev/null
        note=${note%$'\r'}
        [ -n "$note" ] || note="Follow your project's instructions for a session woken after an account switch before anything else."
        out="${out:+$out }Account switch (SMADPickleBot #1665): the Claude account signed in on this box changed since this session's last prompt. ${note}"
      fi
    elif [ "$ack" != "$uuid" ]; then
      printf '%s\n' "$uuid" >"$state/ack-$sid" 2>/dev/null   # no recent switch: this account is simply what it sees
    fi
  fi
  [ -n "$out" ] && FRESH_TEXT=$(_fresh_json "$out")
  return 0
}
