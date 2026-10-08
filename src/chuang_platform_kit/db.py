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
