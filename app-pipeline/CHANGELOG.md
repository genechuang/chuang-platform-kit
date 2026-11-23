# chuang-app-pipeline changelog

## 0.1.0 (2026-11-22)

- New distribution (SMADPickleBot #2161, step 2, #2236). First module `expo_channel`: `parse`, `manifest`, `judge`,
  `bundle`, `same_tree`, `check` and `line`, the check of what an Expo update channel serves a phone after a release.
  It is SMADPickleBot's `pipeline/shared/phone_channel_check.py` with the names taken out: the host passes the update
  server's address, the release's notice, the channels' names, the verdict's `by` and how a commit's app tree is
  read. Standard library only; the transfers are curl's, since Expo's CDN refuses Python's own client.
  Suite: `tests/test-app-expo-channel.py`.
