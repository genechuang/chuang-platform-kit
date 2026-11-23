# chuang-app-pipeline changelog

## 0.3.1 (2026-11-22)

- `runtime.guard_verdict`: a store runtime that was not read (None or empty) is never a match, even against a row that
  records none; 0.3.0 answered `ok` for two unknowns, which would have let an update through unchecked. No host had
  released on 0.3.0.

## 0.3.0 (2026-11-22)

- New module `runtime` (SMADPickleBot #2244, step 3b of #2161), no signature changes elsewhere: the parts of a native
  runtime guard that stand on their own, each a function of what it is handed, with no command run and no word
  printed. `guard_verdict(inputs_now, recorded, installed_runtime, allow)` answers `inputs_moved`, `ok`,
  `runtime_differs` or `allowed`; `install_stale(app, label=)`; `lock_hash(app)` and `installed(app, mark)`;
  `memo_get` / `memo_put` (a passing answer kept `seconds` under a key); `read_builds(ask, tries, pauses, need=,
  sleep=, on_retry=)` (a host's store read tried again, `ReadFailed` with the last cause); `record_inputs(data,
  platform, inputs, keep=, since=)`. The host keeps its own fingerprint read, where it runs Expo, and every sentence
  it tells its people. Suite: `tests/test-app-runtime.py`.

## 0.2.0 (2026-11-22)

- New module `artifact` (SMADPickleBot #2237, step 3a of #2161), no signature changes elsewhere: `config_hash` and
  `key` (a build's content key), `sha256`, `pack`, `sizes` / `mb` / `size_line` (what an export ships, by `KINDS`),
  `release_check(folder, api, non_prod)` and `stamp_check` (fit to release, and this build's), `stale_packages(app)`,
  `runtime_versions(path)`, `expo_error`, `config_files`, `join_exports` (each platform's export into one bundle;
  `JoinError` on a disagreement), `bundle_has`, `posix_metadata`, `tested_here` and `checks_to_run`. They are the leaf
  functions of SMADPickleBot's `pipeline/shared/app/artifact.py`, with the app folder, the release API, the hosts a
  release must never name and the runtime file's path as arguments. Suite: `tests/test-app-artifact.py`.

## 0.1.0 (2026-11-22)

- New distribution (SMADPickleBot #2161, step 2, #2236). First module `expo_channel`: `parse`, `manifest`, `judge`,
  `bundle`, `same_tree`, `check` and `line`, the check of what an Expo update channel serves a phone after a release.
  It is SMADPickleBot's `pipeline/shared/phone_channel_check.py` with the names taken out: the host passes the update
  server's address, the release's notice, the channels' names, the verdict's `by` and how a commit's app tree is
  read. Standard library only; the transfers are curl's, since Expo's CDN refuses Python's own client.
  Suite: `tests/test-app-expo-channel.py`.
