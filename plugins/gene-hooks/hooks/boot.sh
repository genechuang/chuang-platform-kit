#!/usr/bin/env bash
# The plugin's bootstrap loader (Gene, 10/14/26 4:42 PM PT: "do it"). Every hook in hooks.json runs through it:
#   bash "${CLAUDE_PLUGIN_ROOT}/hooks/boot.sh" <script>
# It runs <script> from the NEWEST installed version of this plugin in the plugin cache, not the version this session
# loaded at its start, so a plugin bump reaches every open session at its next hook, with no /reload-plugins and no
# restart (one desktop session ran old stamp rules for twelve hours; a cloud session cannot reload at all). What it cannot do: add a hook on a new event, an agent or a skill; those still load at session start.
#
# Keep this file small and unchanged: a change to the loader itself needs a reload everywhere. The lookup stays in
# this shell (no ls, no sort, no subshell): 0.6 ms a call against 57 ms for a forked lookup on Windows (measured
# 10/14/26). Every path falls back to this session's own copy, and a script that is missing exits 0.
name="$1"
shift
own="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
best="$own"
bestv="${own##*/}"

is_version() { [[ "$1" =~ ^[0-9]+(\.[0-9]+)*$ ]]; }

newer() {   # is dotted version $1 above $2 (numeric per part, so 0.5.10 > 0.5.9)
  local IFS=. i a b
  local -a x=($1) y=($2)
  for ((i = 0; i < ${#x[@]} || i < ${#y[@]}; i++)); do
    a=${x[i]:-0}; b=${y[i]:-0}
    ((10#$a > 10#$b)) && return 0
    ((10#$a < 10#$b)) && return 1
  done
  return 1
}

# Only a versioned cache directory has siblings to choose from (a --plugin-dir or marketplace checkout runs its own).
if is_version "$bestv"; then
  for d in "${own%/*}"/*/; do
    d="${d%/}"
    v="${d##*/}"
    is_version "$v" && [ -f "$d/hooks/$name" ] && newer "$v" "$bestv" && { best="$d"; bestv="$v"; }
  done
fi

[ -f "$best/hooks/$name" ] || exit 0
export CLAUDE_PLUGIN_ROOT="$best"
# Sourced, not exec'd: a second bash is ~17 ms on Windows (measured 10/14/26), the lookup 0.6 ms. A script finds its
# own folder through ${BASH_SOURCE[0]}, never $0 (which is this loader's), and its `exit` ends the hook as before.
. "$best/hooks/$name" "$@"
