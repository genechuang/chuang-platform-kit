"""Postgres on a serverless host (Neon): a connection with a cold-start retry, and a pooled engine.

A serverless Postgres endpoint sleeps when idle and resumes on the first connection, which can be
refused or time out while it wakes (Neon's free plan; measured from Cloud Run us-west1 to Neon
us-west-2 on 2026-10-04 PT: 268 ms to connect against 25 ms a query, so a service keeps a pool and
connects as rarely as it can). This module is the two lines every host needs:

    conn = db.connect()                     # psycopg 3, autocommit, DATABASE_URL, retried while the endpoint wakes
    eng = db.engine(application_name='x')   # a SQLAlchemy engine whose pool connects through connect()

`connect(url)` opens one psycopg 3 connection (the `db` extra installs psycopg) with a connect
timeout; a refusal that a sleeping endpoint explains (an OperationalError) is retried `retries`
times, `wait` seconds and doubling between tries, at WARNING, so a cold endpoint costs a second or
two and never a 500. `engine(url)` is a SQLAlchemy engine (SQLAlchemy is the host's to install; it
is imported on the call) over a small pool that pings a connection before reusing it, because the
far end drops idle ones; every pool connection is made by `connect()`, so the retry covers it too.
`sqlalchemy_url(url)` names the psycopg 3 driver in a plain `postgresql://` URL (SQLAlchemy's default
driver for that scheme is psycopg2).

The URL comes from `url` or, when None, the `DATABASE_URL` environment variable (`URL_ENV`), one of
`redact`'s secret names, and the URL `connect()` is given is registered as a secret value, so neither
the URL nor its password reaches a log line, an email or an issue (a driver's error quotes the
password on its own; `redact` masks it on its own). Nothing in this module names a project: the
application name, the pool size and the timeout are parameters.

Since 0.38.0, what a service needs once it is live: `idle_out(engine, seconds)` (a pooled connection idle that long
is remade at checkout, a busy one kept), `note_drops(engine)` with the ASGI middleware `RetryDroppedReads` (a GET or
HEAD whose 5xx followed the far end closing its connection is run once more; `retried_read` tells error reporting
the first attempt is not news) and `probe(connect)` (the connect and query latency measurement).
"""
import logging
import os
import time

from . import redact as _redact

log = logging.getLogger(__name__)

URL_ENV = 'DATABASE_URL'
CONNECT_TIMEOUT_SECONDS = 10
RETRIES = 2          # tries after the first: three connects in all, about three seconds of waiting
WAIT_SECONDS = 1.0   # before the second try; doubled for each later one
POOL_SIZE = 2        # a serverless free plan allows few connections; a service is one container
MAX_OVERFLOW = 3


class NoDatabaseUrl(RuntimeError):
    """No URL was given and the environment variable is empty."""


def url_from_env(url: str = None, env: dict = None) -> str:
    """`url`, or the `DATABASE_URL` variable (`URL_ENV`) of `env` (the process environment when
    None); raises NoDatabaseUrl when both are empty, so a host answers "not configured" instead
    of connecting to nothing."""
    value = url if url else (os.environ if env is None else env).get(URL_ENV, '')
    if not value:
        raise NoDatabaseUrl(f'{URL_ENV} is not set')
    return value


def sqlalchemy_url(url: str) -> str:
    """The URL with the psycopg 3 driver named: `postgresql://` and `postgres://` become
    `postgresql+psycopg://`; any other URL (a driver already named, sqlite://) is returned as it is."""
    for scheme in ('postgresql://', 'postgres://'):
        if url.startswith(scheme):
            return 'postgresql+psycopg://' + url[len(scheme):]
    return url


def _psycopg():
    import psycopg   # the `db` extra; imported on the call so the kit imports without it
    return psycopg


def _retryable(exc, psycopg=None) -> bool:
    """An error a waking endpoint explains: psycopg's OperationalError (refused, timed out, "the
    database system is starting up"), or any exception named that way from another driver."""
    if psycopg is not None and isinstance(exc, psycopg.OperationalError):
        return True
    return type(exc).__name__ == 'OperationalError'


def connect(url: str = None, *, timeout: float = CONNECT_TIMEOUT_SECONDS, application_name: str = None,
            autocommit: bool = True, retries: int = RETRIES, wait: float = WAIT_SECONDS,
            connector=None, sleep=time.sleep, **kwargs):
    """One psycopg 3 connection to `url` (or `DATABASE_URL`): `connect_timeout` set, autocommit by
    default (a probe's or a script's; an engine's pool passes False), `application_name` shown in the
    server's activity view when given. An OperationalError is retried `retries` times, `wait`
    seconds then double between tries, each at WARNING with the error (redacted); the last one is
    raised. `connector` (psycopg.connect by default) and `sleep` are the test seams."""
    url = url_from_env(url)
    _redact.register_secret_values(url)
    psycopg = None
    if connector is None:
        psycopg = _psycopg()
        connector = psycopg.connect
    if application_name:
        kwargs['application_name'] = application_name
    pause = wait
    for attempt in range(retries + 1):
        try:
            return connector(url, connect_timeout=timeout, autocommit=autocommit, **kwargs)
        except Exception as e:
            if attempt >= retries or not _retryable(e, psycopg):
                raise
            log.warning('database connect failed (try %d of %d, %s: %s); retrying in %.1fs',
                        attempt + 1, retries + 1, type(e).__name__, _redact.redact(str(e)), pause)
            sleep(pause)
            pause *= 2


