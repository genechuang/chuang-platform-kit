#!/usr/bin/env python3
"""chuang_platform_kit.cache: answers kept for a TTL, dropped by a write, a per-caller throttle, a once-a-window gate.

Pinned (the positive case first): put() keeps a value get() answers until the TTL passes; cached() loads
once per TTL and keeps a falsy answer; clear() drops everything or a prefix; throttle() allows `limit` calls
per window, then answers the whole seconds to wait, per key; once() answers True once per window.

Run:  python -B tests/test-cache.py
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import cache as C  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


clock = Clock()
C.reset()

# 1. The positive case: kept for the TTL, gone after it.
check("put answers the value", C.put('k', [1, 2], 10, clock=clock), [1, 2])
check("get within the TTL", C.get('k', clock=clock), [1, 2])
clock.t += 10
check("get after the TTL: None", C.get('k', clock=clock), None)

# 2. cached(): one load per TTL, a falsy answer kept.
loads = []
got = [C.cached('rows', 30, lambda: loads.append(1) or [], clock=clock) for _ in range(3)]
check("cached loads once and keeps the empty list", (got, len(loads)), ([[], [], []], 1))
clock.t += 31
C.cached('rows', 30, lambda: loads.append(1) or [], clock=clock)
check("cached reloads after the TTL", len(loads), 2)

# 3. clear(): everything, or a prefix.
C.put('a:1', 1, 60, clock=clock); C.put('a:2', 2, 60, clock=clock); C.put('b:1', 3, 60, clock=clock)
C.clear('a:')
check("clear(prefix) drops only that prefix", (C.get('a:1', clock=clock), C.get('b:1', clock=clock)), (None, 3))
C.clear()
check("clear() drops everything", C.get('b:1', clock=clock), None)

# 4. throttle(): limit per window, per key, the wait in whole seconds.
allowed = [C.throttle('dev-1', 3, 60, clock=clock) for _ in range(3)]
wait = C.throttle('dev-1', 3, 60, clock=clock)
check("three allowed, the fourth told to wait the rest of the window", (allowed, wait), ([None, None, None], 60))
check("another key has its own budget", C.throttle('dev-2', 3, 60, clock=clock), None)
clock.t += 60
check("the window passed: allowed again", C.throttle('dev-1', 3, 60, clock=clock), None)

# 5. once(): True once per window.
check("once: True, then False within the window", (C.once('w', 60, clock=clock), C.once('w', 60, clock=clock)), (True, False))
clock.t += 60
check("once: True again after the window", C.once('w', 60, clock=clock), True)
C.put('x', 1, 60, clock=clock)
C.clear()
check("clear() does not reset once(): a write is not news for a warning gate", C.once('w', 60, clock=clock), False)

print(f"\n{len(fails)} failure(s)" + (': ' + '; '.join(fails) if fails else ''))
sys.exit(1 if fails else 0)
