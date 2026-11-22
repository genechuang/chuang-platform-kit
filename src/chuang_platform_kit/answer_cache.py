"""Read answers kept in Firestore in front of a database that sleeps, rebuilt at the write and pushed (0.37.0;
SMADPickleBot #1708, out of its services/api/fs_cache.py).

Why. A serverless database (Neon) bills the time it is awake, and wakes for any read. A cache that only drops an
answer when its data is written turns one write into a wake for every reader after it, hours apart: each reader's next
read rebuilds its own answer. Gene, 11/19/26 12:11 PM PT: "make ALL firestore cache a write-time cache now ... this
should be a reusable pattern in chuang-kit, and all app caches use this"; 12:07 PM PT: "change should be pushed to
firebase and then to the view".

The pattern, four parts:

  read      `answer(route, who, key, build)`: one batch read of the scope's version document and the kept answer. A
            kept answer built under the current version, in the route's shape (or by this revision, for a route
            that names none), with the same inputs and before the end its own answer names is served, and the
            database is never touched. Its age is never asked (0.52.0). Any other is built and kept: the fallback, and the first read
            of a new caller or key.
  register  `register(route, rebuild, ...)`: how the route's answer is built with no request in hand, from the `who`
            and `key` its kept document carries, and the domains its build reads.
  write     `bump(domains)` moves the version as the write commits (so a reader never gets the answer from before a
            write it made). Then `rebuild()`, run by the host while the database is still awake from the write (a
            consumer on its event bus), rebuilds every kept answer that is no longer usable and was asked for lately,
            under one lease, newest asked first, within a budget of seconds.
  push      after a document is rebuilt, the host's `pushed(route, who)` is called once for the version it now
            holds (the document's `pushed` field remembers it): the host bumps a change signal a view listens to, and
            the view's re-read finds the kept answer.

Between a write and its rebuild a read builds for itself, so the cache is correct at every moment and the pass only
makes the later reads free.

A version per domain. The version document holds `version` (every write) and a counter `d_<domain>` per domain a
write named. A route that names its domains is kept under those counters and UNKNOWN's alone, so a write elsewhere
leaves it. A host maps its tables to domains; a table it does not map is OTHER, which no route may name.

The store is `firestore.Client` (or anything with its batch_get, set, merge, run_query, lock and release). Nothing
here names a host, a league or a route; a host wires it once (its scope, revision, collections, its encoder).
"""
import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from chuang_platform_kit import firestore

log = logging.getLogger(__name__)

OTHER = 'other'       # every table a host did not map to a domain: no route may name it
UNKNOWN = 'unknown'   # a write whose table could not be read: every domain reader reads this counter too
# Garbage collection, never a correctness rule (0.52.0): a kept answer whose version never moves again is dropped
# this long after it was kept, so a caller who left does not leave a document for good.
LASTING = timedelta(days=30)
MAX_BYTES = 900_000   # under Firestore's 1 MiB document: a larger answer is served, never kept
KEEP_DAYS = 7         # a kept answer nobody has asked for in this long is not rebuilt at a write
ASKED_EVERY = timedelta(hours=6)   # how stale a document's `asked` may be before a served read refreshes it
BUDGET_SECONDS = 45   # one rebuild pass's bound
FINISH_SECONDS = 3    # the longest a response waits for its version bump
SKIP = object()       # a rebuild's answer for "leave this document to its next reader" (never counted as a failure)
SELECT = ('route', 'who', 'key', 'version', 'revision', 'shape', 'inputs', 'until', 'at', 'asked', 'pushed', 'scope')


def instant(text):
    """An ISO stamp as an aware UTC datetime (a naive one is UTC), None for none or unreadable."""
    if isinstance(text, datetime):
        return text if text.tzinfo else text.replace(tzinfo=timezone.utc)  # utc-ok: a naive instant from a machine source is UTC
    if not text:
        return None
    try:
        at = datetime.fromisoformat(str(text).replace('Z', '+00:00'))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)  # utc-ok: a naive instant from a machine source is UTC


def earliest(*stamps):
    """The earliest of ISO stamps or datetimes (None and unreadable ones left out), for a route's `until`."""
    found = [instant(s) for s in stamps]
    found = [s for s in found if s is not None]
    return min(found) if found else None


