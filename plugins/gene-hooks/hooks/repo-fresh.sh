#!/usr/bin/env bash
# SessionStart hook: refresh remote refs so the session's view of the repo
# reflects origin, not just local state.
#
# Other Claude Code sessions push to this repo concurrently, so a session can
# start already behind main with no local signal.
#
# It ALSO handles UserPromptSubmit, which IS registered in settings.json (since
# ad9beeb, "Give the notes channel a doorbell") -- reversing an earlier decision
# to ship SessionStart only. That per-prompt mode was built and measured on
# 2026-08-30 PT (68-77ms cached, ~830ms when the 120s gate expires) and briefly
# shelved before this doorbell needed it: staleness inside a session is exactly
# how a note in session-notes.md (in SMADPickleBot-sessions, since 2026-09-02
# PT), or a CLAUDE.md rewrite, would otherwise sit
# unread until someone thought to ask. See README "Keeping Sessions Fresh Across
# Machines" for the full reasoning and cost numbers.
#
# ---------------------------------------------------------------------------
# TO TURN THIS OFF, ON ANY MACHINE, WITHOUT GIT AND WITHOUT ASKING CLAUDE:
#
#     touch .claude/hooks/.disabled      # from a terminal, or any file tool
#
# The check is the first thing this script does, so it takes effect on the very
# next prompt -- no session restart, no settings reload, no revert. Delete that
# file to re-enable. `export CLAUDE_ORIGIN_HOOK_OFF=1` does the same for one
# shell. See README "Keeping sessions fresh across machines" for the full
# rollback ladder.
# ---------------------------------------------------------------------------
#
# SAFETY. Every exit path is 0 and there is no `exit 2` anywhere, so this can
# never block or erase a prompt -- the worst it can do is be slow or say
# nothing. `git fetch` runs with GIT_TERMINAL_PROMPT=0 so it fails fast instead
# of hanging forever on a credential prompt, which is the one way an advisory
# hook could have held up a turn.
#
# PERFORMANCE. This runs before every prompt, so its cost lands on every turn's
# time-to-first-token. Measured on Windows 2026-08-30 PT: bash startup 25ms,
# each git invocation ~26ms. A five-git-call version cost 250ms per prompt; the
# shape below costs 77ms and spawns no subprocess at all on the common path,
# caching the answer against FETCH_HEAD. Keep it that way -- anything added
# here is paid on every prompt. Since 10/30/26 (#642) the 120-second refresh
# fetches in a detached job too: measured 2.0 s on the prompt path before, and
# the drag-to-work budget is 5 s end to end (docs/drag-to-work-redesign.md).
#
# IN THIS PLUGIN since 1.36.0 (SMADPickleBot #1810): it was one project's `.claude/hooks/fetch-origin.sh`, so only that
# project's sessions had it. Every project name it carried is now worked out: the session channel is the sibling repo
# `<Project>-sessions` (CLAUDE_SESSIONS_DIR overrides), the shared rules repo the sibling `claude-shared`
# (CLAUDE_SHARED_DIR), and the catch-up script is the plugin's own. A project with no sessions repo gets no doorbell
# and no "not cloned" line unless its `.claude/gene-hooks.toml` says `sessions = true`. It stands down while the
# project's own settings still wire `fetch-origin.sh`, or the box's settings its `catch-up-hook.sh` (moved.lib.sh).

