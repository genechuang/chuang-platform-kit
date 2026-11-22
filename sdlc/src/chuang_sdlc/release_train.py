"""The release train's generic parts, moved from SMADPickleBot's pipeline/desktop scripts one at a time (#2159, from
#1685's audit); each host script keeps its names over them.

`Lease`: one push at a time to a shared branch (SMADPickleBot's dock push, #1252). A run takes a lease document in a
record store before it gates, renews it before each push, and frees it at its end; another run waits for it, and a
killed run's lease (its `until` passed) is deleted and taken again by one waiter. A store that cannot be read is pushed
past once, blind: nothing more is written, but the run still frees a lease that names it (SMADPickleBot #2076).
The store is any record store with `create`, `get`, `set` and `delete`. Standard library only.
"""
import time
import uuid
from datetime import datetime, timedelta, timezone

__all__ = ['Lease', 'parse_instant']


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
