#!/usr/bin/env bash
# SessionStart: say so when this installed gene-hooks plugin is behind a chuang-platform-kit checkout beside the project
# (10/12/26: a desktop ran a plugin eight days behind, so every session got old advice; its marketplace had not
# refreshed in a week), and since 1.20.0 (SMADPickleBot #1169) when the newest installed gene-shared is behind a
# claude-shared checkout beside the project, whose skills a session reads (11/8/26: a session followed a stale
# handover skill). Auto-update keeps most boxes current; this catches one that is not. Every exit is 0; one JSON
# object at most.
ver() { sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$1" 2>/dev/null | head -1; }
msg=""
installed=$(ver "${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json")
latest=$(ver "${CLAUDE_PROJECT_DIR:-.}/../chuang-platform-kit/plugins/gene-hooks/.claude-plugin/plugin.json")
if [ -n "$installed" ] && [ -n "$latest" ] && [ "$installed" != "$latest" ]; then
  msg="gene-hooks plugin $installed is installed, chuang-platform-kit has $latest: run  claude plugin marketplace update gene-hooks  then  claude plugin update gene-hooks@gene-hooks  (open sessions pick up hook changes at their next hook)"
fi
shared_cache="${CLAUDE_PLUGIN_ROOT%/*/*/*}/gene-shared/gene-shared"
shared_installed=$(ls -1 "$shared_cache" 2>/dev/null | grep -E '^[0-9]+(\.[0-9]+)*$' | sort -V | tail -1)
shared_latest=$(ver "${CLAUDE_PROJECT_DIR:-.}/../claude-shared/plugins/gene-shared/.claude-plugin/plugin.json")
if [ -n "$shared_installed" ] && [ -n "$shared_latest" ] && [ "$shared_installed" != "$shared_latest" ]; then
  msg="${msg:+$msg; }gene-shared $shared_installed is installed, claude-shared has $shared_latest: run  claude plugin marketplace update gene-shared  then  claude plugin update gene-shared@gene-shared  (a loaded skill is pointed at the newest installed copy)"
fi
[ -n "$msg" ] || exit 0
printf '{"systemMessage":"%s"}\n' "$msg"
exit 0