here="${BASH_SOURCE[0]//\\//}"   # not $0: boot.sh sources this file; Windows hands a path with backslashes
case "$here" in */*) here="${here%/*}" ;; *) here="." ;; esac
case "$here" in /*|?:*) ;; *) here="$PWD/$here" ;; esac   # absolute: the cd below would lose a relative one
. "$here/moved.lib.sh" 2>/dev/null && { moved_in_project fetch-origin.sh || moved_elsewhere catch-up-hook.sh; } && exit 0

cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0

# Kill switch, checked before any work at all.
[ -f ".claude/hooks/.disabled" ] && exit 0
[ -n "$CLAUDE_ORIGIN_HOOK_OFF" ] && exit 0

# Event name, without cat/sed: read stdin into a variable and use bash's own
# pattern matching. Both events send JSON; neither is large.
input=""
[ -t 0 ] || IFS= read -r -d '' -t 1 input 2>/dev/null
event="SessionStart"
case "$input" in *'"hook_event_name"'*'"UserPromptSubmit"'*) event="UserPromptSubmit" ;; esac

# THE PACIFIC CLOCK is not said here: the public gene-hooks plugin (genechuang/chuang-platform-kit) says it on
# every prompt and tool result, on the desktop and, since 10/15/26, in the cloud too, where the environment's
# setup script installs the plugin. This hook's copy made two clock lines in every prompt (desktop 10/13/26,
# cloud until 10/15/26), so the messages below carry only the repo's news and a quiet prompt prints nothing.
quiet_exit() { exit 0; }

# (A project's own fix of a renamed origin is not here: it names two repositories, so it stays that project's hook.)


# Locate .git without spawning git. The plain-directory case is the common one;
# anything else (worktree, submodule) falls back to git itself.
gitdir=".git"
if [ ! -d "$gitdir" ]; then
  gitdir=$(git rev-parse --git-dir 2>/dev/null) || quiet_exit
  [ -n "$gitdir" ] || quiet_exit
fi

fetch_head="$gitdir/FETCH_HEAD"
cache="$gitdir/claude-behind-cache"

# Fetch at most once every 120s. origin does not move faster than that, and a
# network round-trip per prompt is latency the user pays for.
# Read the cache once, up front, and only trust it if it parses. A cache that is
# present but corrupt -- a truncated write -- is logically identical to a missing
# one, and treating it as usable put garbage in $behind, which the final
# numeric test then silently swallowed. Bounded and self-healing, unlike the
# missing-cache bug, because the gate reopens within 120s; fixed anyway, because
# "unparseable" and "absent" are the same answer: I do not know.
cache_ok=0
if [ -f "$cache" ]; then
  IFS=$'\t' read -r behind upstream branch alert <"$cache" 2>/dev/null
  case "$behind" in ''|*[!0-9]*) ;; *) cache_ok=1 ;; esac
fi

# Fetch at most once every 120s. origin does not move faster than that, and a
# network round-trip per prompt is latency the user pays for.
#
# The clock is the CACHE's mtime, not FETCH_HEAD's. FETCH_HEAD is written by any
# `git fetch` in the repo -- the session's own commands, a background watcher,
# anything -- so gating on it meant the hook's rate limiter was driven by a file
# it does not own. Observed 2026-08-30 PT: the gate never opened and the doorbell
# went silent through two notes in a row.
#
# A background poller was the visible cause on one box, but that understates it,
# and the sandbox session found the real version: NO background process is
# needed. CLAUDE.md rule 5 requires a fetch before editing, and this repo's own
# collision rule requires one immediately before prepending to
# ops/session-notes.md. Following the documented process was by itself enough to
# hold FETCH_HEAD under 120s indefinitely -- so the two rules written to prevent
# collisions were disabling the warning that a collision was coming. The failure
# also scales the wrong way: the more git work a session does, the less likely it
# is to be told anything.
#
# The cache is written only by this hook, on the fetch path, so its mtime is
# exactly "when did I last look". Nothing else can starve it.
# Resolved above the fetch gate because BOTH branches need it -- the gated one
# re-checks that the clone exists.
#
# Where the channel is checked out -- ONE definition, in
# .claude/hooks/sessions-dir.sh, because three inline copies kept "identical by
# convention" is how the doorbell and the gate end up watching different
# directories. It resolves the sandbox's lowercase clone as well as the canonical
# name; CLAUDE_SESSIONS_DIR still overrides, verbatim.
#
# The inline fallback is not decoration: a hook that cannot find its helper must
# still behave, and the canonical sibling is the right guess when it cannot ask.
#
# In the plugin the rule is inline (builtins only): the sibling `<Project>-sessions`, its lowercase spelling next, and
# `sessions_wanted` says whether a MISSING clone is worth a line: yes when CLAUDE_SESSIONS_DIR names one or the
# project's `.claude/gene-hooks.toml` says `sessions = true`; no for a project that simply has no session channel.
sessions_wanted=0
if [ -n "$CLAUDE_SESSIONS_DIR" ]; then
  SESSIONS_DIR="$CLAUDE_SESSIONS_DIR"; sessions_wanted=1
else
  root="${PWD//\\//}"; root="${root%/}"; project="${root##*/}"; parent="${root%/*}"
  SESSIONS_DIR="$parent/${project}-sessions"
  for cand in "${project}-sessions" "${project,,}-sessions"; do
    [ -d "$parent/$cand/.git" ] && { SESSIONS_DIR="$parent/$cand"; break; }
  done
  if [ -r ".claude/gene-hooks.toml" ]; then
    cfg=$(<".claude/gene-hooks.toml")
    case $'\n'"$cfg" in *$'\n'"sessions = true"*) sessions_wanted=1 ;; esac
  fi
fi
SHARED_DIR="${CLAUDE_SHARED_DIR:-${PWD%/*}/claude-shared}"   # #621: fetched and caught up beside the sessions repo

# THE NETWORK LEAVES THE PROMPT PATH (SMADPickleBot #642; Gene, 10/30/26: drag-to-work under 5 s, the two network
# hooks being about 2 s of every prompt and every wake). With a cache present but old, the fetch of both repos runs
# DETACHED (one subshell, its streams closed, so the hook's own stdout is not held open and the host is not waited on),
# and this run compares against the refs the last fetch brought, at once and with local git alone. The job leaves its
# mark in $fetch_done ("main=1 sessions=1", each 0 on a refusal, sessions "-" with no clone); a mark newer than the
# cache makes the next prompt look again, so news a background fetch brought is said one prompt later, never 120 s
# later. $fetch_lock keeps one job at a time (a lock older than 180 s is a dead job's). With NO cache the fetch stays
# inline: "I do not know" is answered by looking, the rule this file carries a scar for (case 3 of the suite).
# CLAUDE_FETCH_INLINE=1 keeps every fetch inline (the suite's deterministic cases).
fetch_done="$gitdir/claude-fetch-done"
fetch_lock="$gitdir/claude-fetch-running"
spawn_fetch() {
  local now lk
  now=$(printf '%(%s)T' -1 2>/dev/null)
  lk=$(stat -c %Y "$fetch_lock" 2>/dev/null || echo 0)
  [ -f "$fetch_lock" ] && [ -n "$now" ] && [ $((now - lk)) -lt 180 ] 2>/dev/null && return 0
  : >"$fetch_lock" 2>/dev/null
  (
    m=1; s='-'
    GIT_TERMINAL_PROMPT=0 git fetch origin --quiet 2>/dev/null || m=0
    if [ -d "$SESSIONS_DIR/.git" ]; then
      s=1; GIT_TERMINAL_PROMPT=0 git -C "$SESSIONS_DIR" fetch --quiet origin 2>/dev/null || s=0
    fi
    [ -d "$SHARED_DIR/.git" ] && GIT_TERMINAL_PROMPT=0 git -C "$SHARED_DIR" fetch --quiet origin 2>/dev/null
    printf 'main=%s sessions=%s\n' "$m" "$s" >"$fetch_done.tmp" 2>/dev/null && mv -f "$fetch_done.tmp" "$fetch_done" 2>/dev/null
    rm -f "$fetch_lock" 2>/dev/null
  ) >/dev/null 2>&1 </dev/null &
}

stale=1
if [ "$event" != "SessionStart" ] && [ "$cache_ok" = "1" ] && [ -f "$cache" ]; then
  now=$(printf '%(%s)T' -1 2>/dev/null)          # bash builtin, no `date` process
  last=$(stat -c %Y "$cache" 2>/dev/null || echo 0)
  [ -n "$now" ] && [ "$last" -gt 0 ] 2>/dev/null && [ $((now - last)) -lt 120 ] && stale=0
  # A background fetch finished after the cache was written: its news is not in the cache yet, so look again (#642).
  [ -f "$fetch_done" ] && [ "$fetch_done" -nt "$cache" ] && stale=1
fi
# NOTE the `$cache_ok` above. Without it the gate could send a run down the
# cached path when no cache had ever been written, and the cached path answered
# a missing file with silence -- i.e. reported "nothing to say" for "I do not
# know", which is the failure this repo has a whole section about.
#
# It was not hypothetical and it was not rare. Registering this hook MID-SESSION
# means SessionStart never runs with it, so no cache is ever created; every
# prompt afterwards found FETCH_HEAD fresh (something else had fetched) and a
# cache absent, and went quiet. The doorbell was dead from the moment it was
# switched on, through a real note waiting in ops/session-notes.md, and surfaced
# only because a human said "ring! doorbell!" on 2026-08-30 PT.
#
# A missing cache now means take the full path -- fetch and compute -- rather
# than shrug. That spends one ~830ms fetch on the first prompt after a cache is
# missing, and then never again, because that run writes the cache. Paying it
# once beats a doorbell that is silently disconnected.

if [ "$stale" = "1" ]; then
  # s_fetch_ok: 1 the sessions repo was fetched, 0 its fetch was refused, '' not known (a background fetch is on its
  # way; the count below reads the refs as they stand).
  s_fetch_ok=""
  if [ "$cache_ok" = "1" ] && [ -f "$cache" ] && [ -z "$CLAUDE_FETCH_INLINE" ]; then
    spawn_fetch                                    # #642: the network in the background; compare now against what the last fetch brought
    if [ -f "$fetch_done" ] && [ "$fetch_done" -nt "$cache" ]; then
      read -r fd_line <"$fetch_done" 2>/dev/null
      case "$fd_line" in *'sessions=1'*) s_fetch_ok=1 ;; *'sessions=0'*) s_fetch_ok=0 ;; esac
    fi
  else
    GIT_TERMINAL_PROMPT=0 git fetch origin --quiet 2>/dev/null
    if [ -d "$SESSIONS_DIR/.git" ]; then
      s_fetch_ok=1; GIT_TERMINAL_PROMPT=0 git -C "$SESSIONS_DIR" fetch --quiet origin 2>/dev/null || s_fetch_ok=0
    fi
    [ -d "$SHARED_DIR/.git" ] && GIT_TERMINAL_PROMPT=0 git -C "$SHARED_DIR" fetch --quiet origin 2>/dev/null
  fi

  # CATCH UP, DON'T ONLY WARN (Gene, 10/10/26: "Why don't your pre prompt hook
  # automatically catch main up anymore?"). It never did: it fetched and told the
  # session to fast-forward, and a session busy with something else read past it.
  # claude-shared/hooks/catch-up.sh fast-forwards only when that cannot lose work
  # (on a branch with an upstream, nothing unpushed, no merge or rebase under way,
  # no open edit on a file the upstream changed) and says why when it will not, so
  # the warnings below now fire only for a checkout it could not safely move.
  # Shared so every repo's hook can call it; a missing helper keeps the old
  # warn-only behaviour. CLAUDE_NO_CATCH_UP turns it off.
  caught=""; cu_skip=""; caught_claude=""; sib_note=""
  shared_dir="$SHARED_DIR"
  if [ -z "$CLAUDE_NO_CATCH_UP" ] && [ -r "$here/catch-up.sh" ]; then
    cu=$(bash "$here/catch-up.sh" . 2>/dev/null)
    case "$cu" in
      "caught-up "*)
        caught="${cu#caught-up }"
        cu_range="${caught#* }"; cu_range="${cu_range%% *}"
        # The three-dot diff below is empty once HEAD has moved, so the rules
        # check reads the range the fast-forward covered.
        [ -n "$(git diff --name-only "$cu_range" -- CLAUDE.md 2>/dev/null)" ] && caught_claude=1 ;;
      "skipped "*) cu_skip="${cu#skipped }" ;;
    esac
    # #621 (10/31/26): the sessions repo and claude-shared beside the project are caught up too, WITH --rebase, so a
    # session's own unpushed note or hook commit goes on top of what other sessions pushed instead of leaving it
    # behind. Neither carries a gate's marks. The project itself keeps the fast-forward above: its push gate's rebase
    # is the only rebase after a test mark (Gene, 10/19/26), so a prompt never orphans one.
    for sib in "$SESSIONS_DIR" "$shared_dir"; do
      [ -e "$sib/.git" ] || continue
      sout=$(bash "$here/catch-up.sh" --rebase "$sib" 2>/dev/null)
      case "$sout" in
        "caught-up "*|"rebased "*) sib_note="$sib_note ${sib##*/}: ${sout%% *} ${sout#* }." ;;
        "skipped "*" would conflict on: "*) sib_note="$sib_note ${sib##*/}: ${sout#skipped } (resolve it by hand)." ;;
      esac
    done
  fi

  # MEASURE AGAINST THE DEFAULT BRANCH AS WELL AS @{u}, AND REPORT THE WORSE.
  #
  # This measured `behind` against `@{u}` alone, which is correct only for a
  # session sitting on the default branch. `git push -u origin <branch>` -- which
  # CLAUDE.md's git section mandates -- re-points `@{u}` at the session's own
  # feature branch, a ref only that session pushes to. The count then sits at ~0
  # for the life of the branch.
  #
  # Measured on the cloud sandbox 2026-09-01 PT: `0` reported while that session
  # was 37 commits behind main, through a note left for it and a CLAUDE.md
  # rewrite. That is worse than the missing-cache bug this file already fixed:
  # that one did not know the answer, this one computed a confident 0 against the
  # wrong reference. Silence standing in for all-clear.
  #
  # The documents a session reasons from -- CLAUDE.md here, session-notes.md in
  # the sessions repo -- live on the default branch no matter which branch the
  # work happens on, so
  # that is what has to be watched. @{u} is still measured, because a session
  # working WITH a shared feature branch wants to know when it moves.
  upstream=$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null)

  # origin/main FIRST, origin/HEAD only as a fallback -- deliberately that way
  # round. `refs/remotes/origin/HEAD` is written once at clone time from whatever
  # branch was checked out, and it is unset entirely in this sandbox's clone. The
  # regression fixture clones this repo while a FEATURE branch is checked out, so
  # origin/HEAD there names that feature branch: deriving the "default branch"
  # from it would hand back the very ref this fix exists to look past. Found by
  # the fixture, not by reading.
  default_ref=""
  if git rev-parse --verify -q origin/main >/dev/null 2>&1; then
    default_ref="origin/main"
  else
    default_ref=$(git symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null)
    git rev-parse --verify -q "$default_ref" >/dev/null 2>&1 || default_ref=""
  fi

  behind=0; watch_ref=""
  if [ -n "$upstream" ]; then
    behind=$(git rev-list --count "HEAD..$upstream" 2>/dev/null)
    watch_ref="$upstream"
  fi
  if [ -n "$default_ref" ] && [ "$default_ref" != "$upstream" ]; then
    behind_default=$(git rev-list --count "HEAD..$default_ref" 2>/dev/null)
    case "$behind_default" in ''|*[!0-9]*) behind_default=0 ;; esac
    if [ "$behind_default" -gt "${behind:-0}" ] 2>/dev/null; then
      behind="$behind_default"
      watch_ref="$default_ref"
    fi
  fi
  # No upstream AND no default ref is the only case with nothing to compare
  # against. A branch that was never pushed still has the default branch, and
  # used to exit here in silence.
  [ -n "$watch_ref" ] || quiet_exit
  upstream="$watch_ref"
  branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)

  # Name WHAT changed, not just how many commits. "3 commits behind" does not
  # tell a session that another one left it a note, or that the rules it is
  # working from were rewritten -- and those are the two changes that alter
  # behaviour rather than just files. Both were missed on 2026-08-30 PT: a note
  # sat unread until a human asked why, and a session planned commits under a
  # superseded rule 7 because CLAUDE.md had moved underneath it.
  # One extra git call, on the fetch path only, cached with everything else --
  # so the gated path stays free.
  alert=""
  # THREE dots, not two. `git diff A..B` compares the two endpoints, so it
  # reports OUR OWN unpushed commits as changes -- while `behind` is counted
  # directionally with rev-list and is 0. The doorbell therefore told you to
  # "re-read CLAUDE.md before acting on its rules" because YOU had just edited
  # it, which is worse than useless: it trains the reader to ignore the one
  # alert that means the rules moved underneath them.
  #
  # `A...B` diffs from the merge-base to B, so it shows only what the other side
  # added. When nothing is behind, merge-base == upstream and it is empty.
  #
  # The bug predates the sessions-repo work and was invisible: the old emit line
  # exited unless `behind > 0`, so a wrong alert computed in this state was never
  # printed. Widening that condition to let a sessions-repo note through with a
  # current checkout is what surfaced it. Measured 2026-09-02 PT, one commit
  # ahead: two-dot says "CLAUDE.md", three-dot says "".
  changed=$(git diff --name-only "HEAD...$upstream" -- CLAUDE.md 2>/dev/null)
  case "$changed" in *CLAUDE.md*)
    [ -n "$alert" ] && alert="$alert; "
    alert="${alert}CLAUDE.md (re-read it before acting on its rules)";;
  esac
  # THE SESSION CHANNEL LIVES IN ANOTHER REPO as of 2026-09-02 PT, so the
  # doorbell has to fetch that one too. It was moved out of this repo because
  # every notes commit fired release-notes.yml and sent a WhatsApp message -- 34
  # of the 57 on 2026-09-01 were the two sessions talking, and that burst is the
  # likely cause of WhatsApp restricting the account for six hours.
  #
  # Silence here must mean "checked, nothing new", never "could not look". If the
  # sibling clone is missing, SAY SO: a missing clone is exactly the state of a
  # fresh machine, and answering it the same way as "no new notes" is the
  # cached-branch bug this hook already carries a scar from.
  if [ -d "$SESSIONS_DIR/.git" ]; then
    if [ "$s_fetch_ok" != "0" ]; then          # fetched now, or by the background job, or the refs as they stand (#642)
      s_behind=$(git -C "$SESSIONS_DIR" rev-list --count HEAD..origin/main 2>/dev/null)
      if [ -n "$s_behind" ] && [ "$s_behind" -gt 0 ]; then
        [ -n "$alert" ] && alert="$alert; "
        alert="${alert}session-notes.md in ${SESSIONS_DIR##*/} ($s_behind new - another session left a note, read it)"
      fi
    else
      [ -n "$alert" ] && alert="$alert; "
      alert="${alert}COULD NOT FETCH the sessions repo - a note may be waiting and this cannot tell"
    fi
  elif [ "$sessions_wanted" = 1 ]; then   # a project with no session channel is told nothing of one
    [ -n "$alert" ] && alert="$alert; "
    alert="${alert}sessions repo NOT CLONED at $SESSIONS_DIR - clone it or notes go unread"
  fi
  printf '%s\t%s\t%s\t%s\n' "${behind:-0}" "$upstream" "$branch" "$alert" >"$cache" 2>/dev/null
