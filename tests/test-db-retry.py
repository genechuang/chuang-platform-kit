#!/usr/bin/env python3
"""db: an idle pooled connection is remade, a read whose connection dropped is run once more, and the latency probe.

Lifted from SMAD PickleBot's services/api/db.py (2026-11-19 PT, kit 0.38.0, SMADPickleBot #1849) so a second service
on a serverless Postgres gets the same three guards. Pinned, positive case first:
  - idle_out(): a connection checked in `seconds` or more ago is refused at checkout with the disconnect error (the
    pool then opens a new one), `say` hearing 'idle_out'; a busier one is handed out; `seconds` may be a callable read
    at each checkout; a new connection says 'connect'; a `say` that raises fails nothing;
  - dropped(): a connection-level error whose words say the far end closed it, through `.orig` and `__cause__`;
    a query's own error and a loop of causes are not;
  - on_engine_error() marks a drop a disconnect and notes it on the request; retried_read() is true only then;
  - RetryDroppedReads: a GET answered 5xx after a drop is run once more and only the second answer is sent, with one
    WARNING; a 5xx with no drop, a 200 and a POST are passed on untouched; the rerun carries no mark;
  - probe(): the timings from the clock, the query count and the server line; the connection closed twice;
  - none of it imports SQLAlchemy at module import.

Run:  python -B tests/test-db-retry.py
"""
import asyncio
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import db  # noqa: E402

SQLALCHEMY_AT_IMPORT = 'sqlalchemy' in sys.modules

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


# ------------------------------------------------------------------ idle_out
class Gone(Exception):
    pass


class Record:
    def __init__(self):
        self.info = {}


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def hooked(seconds, say=None):
    hooks, clock = {}, Clock()
    db.idle_out('engine', seconds, say=say, listen=lambda eng, name, fn: hooks.__setitem__(name, fn), disconnect=Gone,
                clock=clock)
    return hooks, clock


def checkout(hooks, record):
    try:
        hooks['checkout'](None, record, None)
        return 'handed out'
    except Gone as e:
        return f'remade: {e}'


said = []
hooks, clock = hooked(240, said.append)
rec = Record()
hooks['checkin'](None, rec)
clock.t += 240
check("a connection idle the whole limit is remade at checkout, and the phase says so",
      (checkout(hooks, rec), said), ('remade: idle 240s or more: the endpoint may have suspended it', ['idle_out']))
check("the three pool events are hooked", sorted(hooks), ['checkin', 'checkout', 'connect'])
hooks['checkin'](None, rec)
clock.t += 239.9
check("a connection used within the limit is handed out, however old", checkout(hooks, rec), 'handed out')
check("  and a connection never checked in (a new one) is handed out", checkout(hooks, Record()), 'handed out')
hooks['connect'](None, rec)
check("a new connection says 'connect'", said, ['idle_out', 'connect'])

limit = [10]
hooks, clock = hooked(lambda: limit[0])
rec = Record()
hooks['checkin'](None, rec)
clock.t += 20
limit[0] = 30
check("a callable limit is read at each checkout", checkout(hooks, rec), 'handed out')
hooks['checkin'](None, rec)
clock.t += 31
check("  and remakes once its newer value is passed", checkout(hooks, rec).split(':')[0], 'remade')


def boom(name):
    raise RuntimeError('a phase note failed')


hooks, clock = hooked(1, boom)
rec = Record()
hooks['checkin'](None, rec)
clock.t += 5
check("a say() that raises fails nothing: the remake still happens", checkout(hooks, rec).split(':')[0], 'remade')
check("  nor a connect", hooks['connect'](None, rec), None)


# ------------------------------------------------------------------ dropped
class OperationalError(Exception):
    pass


class InterfaceError(Exception):
    pass


class Wrapped(Exception):
    def __init__(self, msg, orig=None):
        super().__init__(msg)
        self.orig = orig


eof = OperationalError('consuming input failed: SSL error: unexpected eof while reading')
check("the far end closing the connection is a drop", db.dropped(eof), True)
check("  through a wrapper's .orig", db.dropped(Wrapped('(psycopg.OperationalError) x', eof)), True)
caused = RuntimeError('a route failed')
caused.__cause__ = InterfaceError('connection is closed')
check("  and through __cause__", db.dropped(caused), True)
check("a query's own error is not", db.dropped(OperationalError('relation "x" does not exist')), False)
check("the right words on another kind of error are not", db.dropped(ValueError('connection reset')), False)
a, b = RuntimeError('a'), RuntimeError('b')
a.__cause__, b.__cause__ = b, a
check("a loop of causes ends", db.dropped(a), False)
check("no error at all", db.dropped(None), False)


