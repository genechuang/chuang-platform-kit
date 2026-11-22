"""The release train's generic parts, moved from SMADPickleBot's pipeline/desktop scripts one at a time (#2159, from
#1685's audit); each host script keeps its names over them.

`Lease`: one push at a time to a shared branch (SMADPickleBot's dock push, #1252). A run takes a lease document in a
record store before it gates, renews it before each push, and frees it at its end; another run waits for it, and a
killed run's lease (its `until` passed) is deleted and taken again by one waiter. A store that cannot be read is pushed
past once, blind: nothing more is written, but the run still frees a lease that names it (SMADPickleBot #2076).
The store is any record store with `create`, `get`, `set` and `delete`.

`classify_push`: what one `git push` to the shared branch answered, 'ok', 'behind' (the branch moved: rebase and try
again) or 'refused' (the hook's own refusal, the network), the hook's reason said. `push_loop`: the push's tries under
the lease (take, rebase, gate, push; on 'behind', rebase and carry the gate's mark when nothing it reads came in, else
gate the rebased tree on the next try), every step the host's callable. `release_runs`: the runs a release's head
started, watched to their end within a bound, one line saying what happened.

`newest` and `history_bound`: a wait's bound from what the work took before, the slowest of the last runs on record
times a factor (the timeout rule's 150%), a default when nothing is on record; what a run took is read by the host's
own readers of its own logs. Standard library only.
"""
import json
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone

__all__ = ['Lease', 'parse_instant', 'classify_push', 'push_loop', 'release_runs', 'BEHIND', 'newest',
           'history_bound', 'watch_run', 'run_head', 'dispatched_run', 'watch_jobs']

BEHIND = ('non-fast-forward', 'fetch first', '[rejected]')   # git's words for a push the branch moved ahead of
REASON = r'pre-push|refused|FAILED|no desktop build|has no '      # a hook's reason lines, said before the refusal


def parse_instant(value):
    """A stored ISO instant as an aware datetime (a naive one read as UTC), None when it cannot be read."""
    if not value:
        return None
    try:
        got = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    return got if got.tzinfo else got.replace(tzinfo=timezone.utc)   # utc-ok: a stored naive instant is UTC


def _utcnow():
    return datetime.now(timezone.utc)   # utc-ok: the lease's own instants, compared, never a day


