#!/usr/bin/env python3
"""db: a Postgres connection with a cold-start retry, and a pooled SQLAlchemy engine over it.

Lifted from SMAD PickleBot's services/api/db.py (2026-10-08 PT, kit 0.5.0) so a second project puts its
database on Neon with the same two lines. Pinned, positive case first:
  - connect() opens one connection through the driver with the timeout, autocommit and the application name;
  - a waking endpoint's OperationalError is retried, the wait doubling, at WARNING with the error redacted; the
    last error is raised; any other error is raised at once;
  - the URL comes from the argument or DATABASE_URL, and neither set is NoDatabaseUrl;
  - sqlalchemy_url() names the psycopg 3 driver for postgresql:// and postgres:// and leaves others alone;
  - engine() builds the SQLAlchemy engine over a pool whose creator is connect() (autocommit off, the
    application name and timeout passed), pre-ping on, the pool sized, extra keywords through;
  - DATABASE_URL is a registered secret name, so its value never reaches a log line;
  - the kit imports without psycopg or SQLAlchemy installed (both are the host's).

Run:  python -B tests/test-db.py
"""
import logging
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import db, redact  # noqa: E402

DRIVERS_AT_IMPORT = {m: m in sys.modules for m in ('psycopg', 'sqlalchemy')}

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class OperationalError(Exception):
    pass


class Driver:
    """A fake psycopg.connect: refuses the first `refuse` calls with `error`, then answers a connection."""

    def __init__(self, refuse=0, error=OperationalError('connection refused')):
        self.calls, self.refuse, self.error = [], refuse, error

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        if len(self.calls) <= self.refuse:
            raise self.error
        return ('conn', url, kw)


