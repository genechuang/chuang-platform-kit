#!/usr/bin/env bash
# The Pacific clock on every prompt, for every Claude Code session on every project.
#
# Gene, 10/8/26 ~7:10 AM PT, after a session stamped a 7:00 AM note "~9:25 AM" from an
# estimate and two other sessions inherited the error: "Time is one of the most
# important thing to get right in computer science. And you cannot get it right even
# after months and months of my haranguing you. What's going to change this time??"
# A session's sense of elapsed time is not a clock; a rule in a memory note depends
# on the session remembering to look. This hook puts the clock into the context of
# every turn, so a time a session writes is copied from it, never estimated.
#
# Wiring: this plugin's hooks.json runs it (through boot.sh) on UserPromptSubmit and
# PostToolUse. A project whose own hook already says the clock loses nothing; two
# clocks in one prompt are harmless.
#
# Zero processes: a bash builtin with a POSIX zone string (msys has no zone database,
# so TZ=America/Los_Angeles answers UTC on Windows; the rule string below is the
# US DST law and works on msys, glibc and macOS). Every exit is 0; this can never
# block a prompt. One JSON object, as a hook must emit.
#
# Since 10/12/26 12:57 AM PT the same script also answers PostToolUse (Gene: "add a time
# stamp to beginning of every one of your replies"; a reply between prompts had no clock
# and a session wrote "1:00" at 12:48): every tool result carries the clock too, so a
# reply written mid-turn opens with a fresh stamp, never an extrapolated one. Wired as
# `hooks.PostToolUse` (no matcher) beside the UserPromptSubmit entry.
input=""
[ -t 0 ] || IFS= read -r -d '' -t 1 input 2>/dev/null
case "$input" in
  *'"hook_event_name"'*'"UserPromptSubmit"'*) event=UserPromptSubmit ;;
  *'"hook_event_name"'*'"PostToolUse"'*) event=PostToolUse ;;
  *) exit 0 ;;
esac
# Gene's ship words (10/21/26 9:51 AM PT, "yes" to the push gate's release-order check): a prompt that names ship,
# deploy or release is handed to claude-shared's release-words.py, which records it only when the prompt starts with
# one (his own word, never another session's message). A process only for those prompts; silent; never blocks.
if [ "$event" = UserPromptSubmit ]; then
  shopt -s nocasematch
  case "$input" in
    *ship*|*deploy*|*release*)
      for rw in /d/code/claude-shared/hooks/release-words.py "$HOME/code/claude-shared/hooks/release-words.py"; do
        if [ -f "$rw" ]; then printf '%s' "$input" | python "$rw" record >/dev/null 2>&1; break; fi
      done ;;
  esac
  shopt -u nocasematch
fi
clock=$(TZ='PST8PDT,M3.2.0,M11.1.0' printf '%(%a %m/%d/%y %I:%M %p)T' -1 2>/dev/null)
[ -n "$clock" ] || exit 0
if [ "$event" = PostToolUse ]; then
  printf '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"Pacific clock: %s PT. Every reply you write begins with this stamp, the date and the time (mm/dd/yy h:mm am/pm PT), copied, never estimated."}}' "$clock"
  exit 0
fi
printf '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"Pacific clock: %s PT. Every reply you write begins with this stamp, the date and the time (mm/dd/yy h:mm am/pm PT; Gene, 10/17/26). A time you write (a note'"'"'s stamp, a doc, a reply) is copied from this line, never estimated. A stamp from a tool or a machine (GitHub, Cloud Logging, Sentry, Postgres, Pub/Sub) is UTC unless it names a zone, with or without a Z: convert it (PT = UTC-7 PDT / UTC-8 PST) before quoting it. A time from Gene is PT."}}' "$clock"
exit 0
