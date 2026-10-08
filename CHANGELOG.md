# Changelog

A change that breaks a function's signature is a minor-version bump and names the host call sites to change.

## 0.5.4 - unreleased

Additive; no host call site changes. Both from the ledger project's first real run of 0.5.3 (ChuangFinance, 10/8/26 PT).

- `neon.cap_compute()`: a fresh free-plan endpoint sits at 1-1 CU, so `--cap-cu 0.25` was refused ("autoscaling limit
  min is larger than max"); when the caller set no floor, the refused PATCH is sent once more with the floor lowered to
  the ceiling. A floor the caller did set is never second-guessed.
- `neon.month_to_date()`: `/consumption_history` answers 403 to a personal API key; it then falls back to the project
  object's own period counters (`project_usage()`: the same four metrics for the current billing period) and tags the
  answer `source`; `--usage` says which it printed.

## 0.5.3 - 2026-10-08 PT

The same code as 0.5.2 with its suites green: 0.5.2 was tagged while `test-timecheck.py` failed on the
new usage suite's UTC fixture stamp (the push chain gated on `tail -1`, not pytest's exit code). Pin
0.5.3.

## 0.5.2 - 2026-10-08 PT

Additive; no host call site changes.

- `cache` (Snow White, from PickleBot's API; Gene, 10/8/26: the Neon near-miss safeguards in the kit for
  ChuangFinance's ledger): answers kept for a TTL (`get`/`put`/`cached`), dropped by a write (`clear()`,
  `clear(prefix)`), a per-caller `throttle(key, limit, window)` answering the seconds to wait, and `once(key,
  window)` for a warning said once a minute. Standard library; `clock` is the seam.
- `neon`, the metering half: `consumption(key, project_id, since, until)` sums the period's `*_bytes` and
  `*_seconds` metrics (the storage gauge's latest), `month_to_date()` from the first of the UTC month,
  `transfer_budget(totals, budget_bytes)` the month's transfer against a budget (the free plan's 5 GB by default,
  warned from 80%, 'not reported' when Neon gave no transfer metric), `endpoints()` and `cap_compute(key,
  project_id, max_cu)` the compute ceiling that bounds a paid plan's bill. CLI: `--usage [--budget-gb]` prints the
  month and provisions nothing; `--cap-cu` caps after a provision.
- `docs/metered-database.md`: the rules from the near-miss, each with its kit piece, and the ledger port's own.

- `neon` (ChuangFinance): a personal API key must name the organization on every project call - Neon answered 400 to
  the first real run (Gene, 10/8/26 2:2x PM PT) because `org_id` was never sent. `organizations(key)` reads
  `/users/me/organizations`; `organization_id(key)` is the one it finds (several -> RuntimeError naming them, so the
  caller passes `org_id`; none -> None, an organization key infers its own); `provision()` discovers it when not
  given and sends it on the list and the create. A non-auth HTTP error now carries Neon's own message (redacted)
  instead of a bare HTTPError.

## 0.5.1 - 2026-10-08 PT

Additive; no host call site changes.

- `neon` (Snow White, for ChuangFinance's ledger; Gene: "infra as code" before a console click): a Neon project as
  code over the REST API v2 — `provision(key, name, region_id=, pg_version=, database=, role=, org_id=, pooled=)`
  finds or creates the project (AWS Oregon `aws-us-west-2`, Postgres 18 by default), finds or creates the database
  on the default branch, reads the DIRECT connection URL (pooled on request), registers it with `redact` and answers
  ids, host and flags beside it; `ensure_project()`, `ensure_database()`, `connection_uri()`, `host_of()` on their
  own; `python -m chuang_platform_kit.neon --name X --database Y --key-secret p/NEON_API_KEY --store p/DATABASE_URL`
  provisions and stores the URL as a secret version, printing ids and host only. Idempotent: a second run creates
  nothing. `opener` is the seam.
- `secrets.ensure(project, name)`: the secret exists (created with automatic replication when missing), since
  `add_version()` answers 404 to a secret never created.

## 0.5.0 - 2026-10-08 PT

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
- `gmail_codes` (ChuangFinance, from its portal sign-ins): `wait_for_code(service, query, since_epoch, pattern, attempts=24, pause=5.0)` — the one-time code a
  portal emails during a sign-in, read from Gmail: polls newest-first for a mail matching `query` stamped after the
  sign-in started (a minute of grace), answers the pattern's first group or whole match, None after the attempts. The
  body is the text/plain part, else the HTML stripped (entities decoded, script/style dropped), else the snippet; `sleep`
  is injectable. Standard library only; the Gmail service is the host's. Lifted from ChuangFinance's four copies (SCE,
  State Farm, Monarch, the Jacobson portal).
- `clock` (ChuangFinance): `human_day(day)` -> `10/5/26`, `human_time(d, weekday=True, tz_word=False)` -> `Wed 10/7/26 7:00 AM PT`
  (a naive datetime is UTC), `day_of(stamp)` -> the kit-zone calendar day of a machine timestamp, `tz_label()` -> `PT`
  in either season (US zones; others keep their own name). Each takes `tz=`; `CHUANG_PLATFORM_TZ` stays the default.
  Lifted from ChuangFinance's `config.py` (`human_pt` / `human_day` / `pt_day`).
- `timecheck` (ChuangFinance): `scan(paths)` finds UTC days in disguise — `date.today()`, `datetime.today()`, a zoneless
  `datetime.now()`, `utcnow()`, a UTC midnight, `.date()` off a UTC instant, a fixed `timedelta(hours=7)` — and any UTC
  use on a line without `# utc-ok:` / `# local-ok:`; strings and comments are not code (tokenized). `python -m
  chuang_platform_kit.timecheck src tests` from a shell or CI, exit 1 on findings. From ChuangFinance's
  `tests/test_time_rules.py` and SMAD PickleBot's `tests/test-pacific-days.py`; the kit's own tree passes it (one
  `utc-ok:` added in `whatsapp_publisher`).

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