def version_of(signal: dict, domains=None) -> str:
    """What an answer is kept under: the scope's `version` (every write), or for a route that names its `domains`
    their counters and UNKNOWN's, so a write to another domain leaves it."""
    signal = signal or {}
    if not domains:
        return str(signal.get('version') or 0)
    if OTHER in domains:
        raise ValueError(f"'{OTHER}' is every unmapped table: map the table to a domain instead")
    return 'd:' + ','.join(f'{d}={signal.get("d_" + d) or 0}' for d in sorted({*domains, UNKNOWN}))


class Route:
    """One registered route: how its answer is rebuilt with no request in hand, and what it reads."""

    def __init__(self, name, rebuild, domains=None, until=None, inputs=None, keep=None, shape=None):
        self.name, self.rebuild = name, rebuild
        self.domains = domains if callable(domains) else tuple(domains) if domains else None
        self.until, self.inputs, self.keep = until, inputs, keep
        self.shape = shape

    def domains_of(self, key):
        """The domains this route's answer for `key` reads: its registered ones, or what its function says for the
        key (a route whose reads depend on what was asked for)."""
        return self.domains(key) if callable(self.domains) else self.domains


class AnswerCache:
    """Kept answers for one scope (a league, a tenant) in one Firestore database. `store` is a callable answering the
    Firestore client (so a host's test may swap it), `scope` the version document's id and the prefix of every kept
    document, `revision` the code's (what a route that names no `shape` is kept under), `encode(answer)` the answer
    as JSON text.

    An answer ends when something it was built from changes, and at no other time (0.52.0; Gene, 11/22/26: "It
    should dirty only when data changes, not based on time expiry!", and "when time based data expires, throw an
    event that cache listens to to expire. Change is pushed, not polled"). There is no longest age and no day's end
    here. A figure that changes with the clock (a day's count, a week's list) is a domain like any other: the host
    names it on the routes that hold such a figure and calls `bump` on it at the moment the figure changes, from its
    own clock event, which is also what rebuilds the answer and tells the open screens. A route's own `until` is for
    an end the answer itself names exactly (a game's start), and is the backstop for a clock event that came late."""

    def __init__(self, store, scope, revision='', collection='api_answers', versions='data_versions',
                 locks='answer_cache_locks', encode=None, enabled=True,
                 max_bytes=MAX_BYTES, keep_days=KEEP_DAYS, refused=None, asked_every=ASKED_EVERY):
        # `asked_every` (0.46.0, SMADPickleBot #2109): how stale a document's `asked` may be before a served read
        # refreshes it; a host whose keep_days is minutes (rebuild only for readers seen lately) keeps it below that
        self.asked_every = asked_every
        # `refused(error)` (0.45.0, SMADPickleBot #2029): whether a rebuild's exception is the caller's own refusal (a
        # sign-in no longer linked, a member gone), so the pass drops the kept answer instead of trying it every pass
        self.refused = refused
        self.store, self.scope, self.revision = store, scope, revision
        self.collection, self.versions, self.locks = collection, versions, locks
        self.enabled = enabled
        self.encode = encode or (lambda answer: json.dumps(answer, ensure_ascii=False, separators=(',', ':'), default=str))
        self.max_bytes, self.keep_days = max_bytes, keep_days
        self.routes = {}
        self.counts = {'kept': 0, 'built': 0, 'unread': 0, 'bumps': 0, 'rebuilt': 0}

    # -- register ---------------------------------------------------------------------------------------------------

    def register(self, route, rebuild, domains=None, until=None, inputs=None, keep=None, shape=None) -> Route:
        """Say how `route`'s answer is rebuilt at a write: `rebuild(who, key)` answers what the route's own build
        would for that caller and key (or raises, or answers SKIP, and the document is left for its next reader), `domains` the
        domains its build reads (None: the scope's whole version; a function of the key when they depend on it), `until(answer, now)`, `inputs()` and
        `keep(answer)` as `answer()` takes them, used when the call gives none. A route never registered is kept and
        served as any other and simply not rebuilt.

        `shape` (0.52.0) is the route's own number for the shape of its answer. With one, a kept answer is served
        across deploys until the number moves, so a release flushes only the routes whose answer it changed; the
        host moves the number in the commit that changes the answer's fields or meaning, and pins it with a test.
        With none (the default), the answer is kept under the code's revision, so every deploy builds it again: the
        safe rule for a route nobody has pinned."""
        self.routes[route] = Route(route, rebuild, domains, until, inputs, keep, shape)
        return self.routes[route]

    # -- read -------------------------------------------------------------------------------------------------------

    def doc_id(self, route, who, key) -> str:
        """The kept answer's document: '<scope>__<route>__<hash>' over `who` and `key`, so no caller's id is spelled
        in a document name."""
        raw = json.dumps([list(who), key], default=str, separators=(',', ':'))
        return f'{self.scope}__{route}__{hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]}'

    def usable(self, kept, version, inputs, now, shape=None) -> bool:
        """Whether a kept document answers now: built under `version`, with the same `inputs`, before its own end,
        and in the route's `shape` (or, for a route that names none, by this revision). Its age is never asked."""
        if not kept:
            return False
        until = instant(kept.get('until'))
        same = kept.get('shape') == str(shape) if shape is not None else kept.get('revision') == self.revision
        return (kept.get('version') == version and same and (kept.get('inputs') or '') == inputs
                and until is not None and now < until)

    def answer(self, route, who, key, build, until=None, inputs=None, now=None, keep=None, domains=None, mark=None,
               shape=None):
        """(the answer's JSON text, 'kept' or 'built'): the kept one when it is usable, else `build()`'s, kept for
        next time. `who` is a tuple naming the caller (always part of the key); `key` the route's own, JSON-able.
        `build()` answers or raises as the route does (nothing is kept then). `until(answer, now)` is when the answer
        stops being true by the clock alone, None for never, `now` or earlier for one never to keep; `inputs()` a
        string naming what it reads from outside the database; `keep(answer)` False for an answer never to keep;
        `domains` what its build reads. Each falls back to the route's registration. `mark(name)` is the host's
        phase log. A Firestore that cannot be read costs one try and the built answer, never an error.

        `shape` is `register`'s, for a route that is not registered. Until 0.52.0 every answer also ended at the
        host's day end, twelve hours after it was kept and at every deploy, so each caller's first read after any of
        them was built from the database, which on a database that sleeps is a wake."""
        reg = self.routes.get(route)
        shape = (reg.shape if reg else None) if shape is None else shape
        until = until or (reg.until if reg else None)
        inputs = inputs or (reg.inputs if reg else None)
        keep = keep or (reg.keep if reg else None)
        domains = domains or (reg.domains_of(key) if reg else None)
        now = now or datetime.now(timezone.utc)  # utc-ok: an instant, compared with instants; no day is cut here
        started = time.monotonic()
        stamp = inputs() if inputs else ''
        version = name = None
        if self.enabled:
            name = self.doc_id(route, who, key)
            try:
                signal, kept = self.store().batch_get([(self.versions, self.scope), (self.collection, name)])
                version = version_of(signal, domains)
                if kept and kept.get('json') and self.usable(kept, version, stamp, now, shape):
                    self.counts['kept'] += 1
                    if mark:
                        mark('kept')
                    self._asked(name, kept, now)
                    log.info("answer_cache: %s kept in %.0f ms", route, (time.monotonic() - started) * 1000)
                    return kept['json'], 'kept'
            except firestore.Unreachable as e:
                self.counts['unread'] += 1
                log.info("answer_cache: %s's kept answer could not be read (%s): built from the database", route, e)
            if mark:
                mark('answer_cache')
        answer = build()
        self.counts['built'] += 1
        body = self.encode(answer)
        saved = version is not None and self._keep(route, name, who, key, answer, body, version, stamp, now, until, keep,
                                                   shape=shape)
        if mark and version is not None:
            mark('kept_write')
        if self.enabled:
            log.info("answer_cache: %s built in %.0f ms (%s)", route, (time.monotonic() - started) * 1000,
                     'saved' if saved else 'not saved' if version is not None else 'unread')
        return body, 'built'

    def _ends(self, answer, now, until):
        """The answer's own end, or LASTING (when its document is dropped, nothing having changed)."""
        return earliest(now + LASTING, until(answer, now) if until else None)

    def _keep(self, route, name, who, key, answer, body, version, stamp, now, until, keep, pushed=None,
              shape=None) -> bool:
        """Write the kept document unless the answer is too large, the route says not to keep it or it has ended
        already. False, never an exception, when it is not kept."""
        if len(body.encode('utf-8')) > self.max_bytes or (keep is not None and not keep(answer)):
            return False
        ends = self._ends(answer, now, until)
        if ends is None or ends <= now:
            return False
        fields = {'json': body, 'version': version, 'revision': self.revision,
                  'shape': '' if shape is None else str(shape), 'inputs': stamp, 'until': ends.isoformat(),
                  'at': now.isoformat(), 'asked': now.isoformat(), 'scope': self.scope, 'route': route,
                  'who': [str(w) for w in who], 'key': json.dumps(key, default=str, separators=(',', ':'))}
        if pushed is not None:
            fields['pushed'] = pushed
        try:
            self.store().set(self.collection, name, fields)
            return True
        except firestore.Unreachable as e:
            log.info("answer_cache: %s's answer could not be kept (%s)", route, e)
            return False

    def _asked(self, name, kept, now) -> None:
        """Note that a kept answer was asked for, at most once per `asked_every` (ASKED_EVERY by default) a document,
        so the rebuild pass knows which answers still have a reader. Best effort."""
        asked = instant(kept.get('asked'))
        if asked is not None and now - asked < self.asked_every:
            return
        try:
            self.store().merge(self.collection, name, {'asked': now.isoformat()})
        except firestore.Unreachable:
            pass

    # -- write ------------------------------------------------------------------------------------------------------

    def bump(self, domains=(), now=None, what='write') -> bool:
        """Move the scope's version, and the counter of each domain named, in one write: every kept answer that
        reads them stops being served at once. False, never an exception, when Firestore cannot be reached (kept
        answers may then be a write behind until their end)."""
        now = now or datetime.now(timezone.utc)  # utc-ok: an instant, compared with instants; no day is cut here
        self.counts['bumps'] += 1
        try:
            self.store().merge(self.versions, self.scope, {'at': now.replace(tzinfo=None), 'what': what},
                               increment={'version': 1, **{'d_' + d: 1 for d in domains}})
            return True
        except firestore.Unreachable:
            return False

    def rebuild(self, now=None, budget=BUDGET_SECONDS, pushed=None, clock=time.monotonic, routes=None) -> dict:
        """One rebuild pass, for a host to run after a write while its database is awake: every registered route's
        kept documents of this scope that are no longer usable (the version moved, or their end passed) and were
        asked for within keep_days are rebuilt under the current version, newest asked first, until `budget` seconds
        are spent. `pushed(route, who)` is called once for each document whose version has not been pushed yet (one
        this pass rebuilt, or one a reader rebuilt for itself since), and the document remembers it.

        One pass at a time (a lease): a second caller gets {'busy': True} at once. Before it ends the pass reads the
        version again and goes round once more when a write landed meanwhile, so a busy caller's write is covered.
        {'rebuilt', 'pushed', 'failed', 'left', 'seconds', 'rounds'}; 'left' counts documents the budget did not
        reach, for which the host asks for another pass. A rebuild that raises is counted failed and its document
        left for its next reader. Never raises for Firestore: {'unread': '<why>'}."""
        started = clock()
        report = {'rebuilt': 0, 'pushed': 0, 'failed': 0, 'left': 0, 'rounds': 0}
        lease = f'{self.scope}__rebuild'
        try:
            if not self.store().lock(self.locks, lease, budget + 30):
                return {'busy': True}
        except firestore.Unreachable as e:
            return {'unread': str(e)}
        try:
            seen = None
            while report['rounds'] < 3:
                report['rounds'] += 1
                at = now or datetime.now(timezone.utc)  # utc-ok: an instant, compared with instants; no day is cut here
                signal = self.store().get(self.versions, self.scope) or {}
                if seen is not None and signal.get('version') == seen:
                    break
                seen = signal.get('version')
                report['left'] = 0
                for name in (routes or sorted(self.routes)):
                    self._rebuild_route(self.routes[name], signal, at, report, pushed, lambda: clock() - started >= budget)
                if report['left']:
                    break
        except firestore.Unreachable as e:
            report['unread'] = str(e)
        finally:
            self.store().release(self.locks, lease)
        report['seconds'] = round(clock() - started, 1)
        self.counts['rebuilt'] += report['rebuilt']
        log.info("answer_cache: rebuild pass for %s: %s", self.scope, report)
        return report

    def _refused(self, error) -> bool:
        try:
            return bool(self.refused(error))
        except Exception:   # noqa: BLE001 - a test that fails is no refusal: the answer is kept and tried again
            return False

    def _rebuild_route(self, reg, signal, now, report, pushed, spent) -> None:
        stamp = reg.inputs() if reg.inputs else ''
        since = now - timedelta(days=self.keep_days)
        docs = self.store().run_query(self.collection, where=[('scope', '==', self.scope), ('route', '==', reg.name)],
                                      select=list(SELECT))
        never = datetime.min.replace(tzinfo=timezone.utc)  # utc-ok: an instant before every other, for a document never asked for
        live = [(doc_id, d) for doc_id, d in docs if (instant(d.get('asked')) or never) >= since]
        live.sort(key=lambda item: str(item[1].get('asked') or ''), reverse=True)
        for doc_id, d in live:
            try:
                key = json.loads(d.get('key') or 'null')
                version = version_of(signal, reg.domains_of(key))
            except Exception:   # noqa: BLE001 - a document this code cannot read is its next reader's
                report['failed'] += 1
                continue
            fresh = self.usable(d, version, stamp, now, reg.shape)
            if fresh and d.get('pushed') == version:
                continue
            if spent():
                report['left'] += 1
                continue
            who = tuple(d.get('who') or ())
            if not fresh:
                try:
                    answer = reg.rebuild(who, key)
                    if answer is SKIP:
                        report['skipped'] = report.get('skipped', 0) + 1
                        continue
                    body = self.encode(answer)
                except Exception as e:   # noqa: BLE001 - one caller's answer never stops the pass; its next read builds
                    if self.refused is not None and self._refused(e):
                        # 0.45.0: the caller's own refusal never changes by trying again: the kept answer goes, so no
                        # later pass opens the database for it (SMADPickleBot #2029: 23 a pass, 844 in two hours)
                        try:
                            self.store().delete(self.collection, doc_id)
                        except Exception:   # noqa: BLE001 - left for its age to end, as before
                            pass
                        report['dropped'] = report.get('dropped', 0) + 1
                        continue
                    report['failed'] += 1
                    log.info("answer_cache: %s could not be rebuilt for one caller (%s)", reg.name, type(e).__name__)
                    continue
                if not self._keep(reg.name, doc_id, who, key, answer, body, version, stamp, now, reg.until, reg.keep,
                                  pushed=version, shape=reg.shape):
                    continue
                report['rebuilt'] += 1
            else:
                self.store().merge(self.collection, doc_id, {'pushed': version})
            if pushed:
                try:
                    pushed(reg.name, who)
                    report['pushed'] += 1
                except Exception as e:   # noqa: BLE001 - a missed signal costs a refresh, never the pass
                    log.info("answer_cache: %s's change signal was not sent (%s)", reg.name, type(e).__name__)


