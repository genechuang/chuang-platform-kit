# chuang-alerts

A project's alerting, project-agnostic. A distribution of its own in `alerts/` of chuang-platform-kit's repository,
with its own tags (`alerts-v<version>`), pinned only where it runs:

    chuang-alerts @ git+https://github.com/genechuang/chuang-platform-kit@alerts-v0.1.0#subdirectory=alerts

- `chuang_alerts.digest`: the hourly WARNING digest. A project's WARNING lines folded into kinds, kept in a
  collection, and a kind fired for a person when it is new, growing or repeating; the person's decision (promote,
  demote, keep) recorded on it.
- `chuang_alerts.backlog`: each Pub/Sub subscription's backlog read from Cloud Monitoring and sorted by what it means
  (quiet, informational, pulled and stuck, a push endpoint refusing, in flight, small), before ten refused deliveries
  move a message to its dead letters. The host words each finding.

Nothing here names a project, a subscription or a person: those are the host's arguments. The alert catalog's shape
and the alert watcher's engine follow (SMADPickleBot #1965, #1966).
