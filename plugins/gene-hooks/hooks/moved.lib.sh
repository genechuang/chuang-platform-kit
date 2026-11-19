# Sourced by a hook that moved into this plugin from a box's own settings (SMADPickleBot #1810; Gene, 11/18/26: the
# hooks came "from 3 different places ... other sessions may not have all of them", so each moves here).
#
#   moved_elsewhere <script name>   true while this box still runs its own copy of the hook
#
# A box that had the hook wired by hand keeps running that copy until its settings are cleaned, and the plugin's copy
# stands down meanwhile, so no hook runs twice during the move: a plugin release can be installed everywhere first.
# The test is text alone: the user's settings file (GENE_HOOKS_USER_SETTINGS, default ~/.claude/settings.json) naming
# `hooks/<script name>` outside a plugin's own path. Builtins only: one file read, no process started. A file that is
# missing or unreadable is "not elsewhere", so a box with no settings of its own runs the plugin's copy.
moved_elsewhere() {
  local f="${GENE_HOOKS_USER_SETTINGS:-$HOME/.claude/settings.json}" text
  [ -r "$f" ] || return 1
  text=$(<"$f") || return 1
  case "$text" in *"/hooks/$1"*) return 0 ;; esac
  return 1
}
