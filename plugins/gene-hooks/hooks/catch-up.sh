#!/usr/bin/env bash
# catch-up.sh DIR — fast-forward DIR's checked-out branch to its upstream, but only when that is
# guaranteed safe, so a session is never left working on stale files just because it skipped a
# warning (Gene, 10/10/26 7:07 AM PT: "Why don't your pre prompt hook automatically catch main up
# anymore? ... make sure it's in shared Claude so other sessions can take advantage of it").
#
# It never fetches (the caller already did, so this costs nothing on the common path) and never
# touches a file the session has open edits on. It fast-forwards only when ALL of these hold:
#   - DIR is a git checkout on a branch (not detached) with an upstream;
#   - no merge, rebase, cherry-pick, revert or bisect is in progress;
#   - the branch has no commits of its own that the upstream lacks (nothing unpushed);
#   - it is behind the upstream;
#   - no file the upstream changed is modified, staged or untracked here.
# Then `git merge --ff-only`. Anything else: no change, and it says why.
#
# Output, one line on stdout, for the calling hook to put in front of the session:
#   caught-up <n> <old>..<new> <changed files, comma-separated, at most 8 then "+k more">
#   skipped <reason>
#   current                    (nothing to do)
# Exit status is always 0: a hook must never block a prompt.

# --rebase (SMADPickleBot #621, 10/31/26): a branch with commits of its own that the upstream lacks is rebased onto it
# instead of skipped, when no tracked file has a change and nothing is under way; a conflict aborts the rebase, leaving
# the branch as it was, and says which files (`skipped rebasing ... would conflict on ...`). Output on success:
#   rebased <n mine> <n theirs> <old>..<new> <the upstream's changed files>
# Only for checkouts no gate marks: a project whose push gate carries test marks by tree keeps the fast-forward alone,
# since its gate's rebase is the only one after a mark (SMADPickleBot, Gene 10/19/26).
rebase=0
[ "${1:-}" = --rebase ] && { rebase=1; shift; }
dir="${1:-.}"
g() { GIT_TERMINAL_PROMPT=0 git -C "$dir" "$@" 2>/dev/null; }

top=$(g rev-parse --show-toplevel) || { echo "skipped not a git checkout"; exit 0; }
gitdir=$(g rev-parse --absolute-git-dir) || { echo "skipped no git dir"; exit 0; }
branch=$(g symbolic-ref -q --short HEAD) || { echo "skipped detached HEAD"; exit 0; }
up=$(g rev-parse --abbrev-ref --symbolic-full-name '@{u}') || { echo "skipped $branch has no upstream"; exit 0; }

for f in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD BISECT_LOG rebase-merge rebase-apply; do
  [ -e "$gitdir/$f" ] && { echo "skipped a ${f%%_*} is in progress"; exit 0; }
done

counts=$(g rev-list --left-right --count "HEAD...$up") || { echo "skipped cannot compare with $up"; exit 0; }
ahead=${counts%%[[:space:]]*}
behind=${counts##*[[:space:]]}
case "$ahead$behind" in *[!0-9]*|'') echo "skipped cannot compare with $up"; exit 0 ;; esac
[ "$behind" -eq 0 ] && { echo "current"; exit 0; }
incoming=$(g diff --name-only "HEAD...$up")
if [ "$ahead" -gt 0 ]; then
  [ "$rebase" = 1 ] || { echo "skipped $branch has $ahead unpushed commit(s) of its own"; exit 0; }
  [ -n "$(g status --porcelain=v1 -uno)" ] && { echo "skipped $branch has $ahead unpushed commit(s) and open edits, so it is not rebased"; exit 0; }
  old=$(g rev-parse --short HEAD)
  if ! g rebase --quiet "$up" >/dev/null; then
    clash=$(g diff --name-only --diff-filter=U | head -3 | paste -sd, -)
    g rebase --abort >/dev/null
    echo "skipped rebasing $ahead commit(s) onto $up would conflict on: ${clash:-files git did not name}; left as it was"
    exit 0
  fi
  new=$(g rev-parse --short HEAD)
  n=$(printf '%s\n' "$incoming" | grep -c .)
  list=$(printf '%s\n' "$incoming" | head -8 | paste -sd, -)
  [ "$n" -gt 8 ] && list="$list,+$((n - 8)) more"
  echo "rebased $ahead $behind $old..$new $list"
  exit 0
fi

incoming=$(g diff --name-only "HEAD..$up")
dirty=$(g status --porcelain=v1 -uall | sed -E 's/^.{3}//; s/^.* -> //; s/^"(.*)"$/\1/')
if [ -n "$dirty" ] && [ -n "$incoming" ]; then
  clash=$(printf '%s\n' "$incoming" | grep -Fx -f <(printf '%s\n' "$dirty") | head -3 | paste -sd, -)
  [ -n "$clash" ] && { echo "skipped open edits on files the upstream changed: $clash"; exit 0; }
fi

old=$(g rev-parse --short HEAD)
g merge --ff-only --quiet "$up" >/dev/null || { echo "skipped the fast-forward was refused"; exit 0; }
new=$(g rev-parse --short HEAD)

n=$(printf '%s\n' "$incoming" | grep -c .)
list=$(printf '%s\n' "$incoming" | head -8 | paste -sd, -)
[ "$n" -gt 8 ] && list="$list,+$((n - 8)) more"
echo "caught-up $behind $old..$new $list"
exit 0
