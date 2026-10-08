# Changelog

A change that breaks a function's signature is a minor-version bump and names the host call sites to change.

## 0.5.0 - unreleased

Additive; no host call site changes. Gene's call, 10/8/26: PickleBot's working patterns move into the kit so
ChuangFinance can put its ledger and jobs in the cloud on the same ones. Each module is lifted by the session that
owns its source (SMAD PickleBot's `docs/config-audit.md` is the config half; this is the operations half).

- `db` (Mr Sandman, from PickleBot's API): Postgres on a serverless host (Neon) — `connect(url)` opens one psycopg 3 connection with a connect timeout and
  retries an OperationalError while a sleeping endpoint wakes (`retries`, `wait` doubling, each at WARNING, redacted);
  `engine(url)` is a SQLAlchemy engine (the host's dependency, imported on the call) over a small pre-ping pool whose
  every connection is made by `connect()`; `sqlalchemy_url(url)` names the psycopg 3 driver. The URL is the argument or
  `DATABASE_URL`; `NoDatabaseUrl` when neither. Lifted from SMAD PickleBot's `services/api/db.py`.
- `redact`: `DATABASE_URL` is a secret name, and a connection URL's password is masked on its own as well as inside the
  URL (a driver's error quotes it alone); `db.connect()` registers the URL it is given as a secret value.
- The `db` extra installs `psycopg[binary]`.
- `secrets` (Snow White, from PickleBot's daily job): `access(project, name)`, `load_json(project, name, into_env=)`,
  `add_version(project, name, data)`, `write_file(env, var, path)`, `renew_if_changed(project, name, before, path)`
  — Secret Manager over REST with google-auth's default credentials; `session` is the seam.
- `google_auth` (Snow White, from PickleBot's Gmail chain): `credentials(scopes, token_json=, token_file=, token_env=,
  secret=(project, name))` and `service(api, version, scopes, ...)` for Gmail, Drive, Sheets, People — the first
  token source that parses wins, an expired token is refreshed, and the refresh is written back to the file and to
  Secret Manager. `email_notify` reads its Gmail service through it.
- `gitguardian` (Snow White, from PickleBot's watchdog script): `incidents()`, `open_incidents()`, `with_occurrences()`,
  `detector()`, `files()`, and `ignore(key, id, reason, note)` / `resolve(key, id, note)` — the last two need a key
  with `incidents:write`, refused as PermissionError otherwise.

## 0.4.0 - 2026-10-08 PT

Additive; no host call site changes.

- `config`: a versioned configuration document read at runtime from one source — `Config(fetch, defaults=, fallback=,
  ttl=, retry_after=)` with `get('a.b')`, `typed()`, `snapshot()`, `version`, `mark_dirty()` (the change signal) and
  `on_change()`. `fetch(version)` answers a `Document`, `None` for unchanged, or raises; a failed read keeps the last
  document, marks `stale` and waits `retry_after`. A `fallback` (a host's environment) answers before any document and
  a disagreement with the document is a WARNING once per key. `etag()` and `unchanged()` for a conditional GET.
  `provider(cfg)` is an OpenFeature provider over it (SMAD PickleBot's `docs/config-audit.md` §6–7: environment
  holds wiring, the document holds the rules an operator changes at runtime; a flag service drops in later as another
  provider).
- The `config` extra installs `openfeature-sdk`; `Config` itself needs nothing.

## 0.3.0 - 2026-10-07 PT

Additive; no host call site changes.

- `monitoring`: errors and traces to Sentry behind the kit's own functions, `init(dsn, environment, release, service,
  traces_sample_rate)`, `capture(exc, **context)`, `span(name)`, `transaction(name)`, `flush()`. A no-op without a DSN.
  Every event, transaction and breadcrumb is scrubbed (`redact()`, email addresses, phone numbers and WhatsApp ids),
  `send_default_pii` off. The transport's loggers are quieted to ERROR, and hosts call `flush()` at the end of each
  request or message: on Cloud Run, Sentry's transport once logged about 636 SSL-retry warnings a day.
- The `monitoring` extra installs `sentry-sdk` (2.x).

## 0.2.0 - 2026-10-06 PT

Additive; no host call site changes.

- `email_notify.send_message(service, to, subject, text, *, html=None, sender=None)` -> Gmail message id. For a host that
  keeps its own OAuth token and records delivery (ChuangFinance's finance watch and collector): its Gmail service in, the id
  out, an API error raised. Subject and body pass `redact()`.
- `redact.register_secret_values(*values)`: register secret VALUES the name lists cannot see, such as the contents of one
  JSON secret that Cloud Run mounts as a single variable. A JSON credential is split into its secret fields.
- `__version__` in `__init__.py` had stayed at 0.1.0 through 0.1.1 and 0.1.2; it now matches `pyproject.toml`.
- README: pin the tag's tarball URL in an image with no `git`.

## 0.1.2

`email_notify.configure(footer=)` puts the host's own line under the notification footer.

## 0.1.1

`tzdata` on Windows, so `clock.zone()` resolves on a host with no OS zone database.

## 0.1.0

Extracted from SMAD PickleBot (2026-10-04 PT).
