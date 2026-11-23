"""A desktop worker with a cloud standby: one lease document per piece of work (SMADPickleBot #2158, from #1685's audit).

The desktop claims the work by creating its document, beats into it while it runs, and records how it ended; the
cloud's twin reads it and decides: take the work (no claim, a stale beat, or the desktop's run ended with an error),
run it (the cloud already holds it), stand down (the desktop's run ended well), or wait for a fresh beat. `Lease.gate`
is the twin's stand-by loop until a deadline. The store is any record store with `create`, `get`, `merge` and `set`
(the kit's firestore.Client has them); the collection, the two holders' names, the timings and the words a log line
uses are the host's, so the kit names no project. The lease is standard library only.

`Handover` (SMADPickleBot #2231, the other half of #1685's failover pattern) is for work that never ends: one Pub/Sub
subscription two owners take turns holding. The desktop takes it by making it a pull subscription and pulls it; the
cloud holds it as a push subscription to its own service. One owner record says who holds it, and a dead man's Cloud
Tasks task, which the desktop arms `deadman_s` ahead and re-arms on every beat, posts the host's failover route when
the desktop stops, which gives the subscription back to the cloud. Its Pub/Sub and Cloud Tasks calls are the kit's
google_rest, imported when a call is made, so `Lease` still needs nothing installed.
"""
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

__all__ = ['Handover', 'Lease', 'SwitchFailed', 'aware']


