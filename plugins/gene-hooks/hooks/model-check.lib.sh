#!/usr/bin/env bash
# The model check, on the box's next prompt (SMADPickleBot #423; Gene, 11/7/26 11:37 AM PT: "add the model check to
# pre-prompt hook, if this doesn't add much latency (if it does throw it as an event): if Fable 5.1 (latest) is at
# 99%, switch to Opus 5.5 (latest); if Fable 5.1 resets to 0%, switch back to Fable 5.1. Do this for all sessions.
# And tell BW to switch SW session if SW cannot switch her own"). It replaces the hourly session cron one session
# kept from 10/26 to 11/7/26 (claude-shared `model-check.md`), which Gene stopped at 11:31 AM PT that day as "too
# noisy and not useful": it died with its session, had to be re-made at every start, and reported every hour whether
# or not anything had changed.
#
# The prompt path pays builtins only, except once an hour: no network and no token, and the one process (the records
# reader, 1.25.0) runs only on the prompt where the hour falls due. One file under $HOME holds the epoch of the
# box's last check; the function reads it with `read`, and when MODEL_CHECK_EVERY seconds (3600) have passed it
# writes the new epoch with `printf` and sets MODEL_CHECK_TEXT, the check handed to THIS session as prompt context:
# read the weekly Fable window with its own get_usage tool, switch every live desktop session to the newest Opus at
# 99% or to the newest Fable under it (the window's weekly reset to 0% is what switches everyone back), its own
# model by a peer (set_session_model refuses a session's own; which peer is the project's manifest's to say, since a
# hook here names no session), and say one line to Gene only when a switch was made or refused. And since 1.17.0
# (Gene, 11/7/26 11:58 AM PT: "when all models usage is at 99% start the handoff process") the same read covers the
# all-models window: at 99% the session starts the account handover's part (a) at once, unless the project's handover
# record shows one already begun for the account today, so an hourly check on an account Gene has not yet left starts
# it once, not every hour. And since 1.18.0 (Gene, 11/7/26 1:27 PM PT: "when you did model switch, you forgot tim apple -
# so need to send an inbox model switch message if Tim cannot be messaged directly") a session on another machine is
# asked by message to switch itself (set_session_model reaches this desktop's sessions alone; the Mac mini's switched
# itself on that message at 1:28 PM PT), the project's session mail when it cannot be messaged.
# And since 1.19.0 (SMADPickleBot #1144; Gene, 11/7/26 6:04 PM PT: "Hourly model check - once the switch is made from
# fable to opus, skip the hourly model check and only restart after the weekly session reset happens (or at handoff
# when switching to new fresh sessions)") the check holds itself: once its target was the newest Opus and every
# session is on it, the text has the session run model-check-hold.sh (beside this file, named by its absolute path)
# with the Fable window's resetsAt, which writes that epoch to the hold file ($HOME/.claude/model-check.hold). While
# now is before it, the due check is the all-models read alone (silent unless it reaches 99, when the handover starts
# as before, so an account that runs out mid-hold still hands over); once the reset passes the hold is spent and the
# full check runs at the next due prompt, the switch back to Fable, whose text has the session clear the hold. The
# handover clears it too (the account-handover skill), so the new account's fresh sessions check at once. A hold file
# that is missing, empty or not an epoch is no hold. The hourly cadence is the same for both texts.
# And since 1.24.0 (SMADPickleBot #1475; Gene, 11/11/26 10:18 PM PT: "I hit 100% Fable 5.2 for all local sessions and
# had to switch them manually to Opus"): Fable read 97 at 8:00 PM PT, no session was prompted from 9:00 to 10:16 PM PT,
# and the window reached 100 in between. So the switch comes earlier, at 95, or sooner when the rise since the last
# reading, carried forward one hour, reaches 99: each full check has the session run model-check-hold.sh note with the
# Fable percent it read (one line "<epoch> <percent>", CLAUDE_MODEL_CHECK_READING_FILE, else
# $HOME/.claude/model-check.fable), and the next check's text carries that reading and its age, so the session works out
# the rate with no tool of its own. And a check whose mark is two intervals old or more opens "late by N minutes", for
# the session to say in its one line, so a gap like that evening's is seen. A timer was not added: the hourly session
# cron of 10/26 to 11/7/26 was dropped as "too noisy and not useful"; the margin covers about two idle hours at that
# evening's rate (about 1.5 points an hour).
# And since 1.25.0 (SMADPickleBot #1491; Gene, 11/12/26 11:50 PM PT: "Do fucking 1491 and make sure it works, because
# this is like the 5th patch on model switching"): at 11:00 PM PT that night he found Snow White and Black Widow on Fable
# at 100% and unresponsive, though the check had been held since 11/11/26 10:17 PM PT as "every session on Opus". Their
# replies did run on Opus after his hand switch on 11/11, but the app's own record of both sessions still said Fable 5.2,
# and the app starts a session from that record. list_sessions carries no model, so no session could see the gap. So
# the hook now reads the records itself (model-check-records.py beside this file: the app's claude-code-sessions/<account>
# records, one python process, only on the prompt where the hour falls due). A held check whose records show any
# session off Opus clears the hold and runs the full check, naming those sessions. A due check hands the session every
# record's model and has it switch each session whose record is not the target, not each one whose replies differ. And
# model-check-hold.sh set reads the records again and refuses the hold while any session is recorded off Opus.
# The check is the box's, not a session's: four sessions share the hour, and whichever is prompted next
# does it, so an idle box checks nothing and a busy one checks once an hour. Why the session reads the window and not
# the hook: get_usage and set_session_model are the desktop app's tools, which only a session can call, and the CLI's
# stored OAuth access token lapses eight hours after a sign-in between CLI runs (11/7/26: a 401 from the usage
# endpoint), so a hook fetching usage itself would have to renew a token it does not own.
#
# Where it runs: a desktop-app session only (CLAUDE_CODE_DESKTOP_APP_VERSION in its environment); the terminal CLI, a
# headless `claude -p` pass and a cloud session have no set_session_model and are left alone. Sourced by
# pacific-clock.sh's prompt branch, as the ship-word recorder is, so every open session has it at its next prompt
# through boot.sh with no reload (a new hooks.json entry would wait for each session's restart). The function sets a
# variable and returns; it never exits and never fails the hook. CLAUDE_MODEL_CHECK_OFF=1 turns it off for one shell;
# CLAUDE_MODEL_CHECK_FILE, CLAUDE_MODEL_CHECK_EVERY and CLAUDE_MODEL_CHECK_HOLD_FILE are the suite's
# (tests/test-hooks-model-check.py).
MODEL_CHECK_EVERY_DEFAULT=3600