class Bumps:
    """One request's version bumps: `committed(domains)` on each commit or successful write, `finish()` before the
    response leaves. One bump runs at a time in its own thread, alongside the rest of the request; a commit while it
    runs makes it bump once more, so the last commit is always covered and a request that committed five times bumps
    once or twice, never five. Each commit names the domains of the tables it wrote; a bump carries every domain
    named since the last one. `finish()` waits for it, so a client that wrote and then reads never gets the answer
    from before its write, on any instance."""

    def __init__(self, cache: AnswerCache, finish_seconds=FINISH_SECONDS):
        self.cache, self.finish_seconds = cache, finish_seconds
        self._lock = threading.Lock()
        self._asked = 0
        self._running = False
        self._thread = None
        self._domains = set()
        self._named = False

    def committed(self, domains=()) -> None:
        """A commit landed, having written tables of `domains`: start the bump now, or have the running one go once
        more."""
        if not self.cache.enabled:
            return
        with self._lock:
            self._domains.update(domains)
            self._named = self._named or bool(domains)
            self._asked += 1
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._run, name='answer-cache-bump', daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while True:
            with self._lock:
                target = self._asked
                domains, self._domains = self._domains, set()
            if not self.cache.bump(domains):
                log.warning("answer_cache: the data version could not be bumped: kept answers may be a write behind until their end")
            with self._lock:
                if self._asked == target:
                    self._running = False
                    return

    @property
    def pending(self) -> bool:
        """Whether this request bumped at all (the host then asks for a rebuild pass)."""
        return self._thread is not None

    @property
    def wrote_nothing(self) -> bool:
        """This request moved the version and no commit of it named a table it wrote: such a request ends every kept
        answer for nothing, which a host counts."""
        return self._asked > 0 and not self._named

    def finish(self) -> None:
        """Wait for the bump, at most finish_seconds, before the response leaves."""
        thread = self._thread
        if thread is not None:
            thread.join(self.finish_seconds)
            if thread.is_alive():
                log.warning("answer_cache: the data version bump took over %s s: a kept answer may be served once more",
                            self.finish_seconds)
