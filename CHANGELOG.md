# Changelog

A change that breaks a function's signature is a minor-version bump and names the host call sites to change.

## 0.12.1 - 2026-10-31 PT

Additive; no host call site changes.

- `sentry_api.pt` writes its reading the way `clock.human_time` does, `10/8/26 12:57 AM PT`: a US zone's two-letter
  label in either season (PDT and PST are both PT, the label people write), any other zone's own name, the day and the
  hour unpadded; 0.12.0's `%Z` said PDT, which SMAD PickleBot's suite refused (its rule: labels say PT, logs say `%Z`).
- `secrets.TokenSession(token=, opener=)`: the session over the standard library and a bearer token, by default
  `google_auth.access_token` (a job's variable, a service-account key, the metadata server, gcloud's own sign-in), so a
  box with neither google-auth nor application-default credentials reads and writes a secret; `default_session()`
  falls back to it where google-auth is missing or finds no credentials. Lifted from SMAD PickleBot's
  picklebot_core/claude_accounts.py (#660); `python -m chuang_platform_kit.headless_claude run --project <p>` now
  reaches Secret Manager on a desktop with nothing else passed (Dragnet, 10/31/26 9:15 AM PT).
- `headless_claude.run` and `stream` look `subprocess.Popen` up at the call (`popen=None`), not at import, so a suite
  that replaces it reaches them; 0.11.2's own `main run` case had been starting the real CLI, and its IndexError is
  why the Tests run of 0.11.2 and of 0.12.0 was red. Both were tagged on that red run (the release chain's watch did
  not gate the tag; it does from this one); the code they ship is right, only the suite was.

## 0.12.0 - 2026-10-31 PT

Additive; no host call site changes (SMAD PickleBot's `picklebot_core/sentry_api.py` re-exports it under its old
names). Sentry's issues API for every project (SMAD PickleBot #569, the one-token rule: one user auth token, one client).

- `sentry_api.Client(org, projects=, token=, opener=, timeout=, zone=)`: `lookup(short_id or id)`, `resolve(short_id,
  commit, next_release=, session=, postmortem=)` with its note, `unresolved()` across the projects, `issues(project,
  query=)` following the Link header's pages, `event(issue_id)` raw and `latest_event(short_id)` trimmed for a
  triager, `search(query)`, `comment(short_id, text)`, `run(action, args)` for the named actions (issue, event, search,
  unresolved, comment, resolve); `Refused` (a SystemExit with the call, the status and Sentry's words, never the
  token; a 403 names the scope), `pt(stamp, zone)`, `next_page(link)`. Standard library only; the token is the
  host's, from wherever it keeps it.

## 0.11.2 - 2026-10-31 PT

- `python -m chuang_platform_kit.headless_claude run` falls back to the box's own CLI sign-in by default when no
  account has a token or all rest (0.11.1's command line never passed `login_fallback`, so a box with no tokens got
  exit 75 and the CLI never started: Dragnet, 10/31/26 9:10 AM PT); `--no-login-fallback` keeps the 75 for a service
  with no sign-in of its own.

## 0.11.1 - 2026-10-31 PT

- `headless_claude`: `tried` reads in account order whatever each account did (0.11.0 recorded a later account's "no
  token" before an earlier account's outcome; SMAD PickleBot's runner suite pins the order).

## 0.11.0 - 2026-10-31 PT

Additive; no host call site changes (SMAD PickleBot's claude-runner re-exports it under its old names). Headless Claude
for every project (SMAD PickleBot #660; Gene, 10/31/26: "Headless Claude should be in Chuang kit so we can do handoff
in one place and it's reusable across projects", after a runtime with one account's token stopped at that account's
spend limit while a second account had room).

- `headless_claude.ask(prompt, model=, system=, env=, resting=, names=, project=, zone=)`: the Claude Code CLI in print
  mode (JSON out, no tools, no settings, no MCP servers, no session kept) on the first account of `CLAUDE_ACCOUNTS`
  (or `names`) whose token is in the environment as `CLAUDE_CODE_OAUTH_TOKEN_<NAME>` or in Secret Manager when a
  project is named, and that is not resting; an answer naming a limit rests the account until the reset time it
  names (an epoch, or clock words in the zone the words name, else `zone`, else UTC; else an hour) and the same prompt
  goes to the next account at once. The subscription's token alone reaches the child; the API key never does.
- `headless_claude.run(args, ...)`: the CLI with the caller's own arguments (tools, a permission mode, stream-json, a
  prompt on stdin), stdout and stderr streamed through, judged for a limit at its end, so a scheduled run with tools
  fails over too; a plain failure is returned as the caller's; exit 75 when every account rests or has no token, or,
  with `login_fallback=True`, one more run on the box's own CLI sign-in (the account `login`). Every child starts with
  no console window on Windows.
- `headless_claude.Resting(store)` over any `get(name)` / `put(name, dict)` store, `FileStore(path)` for a box;
  `token_for`, `token_var`, `accounts`, `reset_at`, `judge`, `child_env`, `LIMITED`.
- `python -m chuang_platform_kit.headless_claude accounts | ask | run -- <claude args>` for a script in any language.

## 0.10.0 - 2026-10-29 PT

Additive; no host call site changes. Kept-alive HTTPS for service-to-service calls (SMAD PickleBot #505; Gene, 10/29/26:
"should research first if GCP or open source already has something like this but more robust and prod tested"): the
kit's one dependency, requests, already carries urllib3's pool, so this leans on it rather than a pool of our own.

- `keepalive.request(method, url, headers=, data=, json=, timeout=TIMEOUT_SECONDS, now=)`: a `requests.Response` from
  the process's one pooled session. urllib3 retries a failed connect for any method (`CONNECT_RETRIES`) and a read that
  never answered for idempotent methods only (`READ_RETRIES`), so a POST is never sent twice.
- `keepalive.session(now=)`: that session, closed and replaced after `IDLE_SECONDS` (60) unused, the guard a hand-rolled
  pool needed against connections Cloudflare or Google's front end closed while idle. `keepalive.reset()`.

## 0.9.0 - 2026-10-27 PT

Additive; no host call site changes (a host re-exports them under its old names). One Google token helper for every
project (SMAD PickleBot #413): its functions, jobs, runners and desktop had shared `ci_claim.access_token`.

- `google_auth.access_token(env=, opener=, run=, gcloud_timeout=, env_name='CI_ACCESS_TOKEN', scope=)`: an access token
  for Google's REST APIs with the standard library, or '' when no source answers, never raising: the named environment
  variable, a service-account key under GOOGLE_APPLICATION_CREDENTIALS (a path or the key's JSON; google-auth in-process
  when installed, else the openssl-signed JWT), the metadata server, then gcloud.
- `google_auth.key_file_token(path, opener=, run=, now=, scope=)`: that key's RS256 JWT exchanged at its token endpoint
  with the standard library and the openssl CLI, '' for anything else. `METADATA_TOKEN`, `CLOUD_PLATFORM`, `KEY_ENV`.

## 0.8.0 - 2026-10-26 PT

Additive; no host call site changes. Gene, 10/25/26 11:45 PM PT: "GH access seems to belong in kit" (a host had grown a
dozen GitHub clients and five tokens).

- `github` (new): one repository's REST calls with one token. `token()`, `request()` / `get()`, `issue()`,
  `create_issue()`, `comment()`, `compare()`, `create_release()` (an existing tag's release returned, so a retry adds
  nothing), `dispatch()`, `runners()` / `runner_online()`, `create_deployment()` / `deployment_status()`.
  `GitHubError(status, message)`; a 401 or 403 names the fine-grained permission the call needs. Standard library only.

## 0.7.1 - 2026-10-22 PT

Additive; no host call site changes. A release profile could not say which of the gate's checks took a push's 38 s.

- `push_gate`: each check says its seconds and how it ended (`tests 4.1 s (mark answered)`, `(ran)`, `(ran again)`,
  `(failed, exit 1)`, `(skipped, exit 2)`, `(mark carried)`), and each pushed commit ends with
  `checks 31.2 s, rebase check 1.0 s, base check 0.9 s`. `time.monotonic`, seconds only; `Gate(clock=...)` is the
  tests' seam.

## 0.7.0 - 2026-10-18 PT

Additive; no host call site changes. Gene, 10/18/26 11:34 PM PT, of SMAD PickleBot's pre-push gate: "can it be in
chuang-kit?"

- `push_gate` (new): git's pre-push gate over a repo's `.push-gate.toml` (`chuang-push-gate`, or
  `python -m chuang_platform_kit.push_gate`). For each commit pushed to the guarded branch: a head that does not
  descend from the remote's branch is refused first; each check the push touches runs, skipped when its mark answers
  (a command or a URL), a failure refusing the push; then the branch is fetched again, and if it moved during the
  checks the pushed commits are rebased onto it (a dirty checkout or a conflict refuses with nothing rewritten), the
  checks whose paths the rebase changed run again, the others' marks are carried, and the push is refused with
  "push again, the gate is satisfied". One line per decision; an override variable skips it. Standard library.

## 0.6.0 - 2026-10-16 PT

One bus for every host's Pub/Sub (Gene, 10/16/26: "build a better/generic one for all to use"). No call site has
to change: `whatsapp_publisher`'s functions keep their signatures and return the message id as before.

- `pubsub` (new): `publish()` over REST, `access_token()` (metadata server, then ADC, then gcloud, kept until a
  minute before expiry), `envelope()` for an event, `decode()` for a delivery, `PublishFailed`. Standard library.
- `whatsapp_publisher` publishes through `pubsub.publish`, with the same topic, body and attributes. It no longer
  imports `google-cloud-pubsub`, and a failure is `pubsub.PublishFailed`, not the library's `GoogleAPIError`.
- The `gcp` extra is empty: a host can drop `google-cloud-pubsub` unless it uses the library itself.

## 0.5.6 - 2026-10-12 PT

No host call site changes. The two items promised to ChuangFinance on 10/8/26 for the next kit change.

- `timecheck`: the "fixed Pacific offset" rule no longer flags every `timedelta(hours=7|8)`. It flags one only when it
  is a zone (`timezone(timedelta(hours=-8))`) or is added to or taken from a now or a UTC instant on the same line; a
  seven- or eight-hour duration on its own, such as ChuangFinance's `STALE_INGEST = timedelta(hours=8)`, passes.
- `whatsapp_publisher`: the envelope's `timestamp` comes from `datetime.now(timezone.utc)`, not the deprecated
  `utcnow()`, so it now carries its offset (`2026-10-02T16:00:00+00:00`). Nothing reads the field but logs; a host test
  that fakes the publisher's clock fakes `now(tz)` instead of `utcnow()`.

## 0.5.5 - 2026-10-11 PT

Additive; no host call site changes.

- `greenapi`: every call goes to the instance's own API host (`api_host()`: `https://<first four digits of the
  instance id>.api.greenapi.com`, or the `GREENAPI_API_URL` variable when set), never the shared
  `api.green-api.com`. From 6:30 PM PT on 10/10/26 the shared host timed out on every call for hours while the
  instance host answered in half a second and said "authorized" (SMAD PickleBot, measured from the desktop).

## 0.5.4 - 2026-10-08 PT

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
