#!/usr/bin/env bash
# PreToolUse on every tool: every line a session writes must open with the Pacific stamp ("12:43 AM PT"); a tool call
# after an unstamped line is refused until the session writes a stamped one (Gene, 10/14/26 12:36 AM PT, after the
# one-line updates between tool calls kept arriving on his phone with no time: "enforce the PT stamp on EVERY
# message"). stamp-reply (Stop) covers a turn's last reply.
#
# Which line it checks (fixed 10/14/26 1:20 AM PT; a session found the first cut never blocked):
# the transcript is written as each block finishes, and the text just written before a tool call is often not on disk
# yet when PreToolUse fires. So the gate checks the NEWEST text written since the last real prompt (a user line that
# is neither a tool result nor an isMeta injection), looking past tool results: a miss is caught at the latest one
# call later. A text line already refused is remembered by its uuid (a small state file per session), so one miss
# blocks at most three calls, not every call after it (once until 1.3.0, which a retry slipped past).
#
# Cost is the design: it runs before EVERY tool call. Starting Python costs ~130 ms on the Windows desktop and every
# extra program ~30-40 ms there (msys process start; measured 10/14/26: a 6-program first cut took 240 ms), so this
# is `tail` (the transcript's last 256 KB) and one `awk`, plus one `grep` of the state file only on a miss; the input,
# the paths and the clock are bash builtins. Exempt: a turn with no text yet, a helper (an event naming agent_id), a
# missing transcript. Exit 2 refuses the call with the reason on stderr; anything unexpected exits 0, so a broken hook
# never traps a session.
event=""
[ -t 0 ] || IFS= read -r -d '' -t 1 event 2>/dev/null
case "$event" in *'"agent_id"'*) exit 0 ;; esac
# The foreground guard rides this hook (1.7.0, Gene 10/21/26 12:05 AM PT: long runners in the background, "add it to a
# hook"): a hook added to hooks.json reaches a session only at its next start, while this script runs from the newest
# installed version on every call of every open session (boot.sh). Shell calls only, about 130 ms each.
case "$event" in
  *'"tool_name":"Bash"'*|*'"tool_name": "Bash"'*|*'"tool_name":"PowerShell"'*|*'"tool_name": "PowerShell"'*)
    guard_err=$(printf '%s' "$event" | bash "$(dirname "${BASH_SOURCE[0]}")/foreground-guard.sh" 2>&1 >/dev/null)
    if [ $? -eq 2 ]; then printf '%s\n' "$guard_err" >&2; exit 2; fi ;;
esac
field() {  # the string value of "$1" in the event, by parameter expansion (no grep/sed)
  local v="${event#*\"$1\"}"
  [ "$v" != "$event" ] || return 1
  v="${v#*\"}"
  printf '%s' "${v%%\"*}"
}
t="$(field transcript_path)" || exit 0
t="${t//\\\\//}"
[ -n "$t" ] && [ -f "$t" ] || exit 0

# The Pacific clock now, as numbers for awk (a bash builtin with the US DST rule string; msys has no zone database).
read -r nm nd ny nh nmin < <(TZ='PST8PDT,M3.2.0,M11.1.0' printf '%(%m %d %y %H %M)T\n' -1 2>/dev/null)

# One awk over the tail: the newest assistant text since the last real prompt, its opening and its line's uuid.
# Answers "ok", "no <uuid>" (no stamp) or "ahead <uuid> <stamp>" (since 1.4.0, Gene 10/20/26 12:19 AM PT, a phone
# screenshot of "12:22 AM PT" at 12:19: "Your time stamp is drifting badly from my iOS time ... needs to be more
# globally enforced"): a stamp more than a minute past the clock was written from a running guess, not copied. Only
# ahead is refused here: the newest text can honestly be many minutes old when a long command ends, so behind is the
# Stop hook's to judge, on a reply written moments before it runs.
judge() { tail -c 262144 "$t" | awk -v nm="$nm" -v nd="$nd" -v ny="$ny" -v nh="$nh" -v nmin="$nmin" '
  function mins(y, m, d, h, mi,   a, yy, mm) {  # minutes since an epoch, from the civil date (Howard Hinnant)
    if (y < 100) y += 2000
    a = (m <= 2); yy = y - a; mm = m + (a ? 9 : -3)
    return ((365 * yy + int(yy / 4) - int(yy / 100) + int(yy / 400) + int((153 * mm + 2) / 5) + d) * 1440) + h * 60 + mi
  }
  /"type":"user"/ { if ($0 !~ /"tool_result"/ && $0 !~ /"isMeta":true/) { last = ""; id = "" } next }
  /"type":"assistant"/ {
    s = $0; found = 0
    while ((i = index(s, "\"type\":\"text\",\"text\":\"")) > 0) { s = substr(s, i + 22); last = substr(s, 1, 40); found = 1 }
    if (found) { id = ""; if (match($0, /"uuid":"[^"]*"/)) id = substr($0, RSTART + 8, RLENGTH - 9) }
  }
  END {
    if (last == "") { print "ok"; exit }
    gsub(/^([ \t]|\\n)+/, "", last); sub(/^\*\*/, "", last)
    # The date leads the time (Gene, 10/17/26 5:21 AM PT: "add date too mm/dd/yy"), required since 1.2.0: "10/17/26 5:21 AM PT".
    if (!match(last, /^[0-9][0-9]?\/[0-9][0-9]?\/[0-9][0-9]([0-9][0-9])?[ \t]+[0-9][0-9]?:[0-9][0-9] ?(AM|PM|am|pm)/)) { print "no " id; exit }
    st = substr(last, 1, RLENGTH); split(st, p, /[\/ \t:]+/)
    h = p[4] % 12; if (toupper(substr(st, length(st) - 1)) == "PM") h += 12
    if (nh != "" && mins(p[3] + 0, p[1] + 0, p[2] + 0, h, p[5] + 0) > mins(ny + 0, nm + 0, nd + 0, nh + 0, nmin + 0) + 1) { print "ahead " id " " st; exit }
    print "ok"
  }'; }
# The task-list gate rides this hook too (1.11.0, Gene 10/30/26 12:34 AM PT: "update your steps progress in real time"):
# one awk counts the tool calls since the newest plan call (a tool named *update_plan or TodoWrite) or list close
# (*wrap_up); only at PLAN_GATE_LIMIT or more does plan-gate.py start, to replay the list and refuse when a step is
# still open. Most calls cost the awk alone.
plan_gate() {
  local n
  n=$(tail -c 2000000 "$t" | awk '/"type":"assistant"/ {
      s = $0
      while ((i = index(s, "\"type\":\"tool_use\"")) > 0) {
        s = substr(s, i + 17); nm = s; sub(/^[^}]*"name":"/, "", nm); sub(/".*$/, "", nm)
        if (nm ~ /update_plan$/ || nm == "TodoWrite" || nm ~ /wrap_up$/) c = 0; else c++
      }
    } END { print c + 0 }')
  [ "${n:-0}" -ge "$((${PLAN_GATE_LIMIT:-12} - 1))" ] || return 0
  local err py
  py=$(command -v python || command -v python3) || return 0
  err=$(printf '%s' "$event" | "$py" "$(dirname "${BASH_SOURCE[0]}")/plan-gate.py" 2>&1 >/dev/null)
  if [ $? -eq 2 ]; then printf '%s\n' "$err" >&2; exit 2; fi
  return 0
}
verdict=$(judge)
case "$verdict" in no*|ahead*) ;; *) plan_gate; exit 0 ;; esac