def aware(value):
    """A stored instant as an aware datetime: a datetime (a naive one read as UTC) or an ISO string, None for anything
    else or an unreadable string."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)   # utc-ok: a stored naive instant is UTC
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    return None


def _now(now) -> datetime:
    return now or datetime.now(timezone.utc)   # utc-ok: an instant to compare beats by, never a day


@dataclass(frozen=True)
class Lease:
    """The lease rules for one collection: `desktop` and `cloud` the holders' names, a beat `stale_s` old (or from a
    clock that far ahead) a desktop gone, the twin reading every `poll_s`; `what` names the work in the twin's lines
    ("the night") and `tag` starts them ("[STANDBY]")."""
    collection: str
    desktop: str = 'desktop'
    cloud: str = 'cloud'
    stale_s: int = 30
    poll_s: int = 5
    what: str = 'the work'
    tag: str = '[STANDBY]'

    def claim(self, store, key: str, now: datetime = None) -> bool:
        """The desktop takes `key` unless its document exists: True when this call created it."""
        now = _now(now)
        return bool(store.create(self.collection, key, {
            'holder': self.desktop, 'claimed_at': now, 'beat_at': now, 'reason': f'the desktop claimed {self.what}'}))

    def beat(self, store, key: str, now: datetime = None) -> str:
        """One desktop beat: '' when the desktop still holds `key` and the beat was written; else the holder that has
        it ('none' for no document), nothing written. The store's own error when it cannot be read or written, which
        the desktop counts toward its fence."""
        doc = store.get(self.collection, key)
        holder = (doc or {}).get('holder') or 'none'
        if holder != self.desktop:
            return holder
        store.merge(self.collection, key, {'beat_at': _now(now)})
        return ''

    def done(self, store, key: str, code, now: datetime = None) -> None:
        """The desktop's run ended on its own with exit `code`: an exit 0 settles the work, any other hands it to the
        cloud's twin at its next read."""
        store.merge(self.collection, key, {'done_at': _now(now), 'code': -1 if code is None else int(code)})

    def decide(self, doc, now: datetime = None) -> tuple:
        """(verdict, why) for one read of the twin's document: 'take' when the cloud must take the work first (no
        document, the desktop's beat `stale_s` old or from a clock that far ahead, or its run ended with an error),
        'book' when the cloud already holds it, 'stand down' when the desktop's run ended well, 'wait' for a fresh
        desktop beat."""
        now = _now(now)
        if not doc:
            return 'take', f'the desktop did not claim {self.what}'
        if doc.get('holder') == self.cloud:
            return 'book', f'the cloud holds {self.what}'
        if doc.get('done_at'):
            if doc.get('code') == 0:
                return 'stand down', 'the desktop runner ended well'
            return 'take', f"the desktop runner ended with exit {doc.get('code')}"
        beat_at = aware(doc.get('beat_at'))
        age = (now - beat_at).total_seconds() if beat_at else None
        if age is None or age >= self.stale_s or age < -self.stale_s:
            return 'take', f"the desktop's last beat is {'unreadable' if age is None else f'{int(age)} s old'}"
        return 'wait', f"the desktop holds {self.what}, its beat {max(0, int(age))} s old"

    def take(self, store, key: str, doc, now: datetime, why: str) -> None:
        """The cloud's twin takes `key`: the document written whole, holder cloud, with who held it before."""
        store.set(self.collection, key, {'holder': self.cloud, 'claimed_at': now, 'beat_at': now,
                                         'reason': f'cloud: {why}', 'was': (doc or {}).get('holder') or 'none'})

    def gate(self, store, key: str, name: str, until: datetime, now=None, sleep=time.sleep, log=print,
             unreachable=(Exception,)) -> bool:
        """The cloud twin's stand-by before it runs `key`: True to run (taken, or the cloud's already), False to stand
        down (the desktop's run ended well, or `until` passed with the desktop still holding). Reads every `poll_s`; a
        store that cannot be read for `stale_s` is a desktop that has fenced itself, so it runs. `store` None runs at
        once. Each verdict that ends the wait is one log line, `name` the runner's."""
        clock = now or (lambda: datetime.now(timezone.utc))   # utc-ok: an instant to compare beats by, never a day
        if store is None:
            log(f'{self.tag} {name}: no Firestore client; grabbing')
            return True
        first_fail, said = None, ''
        while True:
            t = clock()
            try:
                doc = store.get(self.collection, key)
            except unreachable as e:
                first_fail = first_fail or t
                if (t - first_fail).total_seconds() >= self.stale_s:
                    log(f'{self.tag} {name}: Firestore unreadable {self.stale_s} s ({type(e).__name__}); the desktop has '
                        'fenced itself, grabbing')
                    return True
                sleep(self.poll_s)
                continue
            first_fail = None
            verdict, why = self.decide(doc, t)
            if verdict == 'take':
                try:
                    self.take(store, key, doc, t, why)
                except unreachable as e:
                    log(f'{self.tag} {name}: the takeover was not written ({type(e).__name__}); grabbing anyway')
                log(f'{self.tag} {name}: {why}; the cloud took {self.what}, grabbing')
                return True
            if verdict == 'book':
                log(f'{self.tag} {name}: {why}; grabbing')
                return True
            if verdict == 'stand down':
                log(f'{self.tag} {name}: {why}; standing down')
                return False
            if t >= until:
                log(f'{self.tag} {name}: the watch ended with the desktop still holding {self.what}; standing down')
                return False
            if not said:
                said = why
                log(f'{self.tag} {name}: {why}; standing by')
            sleep(self.poll_s)


class SwitchFailed(Exception):
    """A Pub/Sub or Cloud Tasks call of a handover was refused or could not be made: the message says which and why."""


_EPOCH = datetime.fromtimestamp(0, timezone.utc)   # utc-ok: the instant an unreadable beat is read as, so it is stale


def _second(value):
    """An instant cut to the second in UTC, so two clients of one record store the same value; anything else as it
    is."""
    if isinstance(value, datetime):
        return aware(value).astimezone(timezone.utc).replace(microsecond=0)   # utc-ok: a stored instant, never a day
    return value


def _stamp(value) -> datetime:
    """A stored instant as an aware datetime, the epoch for one that cannot be read: a beat no one can read is stale."""
    return aware(value if isinstance(value, datetime) else str(value)) or _EPOCH