model_check() {
  MODEL_CHECK_TEXT=""
  [ -n "$CLAUDE_MODEL_CHECK_OFF" ] && return 0
  [ -n "$CLAUDE_CODE_DESKTOP_APP_VERSION" ] || return 0
  local file="${CLAUDE_MODEL_CHECK_FILE:-$HOME/.claude/model-check.last}"
  local every="${CLAUDE_MODEL_CHECK_EVERY:-$MODEL_CHECK_EVERY_DEFAULT}" now last=""
  case "$every" in ''|*[!0-9]*) every=$MODEL_CHECK_EVERY_DEFAULT ;; esac
  printf -v now '%(%s)T' -1 2>/dev/null || return 0
  [ -f "$file" ] && IFS= read -r last <"$file" 2>/dev/null
  last="${last%$'\r'}"   # a CRLF line (a Windows editor or program) is still the mark, as the hold file's is
  case "$last" in ''|*[!0-9]*) last=0 ;; esac
  [ $((now - last)) -ge "$every" ] || return 0
  # Written before the text is set: a second session prompted in the same second reads the box as checked.
  printf '%s\n' "$now" >"$file" 2>/dev/null || return 0
  # Late (1.24.0): a mark two intervals old or more means no session on the box was prompted when the check fell due.
  local late=""
  if [ "$last" -gt 0 ] && [ $((now - last)) -ge $((2 * every)) ]; then
    late="Model check, late by $(((now - last - every) / 60)) minutes: no session on this box was prompted when it fell due; say so to Gene in your one line. "
  fi
  # The hold script by its absolute path, from this file's own folder (BASH_SOURCE, never $0, which is boot.sh's):
  # forward slashes only, made absolute against PWD, and left unnamed when it holds a quote the JSON line cannot carry.
  local src="${BASH_SOURCE[0]//\\//}" dir holder
  dir="${src%/*}"
  [ "$dir" = "$src" ] && dir=.
  case "$dir" in /*|[A-Za-z]:/*) ;; .) dir="$PWD" ;; *) dir="$PWD/$dir" ;; esac
  case "$dir" in *[\"\'%]*) holder="the model-check-hold.sh beside the gene-hooks plugin's model-check.lib.sh" ;; *) holder="bash '$dir/model-check-hold.sh'" ;; esac
  # The app's own records (1.25.0, SMADPickleBot #1491): model-check-records.py reads the model the desktop app has on
  # record for every unarchived session of the signed-in account, which is the model the app starts that session on.
  # It runs only on the prompt where the hour fell due, once an hour, so this is the one process the check spends.
  local py="" p recs="" rrc=3 offlist="" alllist="" line t m s
  for p in python3 python; do
    command -v "$p" >/dev/null 2>&1 && "$p" -c '' >/dev/null 2>&1 && { py="$p"; break; }
  done
  if [ -n "$py" ] && [ -f "$dir/model-check-records.py" ]; then
    recs=$("$py" "$dir/model-check-records.py" list 2>/dev/null); rrc=$?
  fi
  if [ "$rrc" -eq 0 ]; then
    while IFS=$'\t' read -r t m s; do
      [ -n "$t$m" ] || continue
      m="${m%$'\r'}"; s="${s%$'\r'}"
      alllist="${alllist:+$alllist; }$t on $m ($s)"
      case "$m" in claude-opus-*) ;; *) offlist="${offlist:+$offlist; }$t on $m ($s)" ;; esac
    done <<<"$recs"
  fi
  local records_line
  if [ "$rrc" -eq 0 ]; then
    records_line="The app's own records, read by this hook just now (the model the app starts each session on, whatever its last replies ran on; SMADPickleBot #1491): ${alllist:-no unarchived session}."
  else
    records_line="The app's own session records could not be read by this hook, so read each session's model with get_session (list_sessions carries none)."
  fi
  # The hold (1.19.0): an epoch in the hold file still ahead of now hands the all-models read alone. A file that is
  # missing, empty, not an epoch, past, or more than 8 days ahead (past any weekly reset: a wrong field or year, never
  # a hold for good) is no hold, and the full check runs.
  local holdfile="${CLAUDE_MODEL_CHECK_HOLD_FILE:-$HOME/.claude/model-check.hold}" hold=""
  [ -f "$holdfile" ] && IFS= read -r hold <"$holdfile" 2>/dev/null
  hold="${hold%$'\r'}"
  case "$hold" in ''|*[!0-9]*) hold=0 ;; esac
  [ "${#hold}" -gt 12 ] && hold=0
  [ "$hold" -gt $((now + 691200)) ] && hold=0
  # A hold the records contradict (1.25.0, #1491) is no hold: some session would start on Fable, so the hold ends here
  # and the full check runs now, naming those sessions.
  local broken=""
  if [ "$now" -lt "$hold" ] && [ -n "$offlist" ]; then
    rm -f "$holdfile" 2>/dev/null
    hold=0
    broken="Model check, hold broken (SMADPickleBot #1491): it was held as if every session were on Opus, but the app's own records show $offlist. The app starts a session on its record, so switch each of these now, and tell Gene in your one line which ones were off. "
  fi
  if [ "$now" -lt "$hold" ]; then
    MODEL_CHECK_TEXT="${late}Model check, held (every session is on the newest Opus until the Fable window's weekly reset; SMADPickleBot #1144, Gene 11/7/26 6:04 PM PT). Switch no model this turn. Before your answer, call get_usage and read the one window labeled Weekly / all models: at 99 or more the account handover starts now, before anything else, unless your project's handover record shows one already begun for this account today (the account-handover skill says what marks it): tell Gene in one line that the account is at 99 percent and the handover has begun, run $holder clear so the new account's fresh sessions get the full check, then run that skill's part (a). Otherwise say nothing of this check."
    [ "$rrc" -eq 0 ] || MODEL_CHECK_TEXT="$MODEL_CHECK_TEXT The app's session records could not be read by this hook, so this hold was not checked against them (SMADPickleBot #1491): say so to Gene in one line."
    return 0
  fi
  # The last reading (1.24.0): "<epoch> <percent>" from model-check-hold.sh note, read with builtins. A file that is
  # missing, garbled, from the future or over a week old carries no reading, and the 95 rule stands alone.
  local readfile="${CLAUDE_MODEL_CHECK_READING_FILE:-$HOME/.claude/model-check.fable}" rline="" rat="" rpct="" prior
  [ -f "$readfile" ] && IFS= read -r rline <"$readfile" 2>/dev/null
  rline="${rline%$'\r'}"
  rat="${rline%% *}"
  rpct="${rline#* }"
  prior="No earlier reading is on record, so the 95 rule stands alone."
  case "$rat$rpct" in
    ''|*[!0-9]*) ;;
    *)
      if [ "${#rat}" -le 12 ] && [ "${#rpct}" -le 3 ] && [ "$rat" -le "$now" ] && [ $((now - rat)) -le 604800 ] && [ $((now - rat)) -ge 60 ]; then
        prior="The last reading was Fable at $((10#$rpct)) percent, $(((now - rat) / 60)) minutes ago."
      fi
      ;;
  esac
  MODEL_CHECK_TEXT="${late}${broken}Model check, due now (the box's hourly check rides this prompt; SMADPickleBot #423, Gene 11/7/26 11:37 and 11:58 AM PT). This turn, before your answer, call get_usage and read two plan windows. (1) The window labeled Weekly / Fable (its percentUsed): the target is the newest Opus when it is 95 or more, or when its rise since the last reading, carried forward one hour (now plus the rise times 60 over the minutes since that reading), reaches 99 (SMADPickleBot #1475: an idle hour has no check, so the switch leaves that margin); else the newest Fable (the window's weekly reset to 0% is what switches everyone back). $prior $records_line Read the ids from the picker's own list: set_session_model with model list-ids is refused and lists them; the newest is the highest version of that family; never a hand-typed or dated id. Then set_session_model with the target's full id on every unarchived session of this desktop whose recorded model differs from the target (the records above; a session they do not list, read with get_session, since list_sessions carries no model), even one whose replies already run on the target, but your own; for your own, when it differs, one SendMessage to the peer session your project's manifest names for it (claude-shared sessions.md), naming your local_ id and the target id, since the tool refuses a session's own model; a box with no peer session names that in the line below. Then ListAgents: every session on another machine (a Remote Control row) gets one SendMessage asking it to move itself to the target id and answer when it has, since set_session_model reaches this desktop's sessions alone; one that cannot be reached gets your project's session mail instead, and the switch is said as done only for those that answered. (2) The window labeled Weekly / all models: at 99 or more the account handover starts now, before anything else, unless your project's handover record shows one already begun for this account today (the account-handover skill says what marks it): tell Gene in one line that the account is at 99 percent and the handover has begun, then run that skill's part (a). (3) The hold (SMADPickleBot #1144, Gene 11/7/26 6:04 PM PT): when the target was the newest Opus and every session of this desktop is on it (switched, or already) and every session on another machine answered that it is, run $holder set with the Fable window's resetsAt from get_usage, so the box checks only the all-models window until that reset (it reads the app's records again and refuses, exit 3, naming each session still recorded on another model: switch those, or have your peer switch yours, and run it again; never write the hold another way); when the target is the newest Fable, run $holder clear. (4) The reading: run $holder note with the Fable percent you read, whatever the target, so the next check has the rate. Say one line to Gene only when something was switched, refused or started, or the check was late; otherwise say nothing of this check."
  return 0
}
