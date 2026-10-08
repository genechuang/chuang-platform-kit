"""In-process answers kept for a few seconds, a per-caller throttle, and a once-a-window gate: the reads a metered database must not repeat.

Lifted from SMAD PickleBot's `services/api/cache.py` (10/8/26, kit 0.5.2; Gene: make sure the safeguards
from the Neon near-miss are in the kit for ChuangFinance's ledger). On Mon 10/5/26 one stale browser tab
made over 100k reads in four hours, each a fresh trip to Neon, and took the free plan's 5 GB of monthly
network transfer to 4.95 GB by the next morning: a read is not free because the table is small, since the
same rows re-sent a hundred thousand times is gigabytes. So the answers many callers ask for again and again
are kept for a few seconds, a write drops them, and a caller asking more than any screen ever needs is told
to wait.

    from chuang_platform_kit import cache
    rows = cache.get('ledger:balances') or cache.put('ledger:balances', load(), ttl=30)
    cache.clear()                                   # after a write: the next read is fresh
    wait = cache.throttle(f'reads:{caller}', limit=30, window=60)
    if wait: raise TooManyRequests(retry_after=wait)
    if cache.once(f'warn:{caller}', 60): log.warning('%s is reading too much', caller)

Per process: a second instance's copy can be a TTL old after a write here, so the TTLs are seconds, never
minutes, for anything a write changes. Standard library only; `clock` is the test seam.
"""
import threading
import time
from collections import deque

_lock = threading.Lock()
_entries = {}      # key -> (expires_at, value)
_calls = {}        # throttle key -> deque of call times
_once = {}         # once() key -> when it may say yes again (never dropped by clear(): a write is not news here)
_MAX_KEYS = 5000   # idle callers' empty queues are not kept forever


def get(key, clock=time.monotonic):
    """The value kept under `key`, or None when there is none or it is stale."""
    with _lock:
        hit = _entries.get(key)
        if hit is None:
            return None
        if hit[0] <= clock():
            _entries.pop(key, None)
            return None
        return hit[1]


def put(key, value, ttl: float, clock=time.monotonic):
    """Keep `value` under `key` for `ttl` seconds; the value back, so
    `cache.get(k) or cache.put(k, load(), 30)` reads as one line."""
    with _lock:
        _entries[key] = (clock() + ttl, value)
    return value


def cached(key, ttl: float, load, clock=time.monotonic):
    """The kept value under `key`, or `load()` kept for `ttl` seconds. A falsy
    answer (an empty list) is kept too, which `get() or put()` would re-load."""
    with _lock:
        hit = _entries.get(key)
        if hit is not None and hit[0] > clock():
            return hit[1]
    value = load()
    with _lock:
        _entries[key] = (clock() + ttl, value)
    return value


def clear(prefix: str = None) -> None:
    """Drop every kept answer (or those whose key starts with `prefix`): a write
    happened, so the next read is fresh."""
    with _lock:
        if prefix is None:
            _entries.clear()
        else:
            for k in [k for k in _entries if str(k).startswith(prefix)]:
                del _entries[k]


def throttle(key, limit: int, window: float, clock=time.monotonic):
    """None when this call is within `limit` calls per `window` seconds for
    `key`, else the whole seconds to wait (at least 1) before the next. Count
    only the calls the cache could not answer, and give each caller (a session,
    a device, a job) its own key: on 10/6/26 one looping device locked its owner
    out everywhere because every call counted against one key."""
    now = clock()
    with _lock:
        seen = _calls.setdefault(key, deque())
        while seen and seen[0] <= now - window:
            seen.popleft()
        if len(seen) >= limit:
            return max(1, int(seen[0] + window - now + 0.999))
        seen.append(now)
        if len(_calls) > _MAX_KEYS:
            for k in [k for k, q in _calls.items() if not q]:
                del _calls[k]
        return None


def once(key, window: float, clock=time.monotonic) -> bool:
    """True the first time `key` is asked in each `window` seconds, False
    until then: a warning said once a minute however often it applies."""
    now = clock()
    with _lock:
        if _once.get(key, 0) > now:
            return False
        _once[key] = now + window
        if len(_once) > _MAX_KEYS:
            for k in [k for k, until in _once.items() if until <= now]:
                del _once[k]
        return True


def reset() -> None:
    """Forget everything (the tests' fresh start)."""
    with _lock:
        _entries.clear()
        _calls.clear()
        _once.clear()
