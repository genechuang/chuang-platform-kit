"""The transactional outbox: an event written with its record, published after the commit, swept when a publish fails.

A write that others react to writes its event in the record's own transaction, so a record never exists without its
event nor an event without its record. After the commit the host publishes what it wrote; a publish that fails leaves
the event pending for a sweep, which sends every pending event older than `after_s`, oldest first.

Two stores, the same shape:

- **Postgres** (SQLAlchemy), over the host's own model: `emit` in the transaction, `dispatch` after the commit,
  `sweep`, and `drop` when the host undoes its own write before dispatching. The model has the columns `id` (the
  envelope's id, the primary key), `type`, `ordering_key`, `envelope` (JSON), `attempts` (an integer), `created_at`
  (set by the database) and `published_at` (nullable); any other column the host keeps is a keyword to `emit`.
- **Firestore**, over a store the host adapts: `stage` an event, put `fs_writes` in the record's own commit, then
  `publish_staged` after the answer and `sweep_fs`. The store has `pending(limit) -> [(id, fields)]` and
  `mark_sent(ids, now) -> bool`; a host off Firestore passes `store=None`, and its events are only published.

    env = pubsub.envelope('match.recorded', data, source='my-api', subject={'game': 7})
    outbox.emit(s, Outbox, env, ordering_key='game-7', tenant_id=t.id)
    s.commit()
    outbox.dispatch(s, Outbox, publish, topic=TOPIC, attributes=attrs, failure=PublishFailed, state=STATE)

What a host passes, so the kit keeps nothing of its own: `publish(envelope, ordering_key=, topic=, attributes=)`;
`attributes(type, envelope) -> dict`, its attribute rule; `failure`, the exception class (or tuple) its publish raises
for a refusal; `state`, its own `{'sweep': bool}`, one per process (set at a start and after any failed publish, so the
next dispatch sweeps); the `topic`; a logger and a `label` for the lines. `dispatch` and `publish_staged` never raise:
the record has committed, and a failed publish only waits for the sweep. A second publish of one envelope is the
consumers' to absorb (the envelope's id is their dedup key).
"""
import logging
from datetime import datetime, timedelta, timezone

SWEEP_AFTER_S = 60      # an event younger than this may still be on its way through its own dispatch
SWEEP_LIMIT = 100
STUCK_ATTEMPTS = 5
KEEP_DAYS = 2           # the Firestore outbox's TTL: a document is deleted this many days after it was written

_log = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc)   # utc-ok: instants, compared with the stored instants


# --- Postgres -------------------------------------------------------------------------------------------------------

def emit(s, model, envelope: dict, ordering_key: str = '', key: str = 'events', **columns) -> str:
    """One event in `s`'s transaction: its `model` row, for `dispatch` after the commit. The envelope's id, which the
    row keeps as its own; the ids are remembered in `s.info[key]` in emit order."""
    s.add(model(id=envelope['id'], type=envelope.get('type', ''), ordering_key=ordering_key or '', envelope=envelope,
                **columns))
    s.info.setdefault(key, []).append(envelope['id'])
    return envelope['id']


def drop(s, model, key: str = 'events') -> None:
    """Take back the events `s` emitted and has not dispatched (the caller commits): its write was undone."""
    from sqlalchemy import select
    ids = s.info.pop(key, [])
    for row in s.scalars(select(model).where(model.id.in_(ids), model.published_at.is_(None))) if ids else []:
        s.delete(row)


def send_rows(s, rows, publish, *, topic: str, attributes, failure, state: dict, stuck: int = STUCK_ATTEMPTS,
              log=None, label: str = 'events') -> tuple:
    """Publish each row in order, stamp the sent ones' `published_at` and count a refused one's `attempts` (an ERROR
    line once it reaches `stuck`), then commit. (sent, failed)."""
    log = log or _log
    sent = failed = 0
    now = _now()
    for row in rows:
        try:
            publish(row.envelope, ordering_key=row.ordering_key or '', topic=topic,
                    attributes=attributes(row.type, row.envelope))
        except failure as e:
            row.attempts = (row.attempts or 0) + 1
            failed += 1
            state['sweep'] = True
            level = logging.ERROR if row.attempts >= stuck else logging.WARNING
            log.log(level, "%s: %s %s not published (%s), attempt %d; the sweep sends it", label, row.type, row.id, e,
                    row.attempts)
            continue
        row.published_at = now
        sent += 1
    s.commit()
    return sent, failed


