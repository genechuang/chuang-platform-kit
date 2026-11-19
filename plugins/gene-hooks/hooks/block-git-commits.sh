#!/usr/bin/env bash
# PreToolUse(Bash): refuse the git invocations that have produced wrong commits. Exits 2 to block, 0 to allow.
# In this plugin since 1.35.0 (SMADPickleBot #1810), so every project has the shared commit rules; a project whose own
# settings still wire its copy keeps that one, and this stands down meanwhile (moved.lib.sh).
#
# WHAT THIS DELIBERATELY DOES NOT DO: enforce "ask before committing". A hook cannot verify that a human approved
# anything: whatever marker it checked, the agent would be the one setting it. Consent stays a behavioural rule. This
# file only blocks what is mechanically detectable.
#
# Each pattern has an incident behind it:
#   git add -A / git add .   swept hundreds of unrelated deletions into a hotfix
#   git commit -a            left a new file untracked, so a record missed the commit that needed it
#   --no-verify              skips the hooks the deploy gates depend on
#   push --force             clobbers another session's push; several sessions push to one repo
#   commit without           the shared rule "Author as Claude" went unenforced: 10 of 12 commits on a main branch
#   -c user.name=Claude      were authored as the owner by a desktop session (10/4/26)
#
# TO TURN OFF: touch .claude/hooks/.disabled in the project, or export CLAUDE_GIT_GUARD_OFF=1.
#
# SCOPE: this fires on any matching git command, including one aimed at a throwaway repo somewhere else on disk.
# Scoping it by parsing a leading `cd` out of the command was rejected (8/30/26): any command containing a cd
# elsewhere would skip the guard entirely, and compound commands are the common case. The override is the intended
# path for scratch repos, and the deny message says so.
#
# KNOWN FALSE POSITIVE: this matches text, so a command that merely CONTAINS a blocked pattern as data (a test
# harness, a grep for it) is blocked too. Put the text in a file and run the file, or use the override. Blocking a
# command that mentions a footgun is the tolerable failure; missing one that runs it is not.
#
# PERFORMANCE: PreToolUse(Bash) fires on every Bash call, so this starts no process at all. Pure bash matching.
here="$(dirname "${BASH_SOURCE[0]}")"   # not $0: boot.sh sources this file
. "$here/moved.lib.sh" 2>/dev/null && moved_in_project block-git-commits.sh && exit 0

[ -f "${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/.disabled" ] && exit 0
[ -n "$CLAUDE_GIT_GUARD_OFF" ] && exit 0

input=""
[ -t 0 ] || IFS= read -r -d '' -t 1 input 2>/dev/null
case "$input" in *'"command"'*) ;; *) exit 0 ;; esac

# The command string, without jq. Take everything after "command":" and stop at the closing quote, which is the first
# UNESCAPED one: the naive cut stopped at the first quote of any kind, so `git commit -m "fix" && git push -f` was
# seen as `git commit -m \` and the force push sailed through. Escaped quotes are swapped for a byte that cannot
# appear in JSON text, the cut is made at the first survivor, then they are swapped back.
cmd=${input#*'"command"'}
cmd=${cmd#*\"}
cmd=${cmd//\\\"/$'\x01'}
cmd=${cmd%%\"*}
cmd=${cmd//$'\x01'/\"}

case "$cmd" in *git*) ;; *) exit 0 ;; esac

# One trailing space, so every pattern below can demand a word boundary: without it `git add .gitignore` and
# `git add ./a/file.py` were blocked ("git add ." is a prefix) and `git push -f` at the end of a command was allowed.
cmd="$cmd "

deny() {
  printf '%s\n' "$1" >&2
  printf '%s\n' "If this is a THROWAWAY repo (a test fixture, a scratch clone), the override is the intended path: this hook cannot tell a disposable repo from this one. Say why to the user, then: export CLAUDE_GIT_GUARD_OFF=1" >&2
  exit 2
}

case "$cmd" in
  *"git add -A "*|*"git add --all "*|*"git add . "*)
    deny "BLOCKED: 'git add -A/.' stages everything, including unrelated churn. Name the files explicitly." ;;
esac

case "$cmd" in
  # ` commit -a ` with git anywhere before it: a commit authored as Claude has `-c` flags between `git` and `commit`,
  # so "git commit -a" alone let the usual form through (found by this hook's own suite at the move, 11/19/26)
  *"git commit -a "*|*"git commit --all "*|*"commit -am "*|*"commit -ma "*|*git*" commit -a "*|*git*" commit --all "*)
    deny "BLOCKED: 'git commit -a' commits every tracked modification and silently skips new files. Stage explicitly." ;;
esac

case "$cmd" in
  *--no-verify*|*--no-gpg-sign*)
    deny "BLOCKED: skipping hooks or signing. If a hook fails, fix the cause." ;;
esac

# Author as Claude: a commit from a Claude session carries `-c user.name=Claude`, so the history says who wrote it.
# ` commit ` anywhere in a git command is the trigger; a `git log` whose text happens to mention a commit is the
# documented, tolerable false positive. The flags may sit before `commit` (`git -c ... commit`).
case "$cmd" in
  *"git"*" commit "*)
    case "$cmd" in
      *"user.name=Claude"*) ;;
      *) deny "BLOCKED: a Claude session commits as Claude. Re-run as: git -c user.name=Claude -c user.email=noreply@anthropic.com commit ..." ;;
    esac ;;
esac

case "$cmd" in
  *"push"*"--force"*|*"push"*" -f "*)
    case "$cmd" in
      *--force-with-lease*) ;;
      *) deny "BLOCKED: force push without --force-with-lease. Several sessions push to one repo; a plain --force discards whatever another one pushed." ;;
    esac ;;
esac

exit 0