def engine(url: str = None, *, application_name: str = None, pool_size: int = POOL_SIZE,
           max_overflow: int = MAX_OVERFLOW, timeout: float = CONNECT_TIMEOUT_SECONDS, pre_ping: bool = True,
           create_engine=None, **kwargs):
    """A SQLAlchemy engine over `url` (or `DATABASE_URL`): a pool of `pool_size` (+ `max_overflow`)
    connections, each made by `connect()` with the cold-start retry and `application_name`, pinged
    before reuse when `pre_ping` (the far end drops idle connections; a dead one would surface as a
    500 on a request). `kwargs` go to `create_engine`; the host keeps the engine it gets (one per
    process). `create_engine` is the test seam (sqlalchemy.create_engine by default, imported on
    the call: SQLAlchemy is the host's dependency)."""
    url = url_from_env(url)
    if create_engine is None:
        from sqlalchemy import create_engine
    return create_engine(sqlalchemy_url(url), pool_pre_ping=pre_ping, pool_size=pool_size, max_overflow=max_overflow,
                         creator=lambda: connect(url, timeout=timeout, application_name=application_name, autocommit=False),
                         **kwargs)


# ------------------------------------------------------------------ an idle connection is remade, a busy one kept
_IDLE_SINCE = 'idle_since'


def idle_out(engine, seconds, *, say=None, listen=None, disconnect=None, clock=time.monotonic) -> None:
    """Remake at checkout a pooled connection that sat in the pool `seconds` or more since its last use, and keep
    every other however old (0.38.0, out of SMAD PickleBot's services/api/db.py). A serverless endpoint suspends only
    when nobody has used it for a while and takes every connection with it, so a connection in use a minute ago is
    alive whatever its age: an age-based `pool_recycle` remakes busy connections mid-traffic, and `pool_pre_ping`
    costs a round trip on every request. Use it with `engine(pre_ping=False, pool_recycle=-1)`.

    `seconds` is a number or a zero-argument callable read at each checkout (a host that changes it at run time).
    `say(name)`, when given, is told 'idle_out' where a remake begins and 'connect' when a new connection comes up
    (a request's phase marks); what it raises is swallowed. `listen` (sqlalchemy.event.listen), `disconnect`
    (sqlalchemy.exc.DisconnectionError, which makes the pool open a new connection) and `clock` are the test seams."""
    if listen is None or disconnect is None:
        from sqlalchemy import event, exc
        listen = listen or event.listen
        disconnect = disconnect or exc.DisconnectionError

    def _say(name):
        if say is not None:
            try:
                say(name)
            except Exception:   # noqa: BLE001 - a phase note never fails a connection
                pass

    def _checked_in(dbapi_conn, record):
        record.info[_IDLE_SINCE] = clock()

    def _checked_out(dbapi_conn, record, proxy):
        since = record.info.pop(_IDLE_SINCE, None)
        limit = seconds() if callable(seconds) else seconds
        if since is not None and clock() - since >= limit:
            _say('idle_out')   # the remake starts here; 'connect' below is its end
            raise disconnect(f'idle {limit}s or more: the endpoint may have suspended it')

    def _connected(dbapi_conn, record):
        _say('connect')   # a new connection was just opened (the pool had none to give, or remade one)

    listen(engine, 'checkin', _checked_in)
    listen(engine, 'checkout', _checked_out)
    listen(engine, 'connect', _connected)


# ------------------------------------------------------------------ a dropped connection, read once more
# A pooled endpoint can close a connection under a statement ("consuming input failed: SSL error: unexpected eof
# while reading"); the same request seconds later is fine. A route that catches the error and answers its own 5xx
# cannot replay its read, so the engine notes the drop on the request and RetryDroppedReads runs a GET or HEAD whose
# 5xx followed one once more.
DROPPED_WORDS = ('unexpected eof', 'server closed the connection', 'consuming input failed', 'connection reset',
                 'terminating connection', 'connection is closed', 'ssl syscall error', 'connection not open')
_noted = __import__('contextvars').ContextVar('db_dropped', default=None)


