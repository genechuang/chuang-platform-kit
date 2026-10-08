# A database that meters every byte: what SMAD PickleBot learned moving to Neon

Written 10/8/26 for ChuangFinance's ledger (SQLite → Neon Postgres), at Gene's ask: "make sure the
safeguards we put in place ... are in chuang-kit ... and provide him with some best practices /
lessons learned." The incident behind every line is SMAD PickleBot's `docs/LESSONS.md`, "The Neon
Near-Miss: One Tab, 100k Reads, 4.95 of 5 GB (2026-10-06 PT)".

## What happened

Six days into October, with one user and a 35 MB database, Neon emailed that the project had
spent 99% of the free plan's **5 GB of monthly network transfer**. At 100% the free plan
**suspends the database until the next month** — every reader down, nothing broken in the code.

Two causes multiplied. A client effect depended on a value whose identity changed every render,
so render → fetch → render, 3 to 6 reads a second per open tab with nobody touching anything,
about 100k calls in four hours. And every one of those calls pulled each vote's voter as a whole
35-column row: 23 of the 24 KB a call moved came from columns the screen never showed. Nothing
errored, nothing paged; the first signal was Neon's meter.

The arithmetic to keep: metered transfer counts **bytes sent**, so a small table re-sent a hundred
thousand times is gigabytes, and a request rate a hundred times normal is a bug even when every
request succeeds.

## The rules, each with its kit piece

1. **Select the columns the answer shows, never `*`.** The roster read that pulled a year of games
   and votes per request was the second cause. Name the columns in every query; a reader that
   wants "the row" gets the few fields it uses. Pin the narrowing with a test
   (`tests/test-api-votes.py` there asserts the member read loads five player columns).
2. **A loop that reads on every tick is a bug until proven otherwise.** Anything that re-reads on
   a timer, a render, a retry or a watch gets a floor between reads (1.5 s there), stops while
   nobody is looking, and is tested for how many reads it makes over an idle hour with nobody
   touching it (`app/src/lib/__tests__/request-budget.test.tsx`: one settings read, at most 13
   games reads an hour, none from 50 re-renders). A batch loader is the same thing: one
   `SELECT` per row of the source is N round trips; read the target's keys once, then write in
   batches (`executemany`, or `COPY` for a bulk load).
3. **Keep hot answers for seconds, and let a write drop them**: `chuang_platform_kit.cache`
   (`get`/`put`/`cached` with a TTL, `clear()` after a write, `clear(prefix)` for one family).
   Seconds, not minutes, because a second instance's copy can be a TTL old after a write here.
4. **Give every caller a budget**: `cache.throttle(key, limit, window)` answers the seconds to
   wait once a caller passes the limit; count only the reads the cache could not answer, and key
   it per caller (a session, a device, a job), never globally — on 10/6/26 one looping device
   locked its owner out of every device because every call counted against one key. Log a
   caller past the limit once a minute with its user agent (`cache.once`).
5. **Page on the rate, not the error.** An alert when the service answers more than a few
   requests a second for ten minutes (`monitoring.tf` `api_request_spike` there: normal is
   0.04 a second). Nothing in a 100k-read afternoon failed, so an error alert would never fire.
6. **Measure from the database side**: `CREATE EXTENSION pg_stat_statements` (a migration, never a
   hand `CREATE`), and read `calls`, `rows` and `shared_blks_hit` per statement before guessing
   which query is heavy. `pg_column_size()` on the rows a hot read returns tells you what each call
   moves.
7. **Watch the meter yourself**: `python -m chuang_platform_kit.neon --name <project> --usage`
   prints this month's `data_transfer_bytes`, `written_data_bytes`, compute and active hours and
   the transfer against the budget (`--budget-gb`, 5 on the free plan), and warns from 80%; put it
   in a daily job so the first signal is yours, not Neon's email at 99%.
8. **Cap the compute before the first bill**: `--cap-cu 0.25` (or `neon.cap_compute`) on every
   endpoint. The paid plans have spending notifications but no hard cap; the Launch upgrade there
   raised the ceiling to 8 CU, about $620 a month at the worst, and was capped back to 0.25 CU
   (~$19 a month at most) the same hour. The compute cap is what bounds the bill.
9. **Connect through the kit** (`chuang_platform_kit.db.connect` / `engine`): a connect timeout, the
   cold-start retry while the endpoint wakes, a small pre-ping pool, the URL and its password
   redacted. A sleeping free-plan endpoint takes most of a second to wake; a retry loop around a
   fresh connection per query is both slow and a transfer multiplier.
10. **Migrations and bulk loads use the direct endpoint; a web tier uses the pooler.** The
    provisioning script stores the direct URL by default (`--pooled` for the other). Session-level
    settings and `COPY` do not survive the pooler.
11. **The URL is a secret everywhere**: Secret Manager in, `.env` on a desktop, never a note, a
    log or a chat. `db.connect()` registers it with `redact`; a driver's error that quotes the
    password is masked on its way to a log.

## For the ledger port specifically

- Load SQLite → Postgres **once, in batches, from a script that is idempotent** (an upsert on the
  natural key, so a re-run after a failure writes nothing twice). Print row counts per table and
  the bytes moved; compare with `--usage` before and after, so the port's cost is a known number.
- Every reader of the ledger (reports, reconciliation, a dashboard) **reads once per run and
  keeps the result**; a report that re-queries per line item is rule 2.
- Decide the **retry policy** before the first job: a failed statement is retried a bounded number
  of times with a growing wait, and a job that cannot connect exits non-zero and says so. An
  unbounded retry on a suspended database is the loop that spends the next month's budget on
  the first of the month.
- Put `--usage` and the Sentry DSN in the daily job on day one; the 5 GB is a month's budget, and
  a quiet first week says nothing about the first report run.
- **An incremental sync by stamp cannot restore a row deleted from the middle of a table**
  (ChuangFinance, 10/8/26, from a rehearsal on a copy): its stamp sits below the target's
  high-water mark, so the day's-rows pass never sends it again. Compare counts (or a hash) per
  table after the send, and let a mismatch trigger a full upsert of that one table; the daily
  pass stays cheap and the repair stays bounded.
