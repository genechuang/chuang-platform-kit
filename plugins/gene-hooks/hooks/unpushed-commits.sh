#!/usr/bin/env bash
# SessionStart and UserPromptSubmit hook: name every sibling repo with commits
# that were made and never pushed, and whose they are to push.
#
# Why: on 2026-10-01 PT a shared rule sat committed and unpushed for nine
# days; Gene had asked for it to be pushed and every other session went on
# reading the old rules. A commit nobody pushed is invisible from every other
# machine and session.
#
# Whose (2026-10-06 PT): this told every session to "ask whether to push
# them", and one project's session offered to push another session's held
# commit in a worktree that session works in and was keeping local on
# purpose. So each repo is named with its kind:
#   - this session's own project: offer to push it;
#   - a linked worktree (its .git is a file): another session's working copy,
#     named with the repo it belongs to; its owner pushes it;
#   - another project: its own sessions push it.
# Only the first is this session's to push.
#
# Whose commit (Gene, 2026-10-13 9:47 AM PT: "never ask to push another
# session's commit, you don't have context to why they aren't committed yet!!"):
# two sessions can share one project folder (a session started in another
# session's clone was told to ask Gene whether to push that session's three
# held commits). The folder does not say whose a
# commit is; its `Session:` trailer does. So each repo is named with the
# sessions its unpushed commits name, and the advice is to push only your own,
# and never to offer, ask about or push another session's.
#
# When (2026-10-06 PT): at SessionStart only, a commit left unpushed mid-day
# went unseen by every session already running. On UserPromptSubmit it now
# looks again at most every RECHECK seconds, and speaks only when the list
# changed since it last spoke, so a known held commit is not repeated on every
# prompt.
#
# What it checks: the repos side by side with the session's project (the
# parent directory of $CLAUDE_PROJECT_DIR, e.g. D:\code\*), each one's current
# branch against its upstream as last fetched. No fetch: "ahead" is about our
# own commits, which the local refs already know, and a network call would
# delay every prompt. A branch with no upstream is named too, since nothing of
# it is on the remote at all.
#
# SAFETY: every exit is 0, so it can never block a session or a prompt; the
# worst it can do is say nothing. Turn it off for one shell with
# CLAUDE_UNPUSHED_HOOK_OFF=1.

[ -n "$CLAUDE_UNPUSHED_HOOK_OFF" ] && exit 0
RECHECK=600
root="${CLAUDE_PROJECT_DIR:-$PWD}"
root="${root//\\//}"
root="${root%/}"
parent="${root%/*}"
[ "$parent" = "$root" ] && exit 0
own="${root##*/}"

input=""
[ -t 0 ] || IFS= read -r -d '' -t 1 input 2>/dev/null
event="SessionStart"
case "$input" in *'"hook_event_name"'*'"UserPromptSubmit"'*) event="UserPromptSubmit" ;; esac

# The per-prompt gate: the cache file's mtime is when it last looked, its
# contents what it last said. Paid on every prompt, so a stat and a read only.
cache="${TMPDIR:-/tmp}/claude-unpushed-${root//[^A-Za-z0-9]/_}"
said=""
if [ "$event" = "UserPromptSubmit" ] && [ -f "$cache" ]; then
  now=$(printf '%(%s)T' -1 2>/dev/null)
  last=$(stat -c %Y "$cache" 2>/dev/null || echo 0)
  [ -n "$now" ] && [ $((now - last)) -lt "$RECHECK" ] 2>/dev/null && exit 0
  IFS= read -r said <"$cache" 2>/dev/null
fi

found=""
others=0
for dir in "$parent"/*/; do
  dir="${dir%/}"
  [ -e "$dir/.git" ] || continue
  name="${dir##*/}"
  branch=$(git -C "$dir" rev-parse --abbrev-ref HEAD 2>/dev/null) || continue
  [ "$branch" = "HEAD" ] && continue          # detached: nothing to push to
  if git -C "$dir" rev-parse -q --verify '@{u}' >/dev/null 2>&1; then
    ahead=$(git -C "$dir" rev-list --count '@{u}..HEAD' 2>/dev/null)
    case "$ahead" in ''|*[!0-9]*|0) continue ;; esac
    # Who made them: each commit's Session trailer, deduplicated, in order.
    by=$(git -C "$dir" log '@{u}..HEAD' --format='%(trailers:key=Session,valueonly,separator=%x2C)' 2>/dev/null \
         | sed 's/^ *//; s/ *$//' | awk 'NF && !seen[$0]++' | paste -sd, - | sed 's/,/, /g')
    what="$ahead unpushed, by ${by:-a session that left no Session trailer}"
  else
    git -C "$dir" remote 2>/dev/null | grep -q . || continue   # no remote: a local-only repo
    what="never pushed: no upstream"
  fi
  if [ "$name" = "$own" ]; then
    whose="this session's project"
  elif [ -f "$dir/.git" ]; then
    common=$(git -C "$dir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
    common="${common%/.git}"
    whose="a worktree of ${common##*/}: another session's working copy, its owner pushes it"
    others=1
  else
    whose="another project: its own sessions push it"
    others=1
  fi
  found="${found:+$found; }$name ($branch, $what; $whose)"
done

printf '%s\n' "$found" >"$cache" 2>/dev/null
[ -n "$found" ] || exit 0
[ "$event" = "UserPromptSubmit" ] && [ "$found" = "$said" ] && exit 0

# Only your own commits are yours to push (Gene, 2026-10-13): the session a commit's
# Session trailer names holds it for a reason you cannot see, so another session's
# are never offered, asked about or pushed, and not mentioned unless the user asks.
advice="Commits are pushed only by the session their Session trailer names. If any here are yours, push them when your task is done (they are invisible to other sessions and machines until then). Never ask the user whether to push another session's commits, never offer to, and never push them: that session holds them for a reason you do not know. Don't mention them unless the user asks."
if [ "$others" = 1 ]; then
  advice="$advice A worktree of another repo, or another project, is its owner's to push; message that session if it should know."
fi
# Repo and branch names only, so nothing here needs JSON escaping beyond quotes.
found="${found//\"/\'}"
printf '{"systemMessage":"Unpushed commits: %s","hookSpecificOutput":{"hookEventName":"%s","additionalContext":"Unpushed commits in sibling repos: %s. %s"}}' \
  "$found" "$event" "$found" "$advice"
exit 0