class Lease:
    """This run's hold on the shared push (its own `run` token): take() waits for another's, renew() pushes `until` on
    while it is still this run's, free() deletes it only while it is this run's. With no store every call answers True
    and nothing is written. `collection` and `doc_id` name the lease document, `lease_s` how long a hold lasts unless
    renewed, `wait` and `poll` the take's bound and pace; `say` writes its lines, `tag` starting each and `branch`
    naming what is pushed to."""

    def __init__(self, store, session: str, collection: str, doc_id: str, lease_s: int, now=_utcnow, sleep=time.sleep,
                 wait: int = 600, poll: int = 5, say=print, tag: str = 'dock-push', branch: str = 'dock'):
        self.store, self.session, self.now, self.sleep, self.wait, self.poll = store, session, now, sleep, wait, poll
        self.collection, self.doc_id, self.lease_s = collection, doc_id, lease_s
        self.say, self.tag, self.branch = say, tag, branch
        self.run = uuid.uuid4().hex[:12]
        self.blind = False   # take() could not read the store: no renew, but free() still deletes a lease of this run's

    def _doc(self, sha: str) -> dict:
        return {'session': self.session, 'run': self.run, 'sha': sha,
                'until': (self.now() + timedelta(seconds=self.lease_s)).isoformat()}

    def take(self, sha: str) -> bool:
        """True once this run holds the lease (or there is no store, or it could not be read: pushed blind); False when
        another run held it past `wait`."""
        if self.store is None:
            return True
        waited, said, held = 0, False, {}
        while True:
            try:
                if self.store.create(self.collection, self.doc_id, self._doc(sha)):
                    return True
                held = self.store.get(self.collection, self.doc_id) or {}
                if held.get('run') == self.run:
                    return True
                end = parse_instant(held.get('until'))
                if end is None or end <= self.now():
                    # a killed run's: deleted, then the create tried again, so of two waiters only one takes it
                    self.store.delete(self.collection, self.doc_id)
                    continue
            except Exception as e:   # noqa: BLE001
                # A create that timed out may still have been written, so the store is kept for free() alone: the run
                # writes nothing more (renew is off) and at its end deletes the lease if it names it (SMADPickleBot
                # #2076, where setting the store to None left that lease holding every other push).
                self.say(f'{self.tag}: the lease could not be read ({type(e).__name__}); pushing without one')
                self.blind = True
                return True
            if waited >= self.wait:
                self.say(f'{self.tag}: {held.get("session")} has held {self.branch}\'s push lease past {self.wait} s; '
                         'giving up the wait')
                return False
            if not said:
                self.say(f'{self.tag}: {held.get("session")} is pushing {str(held.get("sha"))[:8]} to {self.branch}; '
                         'waiting for it to land')
                said = True
            self.sleep(self.poll)
            waited += self.poll

    def renew(self, sha: str) -> bool:
        """`until` pushed on while the lease is still this run's; False when another run holds it now."""
        if self.store is None or self.blind:
            return True
        try:
            held = self.store.get(self.collection, self.doc_id) or {}
            if held and held.get('run') != self.run:
                self.say(f'{self.tag}: the lease is {held.get("session")}\'s now; this run stops')
                return False
            self.store.set(self.collection, self.doc_id, self._doc(sha))
        except Exception:   # noqa: BLE001 - the push goes on; the lease frees itself
            pass
        return True

    def free(self) -> None:
        """The lease deleted when it is still this run's; a lease left frees itself after `lease_s`."""
        if self.store is None:
            return
        try:
            if (self.store.get(self.collection, self.doc_id) or {}).get('run') == self.run:
                self.store.delete(self.collection, self.doc_id)
        except Exception:   # noqa: BLE001 - a lease left frees itself after lease_s
            pass


def classify_push(returncode: int, stdout: str, stderr: str, say=print, tag: str = 'dock-push') -> str:
    """'ok', 'behind' (the branch moved: rebase and try again) or 'refused' (anything else: the hook's own refusal, the
    network) for one `git push`'s answer. A refusal says the hook's reason lines first, from standard output as well as
    standard error (SMADPickleBot #1276: a hook's reason went to stdout and the refusal read only "failed to push some
    refs"), then git's own last words."""
    if returncode == 0:
        return 'ok'
    err = stderr or ''
    if any(b in err for b in BEHIND) and 'pre-push' not in err:
        return 'behind'
    said = [ln.strip() for ln in ((stdout or '') + '\n' + err).splitlines() if re.search(REASON, ln)]
    for ln in said[-6:]:
        say(f'{tag}: {ln[:400]}')
    say(f'{tag}: the push was refused: {err.strip()[-400:]}')
    return 'refused'


def push_loop(lease, tries: int, head, rebase, gate, push, carry, last, say=print, tag: str = 'dock-push',
              branch: str = 'dock') -> int:
    """The push's tries under `lease`, 0 once it landed, 1 when it stopped. Each try: the lease taken (a push that
    waited for another run's then rebases onto what that one landed), the rebase, the lease renewed and the gate, the
    lease renewed, the push. 'behind' rebases at once and pushes when `carry(old, new)` says the gate's mark still
    answers, else the next try gates the rebased tree; 'refused' stops. The host's callables: `head()` the sha,
    `rebase()` and `gate()` True to go on, `push()` classify_push's word, `last()` the landed commit's line. The lease
    is the caller's to free."""
    for i in range(1, tries + 1):
        if not lease.take(head()):
            return 1
        if not rebase():
            return 1
        if not lease.renew(head()) or not gate():
            say(f'{tag}: try {i} stopped before the push; nothing pushed')
            return 1
        if not lease.renew(head()):
            return 1
        said = push()
        if said == 'ok':
            say(f'{tag}: pushed on try {i}: {last()}')
            return 0
        if said == 'refused':
            return 1
        old = head()
        if not rebase():
            return 1
        if carry(old, head()) and lease.renew(head()) and push() == 'ok':
            say(f'{tag}: {branch} moved; the mark carried and pushed on try {i}: {last()}')
            return 0
        say(f'{tag}: {branch} moved during try {i}; gating the rebased tree')
    say(f'{tag}: not pushed after {tries} tries')
    return 1