def dispatch(s, model, publish, *, topic: str, attributes, failure, state: dict, key: str = 'events',
             sweep_after_s: int = SWEEP_AFTER_S, limit: int = SWEEP_LIMIT, stuck: int = STUCK_ATTEMPTS, log=None,
             label: str = 'events') -> dict:
    """After the commit: publish the events `s` emitted, in the order it emitted them, and stamp them; then, when the
    state asks (a failure or a start left rows behind) and none failed now, sweep. {sent, failed, swept}. Never
    raises."""
    from sqlalchemy import select
    log = log or _log
    out = {'sent': 0, 'failed': 0, 'swept': 0}
    ids = s.info.pop(key, [])
    try:
        if ids:
            rows = s.scalars(select(model).where(model.id.in_(ids), model.published_at.is_(None))).all()
            # In emit order: an id is random, so the read comes back in any order, and a batch's later event must
            # not be published before the ones it follows on the same ordering key.
            place = {x: i for i, x in enumerate(ids)}
            rows = sorted(rows, key=lambda r: place.get(r.id, len(place)))
            out['sent'], out['failed'] = send_rows(s, rows, publish, topic=topic, attributes=attributes,
                                                   failure=failure, state=state, stuck=stuck, log=log, label=label)
        if state.get('sweep') and not out['failed']:
            out['swept'] = sweep(s, model, publish, topic=topic, attributes=attributes, failure=failure, state=state,
                                 after_s=sweep_after_s, limit=limit, stuck=stuck, log=log, label=label)['sent']
    except Exception as e:   # noqa: BLE001 - the record has committed; the sweep catches up
        state['sweep'] = True
        log.warning("%s: dispatch failed (%s: %s); the sweep sends what is left", label, type(e).__name__, e)
    return out


def sweep(s, model, publish, *, topic: str, attributes, failure, state: dict, now=None,
          after_s: int = SWEEP_AFTER_S, limit: int = SWEEP_LIMIT, stuck: int = STUCK_ATTEMPTS, log=None,
          label: str = 'events') -> dict:
    """Publish every unstamped row older than `after_s`, oldest first, at most `limit`. {sent, failed, left}: `left`
    the unstamped rows still there after it. The state keeps asking while any are left, or the limit was reached, or
    one failed (a sweep within `after_s` of a failed publish finds its row too young and must not clear the flag)."""
    from sqlalchemy import select
    log = log or _log
    cutoff = (now or _now()) - timedelta(seconds=after_s)
    rows = s.scalars(select(model).where(model.published_at.is_(None), model.created_at <= cutoff)
                     .order_by(model.created_at).limit(limit)).all()
    sent, failed = send_rows(s, rows, publish, topic=topic, attributes=attributes, failure=failure, state=state,
                             stuck=stuck, log=log, label=label) if rows else (0, 0)
    left = len(s.scalars(select(model.id).where(model.published_at.is_(None))).all())
    state['sweep'] = bool(failed or len(rows) == limit or left)
    if sent or failed:
        log.info("%s: swept %d, %d failed, %d left", label, sent, failed, left)
    return {'sent': sent, 'failed': failed, 'left': left}


# --- Firestore ------------------------------------------------------------------------------------------------------

def stage(envelope: dict, ordering_key: str = '', attributes: dict = None) -> dict:
    """One event for the Firestore outbox: {id, envelope, ordering_key, attributes}, for `fs_writes` in the record's
    own commit and `publish_staged` after the answer."""
    return {'id': envelope['id'], 'envelope': envelope, 'ordering_key': ordering_key or '', 'attributes': attributes or {}}


