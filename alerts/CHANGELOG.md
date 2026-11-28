# chuang-alerts changelog

## 0.4.0 (2026-11-28)

- `engine`: a job run made outside Cloud Run can describe itself on its own lines, and is judged from that
  (SMADPickleBot #2579). A host that runs a job on a box of its own and logs as the job has no execution the Run
  Admin API can answer for: the sweep asked anyway, read nothing, and told a run that had ended as "(unknown)",
  "has not finished" and "still running". A line may now carry `RUN_LABELS` (`alerts/where`, the host's words for
  the place; `alerts/args`; `alerts/started`; `alerts/ended`; `alerts/exit`). `described_run` reads them,
  `on_job_line` keeps them with the hold, and `sweep_job` builds the run from them without the Run Admin API: exit 0
  passes as an execution does, any other exit is told with its place, its real start and its variant, and its link is
  its own lines in the Logs Explorer (`described_link`). Such a run that says it has ended is swept after
  `DESCRIBED_WAIT` (2 minutes), not `END_WAIT`. `handle_job_end` and `compose_job` take `where`. A run with no
  `alerts/where` is an execution, as before. The host gives each run an execution name of its own: one shared name
  is "already told" for `KEEP_PENDING` after its first alert. Suite: `tests/test-alerts-engine.py`.

## 0.3.2 (2026-11-21)

- `engine.decode` reads the pushed message through chuang-platform-kit's `pubsub.decode` instead of its own base64 and
  JSON (SMADPickleBot #1933). It still answers {} for anything unreadable, and now also for JSON that is not an object,
  which `handle` could not have read. Suite: `tests/test-alerts-engine.py`.

## 0.3.1 (2026-11-21)

- `engine.pt` writes its time through chuang-platform-kit's `clock.human_time` instead of a copy of its format
  (SMADPickleBot #1966, after #1932 moved SMAD's own copy onto the clock). With a `zone_label` the text is unchanged;
  without one, a US zone reads its short label ("PT") where it read the season's abbreviation ("PST").

## 0.3.0 (2026-11-20)

- `engine`: an alert watcher's engine, moved unchanged in behavior from SMAD PickleBot's alert watcher (SMADPickleBot
  #1966): incident state in a store (`FirestoreStore`), Google's reads (`Google`: Logging, Run Admin, Cloud Build,
  Scheduler, Pub/Sub's dead-letter pull), one event's world (`Context`), the job, schedule, service, build, task and
  incident handlers, the fold and debounce, the roll-ups, the Fixed and "quiet, not fixed" notes, the sweep, the
  dead-letter read, `decode` and `handle`. Everything a host names comes through one `configure(...)`, the watcher's
  own service name required so it never tells its own lines. Suite: `tests/test-alerts-engine.py`.

## 0.2.0 (2026-11-20)

- `catalog`: an alert catalog's entry shape and lookups (`job_entry`, `schedule_entry`, `task_entry`, `line_rule`,
  `service_entry`, `dead_letter_entry`, `build_entry`) over the host's own tables, moved unchanged from SMAD
  PickleBot's alert watcher (SMADPickleBot #1965); every entry stays the host's data. Suite: `tests/test-alerts-catalog.py`.

## 0.1.0 (2026-11-19)

- The distribution is made (SMADPickleBot #1964, the first step of #1684, Gene's "full").
- `digest`: the hourly WARNING digest, moved unchanged from `chuang_sdlc.warning_kinds` (chuang-sdlc 0.39.0), which
  keeps its copy one release.
- `backlog`: a Pub/Sub subscription's backlog read from Cloud Monitoring (`backlogs`, `read_series`) and sorted by
  kind (`classify`), lifted from SMAD PickleBot's watchdog with its wording left to the host.
