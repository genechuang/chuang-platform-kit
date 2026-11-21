#!/usr/bin/env python3
"""chuang_platform_kit.outbox: the transactional outbox, Postgres (over SQLAlchemy) and Firestore (over a store seam).

Pinned (the positive case first, offline: SQLite in memory for the model, a stand-in publish and store):

- emit then commit then dispatch: each row published once in emit order (whatever order the read returns), with
  the host's attributes and ordering key, then stamped; the host's own column kept on the row
- a refused publish: the row unstamped, its attempts counted, the state asking for a sweep, a WARNING; the next
  dispatch sends nothing more while one fails, and the sweep sends it once it is older than after_s, oldest first
- the attempt that reaches `stuck` is an ERROR line
- the sweep keeps the flag while rows are left (one too young, or past the limit) and clears it when none are
- a programming error inside dispatch never raises to the caller: the state asks for a sweep
- drop takes back the undispatched rows a session emitted, never a published one
- Firestore: fs_writes are create-only documents with pending and the TTL field; publish_staged sends and marks;
  a refused publish keeps its document pending and sets the state; a mark the store refuses is a WARNING; sweep_fs
  sends the pending ones older than after_s oldest first, treats an unreadable stamp as old, and reports `left`;
  no store: published only, nothing swept

Run:  python -B tests/test-outbox.py
"""
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from sqlalchemy import JSON, DateTime, Integer, String, create_engine, func, select  # noqa: E402
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column  # noqa: E402

from chuang_platform_kit import outbox as O  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class Base(DeclarativeBase):
    pass


class Box(Base):
    __tablename__ = 'outbox'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(60))
    ordering_key: Mapped[str] = mapped_column(String(120), default='')
    envelope: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class Refused(Exception):
    pass


class Publish:
    """A stand-in publish: refuses the envelope ids in `refuse`, records every call it takes."""

    def __init__(self, refuse=()):
        self.refuse, self.calls = set(refuse), []

    def __call__(self, env, ordering_key='', topic='', attributes=None):
        if env['id'] in self.refuse:
            raise Refused(f"refused {env['id']}")
        self.calls.append((env['id'], ordering_key, topic, attributes))
        return 'msg-' + env['id']


