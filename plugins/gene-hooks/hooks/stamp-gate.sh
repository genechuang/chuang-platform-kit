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

# The Pacific clock now, as numbers for awk (a bash builtin with the US DST rule string; msys has no zone database).
read -r nm nd ny nh nmin < <(TZ='PST8PDT,M3.2.0,M11.1.0' printf '%(%m %d %y %H %M)T\n' -1 2>/dev/null)

# One awk over the tail: the newest assistant text since the last real prompt, its opening and its line's uuid.
# Answers "ok", "no <uuid>" (no stamp) or "ahead <uuid> <stamp>" (since 1.4.0, Gene 10/20/26 12:19 AM PT, a phone
# screenshot of "12:22 AM PT" at 12:19: "Your time stamp is drifting badly from my iOS time ... needs to be more
# globally enforced"): a stamp more than a minute past the clock was written from a running guess, not copied. Only
# ahead is refused here: the newest text can honestly be many minutes old when a long command ends, so behind is the
# Stop hook's to judge, on a reply written moments before it runs.
verdict=$(tail -c 262144 "$t" | awk -v nm="$nm" -v nd="$nd" -v ny="$ny" -v nh="$nh" -v nmin="$nmin" '
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
  }')
case "$verdict" in no*|ahead*) ;; *) exit 0 ;; esac
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