# A miss is read again before it is refused (1.5.0, Gene 10/20/26 1:55 PM PT; Snow White's transcript that afternoon):
# Claude Code stamps a text entry when it is made but appends it to the file later, so the gate often judged the line
# before the newest one: a stamped line written after a refusal was refused again, 2.5 s after its own timestamp, and an
# unstamped one after a stamped line went through. A short wait lets the newest line land, and costs only a miss; a
# rescue is written to the state file as "rescued <uuid>" so the wait's worth can be counted. STAMP_GATE_WAIT is the
# sleeps in seconds ("" skips them, for the tests).
for w in ${STAMP_GATE_WAIT-0.25 0.25 0.5}; do
  sleep "$w" 2>/dev/null || break
  again=$(judge)
  case "$again" in
    no*|ahead*) verdict="$again" ;;
    *) sid="$(field session_id)" || sid=default
       printf 'rescued %s\n' "${verdict#* }" >> "${TMPDIR:-${TEMP:-/tmp}}/stamp-gate-${sid//[^A-Za-z0-9-]/}" 2>/dev/null
       exit 0 ;;
  esac
done
kind="${verdict%% *}"
id="${verdict#* }"; stamped="${id#* }"; id="${id%% *}"

# Refuse each unstamped line up to three times: its uuid goes into the session's state file once per refusal.
# Once (1.0.0 to 1.2.0) let a session that retried the call without writing a stamped line through, and the
# unstamped line stayed on Gene's phone (10/19/26 11:01 AM PT, Black Widow's "11:00 AM PT": "Sometimes a session
# prompt would forget date stamp"). Three refusals force the stamped line; the cap keeps a stuck session moving.
sid="$(field session_id)" || sid=default
state="${TMPDIR:-${TEMP:-/tmp}}/stamp-gate-${sid//[^A-Za-z0-9-]/}"
if [ -n "$id" ] && [ -f "$state" ] && [ "$(grep -cxF "$id" "$state" 2>/dev/null)" -ge 3 ]; then
  exit 0
fi
[ -n "$id" ] && printf '%s\n' "$id" >> "$state" 2>/dev/null

# pacific-clock.sh's way: a bash builtin with the US DST rule string (msys has no zone database).
clock=$(TZ='PST8PDT,M3.2.0,M11.1.0' printf '%(%m/%d/%y %I:%M %p)T' -1 2>/dev/null)
clock="${clock/ 0/ }"
if [ "$kind" = ahead ]; then
  printf 'Stamp ahead of the clock: your last message says "%s" but it is %s PT now. A stamp is copied from the latest "Pacific clock:" hook line, never advanced by felt time. Write one line that starts "%s PT", then make the call again. (gene-hooks stamp-gate.sh; Gene 10/20/26: "Your time stamp is drifting badly from my iOS time".)\n' "$stamped" "${clock:-<the PT clock>}" "${clock:-<the PT clock>}" >&2
  exit 2
fi
printf 'Stamp first: your last message opened without the Pacific date and time. Start every message with the stamp: write one line that starts "%s PT" (copy the clock from the latest hook line, never estimate), then make the call again. (gene-hooks stamp-gate.sh; Gene 10/14/26: every message carries the PT stamp; 10/17/26: the date too, mm/dd/yy.)\n' "${clock:-<the PT clock>}" >&2
exit 2