class Lines(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append((record.levelname, record.getMessage()))


log = logging.getLogger('test-outbox')
log.setLevel(logging.DEBUG)
log.propagate = False
lines = Lines()
log.addHandler(lines)


def attrs(type_, env):
    return {'type': type_, 'tenant': env.get('tenant', '')}


def env(i, type_='thing.done'):
    return {'id': f'e{i}', 'type': type_, 'tenant': 'acme', 'data': {'n': i}}


def fresh():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    return Session(engine)


def kw(state):
    return {'topic': 'events', 'attributes': attrs, 'failure': Refused, 'state': state, 'log': log, 'label': 'events'}


def age(s, ids, seconds):
    for row in s.scalars(select(Box).where(Box.id.in_(ids))):
        row.created_at = datetime.now(timezone.utc) - timedelta(seconds=seconds)   # utc-ok: an instant, aged
    s.commit()


# --- Postgres -------------------------------------------------------------------------------------------------------

s = fresh()
state = {'sweep': False}
for i in (3, 1, 2):
    O.emit(s, Box, env(i), ordering_key='game-7', tenant_id=42)
s.commit()
pub = Publish()
got = O.dispatch(s, Box, pub, **kw(state))
rows = {r.id: r for r in s.scalars(select(Box))}
check('emit, commit, dispatch: each published once in emit order with attributes and key, stamped, columns kept',
      (got, [c[0] for c in pub.calls], pub.calls[0][1:], all(r.published_at for r in rows.values()), rows['e1'].tenant_id,
       'events' in s.info),
      ({'sent': 3, 'failed': 0, 'swept': 0}, ['e3', 'e1', 'e2'], ('game-7', 'events', {'type': 'thing.done', 'tenant': 'acme'}),
       True, 42, False))

s = fresh()
state = {'sweep': False}
O.emit(s, Box, env(1), tenant_id=1)
O.emit(s, Box, env(2), tenant_id=1)
s.commit()
lines.lines.clear()
pub = Publish(refuse={'e2'})
got = O.dispatch(s, Box, pub, **kw(state))
row = s.get(Box, 'e2')
check('a refused publish: unstamped, attempts counted, the state asks, a WARNING; the other one sent',
      (got, row.published_at, row.attempts, state['sweep'], [lv for lv, _ in lines.lines]),
      ({'sent': 1, 'failed': 1, 'swept': 0}, None, 1, True, ['WARNING']))

pub = Publish()
check('a sweep right after finds the row too young, sends nothing and keeps asking',
      (O.sweep(s, Box, pub, **kw(state)), state['sweep'], pub.calls), ({'sent': 0, 'failed': 0, 'left': 1}, True, []))

age(s, ['e2'], 120)
O.emit(s, Box, env(3), tenant_id=1)
s.commit()
got = O.dispatch(s, Box, pub, **kw(state))
check('the next dispatch sends its own, then sweeps the old one; the flag clears with none left',
      (got, [c[0] for c in pub.calls], s.get(Box, 'e2').published_at is not None, state['sweep']),
      ({'sent': 1, 'failed': 0, 'swept': 1}, ['e3', 'e2'], True, False))

s = fresh()
state = {'sweep': True}
for i in (1, 2, 3):
    O.emit(s, Box, env(i), tenant_id=1)
s.commit()
s.info.clear()
age(s, ['e1'], 300)
age(s, ['e2'], 200)
age(s, ['e3'], 100)
pub = Publish()
got = O.sweep(s, Box, pub, limit=2, **kw(state))
check('the sweep goes oldest first, stops at the limit, and keeps asking while rows are left',
      (got, [c[0] for c in pub.calls], state['sweep']), ({'sent': 2, 'failed': 0, 'left': 1}, ['e1', 'e2'], True))

s = fresh()
state = {'sweep': True}
O.emit(s, Box, env(9), tenant_id=1)
s.commit()
s.info.clear()
age(s, ['e9'], 120)
lines.lines.clear()
pub = Publish(refuse={'e9'})
for _ in range(3):
    O.sweep(s, Box, pub, stuck=3, **kw(state))
check('the attempt that reaches stuck is an ERROR line', ([lv for lv, _ in lines.lines if lv in ('WARNING', 'ERROR')],
                                                         s.get(Box, 'e9').attempts), (['WARNING', 'WARNING', 'ERROR'], 3))


def boom(*a, **k):
    raise KeyError('a bug in the host')


s = fresh()
state = {'sweep': False}
O.emit(s, Box, env(1), tenant_id=1)
s.commit()
lines.lines.clear()
got = O.dispatch(s, Box, boom, **kw(state))
check('a programming error in dispatch never raises: the state asks and a WARNING says it',
      (got, state['sweep'], lines.lines[-1][0], 'dispatch failed (KeyError' in lines.lines[-1][1]),
      ({'sent': 0, 'failed': 0, 'swept': 0}, True, 'WARNING', True))

s = fresh()
O.emit(s, Box, env(1), tenant_id=1)
s.commit()
O.dispatch(s, Box, Publish(), **kw({'sweep': False}))
O.emit(s, Box, env(2), tenant_id=1)
s.commit()
s.info.setdefault('events', []).insert(0, 'e1')
O.drop(s, Box)
s.commit()
check('drop takes back the undispatched row a session emitted, never a published one',
      sorted(r.id for r in s.scalars(select(Box))), ['e1'])

# --- Firestore ------------------------------------------------------------------------------------------------------

now = datetime(2026, 11, 20, 20, 0, tzinfo=timezone.utc)   # utc-ok: a fixed instant for the stamps
staged = [O.stage(env(1), 'chat-1', {'type': 'chat.posted'}), O.stage(env(2), '', None)]
writes = O.fs_writes('event_outbox', staged, now, keep_days=2)
check('stage and fs_writes: create-only documents, pending, the TTL field two days ahead',
      (staged[1], len(writes), writes[0]['update']['name'].endswith('event_outbox/e1'),
       writes[0].get('currentDocument'), writes[0]['update']['fields']['pending']),
      ({'id': 'e2', 'envelope': env(2), 'ordering_key': '', 'attributes': {}}, 2, True, {'exists': False},
       {'booleanValue': True}))


class Store:
    def __init__(self, docs=None, mark_ok=True):
        self.docs, self.mark_ok, self.marked = dict(docs or {}), mark_ok, []

    def pending(self, limit):
        return [(i, f) for i, f in self.docs.items() if f.get('pending')][:limit]

    def mark_sent(self, ids, now):
        self.marked.extend(ids)
        if self.mark_ok:
            for i in ids:
                self.docs.setdefault(i, {})['pending'] = False
        return self.mark_ok


def fkw(state):
    return {'topic': 'events', 'failure': Refused, 'state': state, 'log': log, 'label': 'events'}


store, state, pub = Store(), {'sweep': False}, Publish()
got = O.publish_staged(staged, pub, store=store, **fkw(state))
check('publish_staged sends in order with each key and attributes, and marks the sent ones',
      (got, [(c[0], c[1], c[3]) for c in pub.calls], store.marked),
      ({'sent': 2, 'failed': 0, 'swept': 0}, [('e1', 'chat-1', {'type': 'chat.posted'}), ('e2', '', {})], ['e1', 'e2']))

store, state, pub = Store(), {'sweep': False}, Publish(refuse={'e1'})
lines.lines.clear()
got = O.publish_staged(staged, pub, store=store, **fkw(state))
check('a refused one stays pending and the state asks; a store that refuses the mark is a WARNING',
      (got, store.marked, state['sweep']), ({'sent': 1, 'failed': 1, 'swept': 0}, ['e2'], True))
O.publish_staged(staged[1:], Publish(), store=Store(mark_ok=False), **fkw({'sweep': False}))
check('a mark the store refuses is a WARNING', 'could not be marked' in lines.lines[-1][1], True)

old = (now - timedelta(seconds=300)).isoformat().replace('+00:00', 'Z')
older = (now - timedelta(seconds=600)).isoformat()
young = (now - timedelta(seconds=5)).isoformat()
store = Store({'a': {'pending': True, 'created_at': old, 'envelope': env('a'), 'ordering_key': 'k', 'attributes': {'x': '1'}},
               'b': {'pending': True, 'created_at': older, 'envelope': env('b')},
               'c': {'pending': True, 'created_at': young, 'envelope': env('c')},
               'd': {'pending': True, 'created_at': 'not a time', 'envelope': env('d')},
               'e': {'pending': False, 'created_at': older, 'envelope': env('e')}})
state, pub = {'sweep': True}, Publish()
got = O.sweep_fs(store, pub, now=now, **fkw(state))
check('sweep_fs: the pending ones older than after_s, oldest first, an unreadable stamp as old, the young one left',
      (got, sorted(c[0] for c in pub.calls), pub.calls[0][0], state['sweep']),
      ({'sent': 3, 'failed': 0, 'left': 1}, ['ea', 'eb', 'ed'], 'eb', True))

state, pub = {'sweep': True}, Publish()
got = O.publish_staged(staged, pub, store=None, **fkw(state))
check('no store: published only, nothing marked or swept', (got, len(pub.calls), O.sweep_fs(None, pub, **fkw(state))),
      ({'sent': 2, 'failed': 0, 'swept': 0}, 2, {'sent': 0, 'failed': 0, 'left': 0}))

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
