# Sourced by pacific-clock.sh on PostToolUse (1.20.0, SMADPickleBot #1169): a skill a session loads is checked against
# the newest installed copy of its plugin.
#
# Why: boot.sh makes every HOOK run the newest installed script, but a SKILL's text is the copy the session loaded when
# it started. On 11/8/26 a desktop session begun 10/30/26 on gene-shared 0.8.18 loaded its account-handover skill and
# told Gene to start a new session per project, where 0.8.28, installed on the same box, said to wake the hibernating
# ones (Gene, 12:32 AM PT: "How was this missed in this session, reading old handoff protocol?").
#
# skill_current reads the Skill tool's response in $input for its "Base directory for this skill:" line, the copy's
# folder <cache>/<marketplace>/<plugin>/<version>/skills/<name>. When the newest version folder beside it is a different
# one and has the same skill, it sets SKILL_CURRENT_TEXT to a line naming both and the newest SKILL.md to read instead;
# otherwise it leaves it empty. Sourced (not run) so it costs a tool call that is not a Skill call nothing but one case
# test, and a new hooks.json entry, which a running session would not read until a reload, is never needed.
# Never fails the hook: every miss leaves SKILL_CURRENT_TEXT empty.
SKILL_CURRENT_TEXT=""
skill_current() {
  case "$input" in *'"tool_name":"Skill"'*|*'"tool_name": "Skill"'*) ;; *) return 0 ;; esac
  local re='Base directory for this skill: ([^"]*)' dir
  [[ "$input" =~ $re ]] || return 0
  dir=${BASH_REMATCH[1]}
  dir=${dir%%\\n*}                      # the line ends at the JSON's escaped newline
  dir=${dir//\\\\/\/}                   # JSON's doubled backslashes to slashes
  dir=${dir//\\//}
  # C:/Users/... as /c/Users/... (Git Bash); tr, not ${x,,}, since the Mac's /bin/bash is 3.2
  case "$dir" in [A-Za-z]:/*) dir="/$(printf '%s' "${dir:0:1}" | tr 'A-Z' 'a-z')${dir:2}" ;; esac
  dir=${dir%/}
  local name=${dir##*/} skills=${dir%/*}
  [ "${skills##*/}" = skills ] || return 0
  local verdir=${skills%/*}
  local ver=${verdir##*/} plugdir=${verdir%/*}
  [ -d "$plugdir" ] || return 0
  local newest
  newest=$(ls -1 "$plugdir" 2>/dev/null | grep -E '^[0-9]+(\.[0-9]+)*$' | sort -V | tail -1)
  [ -n "$newest" ] && [ "$newest" != "$ver" ] || return 0
  [ -f "$plugdir/$newest/skills/$name/SKILL.md" ] || return 0
  # Only a newer copy: a session already on the newest needs nothing, and an older folder named newest by a stray
  # name is left out by the version-only filter above.
  [ "$(printf '%s\n%s\n' "$ver" "$newest" | sort -V | tail -1)" = "$newest" ] || return 0
  SKILL_CURRENT_TEXT="This skill's text is the copy this session loaded at its start, ${ver}; the newest installed is ${newest}. Read ${plugdir}/${newest}/skills/${name}/SKILL.md and follow that instead, never the older text above (SMADPickleBot #1169)."
}
