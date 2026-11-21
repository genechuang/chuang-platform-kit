# chuang-alerts changelog

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