else
  # Gated: no fetch happened, so the refs cannot have moved and last run's answer
  # still holds. Read it back rather than re-running three git calls.
  # $behind/$upstream/$branch/$alert were already read above, and $cache_ok
  # proved them parseable -- reaching this branch at all requires it.
  #
  # ...unless the local branch moved. Fast-forwarding does not refresh the cache,
  # so without this the hook keeps insisting you are behind for up to 120s AFTER
  # you have already pulled -- reporting something that is no longer true, which
  # is the one thing this repo's rules are least forgiving about. A newer branch
  # ref than the cache means the answer is unknown, and silence is the honest
  # output: the next fetch is at most 120s away. Compared by mtime, so this stays
  # a stat rather than another `git rev-parse` on the per-prompt path.
  headline=""
  [ -f "$gitdir/HEAD" ] && read -r headline <"$gitdir/HEAD" 2>/dev/null
  headref="$gitdir/${headline#ref: }"
  if [ -f "$headref" ] && [ "$headref" -nt "$cache" ]; then quiet_exit; fi

  # RE-DERIVE ONE FACT: does the sessions clone exist. Only that one, and it is
  # a `[ -d ]` test -- no process, no network -- so the gate's whole purpose is
  # untouched.
  #
  # It cannot be answered from the cache for two reasons. CLAUDE_SESSIONS_DIR
  # can differ from the run that wrote it, and more seriously "I could not look"
  # is the one answer that must never be served stale: a cache saying nothing
  # while the clone is gone is silence standing in for all-clear, which is the
  # failure this hook already carries a scar from and the reason the NOT CLONED
  # branch was written in the first place.
  #
  # Anything needing a fetch stays gated. A note landing inside the 120s window
  # is at most 120s late -- the deal this gate already makes for `behind`.
  #
  # The segment is always appended LAST on the fetch path, so stripping from it
  # to end of string drops it without touching a CLAUDE.md alert in front of it.
  case "$alert" in *"sessions repo NOT CLONED at"*)
    alert="${alert%%sessions repo NOT CLONED at*}"
    alert="${alert%; }" ;;   # extglob is off by default, so trim the
                             # exact separator the fetch path appends
  esac
  if [ ! -d "$SESSIONS_DIR/.git" ] && [ "$sessions_wanted" = 1 ]; then
    [ -n "$alert" ] && alert="$alert; "
    alert="${alert}sessions repo NOT CLONED at $SESSIONS_DIR - clone it or notes go unread"
  fi
