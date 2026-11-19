#!/usr/bin/env bash
# Keep every session's checkout caught up with what other sessions push, on every project.
#
# Gene, 10/10/26 7:07 AM PT: "Why don't your pre prompt hook automatically catch main up anymore? ... make sure it's
# in shared Claude so other sessions can take advantage of it". On UserPromptSubmit and, since this plugin carries it
# (1.35.0, SMADPickleBot #1810), on SessionStart too: at most once every 120 s it fetches origin, then catch-up.sh
# fast-forwards the checked-out branch when that cannot lose work, and the session is told what moved underneath it.
# When it is behind and cannot safely move (open edits on the incoming files, unpushed commits, a merge under way),
# it says so and why.
#
# It stands down, so the fetch never runs twice:
#   - while the box's own settings still wire its copy of this hook (moved.lib.sh);
#   - in a project whose own hook already calls catch-up.sh (its `.claude/hooks/fetch-origin.sh`).
# CLAUDE_NO_CATCH_UP turns it off. Every exit is 0; one JSON object at most.
here="$(dirname "${BASH_SOURCE[0]}")"   # not $0: boot.sh sources this file
. "$here/moved.lib.sh" 2>/dev/null && moved_elsewhere catch-up-hook.sh && exit 0
input=""
[ -t 0 ] || IFS= read -r -d '' -t 1 input 2>/dev/null
case "$input" in
  *'"hook_event_name"'*'"UserPromptSubmit"'*) event=UserPromptSubmit ;;
  *'"hook_event_name"'*'"SessionStart"'*) event=SessionStart ;;
  *) exit 0 ;;
esac
[ -n "$CLAUDE_NO_CATCH_UP" ] && exit 0

proj="${CLAUDE_PROJECT_DIR:-$PWD}"
grep -qs 'catch-up.sh' "$proj/.claude/hooks/fetch-origin.sh" && exit 0
gitdir=$(git -C "$proj" rev-parse --absolute-git-dir 2>/dev/null) || exit 0
git -C "$proj" remote get-url origin >/dev/null 2>&1 || exit 0

# The hook's own clock, not FETCH_HEAD's: any other fetch in the repo touches that.
stamp="$gitdir/claude-catch-up-stamp"
now=$(printf '%(%s)T' -1 2>/dev/null)
last=$(stat -c %Y "$stamp" 2>/dev/null || echo 0)
[ -n "$now" ] && [ $((now - last)) -lt 120 ] 2>/dev/null && exit 0
: >"$stamp" 2>/dev/null
GIT_TERMINAL_PROMPT=0 git -C "$proj" fetch origin --quiet 2>/dev/null

out=$(bash "$here/catch-up.sh" "$proj" 2>/dev/null)
esc() { local s="${1//\\/\\\\}"; printf '%s' "${s//\"/\\\"}"; }
branch=$(git -C "$proj" symbolic-ref -q --short HEAD 2>/dev/null)
case "$out" in
  "caught-up "*)
    rest="${out#caught-up }"; n="${rest%% *}"; rest="${rest#* }"; range="${rest%% *}"
    files="${rest#* }"; [ "$files" = "$rest" ] && files=""
    more=""
    [ -n "$(git -C "$proj" diff --name-only "$range" -- CLAUDE.md 2>/dev/null)" ] \
      && more=" CLAUDE.md changed: re-read it before acting on its rules."
    printf '{"systemMessage":"git: caught up %s commit(s) on %s - changed: %s","hookSpecificOutput":{"hookEventName":"%s","additionalContext":"Repo state: the hook fast-forwarded %s by %s commit(s) another session pushed (%s). Changed: %s. Read what changed before relying on those files.%s"}}' \
      "$n" "$(esc "$branch")" "$(esc "$files")" "$event" "$(esc "$branch")" "$n" "$range" "$(esc "$files")" "$more" ;;
  "skipped a "*|"skipped open edits"*|*"unpushed commit"*|"skipped the fast-forward"*)
    why=$(esc "${out#skipped }")
    up=$(git -C "$proj" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null)
    printf '{"systemMessage":"git: %s is behind %s and was not caught up (%s)","hookSpecificOutput":{"hookEventName":"%s","additionalContext":"Repo state: local branch %s is behind %s; another session has pushed. The hook did not fast-forward it: %s. Bring it up to date before editing (stash uncommitted work, git merge --ff-only %s or rebase your commits, restore)."}}' \
      "$(esc "$branch")" "$(esc "$up")" "$why" "$event" "$(esc "$branch")" "$(esc "$up")" "$why" "$(esc "$up")" ;;
esac
exit 0
