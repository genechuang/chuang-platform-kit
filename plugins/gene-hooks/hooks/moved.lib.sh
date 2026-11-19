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

#   moved_in_project <script name>   true while the project's own settings still wire its copy of the hook
#
# The same stand-down for a hook a project wired in its committed `.claude/settings.json` (1.35.0: the commit-rules
# hook was one project's own): the file under CLAUDE_PROJECT_DIR (else the working folder), read once, text alone.
moved_in_project() {
  local f="${CLAUDE_PROJECT_DIR:-$PWD}/.claude/settings.json" text
  [ -r "$f" ] || return 1
  text=$(<"$f") || return 1
  case "$text" in *"/hooks/$1"*) return 0 ;; esac
  return 1
}