def dropped(exc) -> bool:
    """Whether `exc` (or what it wraps) is the far end closing the connection under a statement, never a query's own
    fault: a connection-level OperationalError or InterfaceError whose message says so (DROPPED_WORDS)."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if type(exc).__name__ in ('OperationalError', 'InterfaceError') and any(
                w in str(exc).lower() for w in DROPPED_WORDS):
            return True
        exc = getattr(exc, 'orig', None) or exc.__cause__
    return False


def note_drops(engine, *, listen=None) -> None:
    """Hook the engine's handle_error: a dropped connection is a disconnect to SQLAlchemy (so that connection is
    thrown away, never handed out again) and is noted on the request for RetryDroppedReads. `listen` is the test
    seam (sqlalchemy.event.listen)."""
    if listen is None:
        from sqlalchemy import event
        listen = event.listen
    listen(engine, 'handle_error', on_engine_error)


def on_engine_error(ctx) -> None:
    """The handle_error listener (note_drops): a dropped connection is marked a disconnect and noted on the request."""
    if dropped(ctx.original_exception):
        ctx.is_disconnect = True
        note_drop()


def note_drop() -> None:
    """Mark the request in flight as one whose database connection dropped (a no-op outside a request)."""
    mark = _noted.get()
    if mark is not None:
        mark[0] = True


def retried_read(event=None, hint=None) -> bool:
    """Whether the request in flight is a read on its first attempt whose connection dropped, so RetryDroppedReads is
    about to run it once more: its error is not news yet. The predicate for `monitoring.drop_events_when`, so a read
    the rerun saves files no issue and no alert. False on the rerun (the mark is cleared for it), for a write, for a
    5xx with no drop and outside a request, so a read that fails twice is reported as before."""
    mark = _noted.get()
    return bool(mark is not None and mark[0])


class RetryDroppedReads:
    """Outermost ASGI middleware: a GET or HEAD answered 5xx after its database connection dropped is run once more,
    on a fresh connection, and its first answer is discarded; a write is never replayed, and a 5xx with no drop is
    passed on as it is. The mark is a list the request's context carries, so a sync route running in a threadpool
    (a copied context) still reaches it. `log`, when given, gets one WARNING per rerun."""

    def __init__(self, app, log=None):
        self.app = app
        self.log = log

    async def __call__(self, scope, receive, send):
        if scope.get('type') != 'http' or scope.get('method') not in ('GET', 'HEAD'):
            return await self.app(scope, receive, send)
        mark = [False]
        held = []
        token = _noted.set(mark)

        async def first(message):
            if message['type'] == 'http.response.start' and message.get('status', 200) >= 500 and mark[0]:
                held.append(message)
                return
            if held:
                return   # the rest of the discarded answer
            await send(message)

        try:
            await self.app(scope, receive, first)
        finally:
            _noted.reset(token)
        if held:
            if self.log:
                self.log.warning('db: %s %s answered %s after its connection dropped: read once more on a fresh one',
                                 scope.get('method'), scope.get('path'), held[0].get('status'))
            # The rerun carries no mark (the context variable was reset above), so nothing of it is held back from
            # error reporting (retried_read) and a drop on it is passed on as the 5xx it is.
            await self.app(scope, receive, send)


# ------------------------------------------------------------------ the latency probe
def _percentile(sorted_ms, q):
    if not sorted_ms:
        return None
    return sorted_ms[min(len(sorted_ms) - 1, int(round(q * (len(sorted_ms) - 1))))]


def probe(connect, n=20, clock=time.perf_counter):
    """Connect with `connect()`, run `n` `SELECT 1`s, reconnect; the timings as a dict: connect_ms, reconnect_ms,
    query_ms (first, p50, p95, min, max), the server's version line and the query count, every figure milliseconds
    rounded to a tenth. The first connect is the cold half, the queries the warm half, the second connect the
    endpoint awake. `clock` is the test seam."""
    import statistics
    t0 = clock()
    conn = connect()
    connect_ms = (clock() - t0) * 1000
    queries = []
    try:
        for _ in range(n):
            t = clock()
            with conn.cursor() as cur:
                cur.execute('SELECT 1')
                cur.fetchone()
            queries.append((clock() - t) * 1000)
        with conn.cursor() as cur:
            cur.execute('SELECT version()')
            server = (cur.fetchone() or [''])[0]
    finally:
        conn.close()
    t0 = clock()
    conn = connect()
    reconnect_ms = (clock() - t0) * 1000
    conn.close()
    ordered = sorted(queries)
    return {
        'n': n,
        'connect_ms': round(connect_ms, 1),
        'reconnect_ms': round(reconnect_ms, 1),
        'query_ms': {
            'first': round(queries[0], 1),
            'p50': round(statistics.median(queries), 1),
            'p95': round(_percentile(ordered, 0.95), 1),
            'min': round(ordered[0], 1),
            'max': round(ordered[-1], 1),
        },
        'server': str(server).split(' on ')[0][:80],
    }
