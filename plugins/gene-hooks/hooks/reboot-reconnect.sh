#!/usr/bin/env bash
# After a reboot, the first session that wakes reconnects every other local session's Remote Control.
#
# Gene, 10/19/26 9:11 AM PT, after the desktop's Hypervisor Platform reboot: "Claude app came up because
# it's in startup run, but none of the local sessions started remote control automatically and I have
# to be at the dev box now to reconnect you all"; 9:17 AM: "Why don't you put this in a session
# awake/restart hook so I don't have to prompt it for you".
#
# What happens at a reboot: the app restores its sessions, but an idle one has no turn, and the app's
# Remote Control request is refused until it runs one ("requires an active session. Send a message
# first"). A session restored busy does run a turn, and this hook fires on it. A hook is a shell
# script with no access to the app's session tools, so it does not reconnect anything itself: it
# notices the fresh boot, claims the job once per boot (a marker named by the boot time), and hands
# the session the instruction, which it carries out with list_sessions and SendMessage to each
# session's local_... id (the bare name resolves to the dead claude.ai row and wakes nothing).
#
# Fires on SessionStart and UserPromptSubmit, Windows (Git Bash) only: the desktop is where the local
# sessions live; the Mac mini and the cloud sandboxes have no app sessions to reconnect. Within
# REBOOT_WINDOW seconds of the boot only. Every exit is 0; one JSON object at most.
#   CLAUDE_REBOOT_UPTIME=<seconds>  (tests) stand in for /proc/uptime and skip the OS check
#   CLAUDE_REBOOT_MARK_DIR=<dir>    (tests) where the boot's marker is kept (default ~/.claude/reboot-reconnect)
#   CLAUDE_NO_REBOOT_RECONNECT=1    turns it off
#
# In this plugin since SMADPickleBot #1810 (claude-shared's hooks/reboot-reconnect.sh until then, wired by hand in the
# desktop's own settings, where it fired for every session on the box). Two things changed in the move: while a box
# still wires its own copy, this one stands down (moved.lib.sh), so the job is never handed over twice; and it speaks
# only in a project that keeps a sessions manifest, the file its committed `.claude/gene-hooks.toml` names as
# `[sessions] manifest` (a path relative to the project's root, the folder CLAUDE_PROJECT_DIR or the working folder is
# in). A project that names none, or whose file is not there, has no sessions to reconnect: nothing is said and no
# boot is claimed. The file is read with builtins alone, as project_config.py's small reader reads it.
here="$(dirname "${BASH_SOURCE[0]}")"   # not $0: boot.sh sources this file
. "$here/moved.lib.sh" 2>/dev/null && moved_elsewhere reboot-reconnect.sh && exit 0

manifest_there() {   # true when the project's config names a sessions manifest and the file is there
  local top="${CLAUDE_PROJECT_DIR:-$PWD}" line section="" rel=""
  top="${top//\\//}"; top="${top%/}"
  while [ -n "$top" ] && [ ! -f "$top/.claude/gene-hooks.toml" ]; do
    case "$top" in */*) top="${top%/*}" ;; *) return 1 ;; esac
  done
  [ -n "$top" ] && [ -f "$top/.claude/gene-hooks.toml" ] || return 1
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    line="${line#"${line%%[![:space:]]*}"}"
    case "$line" in
      '['*) section="${line%%]*}"; section="${section#[}" ;;
      manifest*=*)
        [ "$section" = sessions ] || continue
        rel="${line#*=}"; rel="${rel#*[\"\']}"; rel="${rel%%[\"\']*}" ;;
    esac
  done <"$top/.claude/gene-hooks.toml"
  [ -n "$rel" ] || return 1
  case "$rel" in /*|[A-Za-z]:*) [ -f "$rel" ] ;; *) [ -f "$top/$rel" ] ;; esac
}

input=""
[ -t 0 ] || IFS= read -r -d '' -t 1 input 2>/dev/null
case "$input" in
  *'"hook_event_name"'*'"SessionStart"'*) event=SessionStart ;;
  *'"hook_event_name"'*'"UserPromptSubmit"'*) event=UserPromptSubmit ;;
  *) exit 0 ;;
esac
[ -n "$CLAUDE_NO_REBOOT_RECONNECT" ] && exit 0

REBOOT_WINDOW=1800   # 30 minutes: long enough for the app to restore and a restored session to wake

if [ -n "$CLAUDE_REBOOT_UPTIME" ]; then
  up="$CLAUDE_REBOOT_UPTIME"
else
  case "$(uname -s 2>/dev/null)" in MINGW*|MSYS*) ;; *) exit 0 ;; esac
  read -r up _ </proc/uptime 2>/dev/null || exit 0
fi
up="${up%%.*}"
case "$up" in ''|*[!0-9]*) exit 0 ;; esac
[ "$up" -gt "$REBOOT_WINDOW" ] && exit 0
manifest_there || exit 0

now=$(printf '%(%s)T' -1 2>/dev/null) || exit 0
boot=$(( (now - up) / 60 * 60 ))   # the boot time to the minute: one marker per boot, however many sessions wake
dir="${CLAUDE_REBOOT_MARK_DIR:-$HOME/.claude/reboot-reconnect}"
mkdir -p "$dir" 2>/dev/null || exit 0
mark="$dir/$boot"
# The claim: the first session to create the marker does the job; set -C makes the create fail for the rest.
( set -C; : >"$mark" ) 2>/dev/null || exit 0
minutes=$(( up / 60 ))

task="This computer booted $minutes minute(s) ago. The Claude app restored its sessions, but an idle local session has no Remote Control until it runs a turn, and Gene could not reach them from his phone (10/19/26). This session claimed the job for this boot; do it FIRST, before anything else: (0) call mcp__ccd_session_mgmt__set_remote_control with session_id self and enabled true (list_sessions leaves this session out); (1) call mcp__ccd_session_mgmt__list_sessions; (2) for every session that is not archived, has remoteControlActive false and a lastActivityAt within the last 3 days, call SendMessage with \`to\` set to its local_... sessionId (NEVER its name: the name resolves to the dead claude.ai Remote Control row and wakes nothing), one line saying the box rebooted and this turn reconnects it; (3) call list_sessions again and tell Gene in one line which sessions are back on Remote Control and which are not. Memory: session-unreachable-fix-it."
esc() { local s="${1//\\/\\\\}"; printf '%s' "${s//\"/\\\"}"; }
printf '{"systemMessage":"reboot %s min ago: this session reconnects the other local sessions","hookSpecificOutput":{"hookEventName":"%s","additionalContext":"%s"}}' \
  "$minutes" "$event" "$(esc "$task")"
exit 0