def release_runs(sha: str, gh, run, at_most: int, branch: str = 'main', sleep=time.sleep, now=time.time,
                 retry_empty: bool = True, only_open: bool = False, cwd: str = None) -> str:
    """The runs `sha` started on `branch` by its push, watched to their end (`gh run watch`, never a poll), `at_most`
    seconds in all; what happened, in one line. `gh(args)` answers `gh run list` (returncode, stdout), `run(argv, cwd=,
    timeout=)` runs the watch. The list is asked once more after 5 s when it is empty (a push's runs exist within
    seconds), unless `retry_empty` is False; `only_open` answers '' when none is still open or they could not be listed
    (the check made when no release is under way, which must cost nothing when idle). Only the push's own runs count: a
    run GitHub left queued for another event (Dependency Graph's `dynamic`) or a dispatch on the same head held a push
    its whole bound (SMADPickleBot #2152); a row with no event is kept."""
    def listed():
        got = gh(['run', 'list', '--commit', sha, '--json', 'databaseId,status,headBranch,event'])
        try:
            rows = json.loads(got.stdout) if got.returncode == 0 and (got.stdout or '').strip() else None
        except ValueError:
            rows = None
        return None if rows is None else [r for r in rows if r.get('headBranch') == branch
                                           and r.get('event', 'push') == 'push']
    rows = listed()
    if rows == [] and retry_empty:
        sleep(5)
        rows = listed()
    if rows is None:
        return '' if only_open else "the release's runs could not be listed; gating now"
    began, open_ = now(), [r['databaseId'] for r in rows if r.get('status') != 'completed']
    if only_open and not open_:
        return ''
    for n, run_id in enumerate(open_):
        left = at_most - (now() - began)
        try:
            if left <= 0:
                raise subprocess.TimeoutExpired('gh run watch', at_most)
            run(['gh', 'run', 'watch', str(run_id)], cwd=cwd, timeout=left)
        except subprocess.TimeoutExpired:
            return f"{len(open_) - n} of the release's {len(rows)} run(s) still ran after {at_most} s; gating beside them"
    return f"the release's {len(rows)} run(s) are done ({len(open_)} waited for, {round(now() - began)} s)"


def newest(folder: str, prefix: str, suffix: str) -> list:
    """The paths in `folder` named prefix…suffix, newest first by file time; [] when the folder cannot be read (a file
    removed since the listing sorts last, and its reader then skips it)."""
    try:
        names = [n for n in os.listdir(folder) if n.startswith(prefix) and n.endswith(suffix)]
    except OSError:
        return []

    def written(path):
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0
    return sorted((os.path.join(folder, n) for n in names), key=written, reverse=True)


def history_bound(sources, factor: float, default: int, last: int) -> tuple:
    """(seconds, found, slowest) for a wait on work that ran before: `factor` times the slowest of the last `last`
    readable runs of each source, or (`default`, found, None) when no source has one. `sources` is (kind, paths
    newest first, read) each, `read(path)` the run's seconds or None to skip it; `found` is {kind: [seconds, ...]} for
    the host's own line saying where the bound came from."""
    found = {kind: [] for kind, _, _ in sources}
    for kind, paths, read in sources:
        for path in paths:
            if len(found[kind]) >= last:
                break
            got = read(path)
            if got is not None:
                found[kind].append(got)
    every = [v for values in found.values() for v in values]
    if not every:
        return default, found, None
    slowest = max(every)
    return round(slowest * factor), found, slowest


