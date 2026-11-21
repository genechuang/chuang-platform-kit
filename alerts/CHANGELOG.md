# chuang-alerts changelog

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