fi

# JSON-ESCAPE THE ALERT BEFORE INTERPOLATING IT.
#
# $alert can carry a filesystem path -- the NOT CLONED branch names the directory
# it looked in -- and on Windows that path holds backslashes. A raw C:\Users in a
# JSON string is the invalid escape \U, so the whole object fails to parse and the
# host DROPS the hook output. The failure mode is precisely the one this branch
# exists to prevent: silence, indistinguishable from "nothing to report", from the
# code whose entire job is to say "I could not look".
#
# Escaped HERE and not earlier, deliberately: the cache above stores the raw
# string, so escapes cannot accumulate across the gated read-write cycle.
# Backslash first, then the quote, or the escapes escape each other.
alert_json="${alert//\/\\}"
alert_json="${alert_json//\"/\\\"}"

# Emit when this repo is behind OR when there is anything to say about the
# sessions repo. The old form exited unless THIS repo was behind, which was
# right while the notes lived here and became silent-for-the-channel the moment
# they moved: a note could sit unread with main perfectly up to date.
stale=0
[ -n "$behind" ] && [ "$behind" -gt 0 ] 2>/dev/null && stale=1

# The hook moved the checkout itself: say so once, on this run only (it is not
# cached, so the gated runs after it stay quiet), with what changed underneath
# the session, and anything still worth saying after it.
sib_esc="${sib_note//\\/\\\\}"; sib_esc="${sib_esc//\"/\\\"}"   # #621: the sessions repo's and claude-shared's catch-up
if [ -n "$caught" ]; then
  cu_n="${caught%% *}"; cu_rest="${caught#* }"; cu_files="${cu_rest#* }"
  [ "$cu_files" = "$cu_rest" ] && cu_files=""
  cu_files="${cu_files//\\/\\\\}"; cu_files="${cu_files//\"/\\\"}"
  more=""
  [ -n "$caught_claude" ] && more=" CLAUDE.md changed: re-read it before acting on its rules."
  [ -n "$sib_esc" ] && more="$more Beside it:$sib_esc"
  [ -n "$alert_json" ] && more="$more Also: $alert_json."
  [ "$stale" = 1 ] && more="$more Still $behind commit(s) behind $upstream: fast-forward that by hand (stash uncommitted work, git merge --ff-only $upstream, restore)."
  printf '{"systemMessage":"git: caught up %s commit(s) on %s - changed: %s","hookSpecificOutput":{"hookEventName":"%s","additionalContext":"Repo state: the hook fast-forwarded %s by %s commit(s) another session pushed (%s). Changed: %s. Read what changed before relying on those files.%s"}}' \
    "$cu_n" "$branch" "$cu_files" \
    "$event" \
    "$branch" "$cu_n" "${cu_rest%% *}" "$cu_files" "$more"
  exit 0
