#!/usr/bin/env bash
# The model check, on the box's next prompt (SMADPickleBot #423; Gene, 11/7/26 11:37 AM PT: "add the model check to
# pre-prompt hook, if this doesn't add much latency (if it does throw it as an event): if Fable 5.1 (latest) is at
# 99%, switch to Opus 5.5 (latest); if Fable 5.1 resets to 0%, switch back to Fable 5.1. Do this for all sessions.
# And tell BW to switch SW session if SW cannot switch her own"). It replaces the hourly session cron one session
# kept from 10/26 to 11/7/26 (claude-shared `model-check.md`), which Gene stopped at 11:31 AM PT that day as "too
# noisy and not useful": it died with its session, had to be re-made at every start, and reported every hour whether
# or not anything had changed.
#
# The prompt path pays builtins only: no process, no network, no token. One file under $HOME holds the epoch of the
# box's last check; the function reads it with `read`, and when MODEL_CHECK_EVERY seconds (3600) have passed it
# writes the new epoch with `printf` and sets MODEL_CHECK_TEXT, the check handed to THIS session as prompt context:
# read the weekly Fable window with its own get_usage tool, switch every live desktop session to the newest Opus at
# 99% or to the newest Fable under it (the window's weekly reset to 0% is what switches everyone back), its own
# model by a peer (set_session_model refuses a session's own; which peer is the project's manifest's to say, since a
# hook here names no session), and say one line to Gene only when a switch was made or refused. And since 1.17.0
# (Gene, 11/7/26 11:58 AM PT: "when all models usage is at 99% start the handoff process") the same read covers the
# all-models window: at 99% the session starts the account handover's part (a) at once, unless the project's handover
# record shows one already begun for the account today, so an hourly check on an account Gene has not yet left starts
# it once, not every hour. The check is the box's, not a session's: four sessions share the hour, and whichever is prompted next
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
# CLAUDE_MODEL_CHECK_FILE and CLAUDE_MODEL_CHECK_EVERY are the suite's (tests/test-hooks-model-check.py).
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
  case "$last" in ''|*[!0-9]*) last=0 ;; esac
  [ $((now - last)) -ge "$every" ] || return 0
  # Written before the text is set: a second session prompted in the same second reads the box as checked.
  printf '%s\n' "$now" >"$file" 2>/dev/null || return 0
  MODEL_CHECK_TEXT="Model check, due now (the box's hourly check rides this prompt; SMADPickleBot #423, Gene 11/7/26 11:37 and 11:58 AM PT). This turn, before your answer, call get_usage and read two plan windows. (1) The window labeled Weekly / Fable (its percentUsed): the target is the newest Opus when it is 99 or more, else the newest Fable (the window's weekly reset to 0% is what switches everyone back). Read the ids from the picker's own list: set_session_model with model list-ids is refused and lists them; the newest is the highest version of that family; never a hand-typed or dated id. Then list_sessions and set_session_model with the target's full id on every live, unarchived session of this desktop whose model differs, but your own; for your own, when it differs, one SendMessage to the peer session your project's manifest names for it (claude-shared sessions.md), naming your local_ id and the target id, since the tool refuses a session's own model; a box with no peer session names that in the line below. (2) The window labeled Weekly / all models: at 99 or more the account handover starts now, before anything else, unless your project's handover record shows one already begun for this account today (the account-handover skill says what marks it): tell Gene in one line that the account is at 99 percent and the handover has begun, then run that skill's part (a). Say one line to Gene only when something was switched, refused or started; otherwise say nothing of this check."
  return 0
}
