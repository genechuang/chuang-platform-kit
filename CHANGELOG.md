# Changelog

A change that breaks a function's signature is a minor-version bump and names the host call sites to change.

## 0.49.0 - 2026-11-21 PT

Additions to `app_store_connect`, no signature changes (SMADPickleBot #2151): `group_testers(app_id, bearer)` reads
every TestFlight group's testers once; `tester_groups(...)` is {`email_key(email)`: 'internal' | 'external'} (the
SHA-256 of the lowercased address, so a record holds none; internal wins for a tester in both); `internal_testers`
takes `rows=` to reuse the read. `snapshot` carries `testers` and each build's `internal` state beside `external`, so
a host can tell which build each group is testing. Suite: `tests/test-app-store-connect.py`.

## 0.48.0 - 2026-11-21 PT

New module `site_page` (SMADPickleBot #1852): a dashboard builder's fragment as a whole document for a host's site,
`standalone(html, site, path=, description=, title=, nav=)` with `Site(name, url, og_image=, og_image_alt=,
description=, icon=, touch_icon=)`, `head` and `page_title`; the host's nav bar is passed in as markup or a function
of the path. It is SMADPickleBot's `scripts/site_page.py` shell with the site's names taken out, so SMPDReport builds
/police without a SMADPickleBot checkout. Suite: `tests/test-site-page.py`.

Additions to `gmail`, no signature changes (SMADPickleBot #1852): `Mailbox.address()` (the profile's address),
`message_ids(query, limit=)` (every page of matching message ids), `attachments(message_id, suffix=)` (a message's
attachments listed: filename, type, id, size) and `attachment(message_id, attachment_id)` (its bytes), with the
module function `attachment_parts(payload, suffix=)`. Client-library transport only, like `search`. Suite:
`tests/test-gmail.py`.

## 0.47.0 - 2026-11-21 PT

Additions to `keepalive`, no signature changes (SMADPickleBot #1933): `request(retry=, redirects=)` and
`urlopen(retry=, redirects=)`. `retry=False` sends a call once on a second kept session whose adapter retries
nothing, so the caller's timeout bounds the whole call; `redirects=False` returns a 3xx as the answer. `urlopen`
passes only a flag that is off, so a host's stand-in for `request` keeps taking every default call. `reset()` closes
both sessions. Suite: `tests/test-keepalive.py`.

Additions to `firestore` and `pubsub`, no signature changes: `Client.claim(server_time=)` and `update(server_time=)`
set the named fields to Firestore's own commit time (REQUEST_TIME, the SDK's SERVER_TIMESTAMP), so a host's create-only
claim keeps its `claimed_at` on REST; `Client.release(say=)` hands the caller why a release failed. `pubsub.NoMessage`,
a ValueError, is what `decode` raises for a delivery with no message, so a caller can tell it from data that is not
base64 or UTF-8. Suites: `tests/test-firestore.py`, `tests/test-pubsub.py`.

## 0.46.0 - 2026-11-20 PT

Addition to `answer_cache`, no signature changes (SMADPickleBot #2109): `AnswerCache(asked_every=)`, how stale a
document's `asked` may be before a served read refreshes it (`ASKED_EVERY`, six hours, by default). A host that
rebuilds at a write only for readers seen in the last minutes sets its `keep_days` to those minutes and `asked_every`
below them. Suite: `tests/test-answer-cache.py`.

## 0.45.0 - 2026-11-20 PT

Addition to `google_rest`, no signature changes (SMADPickleBot #1939): `log_entries(project, filter, token, order=,
page_size=, limit=, **kw)` reads Cloud Logging's entries:list (a POST, so not `list_all`) every page, in order, up to
`limit`, through the one `call` (its guard 'none' by default, since it only reads). Suite: `tests/test-log-entries.py`.

Addition to `google_rest` (SMADPickleBot #1940): `set_service_min(project, region, service, count, token,
dry_run=, timeout=)` sets a Cloud Run Service's service-level minimum through the v1 API, `dryRun=all` for a dry run;
(was, changed), nothing written when it already holds `count`. Suite: `tests/test-google-rest.py`.

Addition to `slack` (SMADPickleBot #1940): `verify_request(headers, body, secret, now=, max_skew=)`, Slack's v0
signature and its five-minute replay window. Suite: `tests/test-slack.py`.

Addition to `github` (SMADPickleBot #1994): every call that passes the throttle is counted against the current
`request_scope` by resource, and `scope_calls()` returns the scope's tally, so a host logs a delivery's spend. Suite:
`tests/test-github.py`.

Addition to `answer_cache` (SMADPickleBot #2029): `AnswerCache(refused=)`, a test of a rebuild's exception; a
caller's own refusal drops the kept answer (`dropped` in the pass's report) instead of failing it every pass. No
signature changes. Suite: `tests/test-answer-cache.py`.

## 0.44.0 - 2026-11-20 PT

Additions to `gcs`, no signature changes (SMADPickleBot #2095): `Client.stat(bucket, name)` reads an object's
resource (`metadata`, `updated`, `contentType`, ...), None on a 404; `upload_bytes(..., metadata=)` sets custom object
metadata ({key: text}) in the same multipart request as `cache_control`. Suite: `tests/test-gcs.py`.

## 0.43.0 - 2026-11-20 PT

Additions to `neon`, no signature changes (SMADPickleBot #1938, out of its `picklebot_core/neon_branches.py`):
`request(method, path, key, body=, opener=, timeout=)` is a public call of Neon's API whose refusal is the new
`NeonError` ("Neon answered HTTP <code>: <Neon's words>", `status`, the key masked by value, the words redacted; no
answer "Neon did not answer (<kind>)"); `branches`, `create_branch` (idempotent: a branch of that name is returned
with `made` False; cut from the default branch unless given a parent) and `delete_branch` use it. `_call` and the
functions on it keep their own errors. Suite: `tests/test-neon-branches.py`.

## 0.42.0 - 2026-11-20 PT

New module, and two keywords added to `google_rest.call` with defaults that change no caller (SMADPickleBot #1937,
out of its `picklebot_core/db_backup.py` and `jobs/runner.py`). `gcs.Client(token=, opener=, timeout=, guard=)`:
Cloud Storage over its JSON API, standard library only: `list` (every page, {name, size}), `upload_bytes` and
`upload_file` (a media upload, or one multipart request that sets `cacheControl` with the bytes; the content type from
the file name when not given), `download` and `download_text`, `copy`, `delete` (False on a 404), `download_prefix`
and `upload_dir`. `google_rest.call(data=, content_type=, raw=)` sends bytes in place of a JSON body and returns the
answer's bytes, under the same live guard and error. Suite: `tests/test-gcs.py`.

## 0.41.0 - 2026-11-20 PT

New module, no signature changes: `link_preview`, a page's preview fetched safely, standard library only
(SMADPickleBot #1936, out of its `services/api/link_preview.py`). `check_url` (https on the standard port, no
credentials, every resolved address public; a malformed port is Refused too), `get` and the no-redirect `OPENER`,
`fetch` (redirects followed by hand with each hop checked, `max_redirects`, `max_bytes`, HTML alone; a charset Python
lacks reads as UTF-8), `parse` (og:*, twitter:*, <title>), `is_youtube` and `oembed`, and `preview`, all of it. The
host names itself in `agent`. Suite: `tests/test-link-preview.py`.

## 0.40.0 - 2026-11-20 PT

New module, no signature changes: `outbox`, the transactional outbox (SMADPickleBot #1935, out of its
`services/api/events.py`). An event written in its record's own transaction, published after the commit, swept when
a publish fails. Postgres over the host's SQLAlchemy model (`emit`, `drop`, `dispatch`, `sweep`, `send_rows`: emit
order kept, attempts counted, an ERROR at `stuck`, the sweep flag kept while rows are left) and Firestore over a
store the host adapts (`stage`, `fs_writes`, `publish_staged`, `sweep_fs`; the store has `pending(limit)` and
`mark_sent(ids, now)`). The kit keeps no state: the host passes its publish, attribute rule, failure class, topic
and its own `{'sweep': bool}` at each call. SQLAlchemy is the host's own pin, imported only by the Postgres half.
Suite: `tests/test-outbox.py`.

## 0.39.0 - 2026-11-20 PT

New module, no signature changes: `expo_push`, push notifications to phones through Expo's push service on the
standard library alone (SMADPickleBot #1934, out of its `services/api/push.py`). `token_ok` and `scrub` (a token
never leaves the host, and every error text returned is scrubbed), `message` (urgent's time-sensitive level and high
priority, an Android channel, a badge), `send(rows, build, post=)` in batches of 100 with tickets and the gone list
(never raising for Expo's answer or a network failure, always for a programming error), `receipts(ids, get=)` in
batches of 1000, `phone_gone` and `is_alarm` (one phone's token against the sender's credentials), and the dedupe
pieces `dedupe_key`, `dedupe_slots`, `MemoryDedupe`. `post` and `get_receipts` are the transports a test replaces.
Suite: `tests/test-expo-push.py`.

## 0.38.0 - 2026-11-19 PT

New in `db`, no signature changes (SMADPickleBot #1849, out of its `services/api/db.py`): what a service on a
serverless Postgres needs beyond a connection. `idle_out(engine, seconds)` remakes at checkout a pooled connection
that sat idle `seconds` since its last use and keeps a busy one however old (use with `engine(pre_ping=False,
pool_recycle=-1)`). `note_drops(engine)`, `dropped`, `on_engine_error`, `note_drop`, `retried_read` and the ASGI
middleware `RetryDroppedReads` run a GET or HEAD once more when its 5xx followed the far end closing its connection,
never a write; `retried_read` is the predicate for `monitoring.drop_events_when`. `probe(connect, n)` is the
connect, query and reconnect latency measurement. Suite: `tests/test-db-retry.py`.

## 0.37.0 - 2026-11-19 PT

New module, no signature changes: `answer_cache`, read answers kept in Firestore in front of a database that sleeps,
rebuilt at the write and pushed (SMADPickleBot #1708, out of its `services/api/fs_cache.py`). A cache that only drops
an answer at a write turns one write into a database start for every reader after it; this one builds each ended
answer again in a pass the host runs right after the write, while the database is awake, then tells the host which
reader's answer changed so it can bump a signal a view listens to. `AnswerCache` (`answer`, `register`, `bump`,
`rebuild`), `Bumps` (one request's version bumps), `version_of`, `SKIP`. Suite: `tests/test-answer-cache.py`.

## 0.36.1 - 2026-11-18 PT

0.36.0's `firestore.Client.name()` refused an id holding `/`; a host keys locks by dates (`name-10/06`), found by
its own suites before any deploy. A `/` is again stored as `%2F`, as every release before 0.36.0 did
(`firestore.stored_id`), and the URL now encodes that stored id, so `get()` and `delete()` reach the document `set()`
and a claim wrote: a lock on such a key can be released, which it could not before. No host should pin 0.36.0.

## 0.36.0 - 2026-11-18 PT

A fix that changes where a write lands for one kind of document id; no signature changes. `firestore.Client.name()`
returned the id percent-encoded, which is right in a URL and wrong in a request body, where Firestore takes the name
literally. So `set()`, `merge()`, `claim()`, the locks and `batch_get()` addressed `a%40b.com` while `get()` and
`delete()` addressed `a@b.com`: a set could not be read back, and a claim on such an id could not be released
(SMADPickleBot #1832, found as #1831: a bounce watch whose record every read missed). Now `name()` is the id as it
is and only the URL builder encodes it.

- Affected ids: any holding a character outside letters, digits and `-._~` (`@`, `:`, `+`, a space). Ids of plain
  characters are unchanged.
- **Host action:** a document already written by `set`/`merge`/a claim under an encoded name stays where it is and is
  no longer the one those calls address. Count them before the bump (a names-only read of each collection for ids
  holding `%XX`); SMADPickleBot had 13, none live.
- `name()` raises ValueError for an id holding `/`, which Firestore would read as a path.

Also additive, out of a host's own modules (SMADPickleBot #1729):

- `slack_post`: one Slack post as an event. `event_data()` is the event's one shape; `deliver(data, token, store,
  collection, resolve=)` is the token holder's answer: the key claimed once, the channel resolved by the host's
  `resolve`, the post made (a file with its blocks through `upload()`, else `post_message()`; `chat.update` for
  `update_of`), its ts kept for the threads and redraws that follow (`kept()`); `Retry` for what a redelivery may
  fix, an ERROR line for a refusal in `FINAL_ERRORS`. `form_answer()`, `json_answer()` and `share_ts()` are the
  never-raising Slack calls it leans on. Suite `tests/test-slack-post.py`.
- `github.label_ladder(tok, repo, number, wanted)`: every label on an issue in one request, then one at a time with a
  missing label made first; answers what did not land, never raises.

## 0.35.0 - 2026-11-17 PT

Additive; no host call site changes. `play_developer`: Google Play's Developer API (androidpublisher v3), one client
for every project, out of a host's own status module and the edit calls of its Android upload (SMADPickleBot #1680),
with its suite `tests/test-play-developer.py`. Standard library, over `google_rest.call` (guard 'none') and
`google_auth.impersonate`; no new dependency.

- The token: `publisher_token(account, source=, opener=, what=, error=)` mints an hour's access token for a keyless
  publisher service account through IAM Credentials, for `SCOPE` (androidpublisher), from `source`'s token
  (`google_auth.access_token` by default); `iam_token_url(account)` names the address it calls.
- One call: `call(method, url, token, body=, data=, content_type=, opener=, timeout=, error=)`, JSON through
  `google_rest.call`, a raw upload (`data`) sent as bytes; a refusal is `PlayError` (or the host's `error`) with
  Google's status and message, never a token. `api(package)` and `upload_api(package)` are the addresses.
- An edit: `open_edit`, `commit_edit`, `validate_edit`, `delete_edit` (best effort, never raises), `edit_tracks`,
  `edit_track`, `set_track(package, edit, track, releases, token)`, `upload_bundle` and `upload_mapping`.
- `tracks(package, token)`: every track, read in an edit that is always deleted after; an edit Play says was deleted
  under the read (`EDIT_DELETED`: another edit's commit) is opened again once.
- Play's answers, read (pure): `release(code, status, name=, notes=, language=)` (notes cut to `NOTES_MAX`),
  `version_codes(releases)`, `newest(releases)`, `fact(track, release)` ({track, version, codes, status, fraction})
  and `newest_on(listed, names)` (the first of several tracks that holds a release; status `NONE` when none does).

What a host passes in: its Android package name on every call, its publisher account's email (and its own word for
it in a refusal), and its token source. What stays the host's: which tracks it reads and in what order, every word a
person reads for a status (Play's API says nothing of Google's review), and any record it keeps of a review.

## 0.34.0 - 2026-11-17 PT

Additive; no host call site changes. Five small standard-library modules out of a host's own core (SMADPickleBot
#1679), each with its suite; the host keeps a thin module of its own names over each:

- `public_text`: emails and US-shaped phone numbers scrubbed from text bound for a public page, a group or a public
  ticket (`redact`), or listed for a check that refuses the text (`findings`); `EMAIL_RE`, `PHONE_RE`. Nothing to
  pass in. It is not `redact`, which scrubs tokens and keys.
- `signed_link`: `signature(key, value)` (HMAC-SHA256, its first 32 hex characters), `verify(key, value, sig)` in
  constant time and `signed_url(base, path, key, value)`. The host passes its key, its address and route, and
  normalizes the value it signs (an id's case) itself.
- `sentry_rules`: `is_alarm(issue, event, dev_hosts=())`, `is_performance`, `tags`: an alarm is error or fatal (a
  performance issue at any level), from production (an emulator or simulator run from any environment), not from a
  developer's machine. The host passes `dev_hosts`, the whole names of its own development hosts; the loopbacks are
  built in.
- `sentry_blame`: `blame(get, issue, event, org, repo)` and `lines(b)`, with `top_frame`, `release_sha`,
  `repo_path`, `session_of`, `code_link`, `suspects`, `released_by` and `commit_url`: the failing line on GitHub at
  the release, who shipped it and Sentry's suspect commits. The host passes `get` (its own Sentry read, so it holds
  the token), its Sentry organization's slug and the GitHub repository its commit links point at ('' for none: no
  commit links, and the release line is its short sha alone).
- `image_keep`: `consumers(project, region)` and `keep(project, region)`: every image Cloud Run runs (each serving
  or tagged revision, each job) keeps a `keep-<consumer>` tag in the project's Artifact Registry, a moved tag
  rewritten and a gone consumer's deleted, over `google_rest`. The host passes its project and region, and
  `retry_seconds` when the built-in waits before a refused revision read is asked again do not suit it.

## 0.33.0 - 2026-11-16 PT

Additive; no host call site changes. **A reaction is a WhatsApp message type** (SMADPickleBot #1671: a reaction made
in a host's app, mirrored onto the message in the WhatsApp group). `whatsapp_message.MessageType.REACTION` with the new
envelope field `reaction_to` (the WhatsApp id of the message reacted on; `content` is the emoji, `''` takes the
reaction off), `create_reaction` and `whatsapp_publisher.send_reaction`; `greenapi.send_reaction`,
`REACTION_METHOD` and `reaction_payload` over GREEN-API's `sendReaction`. WhatsApp keeps one reaction an account on a
message, so the instance's newest reaction replaces its last. A host's sender that reads the envelope with
`WhatsAppMessage.from_dict` must run this version before a publisher sends it the new field.

## 0.32.1 - 2026-11-16 PT

Additive; no host call site changes. `redact` cuts a Web Push endpoint to its host (SMADPickleBot #1592's review: a
browser's subscription arrives in a request body and its endpoint is the URL of an outbound request, so both could
reach a Sentry event or a log line whole, and an endpoint is a capability URL, RFC 8030 section 8.3). The pattern is
`web_push`'s allow-list: `https://fcm.googleapis.com/…`, `https://updates.push.services.mozilla.com/…`,
`https://<anything>.push.apple.com/…` and `https://<anything>.notify.windows.com/…` keep the host and lose the rest,
everywhere `redact()` runs (the log formatter, `monitoring`'s `before_send`, the email senders). Other addresses at
those companies are untouched.

## 0.32.0 - 2026-11-15 PT

Additive; no host call site changes. `web_push` (SMADPickleBot #1592: push notifications to a browser on the Web Push
standard directly, no vendor SDK): a subscription checked (`check_subscription`: the endpoint on the push services
browsers use, https, no credentials, the default port; `p256dh` a point on P-256; `auth` 16 octets), the server's VAPID
key (`new_private_key`, `public_key`), RFC 8291's aes128gcm encryption (`encrypt`, pinned to the RFC's own published
example byte for byte), the RFC 8292 Authorization (`vapid_header`) and `send`, which posts one message, follows no
redirect, takes no proxy from the environment, never raises for an answer or a network failure, and returns
`{status, ok, gone, retry_after, host, error}`: `gone` on a 404 or 410, never on a 403. New extra `webpush`
(cryptography); CI installs it. Reviewed before release by three independent lenses with a skeptic per finding; what
they found is fixed and pinned: the endpoint check reads an address exactly as the request library does (no tab,
newline, backslash, percent sign, empty port or `:0443` gets a second reading), `normal` stores one form of an
endpoint, the VAPID `aud` is the lower-case origin with no port, keys are checked again at `encrypt`, and `send`
refuses a malformed stored row in words. Also: chuang-sdlc's `__version__` now says 0.28.0, as its package does.

## 0.31.0 - 2026-11-11 PT

Additive; no host call site changes. `monitoring.drop_events_when(predicate)` (SMADPickleBot #1454: a read that lost
its database connection was run once more and answered 200, and Sentry still filed the first attempt's exception as an
issue and an alert): a host registers `predicate(event, hint) -> bool`, and `before_send` sends nothing for an error
event a predicate names. Transactions are never dropped; a predicate that raises drops nothing and says so in one
WARNING; the same function registered twice is one.

## 0.30.0 - 2026-11-07 PT

Additive; no host call site changes. `posthog_api` is PostHog's REST API, one client for every project (SMADPickleBot
#1132; Gene, 11/7/26 4:25 PM PT: "picklebot runtime should only use chuang-kit sdks"), out of SMADPickleBot's
`picklebot_core/posthog_api.py` as `sentry_api` came out in 0.12.0: `Client(project_id, key, host=)` with `events`,
`query` (HogQL), `insight`, `api` by path, `replay_url` and `person_url`, and `run(action, args)` for a script or a
listener; a refusal is `Refused`, a SystemExit naming the call, the status and PostHog's words, never the key; the key
may be a function the client calls at its first call. A fix in `sentry_api`: `Client._tok` called `.strip()` before
its `callable()` check, so a token passed as a function raised AttributeError; it is called first now.

## 0.29.1 - 2026-11-07 PT

A fix; no host call site changes. `secrets.writers` no longer reports the module's own command
(`python -m chuang_platform_kit.secrets delete ...`) as a Secret Manager write outside the kit (SMADPickleBot #1109).

## 0.29.0 - 2026-11-07 PT

Additive; no host call site changes. `secrets` is the one place a secret is written (SMADPickleBot #1109; Gene, 11/7/26:
"secrets write and management should be done in ONE place in chuang-kit"): `add_version` now destroys the versions it
replaced once the new one is in (`keep=1` by default, `keep=0` to skip; a refused destroy is a WARNING, never the
write's failure), since Secret Manager bills every version not destroyed and readers take `latest`; `versions`,
`prune` and `delete` beside it; `python -m chuang_platform_kit.secrets add|prune|delete PROJECT NAME` (the value on
stdin, never printed) for a script or a person; and `writers(root)`, the check a host's suite runs to refuse any other
Secret Manager write in its tree. A writer whose role adds versions but cannot destroy them (`secretVersionAdder`)
needs `roles/secretmanager.secretVersionManager` on that secret for the prune.

## 0.28.0 - 2026-11-07 PT

Additive; no host call site changes. `greenapi.post` (SMADPickleBot #1064): one POST to a GREEN-API method with the
Response as it came, the connect-timeout and status retries lifted out of `call_api`, which is now that call with the
JSON read and the ERROR on a refusal. For a caller that keeps its own status handling and pages once per lost message
(SMADPickleBot's WhatsApp sender, whose own copy of the connect-timeout loop goes).

## 0.27.0 - 2026-11-06 PT

Additive; no host call site changes. The graduations of SMADPickleBot's #971 audit, each replacing the project's own
copies in its phase 2:
- `firestore` (#1015): Firestore over REST with the standard library, `Client` and project-first wrappers: get, create,
  set, merge (masks, increment, append, remove), delete, commit, batch_get, run_query, the codec (`encode`, `decode`,
  `to_fields`, `from_fields`, `instant`), `claim` (create-only, `more_ids` all or none, a TTL), `release`, `lock` (a lease
  taken over only on the updateTime read). `Unreachable` and `Conflict`.
- `google_rest` (#1016): `call` with `GoogleError` and a live guard (`configure(allow_env=)` names the host's opt-in
  variable; a guarded write off Cloud Run on the real transport is `RefusedHere` before any token), `list_all`, and Cloud
  Tasks (`http_task`, `create_task`, `delete_task`, `list_tasks`), Cloud Scheduler (`http_job`, `create_job`,
  `delete_job`, `get_job`, `list_jobs`) and Cloud Run (`run_job`, `run_body`, `get_job_spec`, `get_service`,
  `list_executions`, `get_execution`, `execution_state`).
- `webhook_auth` (#1017): `google_push` / `google_push_claims` (google-auth, imported lazily) and `hmac_sha256` (prefix,
  hex or base64, constant-time, a non-ASCII signature refused, never a TypeError).
- `google_auth` (#1018): `metadata_token` (kept to two minutes before expiry), `impersonate`, `id_token` (the metadata
  server's, or generateIdToken as an account), `adc_token`, `authorized_session`, `forget`, `TokenFailed`.
- #1020: `gitguardian.incidents` asks for each state by its `status` query and `incident(key, id)` reads one by id;
  `greenapi.call_api(status_retries=)` asks again after a 429 or 5xx (off by default); `secrets.resolve_env` reads a
  Cloud Run container's env with its secret references, `secrets.access_url` the one address.

## 0.26.0 - 2026-11-05 PT

Removed: `push_gate` and the `chuang-push-gate` script (SMADPickleBot #1019, from #971's audit). git's pre-push gate is
claude-shared's `hooks/push_gate.py` alone; the kit's copy had the same logic, 14 lines of usage text apart. Every host
hook calls claude-shared's now: SMADPickleBot's since 10/22/26, ChuangFinance's (1d06431) and SMPDReport's (715270b)
since 11/5/26. No service imports it, so no host's pin needs to move for this release.

## 0.25.1 - 2026-11-05 PT

Fix; no host call site changes. `gmail_watch.changes` skips a message history names but Gmail answers 404 for (deleted
or moved before the read) and logs the count, instead of raising for the whole batch: one gone message had left the host
cursor unmoved and Pub/Sub redelivering the same window forever (ChuangFinance's financewatch, 10/5-10/6/26). Any other
failure still raises. `__version__` caught up (it still said 0.24.0 in 0.25.0). For ChuangFinance's move onto the
module (SMADPickleBot #999).

## 0.25.0 - 2026-11-05 PT

Additive; no host call site changes. `gmail_watch` (SMADPickleBot #969): Gmail's push as a webhook, one copy for every
host (SMAD's Venmo trigger, ChuangFinance's financewatch, SMAD's Play Console Watch each had their own): `watch`,
`renew_due` / `renew` (the stored cursor kept), `changes` over history.list with a catch-up search when Gmail answers
404 for a stale cursor, `push` / `is_gmail_push`; and labels and filters as code from a declared JSON file,
`plan` / `apply`, `python -m chuang_platform_kit.gmail_watch apply FILE --secret PROJECT:SECRET [--dry-run]`. REST with
the standard library on a `gmail.Mailbox`'s token; filters need gmail.settings.basic on that token.

## 0.24.0 - 2026-11-02 PT

Additive; no host call site changes. `greenapi` calls through one module-level kept-alive `requests.Session`
(`_session()`), its adapter's own retries off (`max_retries=0`), so a warm process reuses its TLS connection to the
instance host (SMADPickleBot #664: 362 ms off a warm send) and `call_api`'s `connect_retries` loop stays the only
retry budget. A host suite that stubbed `requests.post` / `get` for greenapi now stubs the session (`_SESSION`), or
hands it one that forwards to its stubs.

## 0.23.0 - 2026-11-02 PT

Additive; no host call site changes. `github.project_set_fields(tok, project_id, item_id, {field_id: value, ...})`:
several fields of one Project item in ONE GraphQL request, an aliased `updateProjectV2ItemFieldValue` (a single-select
option's id, or a ProjectV2FieldValue dict) or `clearProjectV2ItemFieldValue` (None) per field in one mutation
document (SMADPickleBot #817; Gene, 11/2/26: a feedback card placed in six calls, "the boards 6 GraphQL writes which
seems highly inefficient ... should belong in Chuang-kit Github kit and all should use so we don't reinvent a super
inefficient wheel"). The one-field verbs stay. A host that keeps its own copy of the Project mutations (SMAD's
`ticket_moves` did, beside 0.21.0's) replaces it with these.

## 0.22.0 - 2026-11-01 PT

Additive; no host call site changes. `slack`, Slack's one client for every project (SMAD PickleBot #683: "every
third-party API call goes through chuang-platform-kit's one client for that vendor"; two hand-written copies until now,
SMAD's `slack_app.api` and five `chat.postMessage` calls beside it, and ChuangFinance's webhook `send_slack`):
`post(token, channel, text, blocks=None, thread_ts=None, unfurl_links=False, unfurl_media=None, opener=None,
timeout=15)` over `chat.postMessage`, Slack's answer with the message's `ts` and `channel`; `update(token, channel, ts,
text, blocks=None)` over `chat.update`; `call(method, token, payload)` for any other Web API method as JSON and
`form(method, token, params)` for one that takes form fields; and `webhook_post(url, payload)` for an incoming webhook,
the second door a host keeps until it moves to a bot token. A refusal is `SlackError` with the HTTP `status` (200 for
Slack's `ok: false`, 0 when nothing answered) and `error` (Slack's string, else the failure's kind); the token and the
webhook URL are in no exception text, and nothing is printed or logged. Standard library; `opener=` is the seam.

## 0.21.0 - 2026-11-01 PT

Additive; no host call site changes. `github` gains the verbs a ticket tool needs so a production job runs no gh CLI
(SMAD PickleBot #685): `issues(tok, repo, state, labels, search, limit, comments)` lists or searches a repository's
issues over one GraphQL call a page, in the shape `gh issue list --json` gave (state OPEN/CLOSED, stateReason,
createdAt, labels [{name}], comments with their authors when asked); `add_labels`, `remove_label` (a 404 is False, never
an error), `create_label` (an existing name updated, gh's `--force`), `add_assignees`, `remove_assignees`, `edit_issue`,
`close_issue` (a comment first when given), `reopen_issue`, `add_sub_issue` and `edit_comment` over REST; and the
Projects v2 mutations `project_add_item`, `project_add_draft`, `project_set_field` (a single-select by option id or a
text), `project_clear_field` and `project_item_to_top`. `issue()` answers more keys beside the four it had (`id`,
`node_id`, `body`, `state_reason`, `html_url`, `created_at`, `pull_request`, `label_names`), and `create_issue()` the
new issue's `node_id`.

## 0.20.0 - 2026-11-01 PT

Two additions; no host call site changes. `google_auth.impersonated_token(path)` reads an
`impersonated_service_account` credential file through google-auth (the kind `gcloud auth application-default login
--impersonate-service-account` writes, and a container given one under GOOGLE_APPLICATION_CREDENTIALS), minted once and
refreshed at expiry, and `access_token()` takes it before the key, the metadata server and gcloud; only that type and
only the file given, never google-auth's own discovery. SMAD PickleBot #756 retires its own copy (#755's production fix).
And `email_notify.whatsapp_to_html` no longer turns a code block's own newlines, or the one right after it, into `<br>`:
a `<pre>` is a block, so each had been an empty line under every table in an email (SMAD PickleBot #765).

## 0.19.1 - 2026-10-31 PT

A fix; no host call site changes. `github.cli(..., check=True)` raises CalledProcessError on a failed run, a missing
gh's 127 included: from 0.17.1 a caller's `check=True` reached the runner, whose CalledProcessError cli then caught as
"gh missing" and returned as 127, so the failure went unraised. And a binary run's (`text=False`) bytes stderr is read
as UTF-8 for the rate-limit words instead of raising TypeError. SMAD PickleBot #364's last raw `gh` runs (the alert
sweeps' `gh issue` calls, all `check=True`) move onto cli with it.

## 0.19.0 - 2026-10-31 PT

Additive; no host call site changes. `gmail.Mailbox` gains the REST transport (`rest=True`, an `opener` given, or no
google-api-python-client installed): `send` and `reply_all` over users.messages.send with the standard library alone,
the access token refreshed through `google_auth.refresh_access_token` and kept on the mailbox until a minute before it
expires; `search` and `thread` on a REST mailbox are NoMailbox; a refused send is `gmail.SendFailed` with Google's code
or the error's kind, never a token. `google_auth.token_info(**sources)` reads the stored authorized-user token itself
(the same source order, no google-auth), `refresh_access_token(info, opener=None, timeout=15)` trades it for (access
token, seconds), `RefreshFailed` on a refusal; `credentials()` and `service()` take `service_account_file=` for a
service account's key (a sheet shared to that account). SMAD PickleBot's API mailer and watchdog job (#661) carried
the refresh and the send themselves because their images have no Google client library; ChuangFinance's ledger sheets
read on a service account (ChuangFinance#2).

## 0.18.1 - 2026-10-31 PT

Additive; no host call site changes. `anthropic_api.admin_get(path, admin_key, params=None, timeout=30, http=None,
user_agent=)`: one Admin API GET (an organization's usage and cost reports) under the admin key with the version
header, answered in requests.Response's shape; `http` is requests, a fake with .get, or None for the standard library.
SMAD PickleBot's cost script (#573) read the Admin API with its own call until now.

## 0.18.0 - 2026-10-31 PT

Additive; no host call site changes. Two Sentry calls SMAD PickleBot's #569 audit found still written by hand needed
shapes `sentry_api.Client.call` has not:

- `Client.request(method, path, body=None, tok='', full_url='', timeout=None)`: (status, parsed JSON or text), never
  raising, for a host that reads the status itself (a release's deploy marker takes a 2xx and retries anything else);
  a refusal is its status and Sentry's text, no answer or no token 0 and the reason.
- `Client.get_bytes(path, tok='', full_url='', timeout=None)`: a raw GET's bytes (an attachment's download), Refused
  as `call` raises it.

## 0.17.1 - 2026-10-31 PT

Additive; no host call site changes. Two things SMAD PickleBot's #364 migration met:

- `github.request`: a refusal whose body carries no `message` takes the HTTP reason phrase ("Not Found") as its message,
  so a `GitHubError` always reads as something.
- `github.cli`: a caller's own `capture_output`, `text`, `encoding`, `errors` or `timeout` keywords are taken over the
  defaults instead of raising "multiple values", so a `subprocess.run(['gh', ...], capture_output=True, text=True)` line
  moves over as `github.cli([...], capture_output=True, text=True)`.

## 0.17.0 - 2026-10-31 PT

Additive; no host call site changes. **GitHub calls are throttled** (SMAD PickleBot #679; Gene, 10/31/26 12:29 PM PT:
"at least the 2nd time broke github GraphQL quota by runaway queries ... add a throttle mechanism so we don't have this
problem again": a board watch spent an hour's 5,000 GraphQL points in a minute of reads on 10/27/26, and a retried
event searched issues 300 times a minute into the search quota's 403 on 10/31/26).

- `github.request` (and so every verb) keeps GitHub's own count of the token per rate-limit resource (core, search,
  graphql, code_search) from every answer's `X-RateLimit-*` headers, and refuses a call locally, with no network call,
  once the resource is under its `FLOOR` (core 100, search 3, graphql 250, code_search 2), spent (a 403 or 429 with
  remaining 0, a `Retry-After`, or a message naming a rate limit; held `SPENT_FOR` 60 s when no reset can be read) or
  over this process's `LOCAL_CAP` a minute (core 120, search 10, graphql 60, code_search 5). The refusal is
  `RateLimited`, a `GitHubError` with status 429, `resource`, `remaining` and `reset_at`; nothing is retried.
  `limits()` reads the count, `wait_until(resource, cap=120)` sleeps to the reset for a script that can, `reset_limits()`
  is the suite's seam, and `resource_of(path)` names a path's resource.
- `github.graphql(tok, query, variables=None)`: one GraphQL call's `data` through the same throttle; `GitHubError` with
  the first error's message, `RateLimited` when that message names the rate limit.
- `github.cli(args, run=subprocess.run, timeout=60)`: `gh <args>` through the same local cap, for a script or daemon on
  the CLI (gh hides GitHub's headers); a failed run whose stderr names a rate limit spends the resource; a missing gh is
  returncode 127.

## 0.16.0 - 2026-10-31 PT

Additive; no host call site changes (a host that passes its own opener or fetch keeps it). Kept-alive connections for
the kit's repeat callers (SMAD PickleBot #562, phase 2 of #505), each measured first from the desktop, a fresh
connection per call against a warm kept-alive one: GitHub 159 ms saved a call, Pub/Sub 34 ms (GREEN-API 362 ms, Firestore
157 ms and SMAD's API 31 ms, the hosts' own callers).

- `keepalive.urlopen(req, timeout=)`: urllib.request.urlopen's shape over the pooled session, lifted from SMAD's
  api_client GET adapter (#500) and widened to every method: the request's method, headers and body sent as they are,
  the answer read whole (`Answer`: status, headers, read(), a context manager), a 4xx or 5xx raised as HTTPError with
  its body, no answer as URLError; plain http (the metadata server) goes to urllib's own. A POST is never re-sent once
  it went out (the pool's retry rule).
- `github.request` and `pubsub.publish` default to it when no opener / fetch is given.
- GREEN-API is not moved here: its own connect-retry loop would stack on the pool's (three tries each), about 90 s on
  an outage against the sender's timeout, so it moves in its own release with one retry budget.
- tests/test-keepalive.py: urlopen's method, headers, body, answer, HTTPError, URLError and plain http.

## 0.15.1 - 2026-10-31 PT

- `gmail.Mailbox` and `mailbox()` use the token's own granted scopes by default (`scopes=None`); 0.15.0 asked every
  refresh for gmail.readonly and gmail.send, which Google refuses for a token granted gmail.modify or read alone, so
  a mailbox would have been NoMailbox. A caller names scopes only to narrow them.
- `google_auth.credentials` and `service` take `scopes=None` for the same: the token's own scopes.

## 0.15.0 - 2026-10-31 PT

Additive; no host call site changes. Clerk and Gmail for every project (SMAD PickleBot #571 and #661), one release.

- `clerk_api`: Clerk has two APIs behind two credentials that neither takes the other's, so the consolidation is one
  module with both. `Backend(secret_key, opener=, timeout=, user_agent=)`: `call(path, method=, body=, query=)`,
  `pages()`, and the named calls (`user`, `users`, `session`, `sessions`, `revoke_session`, `sign_in_token`, `invite`,
  `revoke_invitation`, `add_email`, `update_email`, `delete_email`, `delete_external_account`, `ban`, `unban`,
  `delete_user`). `Platform(token, ...)`: `call`, `applications`, `application(include_secret_keys=)`, `config(keys=,
  schema=)`, `patch_config(if_match=, dry_run=)`. `Refused` (a RuntimeError) reads `<METHOD> <address> answered
  <status>: <Clerk's long_message>` or `... did not answer (<error>)`, with `status`, `detail` and `kind`, never a key.
  `verify_session(authorization, publishable_key, test_publishable_key=)` → (claims, signed by the second instance),
  the JWKS clients kept per address (`jwk_client`), `Unauthorized` / `NotConfigured`; `frontend_api`, `issuer`,
  `jwks_url`. The `clerk` extra (PyJWT with cryptography) is for the session check alone.
- `gmail`: `Mailbox(scopes=, service=, **sources)` on the kit's `google_auth` token readers: `search(query)`,
  `thread(id)` (headers only, oldest first), `has_message_from(thread, address)`, `reply_all(message, text=, html=,
  to=, cc=)` (Re: once, In-Reply-To, References, in the thread), `send(...)`; `mailbox(name, mailboxes, project)` by
  the host's map of mailbox to token name, the environment first and Secret Manager second; `NoMailbox` without a
  usable token. Built for a headless run on a setup token, which carries no claude.ai connector (Dragnet, 10/31/26).
- tests/test-clerk-api.py, tests/test-gmail.py; the Tests workflow installs the `clerk` extra.

## 0.14.0 - 2026-10-31 PT

Additive; no host call site changes. Anthropic's Messages API for every project (SMAD PickleBot #573: three callers,
one client), lifted from its Post Game story's and /pb reader's own `requests.post` calls.

- `anthropic_api.messages(model, messages, system=, max_tokens=, api_key=, timeout=, headers=, extra=, usage=, post=,
  env=)` and `ask(prompt, model, ...)`: {'text', 'body', 'stop_reason', 'model'}, the model the body names (a
  server-side fallback names what ran); `headers` added to the request's (a beta header), `extra` to the payload
  (`output_config`, `fallbacks`); `usage(body, model)` the host's meter, run on every 200 and never allowed to fail the
  call; the key `api_key` else ANTHROPIC_API_KEY, none Refused before any call. `Refused` (a RuntimeError) carries
  `status`, `detail` (Anthropic's own message, never the key) and `out_of_credits` (400/402/403 with the billing
  words, `out_of_credits(status, text)`). The transport is `post(url, headers=, json=, timeout=)` in requests.post's
  shape: `urllib_post` by default (an HTTP error an `Answer` with its status and body), `requests.post` or a kept-alive
  session's where the host has one, a suite's fake.
- tests/test-anthropic-api.py.

## 0.13.0 - 2026-10-31 PT

Additive; no host call site changes (SMAD PickleBot's `picklebot_core/app_store.py` binds its app to it under its old
names). App Store Connect's API for every project (SMAD PickleBot #570, the one-key rule: the App Manager key, one
client), lifted from its `picklebot_core/app_store.py` with the app id made a parameter.

- `app_store_connect`: `token(key, key_id, issuer_id, now=, seconds=)` the ES256 bearer (PyJWT with cryptography, the
  new `asc` extra); `call(method, path, bearer, body=, query=, opener=)` and `get()` with `Refused` (a RuntimeError:
  the call, the status and Apple's own reason, or the error's name when nothing answers; never the key);
  `builds(app_id, bearer, limit=)`, `store_versions(app_id, bearer, with_builds=)`, `internal_testers(app_id,
  bearer)`, `snapshot(app_id, bearer)` (the three read side by side); `private_key(name, env=, project=, access=)` from
  the environment, else Secret Manager, '' when neither has it. Apple's state words stay the host's to translate.
- tests/test-app-store-connect.py; the Tests workflow installs the `asc` extra.

## 0.12.2 - 2026-10-31 PT

Additive; no host call site changes.

- `headless_claude.run` reads a `stdin` stream once and feeds every attempt the whole of it on a pipe of its own
  (`stream` takes the prompt itself, str or bytes); before, the same `sys.stdin` went to every Popen, the first attempt
  read it to EOF and the one after a limited account got nothing: "Error: Input must be provided either through stdin
  or as a prompt argument when using --print", exit 1 (Dragnet, 10/31/26 9:37 AM PT, the SMPD weekly report's probe
  with gene at its monthly spend limit). `main run` passes stdin on only when it is not a terminal.
- `sentry_api.Client.paged(path, limit_pages=)`: (rows, capped), `pages()`'s rows with whether the list was cut at the
  cap with a page still to come, so a sweep can say so (SMAD PickleBot's alerts sweep, #371's warning, moving onto
  the client in #569).

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
