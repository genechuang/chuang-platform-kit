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
field() {  # the string value of "$1" in the event, by parameter expansion (no grep/sed)
  local v="${event#*\"$1\"}"
  [ "$v" != "$event" ] || return 1
  v="${v#*\"}"
  printf '%s' "${v%%\"*}"
}
t="$(field transcript_path)" || exit 0
t="${t//\\\\//}"
[ -n "$t" ] && [ -f "$t" ] || exit 0

# One awk over the tail: the newest assistant text since the last real prompt, its opening and its line's uuid.
# Answers "ok", or "no <uuid>".
verdict=$(tail -c 262144 "$t" | awk '
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
    print (last ~ /^[0-9][0-9]?\/[0-9][0-9]?\/[0-9][0-9]([0-9][0-9])?[ \t]+[0-9][0-9]?:[0-9][0-9] ?(AM|PM|am|pm)/) ? "ok" : "no " id
  }')
case "$verdict" in no*) ;; *) exit 0 ;; esac
id="${verdict#no }"

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
printf 'Stamp first: your last message opened without the Pacific date and time. Start every message with the stamp: write one line that starts "%s PT" (copy the clock from the latest hook line, never estimate), then make the call again. (gene-hooks stamp-gate.sh; Gene 10/14/26: every message carries the PT stamp; 10/17/26: the date too, mm/dd/yy.)\n' "${clock:-<the PT clock>}" >&2
exit 2