fi

# Behind and the hook could not move it: the reason goes in the warning.
cu_note=""
if [ -n "$cu_skip" ]; then
  cu_note="${cu_skip//\\/\\\\}"; cu_note="${cu_note//\"/\\\"}"
  cu_note=" (auto catch-up skipped: $cu_note)"
fi
if [ "$stale" = 0 ] && [ -z "$alert" ] && [ -n "$sib_esc" ]; then
  # #621: this checkout is current, and the hook moved the sessions repo or claude-shared beside it.
  printf '{"systemMessage":"git: caught up beside the project:%s","hookSpecificOutput":{"hookEventName":"%s","additionalContext":"Repo state: this checkout is current; the hook caught up the repos beside it:%s Read what changed there before relying on it."}}' \
    "$sib_esc" "$event" "$sib_esc"
  exit 0
fi
[ "$stale" = 0 ] && [ -z "$alert" ] && quiet_exit

if [ "$stale" = 0 ]; then
  # Nothing wrong with this checkout -- the news is entirely from the sessions
  # repo, so do not tell anyone to fast-forward a branch that is already current.
  sdir="${SESSIONS_DIR##*/}"; sdir="${sdir//\\/\\\\}"; sdir="${sdir//\"/\\\"}"
  printf '{"systemMessage":"session channel: %s","hookSpecificOutput":{"hookEventName":"%s","additionalContext":"%s. This repo is up to date; the news is in the sessions repo. Pull it there (git -C ../%s pull) and read the entry before continuing."}}' \
    "$alert_json" "$event" "$alert_json" "$sdir"
  exit 0
fi

if [ -n "$alert_json" ]; then
  printf '{"systemMessage":"git: %s is %s commit(s) behind %s - changed: %s%s","hookSpecificOutput":{"hookEventName":"%s","additionalContext":"Repo state: local branch %s is %s commit(s) behind %s%s. Another session has pushed, and it touched: %s. Fast-forward (stash uncommitted work, git merge --ff-only %s, restore) and read what changed before continuing."}}' \
    "$branch" "$behind" "$upstream" "$alert_json" "$cu_note" \
    "$event" \
    "$branch" "$behind" "$upstream" "$cu_note" "$alert_json" "$upstream"
else
  printf '{"systemMessage":"git: %s is %s commit(s) behind %s - fast-forward before editing%s.","hookSpecificOutput":{"hookEventName":"%s","additionalContext":"Repo state: local branch %s is %s commit(s) behind %s%s. Another session has pushed. Fast-forward before making any code changes (stash uncommitted work, git merge --ff-only %s, restore) rather than editing stale files."}}' \
    "$branch" "$behind" "$upstream" "$cu_note" \
    "$event" \
    "$branch" "$behind" "$upstream" "$cu_note" "$upstream"
fi
exit 0