# ------------------------------------------------------------------ the mark and the middleware
class Ctx:
    def __init__(self, exc):
        self.original_exception, self.is_disconnect = exc, False


check("outside a request nothing is marked and nothing is a retried read",
      (db.note_drop(), db.retried_read()), (None, False))
ctx = Ctx(OperationalError('relation "x" does not exist'))
db.on_engine_error(ctx)
check("a query's own error is not marked a disconnect", ctx.is_disconnect, False)
ctx = Ctx(eof)
db.on_engine_error(ctx)
check("a drop is marked a disconnect even outside a request", ctx.is_disconnect, True)

listened = []
db.note_drops('engine', listen=lambda eng, name, fn: listened.append((eng, name, fn)))
check("note_drops hooks the engine's handle_error", listened, [('engine', 'handle_error', db.on_engine_error)])


class Log:
    def __init__(self):
        self.lines = []

    def warning(self, fmt, *args):
        self.lines.append(fmt % args)


def run(method, answers, drop_on=()):
    """Run one request through the middleware over an app that answers `answers` in turn, dropping its connection on
    the attempts in `drop_on`; (what was sent, attempts, the WARNINGs, retried_read as each attempt saw it)."""
    sent, seen, log, calls = [], [], Log(), [0]

    async def app(scope, receive, send):
        n = calls[0]
        calls[0] += 1
        if n in drop_on:
            db.on_engine_error(Ctx(eof))
        seen.append(db.retried_read())
        await send({'type': 'http.response.start', 'status': answers[n]})
        await send({'type': 'http.response.body', 'body': str(answers[n]).encode()})

    async def send(message):
        sent.append(message.get('status', message.get('body')))

    asyncio.run(db.RetryDroppedReads(app, log)({'type': 'http', 'method': method, 'path': '/v1/x'}, None, send))
    return sent, calls[0], log.lines, seen


check("a GET answered 502 after its connection dropped is run once more, and only the second answer is sent",
      run('GET', [502, 200], drop_on=(0,)),
      ([200, b'200'], 2, ['db: GET /v1/x answered 502 after its connection dropped: read once more on a fresh one'],
       [True, False]))
check("a rerun that drops too is passed on as the 5xx it is",
      run('HEAD', [502, 503], drop_on=(0, 1))[:2], ([503, b'503'], 2))
check("  and the rerun is never a retried read's first attempt",
      run('GET', [502, 503], drop_on=(0, 1))[3], [True, False])
check("a 5xx with no drop is passed on untouched", run('GET', [500])[:3], ([500, b'500'], 1, []))
check("a 200 after a drop the route recovered from is passed on", run('GET', [200], drop_on=(0,))[:2], ([200, b'200'], 1))
check("a write is never replayed", run('POST', [502, 200], drop_on=(0,)), ([502, b'502'], 1, [], [False]))


async def other(scope, receive, send):
    await send('passed')

got = []


async def keep(message):
    got.append(message)

asyncio.run(db.RetryDroppedReads(other)({'type': 'lifespan'}, None, keep))
check("anything but an http request goes straight through", got, ['passed'])
check("after the requests nothing is marked", db.retried_read(), False)


# ------------------------------------------------------------------ probe
class Cursor:
    def __init__(self, conn):
        self.conn, self.last = conn, None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql):
        self.last = sql
        self.conn.ran.append(sql)

    def fetchone(self):
        return ['PostgreSQL 17.2 on x86_64-pc-linux-gnu'] if 'version' in self.last else [1]


class Conn:
    made = []

    def __init__(self):
        self.ran, self.closed = [], False
        Conn.made.append(self)

    def cursor(self):
        return Cursor(self)

    def close(self):
        self.closed = True


ticks = iter(range(0, 10_000))
got = db.probe(Conn, n=3, clock=lambda: next(ticks) / 1000)
check("probe: the connect, the queries and the reconnect, in milliseconds from the clock",
      got, {'n': 3, 'connect_ms': 1.0, 'reconnect_ms': 1.0,
            'query_ms': {'first': 1.0, 'p50': 1.0, 'p95': 1.0, 'min': 1.0, 'max': 1.0}, 'server': 'PostgreSQL 17.2'})
check("  three SELECT 1 and the version on the first connection, both closed",
      (Conn.made[0].ran, [c.closed for c in Conn.made]), (['SELECT 1'] * 3 + ['SELECT version()'], [True, True]))
check("percentile of nothing is None, of a list its nearest rank",
      (db._percentile([], 0.95), db._percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.95)), (None, 10))

check("the module imported without SQLAlchemy, and the fakes never loaded it",
      (SQLALCHEMY_AT_IMPORT, 'sqlalchemy' in sys.modules), (False, False))

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
