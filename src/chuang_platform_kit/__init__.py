"""chuang_platform_kit: the project-agnostic half of Gene Chuang's projects.

Extracted from SMAD PickleBot on 2026-10-04 PT (its ROADMAP, step 1) so that
ChuangFinance and the next project install one tested package instead of
copying files. Nothing in here knows about pickleball, a league or a sheet:

  redact             scrub tokens and keys out of text before it reaches a log line, an email or an issue
  cloud_logging      one JSON line per record with a severity, redacted, for Cloud Run / Cloud Functions
  lazy_import        import a heavy module on first use, not at cold start
  phone_utils        normalize a phone number the way WhatsApp ids spell it
  request_scope      a per-request cache that is dropped when the request ends
  whatsapp_message   the message shape every WhatsApp send is built from
  whatsapp_publisher publish a WhatsApp message to Pub/Sub with a deterministic correlation id
  pubsub             Pub/Sub without the client library: a REST publish, one token chain, an event envelope, a
                     delivery decoder
  greenapi           the GREEN-API transport: send, poll the instance state, retry a connect timeout
  email_notify       send mail through Gmail, redacted, with a throttle for repeated notices
  clock              the kit's time zone (CHUANG_PLATFORM_TZ, Pacific by default)
  monitoring         errors and traces to Sentry, scrubbed, a no-op without a DSN (the `monitoring` extra)
  config             a versioned configuration document read at runtime, cached, with a change signal, and an
                     OpenFeature provider over it (the `config` extra)
  db                 Postgres on a serverless host: a connection retried while the endpoint wakes, a pooled SQLAlchemy
                     engine over it (the `db` extra installs psycopg 3)
  secrets            Secret Manager: a JSON secret read into the environment, a new version written back; a token
                     session over the standard library for a box with no application-default credentials
  google_auth        a Gmail / Drive / Sheets / People service from a token in a file, the environment or Secret
                     Manager, refreshed and written back (the `gmail` extra); an access token for Google's REST
                     APIs with the standard library alone (a job's variable, a service-account key, the metadata
                     server, gcloud), '' when no source answers
  gitguardian        a repository's GitGuardian incidents listed with their occurrences, ignored or resolved with a note
  github             one repository's GitHub REST calls with one token: issues, compare, releases, dispatch, the
                     runner list, deployment records; a refusal names the permission the token lacks
  neon               a Neon project as code: found or created with its database, the direct connection URL read
                     and stored as a secret version, never printed; the month's metered consumption against a
                     budget, and the compute cap that bounds the bill
  cache              answers a metered database must not re-send: kept for seconds, dropped by a write, a
                     per-caller throttle, a once-a-window gate (docs/metered-database.md)
  keepalive          HTTPS calls on kept-alive connections: one pooled requests.Session a process, urllib3's
                     retry (connect errors any method, read errors idempotent methods only), replaced after a
                     minute idle, a timeout on every call
  push_gate          git's pre-push gate over a repo's .push-gate.toml: the stale-base refusal, each check skipped
                     on its mark, and a main that moved during the checks rebased onto for the second push
                     (`chuang-push-gate`)
  headless_claude    the Claude Code CLI in print mode on a subscription account's token, failing over between
                     accounts (CLAUDE_ACCOUNTS, CLAUDE_CODE_OAUTH_TOKEN_<NAME> or Secret Manager): `ask` one prompt
                     for the text, `run` the caller's own CLI arguments streamed through; a limited account rests
                     until the reset time its answer names (`python -m chuang_platform_kit.headless_claude`)
  sentry_api         Sentry's issues API, one client for every project: an issue by short id or id, its newest
                     event trimmed for a triager, the unresolved list, a search, a note, a resolve with its note,
                     the paged list a sweep walks, and the named actions a script or listener asks for; a refusal
                     is `Refused` with Sentry's words, never the token
  app_store_connect  App Store Connect's API, one client for every project: the ES256 bearer from a team key, one
                     call with Apple's own reason on a refusal, and an app's builds, App Store versions, internal
                     testers and their snapshot, the app id a parameter (the `asc` extra signs the bearer)
  anthropic_api      Anthropic's Messages API, one client for every project: one call with the host's headers and
                     extras, the text and the body back with the model that answered, the host's meter run on every
                     answer; a refusal names the status and Anthropic's words and says when the account cannot pay
  clerk_api          Clerk's two APIs, one module: the Backend API on an instance's secret key, the Platform API on an
                     account's token, and the session check from the publishable key (the `clerk` extra)
  gmail              one Gmail client per mailbox on its stored token: search threads, read a thread's senders and
                     subjects, the never-send-twice check, reply-all with an explicit `to`, send; a host's map names
                     each mailbox's token once

Every version is tagged; a host pins `chuang-platform-kit @ git+https://github.com/genechuang/chuang-platform-kit@v<tag>`,
or, in an image with no git, `chuang-platform-kit @ https://github.com/genechuang/chuang-platform-kit/archive/refs/tags/v<tag>.tar.gz`.
"""
__version__ = '0.17.0'