def fs_writes(collection: str, staged: list, now: datetime, keep_days: int = KEEP_DAYS) -> list:
    """The Firestore writes that keep `staged` events pending, for the record's own commit: each its
    `<collection>/<id>` document, created only (a second write of one id is refused with its commit), with the TTL
    field `keep_days` ahead."""
    from . import firestore
    return [firestore.update(f'{collection}/{e["id"]}', {
        'envelope': e['envelope'], 'ordering_key': e['ordering_key'], 'attributes': e['attributes'], 'pending': True,
        'created_at': now, firestore.EXPIRES_FIELD: now + timedelta(days=keep_days)}, exists=False) for e in staged]


def _fs_send(items: list, store, publish, *, topic: str, failure, state: dict, log, label: str) -> tuple:
    """Publish each (id, envelope, ordering_key, attributes) in order and mark the sent ones. (sent, failed)."""
    sent, failed = [], 0
    for doc_id, env, key, attrs in items:
        try:
            publish(env, ordering_key=key or '', topic=topic, attributes=attrs)
        except failure as e:
            failed += 1
            state['sweep'] = True
            log.warning("%s: %s %s not published (%s); the Firestore sweep sends it", label, env.get('type'), doc_id, e)
            continue
        sent.append(doc_id)
    if sent and store is not None and not store.mark_sent(sent, _now()):
        # Published but still marked pending: the sweep sends them again, which downstream absorbs.
        log.warning("%s: %d published event(s) could not be marked; the Firestore sweep may send them again", label,
                    len(sent))
    return len(sent), failed


def publish_staged(staged: list, publish, *, store, topic: str, failure, state: dict, after_s: int = SWEEP_AFTER_S,
                   limit: int = SWEEP_LIMIT, log=None, label: str = 'events') -> dict:
    """After the answer: publish the events a record's commit kept (`stage`, `fs_writes`) and mark them; then, when
    the state asks and none failed now, sweep. {sent, failed, swept}. Never raises."""
    log = log or _log
    out = {'sent': 0, 'failed': 0, 'swept': 0}
    try:
        if staged:
            out['sent'], out['failed'] = _fs_send([(e['id'], e['envelope'], e['ordering_key'], e['attributes'])
                                                   for e in staged], store, publish, topic=topic, failure=failure,
                                                  state=state, log=log, label=label)
        if state.get('sweep') and not out['failed'] and store is not None:
            out['swept'] = sweep_fs(store, publish, topic=topic, failure=failure, state=state, after_s=after_s,
                                    limit=limit, log=log, label=label)['sent']
    except Exception as e:   # noqa: BLE001 - the answer has gone; the sweep catches up
        state['sweep'] = True
        log.warning("%s: the Firestore publish failed (%s: %s); the sweep sends what is left", label, type(e).__name__, e)
    return out


def _made(fields: dict, cutoff: datetime) -> datetime:
    at = fields.get('created_at')
    try:
        return datetime.fromisoformat(at.replace('Z', '+00:00')) if isinstance(at, str) else (at or cutoff)
    except ValueError:   # an unreadable stamp is old enough: sent rather than kept forever
        return cutoff


def sweep_fs(store, publish, *, topic: str, failure, state: dict, now=None, after_s: int = SWEEP_AFTER_S,
             limit: int = SWEEP_LIMIT, scan: int = 1000, log=None, label: str = 'events') -> dict:
    """Publish every pending document older than `after_s`, oldest first, at most `limit` (reading at most `scan`).
    {sent, failed, left}, `left` the pending ones it did not send (too young, or past the limit)."""
    log = log or _log
    if store is None:
        return {'sent': 0, 'failed': 0, 'left': 0}
    cutoff = (now or _now()) - timedelta(seconds=after_s)
    pending = sorted(store.pending(scan), key=lambda d: _made(d[1], cutoff))
    due = [d for d in pending if _made(d[1], cutoff) <= cutoff][:limit]
    sent, failed = _fs_send([(i, f.get('envelope') or {}, f.get('ordering_key', ''), f.get('attributes') or {})
                             for i, f in due], store, publish, topic=topic, failure=failure, state=state, log=log,
                            label=label) if due else (0, 0)
    left = len(pending) - sent
    state['sweep'] = bool(failed or left)
    if sent or failed:
        log.info("%s: swept %d from Firestore, %d failed, %d left", label, sent, failed, left)
    return {'sent': sent, 'failed': failed, 'left': left}
