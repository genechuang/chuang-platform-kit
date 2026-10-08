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
  greenapi           the GREEN-API transport: send, poll the instance state, retry a connect timeout
  email_notify       send mail through Gmail, redacted, with a throttle for repeated notices
  clock              the kit's time zone (CHUANG_PLATFORM_TZ, Pacific by default)
  monitoring         errors and traces to Sentry, scrubbed, a no-op without a DSN (the `monitoring` extra)
  config             a versioned configuration document read at runtime, cached, with a change signal, and an
                     OpenFeature provider over it (the `config` extra)
  db                 Postgres on a serverless host: a connection retried while the endpoint wakes, a pooled SQLAlchemy
                     engine over it (the `db` extra installs psycopg 3)
  secrets            Secret Manager: a JSON secret read into the environment, a new version written back
  google_auth        a Gmail / Drive / Sheets / People service from a token in a file, the environment or Secret
                     Manager, refreshed and written back (the `gmail` extra)
  gitguardian        a repository's GitGuardian incidents listed with their occurrences, ignored or resolved with a note
  neon               a Neon project as code: found or created with its database, the direct connection URL read
                     and stored as a secret version, never printed; the month's metered consumption against a
                     budget, and the compute cap that bounds the bill
  cache              answers a metered database must not re-send: kept for seconds, dropped by a write, a
                     per-caller throttle, a once-a-window gate (docs/metered-database.md)

Every version is tagged; a host pins `chuang-platform-kit @ git+https://github.com/genechuang/chuang-platform-kit@v<tag>`,
or, in an image with no git, `chuang-platform-kit @ https://github.com/genechuang/chuang-platform-kit/archive/refs/tags/v<tag>.tar.gz`.
"""
__version__ = '0.5.2'
