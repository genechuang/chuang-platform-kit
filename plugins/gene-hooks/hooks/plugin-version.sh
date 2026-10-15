#!/usr/bin/env bash
# SessionStart: say so when this installed gene-hooks plugin is behind a chuang-platform-kit checkout beside the project
# (10/12/26: a desktop ran a plugin eight days behind, so every session got old advice; its marketplace had not
# refreshed in a week). Auto-update keeps most boxes current; this catches one that is not. Every exit is 0; one JSON
# object at most.
installed=$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json" 2>/dev/null | head -1)
source_json="${CLAUDE_PROJECT_DIR:-.}/../chuang-platform-kit/plugins/gene-hooks/.claude-plugin/plugin.json"
latest=$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$source_json" 2>/dev/null | head -1)
[ -n "$installed" ] && [ -n "$latest" ] && [ "$installed" != "$latest" ] || exit 0
printf '{"systemMessage":"gene-hooks plugin %s is installed, chuang-platform-kit has %s: run  claude plugin marketplace update gene-hooks  then  claude plugin update gene-hooks@gene-hooks  (open sessions pick up hook changes at their next hook)"}\n' "$installed" "$latest"
exit 0