class Log(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append((record.levelname, record.getMessage()))


# A fixture, never a database, assembled from parts: a literal `scheme://user:password@host` with a password this
# long is a credential to a secret scanner whatever the names say (two GitGuardian incidents on 10/8/26), so the
# shape never appears in the file.
PASSWORD = 'FAKE-s3cretpassw0rd'
URL = 'postgresql://' + 'FAKE-app:' + PASSWORD + '@' + 'db.example.test' + '/ledger?sslmode=require'
handler = Log()
db.log.addHandler(handler)
db.log.setLevel(logging.DEBUG)

print("\n-- connect(): one connection through the driver")
d = Driver()
conn = db.connect(URL, application_name='svc', connector=d, sleep=lambda s: None)
check("the positive case: connect() opens the connection with the timeout, autocommit and the application name",
      (conn[1], conn[2], len(d.calls)),
      (URL, {'connect_timeout': db.CONNECT_TIMEOUT_SECONDS, 'autocommit': True, 'application_name': 'svc'}, 1))
d = Driver()
db.connect(URL, timeout=3, autocommit=False, connector=d, sleep=lambda s: None, options='-c statement_timeout=5000')
check("timeout, autocommit and extra driver keywords pass through; no application name when none given",
      d.calls[0][1], {'connect_timeout': 3, 'autocommit': False, 'options': '-c statement_timeout=5000'})

print("\n-- the cold-start retry")
handler.lines.clear()
slept = []
d = Driver(refuse=2, error=OperationalError('connection to server failed: the database system is starting up; password ' + PASSWORD))
conn = db.connect(URL, connector=d, sleep=slept.append)
check("two refusals from a waking endpoint, then the connection: three driver calls, the wait doubling",
      (conn[0], len(d.calls), slept), ('conn', 3, [1.0, 2.0]))
check("each retry is one WARNING naming the try and the error",
      [(lvl, 'try 1 of 3' in msg, 'try 2 of 3' in msg, 'OperationalError' in msg) for lvl, msg in handler.lines],
      [('WARNING', True, False, True), ('WARNING', False, True, True)])
check("the password in the driver's error is masked in the log line", any(PASSWORD in msg for _, msg in handler.lines), False)
d = Driver(refuse=5)
try:
    db.connect(URL, retries=2, connector=d, sleep=slept.append)
    raised = None
except OperationalError as e:
    raised = str(e)
check("still refused after the retries: the last error is raised, retries + 1 driver calls", (raised, len(d.calls)), ('connection refused', 3))
d = Driver(refuse=1, error=ValueError('bad url'))
try:
    db.connect(URL, connector=d, sleep=slept.append)
    raised = None
except ValueError as e:
    raised = str(e)
check("an error that is not the driver's OperationalError is raised at once, no retry", (raised, len(d.calls)), ('bad url', 1))
d = Driver(refuse=1)
try:
    db.connect(URL, retries=0, connector=d, sleep=slept.append)
    raised = None
except OperationalError:
    raised = 'refused'
check("retries=0 is one try", (raised, len(d.calls)), ('refused', 1))
check("wait=0.5 starts the doubling there", (db.connect(URL, wait=0.5, connector=Driver(refuse=2), sleep=slept.append) is not None, slept[-2:]),
      (True, [0.5, 1.0]))

print("\n-- the URL: the argument or DATABASE_URL")
keep = os.environ.pop('DATABASE_URL', None)
try:
    try:
        db.connect(connector=Driver())
        raised = None
    except db.NoDatabaseUrl as e:
        raised = str(e)
    check("no URL and no DATABASE_URL: NoDatabaseUrl, before any driver call", raised, 'DATABASE_URL is not set')
    os.environ['DATABASE_URL'] = 'postgresql://env/db'
    d = Driver()
    db.connect(connector=d)
    check("DATABASE_URL is the URL when none is given", d.calls[0][0], 'postgresql://env/db')
    d = Driver()
    db.connect('postgresql://arg/db', connector=d)
    check("the argument wins over the variable", d.calls[0][0], 'postgresql://arg/db')
    check("url_from_env() reads a given mapping", (db.url_from_env(env={'DATABASE_URL': 'x'}), db.url_from_env('y', env={})), ('x', 'y'))
finally:
    os.environ.pop('DATABASE_URL', None)
    if keep is not None:
        os.environ['DATABASE_URL'] = keep

print("\n-- sqlalchemy_url()")
check("postgresql:// names the psycopg 3 driver", db.sqlalchemy_url('postgresql://u:p@h/d'), 'postgresql+psycopg://u:p@h/d')
check("postgres:// too", db.sqlalchemy_url('postgres://u:p@h/d?sslmode=require'), 'postgresql+psycopg://u:p@h/d?sslmode=require')
check("a driver already named, or another database, is left alone",
      [db.sqlalchemy_url(u) for u in ('postgresql+psycopg://h/d', 'postgresql+psycopg2://h/d', 'sqlite://')],
      ['postgresql+psycopg://h/d', 'postgresql+psycopg2://h/d', 'sqlite://'])

print("\n-- engine(): the SQLAlchemy engine over a pool that connects through connect()")
made = []


def fake_create_engine(url, **kw):
    made.append((url, kw))
    return 'ENGINE'


eng = db.engine(URL, application_name='svc', create_engine=fake_create_engine, echo=True)
url, kw = made[0]
check("the engine is made over the driver URL, pre-ping on, the pool sized, extra keywords through",
      (eng, url, kw['pool_pre_ping'], kw['pool_size'], kw['max_overflow'], kw['echo']),
      ('ENGINE', db.sqlalchemy_url(URL), True, db.POOL_SIZE, db.MAX_OVERFLOW, True))
creator = kw['creator']
check("the pool's creator is connect(): autocommit off, the application name and timeout passed", callable(creator), True)
d = Driver(refuse=1)
real_psycopg = sys.modules.get('psycopg')
sys.modules['psycopg'] = type(sys)('psycopg')
sys.modules['psycopg'].connect = d
sys.modules['psycopg'].OperationalError = OperationalError
try:
    kept_sleep = db.time.sleep
    db.time.sleep = slept.append
    try:
        conn = creator()
    finally:
        db.time.sleep = kept_sleep
finally:
    if real_psycopg is None:
        sys.modules.pop('psycopg', None)
    else:
        sys.modules['psycopg'] = real_psycopg
check("... through the driver (psycopg.connect) with the cold-start retry",
      (conn[0], len(d.calls), d.calls[-1][1]), ('conn', 2, {'connect_timeout': db.CONNECT_TIMEOUT_SECONDS, 'autocommit': False, 'application_name': 'svc'}))
made.clear()
db.engine(URL, pool_size=5, max_overflow=0, pre_ping=False, create_engine=fake_create_engine)
check("pool_size, max_overflow and pre_ping are parameters", (made[0][1]['pool_size'], made[0][1]['max_overflow'], made[0][1]['pool_pre_ping']), (5, 0, False))
keep = os.environ.pop('DATABASE_URL', None)
try:
    db.engine('', create_engine=fake_create_engine)
    raised = None
except db.NoDatabaseUrl:
    raised = 'no url'
finally:
    if keep is not None:
        os.environ['DATABASE_URL'] = keep
check("engine() with no URL anywhere is NoDatabaseUrl", raised, 'no url')

print("\n-- the secret name and the imports")
check("DATABASE_URL is a secret name: its value, and its password on its own, are masked by redact()",
      ('DATABASE_URL' in redact.secret_names({'DATABASE_URL': URL}), redact.redact('url is ' + URL, {'DATABASE_URL': URL}),
       redact.redact('password authentication failed for ' + PASSWORD, {'DATABASE_URL': URL})),
      (True, 'url is ***', 'password authentication failed for ***'))
check("a URL given to connect() is masked from then on, wherever it came from",
      redact.redact('dsn ' + URL + ' and ' + PASSWORD),
      'dsn *** and ***')
check("the module imported without psycopg or SQLAlchemy (both the host's extras)", DRIVERS_AT_IMPORT, {'psycopg': False, 'sqlalchemy': False})
src = open(os.path.join(ROOT, 'src', 'chuang_platform_kit', 'db.py'), encoding='utf-8').read()
check("psycopg and sqlalchemy are imported on the call, never at the top",
      (src.count('\nimport psycopg'), src.count('\nfrom sqlalchemy'), 'import psycopg' in src, 'from sqlalchemy import create_engine' in src),
      (0, 0, True, True))

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