@dataclass(frozen=True)
class Handover:
    """One Pub/Sub subscription two owners take turns holding (module docstring). `subscription` and `queue` are
    resource names, `pusher` the service account the subscription pushes as and the dead man's task calls as,
    `cloud_url` the cloud worker's address (the push endpoint and its token's audience), `collection`/`doc` the owner
    record {holder, beat_at, reason, since}. A dead man's task is due `deadman_s` after it is armed and posts the
    host's own address + `failover_path`; the desktop takes the subscription back only after `steady_s` of steady
    beats. `what` names the subscription and `cloud_what` the cloud worker in a line, `tag` starts every line;
    `signal`, a (collection, doc) pair, is a change-signal document {holder, since, at, **signal_extra} written only
    when the holder changes, for clients that show who holds it. `error` is raised for a refused call."""
    subscription: str
    pusher: str
    queue: str
    cloud_url: str
    collection: str
    doc: str
    failover_path: str = '/worker/failover'
    desktop: str = 'desktop'
    cloud: str = 'cloud'
    deadman_s: int = 70
    steady_s: int = 300
    timeout: float = 5
    what: str = 'the subscription'
    cloud_what: str = 'the cloud worker'
    tag: str = 'worker owner'
    signal: tuple = None
    signal_extra: dict = field(default_factory=dict)
    error: type = SwitchFailed

    def _kw(self, opener) -> dict:
        # guard 'none': the desktop makes these calls from off the cloud, where the kit's live guard would refuse them
        return {'timeout': self.timeout, 'guard': 'none', 'error': self.error, 'opener': opener}

    def push_config(self, url: str = None) -> dict:
        """The subscription's push config for the cloud worker: its URL and the pusher's OIDC token for that
        audience."""
        url = url or self.cloud_url
        return {'pushEndpoint': url, 'oidcToken': {'serviceAccountEmail': self.pusher, 'audience': url}}

    def set_push(self, token, config: dict = None, opener=None) -> None:
        """Make the subscription a push subscription with `config`, or a pull subscription with none."""
        from . import google_rest
        google_rest.call('POST', f'https://pubsub.googleapis.com/v1/{self.subscription}:modifyPushConfig', token,
                         {'pushConfig': config or {}}, **self._kw(opener))

    def subscription_holder(self, token, opener=None) -> str:
        """Who the subscription itself says holds it: the cloud while it pushes, the desktop while it is a pull
        subscription."""
        from . import google_rest
        got = google_rest.call('GET', f'https://pubsub.googleapis.com/v1/{self.subscription}', token,
                               **self._kw(opener))
        return self.cloud if (got.get('pushConfig') or {}).get('pushEndpoint') else self.desktop

    def read(self, store) -> dict:
        """The owner record, or {} before the first switch (the cloud then holds it: the subscription pushes)."""
        return store.get(self.collection, self.doc) or {}

    def write(self, store, holder: str, reason: str, now: datetime = None, since: datetime = None) -> dict:
        """The owner record whole: holder, beat_at now, the reason and since when, every instant cut to the second.
        With a `signal`, that document is written too when it names another holder or is missing; reading or writing
        it never fails the record's own write, and the next beat puts a stale one right."""
        now = _second(_now(now))
        doc = {'holder': holder, 'beat_at': now, 'reason': reason, 'since': _second(since or now)}
        was = holder
        if self.signal:
            try:
                was = (store.get(*self.signal) or {}).get('holder')
            except Exception:   # noqa: BLE001 - unread: the next beat tries again; the owner write is what counts
                was = holder
        store.set(self.collection, self.doc, doc)
        if self.signal and was != holder:
            try:
                self.send_signal(store, holder, doc['since'], now)
            except Exception:   # noqa: BLE001 - the clients' signal is best effort; the switch stands
                pass
        return doc

    def send_signal(self, store, holder: str, since, now: datetime = None) -> dict:
        """The change signal written whole: {holder, since, at} and the host's `signal_extra`."""
        now = _second(_now(now))
        doc = {'holder': holder, 'since': _second(since or now), 'at': now, **self.signal_extra}
        store.set(*self.signal, doc)
        return doc

    def take(self, store, token, reason: str, now: datetime = None, opener=None) -> str:
        """The desktop takes the subscription: the owner written first (so a failover that fires meanwhile sees the
        desktop as the holder and restores the push), then the subscription made a pull one. The line to log."""
        self.write(store, self.desktop, reason, now=now)
        self.set_push(token, None, opener)
        return f'{self.tag}: the desktop holds {self.what} ({reason})'

    def give_back(self, store, token, reason: str, now: datetime = None, opener=None) -> str:
        """The cloud takes the subscription back: the push endpoint restored first (messages flow at once), then the
        owner written. The line to log."""
        self.set_push(token, self.push_config(), opener)
        self.write(store, self.cloud, reason, now=now)
        return f'{self.tag}: the cloud holds {self.what} ({reason})'

    def failover(self, store, token, now: datetime = None, opener=None) -> tuple:
        """The dead man's task fired: (changed, line). Nothing when the cloud already holds the subscription (an old
        task, or a hand-back that raced it), and nothing when the desktop has beaten within `deadman_s`: a task fires
        `deadman_s` after it was armed, so a beat since then is a live desktop whose beat could not delete this task,
        and the task that beat armed covers it. Otherwise the push is restored, the cloud recorded as the holder with
        the beat's age, and the line starts `ERROR:` for the host's alerting."""
        now = _now(now)
        doc = self.read(store)
        if doc.get('holder') != self.desktop:
            return False, f'{self.tag}: the dead man fired but the cloud already holds {self.what}; nothing to do'
        age = int((now - _stamp(doc.get('beat_at'))).total_seconds())
        if 0 <= age < self.deadman_s:
            return False, (f'{self.tag}: the dead man fired but the desktop beat {age} s ago: a stray task; the one '
                           'armed since covers it')
        self.give_back(store, token, f'failover: the desktop missed its heartbeat (owner beat {age} s ago)', now=now,
                       opener=opener)
        return True, f'ERROR: {self.tag}: the desktop stopped answering; {self.what} failed over to {self.cloud_what}'

    def deadman_task(self, token, host_url: str, seq: int, now: datetime = None, opener=None) -> str:
        """Arm one dead man's task due `deadman_s` from now, posting `host_url` + `failover_path` with the pusher's
        OIDC token; its name, for the next beat to delete."""
        from . import google_rest
        url = host_url.rstrip('/') + self.failover_path
        task = google_rest.http_task(url, {'seq': seq}, at=_now(now) + timedelta(seconds=self.deadman_s),
                                     oidc=(self.pusher, url))
        got = google_rest.create_task(self.queue, task, token, exists_ok=False, **self._kw(opener))
        return got.get('name', '')

    def disarm(self, token, name: str, opener=None) -> None:
        """Delete one dead man's task; one already gone (it fired, or was deleted) is fine."""
        if not name:
            return
        from . import google_rest
        google_rest.delete_task(name, token, queue=self.queue, **self._kw(opener))

    def pending_tasks(self, token, opener=None) -> list:
        """Every task waiting on the queue, by name: the orphans a resuming worker deletes once its own is armed."""
        from . import google_rest
        return [t['name'] for t in google_rest.list_tasks(self.queue, token, **self._kw(opener)) if t.get('name')]

    def may_resume(self, doc: dict, now: datetime = None) -> bool:
        """Whether a worker just started may resume the desktop's hold: the record names the desktop and its last beat
        is under `deadman_s` old, so the stopped worker's dead man task has not fired yet (a crash or a restart skipped
        the hand-back, and waiting out the steady time would let that orphan task fail over)."""
        if doc.get('holder') != self.desktop or not doc.get('beat_at'):
            return False
        return 0 <= (_now(now) - _stamp(doc.get('beat_at'))).total_seconds() < self.deadman_s

    def may_take_back(self, doc: dict, steady_since: datetime, now: datetime = None, busy: str = '') -> str:
        """Why the desktop may not take the subscription back now, or '' when it may: it must not already hold it, the
        host must not be `busy` (its own reason, returned as it is: work in flight that a switch would disturb), and
        the desktop's heartbeat must have been steady `steady_s`."""
        if doc.get('holder') == self.desktop:
            return 'the desktop already holds it'
        if busy:
            return busy
        if steady_since is None or (_now(now) - steady_since).total_seconds() < self.steady_s:
            return f'the heartbeat has not been steady {self.steady_s} s'
        return ''
