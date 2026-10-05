"""One bot request's scope: sheet reads shared across it, and its step timings.

2026-10-01 PT profiling of /pb match, /pb match undo, /pb game plan and
/pb post game: one reply read the roster two to four times (get_poll_games()
reads it on every call, current_session() calls that up to three times), the
Win Loss Log four or five times and the SMAD DUPR Log twice, each a Sheets
round trip of 0.2-0.6 s. Inside scope() each of those is read once and reused
until the request writes to it (drop()); outside a scope nothing is cached,
so a CLI run, a scheduled job or another thread always reads the sheet.

The scope also carries a StepTimer: shared code calls mark('plan') where a
step ends, and the scope logs one `[TIMING]` line for the request. Outside a
scope mark() does nothing.

Stdlib only: copied into every Cloud Function with the rest of shared/.
"""
import logging
import threading
import time
from contextlib import contextmanager

logger = logging.getLogger(__name__)

_local = threading.local()


class StepTimer:
    """Seconds per step of one reply, logged as one `[TIMING]` line, so a slow
    reply says where its time went (Gene, 2026-10-01 PT: "I want to profile
    pb match and pb match undo to get their times down quicker")."""

    def __init__(self, label: str):
        self.label, self.steps = label, []
        self.start = self.last = time.time()

    def mark(self, step: str) -> None:
        now = time.time()
        self.steps.append((step, now - self.last))
        self.last = now

    def line(self) -> str:
        parts = ' '.join(f"{s}={d:.2f}s" for s, d in self.steps)
        return f"[TIMING] {self.label} {self.last - self.start:.2f}s: {parts}"


@contextmanager
def scope(label: str = ''):
    """Run one request inside it: reads through cached() are shared, and with
    a `label` its steps (mark()) are logged as one [TIMING] line at the end.
    Yields the StepTimer (None without a label). A nested scope keeps the
    outer one's cache and timer."""
    if getattr(_local, 'cache', None) is not None:
        yield getattr(_local, 'timer', None)
        return
    _local.cache = {}
    _local.timer = StepTimer(label) if label else None
    try:
        yield _local.timer
    finally:
        timer = _local.timer
        _local.cache, _local.timer = None, None
        if timer is not None:
            logger.info(timer.line())


def active() -> bool:
    """True inside scope()."""
    return getattr(_local, 'cache', None) is not None


def cached(key: str, load):
    """`load()`'s value, read once per scope under `key`; called every time
    outside one. A None or empty value (a failed read) is not kept, so the
    next caller tries again."""
    cache = getattr(_local, 'cache', None)
    if cache is None:
        return load()
    if key in cache:
        return cache[key]
    value = load()
    if value:
        cache[key] = value
    return value


def peek(key: str, default=None):
    """`key`'s value in this scope, `default` when absent or outside a scope."""
    cache = getattr(_local, 'cache', None)
    return default if cache is None else cache.get(key, default)


def put(key: str, value) -> None:
    """Keep `value` under `key` for this scope, whatever it is (a missing
    Firestore document is an answer too); nothing outside a scope."""
    cache = getattr(_local, 'cache', None)
    if cache is not None:
        cache[key] = value


def drop(key: str) -> None:
    """Forget `key` in this scope: the request just wrote to what it read."""
    cache = getattr(_local, 'cache', None)
    if cache is not None:
        cache.pop(key, None)


def mark(step: str) -> None:
    """End a step on this scope's timer; nothing outside a labelled scope."""
    timer = getattr(_local, 'timer', None)
    if timer is not None:
        timer.mark(step)
