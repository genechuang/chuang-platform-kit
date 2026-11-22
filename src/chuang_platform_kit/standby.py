"""A desktop worker with a cloud standby: one lease document per piece of work (SMADPickleBot #2158, from #1685's audit).

The desktop claims the work by creating its document, beats into it while it runs, and records how it ended; the
cloud's twin reads it and decides: take the work (no claim, a stale beat, or the desktop's run ended with an error),
run it (the cloud already holds it), stand down (the desktop's run ended well), or wait for a fresh beat. `Lease.gate`
is the twin's stand-by loop until a deadline. The store is any record store with `create`, `get`, `merge` and `set`
(the kit's firestore.Client has them); the collection, the two holders' names, the timings and the words a log line
uses are the host's, so the kit names no project. Standard library only.
"""
import time
from dataclasses import dataclass
from datetime import datetime, timezone

__all__ = ['Lease', 'aware']


def aware(value):
    """A stored instant as an aware datetime: a datetime (a naive one read as UTC) or an ISO string, None for anything
    else or an unreadable string."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    return None


def _now(now) -> datetime:
    return now or datetime.now(timezone.utc)


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
        clock = now or (lambda: datetime.now(timezone.utc))
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
