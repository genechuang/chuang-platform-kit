# chuang-platform-kit

The project-agnostic half of [Gene Chuang](https://github.com/genechuang)'s projects, as one tested, tagged
Python package. It was extracted from [SMAD PickleBot](https://github.com/genechuang/SMADPickleBot) on
2026-10-04 PT so that its sibling projects install one package instead of copying files.

| Module | What it does |
|---|---|
| `redact` | Scrub tokens and keys out of text before it reaches a log line, an email or an issue: URL-embedded tokens, token-shaped strings, and the literal value of every secret the process holds (`SECRET_ENV_NAMES`). `register_secret_values()` adds values no variable name flags, such as the contents of one JSON secret mounted as a single variable. |
| `cloud_logging` | `setup_logging()`: one JSON line per record with a `severity`, redacted, the shape Cloud Logging and Error Reporting read from Cloud Run and Cloud Functions. |
| `lazy_import` | `lazy('module')`: import a heavy library on first use, not at cold start. |
| `phone_utils` | Normalize a phone number the way WhatsApp ids spell it. |
| `request_scope` | A per-request cache (`cached`, `drop`, `scope()`): read a thing once per request, never across requests. |
| `whatsapp_message` | The message shape every WhatsApp send is built from. |
| `whatsapp_publisher` | Publish a WhatsApp message to a Pub/Sub topic with a deterministic correlation id per target and part, so a sender can dedupe retries and nothing goes out uncounted. |
| `pubsub` | Pub/Sub without the client library or gRPC: `publish(topic, data, project=, attributes=, ordering_key=)` is one REST POST (the regional endpoint with an ordering key), a 5xx or dropped connection tried once more; `access_token()` from the metadata server on Google Cloud, else Application Default Credentials, else `gcloud`, kept until it nears expiry; `envelope(type, data, source=, subject=, actor=, correlation_id=)` is an event's shape; `decode(delivery)` reads a push body or an Eventarc CloudEvent. Standard library. |
| `greenapi` | The GREEN-API transport: send, poll the instance state, retry a connect timeout, never log the URL (the token is in its path). |
| `email_notify` | Send mail through Gmail, redacted, with a throttle for repeated notices and dated subjects. `send_message(service, to, subject, text)` sends through a Gmail service the host built and returns the message id, for hosts that keep their own OAuth token and record delivery. |
| `gmail_codes` | `wait_for_code(service, query, since_epoch, pattern)`: the one-time code a portal emails during a sign-in, read from Gmail newest-first (plain text, else HTML stripped, else the snippet), a mail older than the sign-in skipped, None after the attempts. Standard library only; the Gmail service is the host's. |
| `clock` | The kit's time zone (`CHUANG_PLATFORM_TZ`, Pacific by default) and the ways a time is written or cut for a person: `human_day()` -> `10/5/26`, `human_time()` -> `Wed 10/7/26 7:00 AM PT`, `day_of(stamp)` -> the zone's calendar day of a machine timestamp, `tz_label()` -> `PT` in either season. |
| `timecheck` | `scan(paths)` / `python -m chuang_platform_kit.timecheck src tests`: finds UTC days in disguise (`date.today()`, a zoneless `now()`, `utcnow()`, a UTC midnight, a fixed offset) and any UTC use on a line without `# utc-ok:` / `# local-ok:`; strings and comments are not code. One line in a host's test suite keeps the rule. |
| `monitoring` | Errors and traces to Sentry behind our own functions: `init(dsn, environment, release, service)` (a no-op without a DSN), `capture()`, `span()`, `transaction()`, `flush()`. Every event and breadcrumb is scrubbed (`redact()`, emails, phone numbers), PII off. Install with the `monitoring` extra. |
| `config` | A versioned configuration document read at runtime from one source: `Config(fetch, defaults, fallback, ttl)` with `get('a.b')`, `typed()`, `version`, `mark_dirty()` (the change signal), `on_change()`; a failed read keeps the last document and waits before retrying; a fallback (the host's environment) answers before any document and disagrees at WARNING. `etag()` / `unchanged()` for a conditional GET. `provider(cfg)` is an OpenFeature provider over it (the `config` extra), so a flag service can replace the document later without touching call sites. |
| `db` | Postgres on a serverless host (Neon): `connect(url)` (psycopg 3, a connect timeout, an OperationalError retried while the endpoint wakes), `engine(url)` (a SQLAlchemy engine over a small pre-ping pool that connects through `connect()`), `sqlalchemy_url()`. The URL or `DATABASE_URL`, masked by `redact` with its password. Install with the `db` extra; SQLAlchemy is the host's. |
| `secrets` | Secret Manager over REST with google-auth's default credentials: `access()`, `load_json(project, name, into_env=)` (a JSON secret of logins into the environment), `ensure()` (the secret exists), `add_version()` (a token written back), `write_file()` (a secret as a 0600 file for a script that reads one), `renew_if_changed()`. `session` is the seam. |
| `neon` | A Neon project as code: `provision(key, name, region_id=, pg_version=, database=, pooled=)` finds or creates the project and its database and reads the direct connection URL (registered with `redact`; `host_of()` is the printable part); `python -m chuang_platform_kit.neon --name X --database Y --key-secret p/NEON_API_KEY --store p/DATABASE_URL` stores it as a secret version, printing ids and host only. Idempotent. The meter: `--usage` (`month_to_date()`, `transfer_budget()`: this month's transfer against the free plan's 5 GB, warned from 80%) and `--cap-cu 0.25` (`cap_compute()`, the ceiling that bounds a paid plan's bill). |
| `cache` | Answers a metered database must not re-send: `get`/`put`/`cached(key, ttl, load)` kept for seconds, `clear()` / `clear(prefix)` after a write, `throttle(key, limit, window)` the seconds a caller past its budget must wait (per caller, never global), `once(key, window)` a warning said once a minute. Standard library. The rules behind it: `docs/metered-database.md`. |
| `google_auth` | `service(api, version, scopes, token_file=, token_env=, secret=(project, name))`: a Gmail / Drive / Sheets / People service from the first token source that parses, refreshed, the refresh written back to the file and to Secret Manager; `credentials()` is the half without the client library. The `gmail` extra. `access_token(env=, opener=, run=, gcloud_timeout=, env_name=)`: an access token for Google's REST APIs with the standard library alone, '' when no source answers (the named variable, a service-account key under GOOGLE_APPLICATION_CREDENTIALS as a path or the key's JSON, the metadata server, gcloud); `key_file_token(path)` is the key's openssl-signed JWT grant. |
| `headless_claude` | The Claude Code CLI in print mode on a subscription account's long-lived token, failing over between accounts: `ask(prompt, model=, system=, resting=, names=, project=, zone=)` one prompt for the text (no tools, no settings, no MCP, no session kept); `run(args, ...)` the caller's own CLI arguments streamed through, judged for a limit at the end; the accounts from `CLAUDE_ACCOUNTS`, each token from `CLAUDE_CODE_OAUTH_TOKEN_<NAME>` or Secret Manager (`token_for`), a limited account resting (`Resting` over any get/put store, `FileStore` for a box) until the reset time its answer names (`reset_at`); `python -m chuang_platform_kit.headless_claude accounts | ask | run -- <args>`. |
| `keepalive` | HTTPS calls on kept-alive connections: `request(method, url, headers=, data=, json=, timeout=)` on one pooled `requests.Session` a process (`session()`), urllib3's retry (a failed connect for any method, a lost answer for idempotent methods only, so a POST is never sent twice), the session replaced after `IDLE_SECONDS` unused, a timeout on every call. |
| `github` | One repository's GitHub REST API with one token: `token()` (GH_TOKEN, GITHUB_TOKEN, then `gh auth token`), `request()` / `get()`, `issue()`, `create_issue()`, `comment()`, `compare()`, `create_release()` (an existing tag's release returned), `dispatch()`, `runners()` / `runner_online()`, `create_deployment()` / `deployment_status()`. A refusal is `GitHubError` with GitHub's message and, for a 401 or 403, the permission the token needs. |
| `gitguardian` | A repository's secret incidents: `open_incidents(key, repo)` with occurrences, `detector()`/`files()` (the safe parts), `ignore(key, id, reason, note)` and `resolve(key, id, note)` — those two need a key with `incidents:write` (a read-only key is a `PermissionError` naming the scope). |

| `push_gate` | git's pre-push gate over a repo's `.push-gate.toml` (`chuang-push-gate`, `python -m chuang_platform_kit.push_gate`): a stale base refused first, each check skipped on its mark, and a main that moved during the checks rebased onto for the second push. See [The push gate](#the-push-gate). Standard library. |

## Install

```
pip install "chuang-platform-kit @ git+https://github.com/genechuang/chuang-platform-kit@v0.2.0"
pip install "chuang-platform-kit[gcp,gmail] @ git+https://github.com/genechuang/chuang-platform-kit@v0.2.0"
```

A slim container image has no `git`, so `git+https` fails there; pin the tag's tarball instead (the repo is public):

```
chuang-platform-kit[gmail] @ https://github.com/genechuang/chuang-platform-kit/archive/refs/tags/v0.2.0.tar.gz
```

`gmail` adds the Google API client the email sender needs. `gcp` is empty since 0.6.0: Pub/Sub goes over REST
(`pubsub`), so the publisher needs no client library.
Hosts that already carry those libraries install the bare kit. On Windows the kit pulls `tzdata` for `clock`.

## Name the host

Nothing in the kit knows which project it serves. A host says so once, in its own config module,
before anything sends or logs:

```python
from chuang_platform_kit import email_notify, redact, whatsapp_publisher

whatsapp_publisher.configure(project_id='my-gcp-project', topic='whatsapp-messages')
email_notify.configure(subject_prefix='My Project', footer='The long name under every notification')
redact.register_secret_names('MY_ODDLY_NAMED_SECRET')   # names the TOKEN/KEY/SECRET/PASSWORD/CREDENTIALS shape misses
```

The same settings are read from the environment when a host prefers that: `GCS_PROJECT_ID` or `GCP_PROJECT_ID`
and `WHATSAPP_PUBSUB_TOPIC`, `EMAIL_SUBJECT_PREFIX` and `EMAIL_FOOTER`, `CHUANG_PLATFORM_TZ`.
`python -m chuang_platform_kit.redact` is the same scrub as a stdin filter, for a CI step that posts text.

## Claude Code hooks: the `gene-hooks` plugin

This repo is also a Claude Code plugin marketplace (`.claude-plugin/marketplace.json`) with one plugin,
`plugins/gene-hooks/`: the hooks every Claude Code session of Gene's runs. It is public so a cloud session can
install it before it has any GitHub access.

```bash
claude plugin marketplace add genechuang/chuang-platform-kit
claude plugin install gene-hooks@gene-hooks
```

or, committed in a project's `.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": { "gene-hooks": { "source": { "source": "github", "repo": "genechuang/chuang-platform-kit" } } },
  "enabledPlugins": { "gene-hooks@gene-hooks": true }
}
```

| Event | Hook | What it does |
|---|---|---|
| every hook | `boot.sh` | runs the named script from the newest installed version, so a release reaches open sessions at their next hook |
| UserPromptSubmit, PostToolUse | `pacific-clock.sh` | puts `Pacific clock: <day date time> PT` into context, so a time is copied, never estimated |
| PreToolUse | `stamp-gate.sh` | refuses a tool call when the line written before it does not open with the Pacific stamp, the date and the time (`10/17/26 5:21 AM PT`; the time alone is refused since 1.2.0); since 1.5.0 a miss is read again for up to a second before it is refused, because the newest line reaches the transcript after the call starts |
| PreToolUse (inside `stamp-gate.sh`) | `foreground-guard.sh` / `.py` | refuses a Bash or PowerShell call that is not `run_in_background` when it names a known long runner (ship, build, smoke, the full suites, a git push but to `dock`, run watches, Cloud Build, Terraform, EAS, an Expo export) or its timeout is two minutes or more: the session stays reachable (since 1.7.0) |
| SessionStart | `no-console-windows.sh` | on a Windows box with claude-shared beside the projects, runs its `no-console-windows.ps1` over the box's scheduled tasks and the session's repo; a launch that would pop up a console window is one line for Gene and listed in the session's context; nothing anywhere else (since 1.6.0) |
| Stop | `stamp-reply.sh` / `.py` | blocks a turn whose last reply does not open and close with the stamp, date and time on both lines |
| PreToolUse (inside `stamp-gate.sh`) | `plan-gate.py` | refuses a tool call while a task list (a `*update_plan` tool or TodoWrite) has a step open and 12 tool calls have gone by since it last changed; one awk on most calls, Python only past the count, the same stale run refused at most 3 times (since 1.11.0) |
| Stop (inside `stamp-reply.py`) | `evidence-gate.py` | blocks a reply that names a session (from the repo's `Session:` trailers) as a file's builder or owner with no `git log`/`blame`/`show` that turn, or states how production stands (live, shipped, deployed, booked, serving) having read nothing that turn (since 1.10.0) |
| SessionStart, UserPromptSubmit | `unpushed-commits.sh` | names sibling repos with unpushed commits and whose they are to push |
| SessionStart | `plugin-version.sh` | says when the install is behind a checkout of this repo beside the project |

Every hook exits 0 on anything unexpected, so a broken hook never traps a session. Their suites are
`tests/test-hooks-*.py`. The plugin has its own version (`plugins/gene-hooks/.claude-plugin/plugin.json`), bumped with
every change under `plugins/gene-hooks/`; it needs no package tag, since no host imports it.

## The push gate

`push_gate` runs a repo's checks in git's pre-push hook, so a push leaves the box tested. A repo turns it on with a
hook that calls it and a config at its root:

```sh
# .githooks/pre-push  (then: git config core.hooksPath .githooks)
#!/bin/sh
exec python -m chuang_platform_kit.push_gate "$@"
```

```toml
# .push-gate.toml
remote = "origin"
branch = "main"
override = "MY_PUSH_UNCHECKED"     # MY_PUSH_UNCHECKED=1 git push: no gate
gate = ["**"]                      # a push touching none of these goes unchecked (told which marks are missing)

[[check]]
name = "tests"
run = ["{python}", "-m", "pytest", "-q"]           # an argv; nonzero refuses the push
paths = ["src/**", "tests/**"]                     # what its result depends on; `!x/**` excludes
when = ["**"]                                      # what makes it run (default: its paths)
mark_url = "https://storage.example/marks/{tree}.json"   # a 200 means it already ran for this tree; or
# mark = ["{python}", "is_marked.py", "{sha}"]     # a command answering 0
carry = ["{python}", "carry.py", "{old}", "{new}"] # moves the mark across a rebase
missing = "has no test mark: CI runs the suites"   # said to a push outside `gate` without the mark
refusal = "fix the suite, then push"
skip_codes = [2]                                   # exits that mean "not applicable", not a failure
```

Placeholders: `{python}`, `{root}`, `{sha}`, `{tree}`, `{base}` (the remote's old sha), `{old}` / `{new}` (across
a rebase), `{remote}`, `{branch}`. Globs: `**` any folders, `*` within one, a trailing `/` the folder.
`paths_command` (an argv printing one glob a line) adds paths a script computes.

For each commit pushed to the branch, one line per decision:

1. The branch is fetched, and a head that does not descend from it is refused at once (git would refuse it after the
   checks anyway).
2. Each check whose `when` the push touches runs, unless its mark answers; a failure refuses the push. The checks
   test the checkout, so the pushed commit must be its HEAD (`require_head = false` turns that off).
3. The branch is fetched again. If it moved during the checks, the pushed commits are rebased onto it (a dirty
   checkout or a conflict refuses the push with nothing rewritten); the checks whose `paths` the rebase changed run
   again on the new head, the others' marks are carried; and the push is refused with "rebased onto <sha>; push
   again, the gate is satisfied", since git pushes the sha it gave the hook. The second push clears on the marks.

Every check it looks at says its seconds and how it ended (`push-gate: tests 4.1 s (mark answered)`; `ran`,
`ran again`, `failed, exit 1`, `skipped, exit 2`, `mark carried`), and each pushed commit ends with the split,
`push-gate: checks 31.2 s, rebase check 1.0 s, base check 0.9 s` (steps 2, 3 and 1), so a slow push says where its
time went. A check that hands its work elsewhere and exits 0 reads as `ran`.

A config that cannot be read refuses the push; a repo without one is not gated.

## Develop

```
pip install -e ".[gcp,gmail,dev]"
python -m pytest -n auto
```

The suites are hand-rolled scripts under `tests/` (one `PASS`/`FAIL` line per check, a final `N failure(s)`
line); `tests/conftest.py` runs each as one pytest item. A tag is cut only from a green `main`.

## License

MIT.
