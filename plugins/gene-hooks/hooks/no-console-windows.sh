#!/usr/bin/env bash
# SessionStart: claude-shared's "No window pops up" check over this Windows box's scheduled tasks and the session's
# own repo (Gene, 10/20/26 10:44 PM PT: "no-console check at session start"; claude-shared hooks/no-console-windows.ps1,
# which the app watchdog also runs hourly with -Fix and each repo's push gate runs with -Repo .). A finding becomes one
# line for Gene and the list in the session's context, so the session fixes it in its own code; it never blocks.
# Anywhere but a Windows box with claude-shared beside the projects (the Mac, a cloud session) it does nothing.
event=""
[ -t 0 ] || IFS= read -r -d '' -t 1 event 2>/dev/null
check="${NO_CONSOLE_CHECK:-D:/code/claude-shared/hooks/no-console-windows.ps1}"
command -v powershell.exe >/dev/null 2>&1 || exit 0
[ -f "$check" ] || exit 0
cwd="${event#*\"cwd\"}"
if [ "$cwd" != "$event" ]; then cwd="${cwd#*\"}"; cwd="${cwd%%\"*}"; cwd="${cwd//\\\\/\\}"; else cwd="$PWD"; fi

found=$(powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "& '$check' -TasksOnly | Where-Object { \$_ -like 'task:*' }; if (Test-Path (Join-Path '$cwd' '.git')) { & '$check' -Repo '$cwd' | Where-Object { \$_ -like 'code:*' -or \$_ -like '      *' } }" 2>/dev/null | tr -d '\r')
[ -n "$found" ] || exit 0
n=$(printf '%s\n' "$found" | grep -c '^\(task\|code\):')
esc() { local s="${1//\\/\\\\}"; s="${s//\"/\\\"}"; s="${s//$'\t'/ }"; printf '%s' "${s//$'\n'/\\n}"; }
advice="claude-shared CLAUDE.md, No window pops up: a task starts conhost.exe --headless <program> (no-console-windows.ps1 -Fix rewrites it); code uses CREATE_NO_WINDOW, never DETACHED_PROCESS; windowsHide: true beside detached: true; Start-Process with -WindowStyle Hidden or -NoNewWindow. Fix what is yours; tell its owner otherwise."
printf '{"systemMessage":"No console windows: %s finding(s) on this box; the session has the list","hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"%s"}}' \
  "$n" "$(esc "Console-window findings (a window would pop up on Gene's desktop):
$found
$advice")"
exit 0
