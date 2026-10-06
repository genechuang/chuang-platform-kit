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
| `greenapi` | The GREEN-API transport: send, poll the instance state, retry a connect timeout, never log the URL (the token is in its path). |
| `email_notify` | Send mail through Gmail, redacted, with a throttle for repeated notices and dated subjects. `send_message(service, to, subject, text)` sends through a Gmail service the host built and returns the message id, for hosts that keep their own OAuth token and record delivery. |
| `clock` | The kit's time zone: `CHUANG_PLATFORM_TZ`, Pacific by default. |

## Install

```
pip install "chuang-platform-kit @ git+https://github.com/genechuang/chuang-platform-kit@v0.2.0"
pip install "chuang-platform-kit[gcp,gmail] @ git+https://github.com/genechuang/chuang-platform-kit@v0.2.0"
```

A slim container image has no `git`, so `git+https` fails there; pin the tag's tarball instead (the repo is public):

```
chuang-platform-kit[gmail] @ https://github.com/genechuang/chuang-platform-kit/archive/refs/tags/v0.2.0.tar.gz
```

`gcp` adds the Pub/Sub client the publisher needs; `gmail` adds the Google API client the email sender needs.
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

## Develop

```
pip install -e ".[gcp,gmail,dev]"
python -m pytest -n auto
```

The suites are hand-rolled scripts under `tests/` (one `PASS`/`FAIL` line per check, a final `N failure(s)`
line); `tests/conftest.py` runs each as one pytest item. A tag is cut only from a green `main`.

## License

MIT.