def watch_run(gh, repo: str, run_id, bound_s: int, again_s: int = 30, sleep=time.sleep, now=time.monotonic) -> str:
    """A GitHub Actions run watched to its end (`gh run watch`, never a poll); its conclusion. A watch that returns
    while the run is still going (its conclusion empty, its status not `completed`) is watched again after `again_s`,
    within `bound_s` in all, so a run is never called ended before GitHub says so (SMADPickleBot, 11/21/26: a store run
    read as "ended unknown" with Apple's TestFlight step still running stopped a passing chain). 'unknown' only for a
    completed run with no conclusion, or one still going at the bound. `gh(args, timeout=)` answers (returncode,
    stdout)."""
    deadline = now() + bound_s
    while True:
        left = max(60, int(deadline - now()))
        gh(['run', 'watch', str(run_id), '--exit-status', '--repo', repo], timeout=left)
        got = gh(['run', 'view', str(run_id), '--json', 'conclusion', '-q', '.conclusion', '--repo', repo], timeout=60)
        conclusion = (got.stdout or '').strip() if got.returncode == 0 else ''
        if conclusion:
            return conclusion
        state = gh(['run', 'view', str(run_id), '--json', 'status', '-q', '.status', '--repo', repo], timeout=60)
        if (state.stdout or '').strip() == 'completed' or now() + again_s >= deadline:
            return 'unknown'
        sleep(again_s)


def run_head(gh, repo: str, run_id) -> str:
    """The commit a run builds (its headSha), which a dispatch on a branch takes from the branch at GitHub's dispatch;
    '' when GitHub does not say."""
    got = gh(['run', 'view', str(run_id), '--json', 'headSha', '-q', '.headSha', '--repo', repo], timeout=60)
    return (got.stdout or '').strip() if got.returncode == 0 else ''


def dispatched_run(gh, repo: str, workflow: str, since: float, tries: int, wait: float, sleep=time.sleep) -> str:
    """The run a `gh workflow run` just made: the oldest `workflow_dispatch` run of `workflow` created at or after
    `since` (epoch seconds), asked up to `tries` times `wait` seconds apart, since a dispatch's run appears a few seconds
    after it; '' when none appeared."""
    for _ in range(tries):
        listed = gh(['run', 'list', '--workflow', workflow, '--event', 'workflow_dispatch', '--limit', '5',
                     '--json', 'databaseId,createdAt', '--repo', repo], timeout=60)
        try:
            runs = json.loads(listed.stdout or '[]')
        except ValueError:
            runs = []
        mine = [r for r in runs if (parse_instant(r.get('createdAt')) or datetime.min.replace(tzinfo=timezone.utc)
                                    ).timestamp() >= since]
        if mine:
            return str(sorted(mine, key=lambda r: r['createdAt'])[0]['databaseId'])
        sleep(wait)
    return ''


def watch_jobs(read_jobs, done, ok, timeout: int, step: int = 5, pause=time.sleep,
               unreadable=(ValueError, AttributeError, subprocess.TimeoutExpired)) -> bool:
    """A run waited on to its end by its jobs, read every `step` s for at most `timeout`: whether every job succeeded
    (`ok(job)` for each), a wait that runs out never a success. `done(jobs)` must hold on two reads in a row, since a
    job that waits on another may not be listed yet. `read_jobs()` answers the jobs API's JSON ({'jobs': [...]}); a read
    that raises one of `unreadable` counts as no jobs."""
    agreed = 0
    for _ in range(max(1, timeout // step)):
        try:
            jobs = read_jobs().get('jobs') or []
        except unreadable:
            jobs = []
        if done(jobs):
            agreed += 1
            if agreed >= 2:
                return all(ok(j) for j in jobs)
        else:
            agreed = 0
        pause(step)
    return False
