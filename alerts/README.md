# chuang-alerts

A project's alerting, project-agnostic. A distribution of its own in `alerts/` of chuang-platform-kit's repository,
with its own tags (`alerts-v<version>`), pinned only where it runs:

    chuang-alerts @ git+https://github.com/genechuang/chuang-platform-kit@alerts-v0.3.0#subdirectory=alerts

- `chuang_alerts.digest`: the hourly WARNING digest. A project's WARNING lines folded into kinds, kept in a
  collection, and a kind fired for a person when it is new, growing or repeating; the person's decision (promote,
  demote, keep) recorded on it.
- `chuang_alerts.backlog`: each Pub/Sub subscription's backlog read from Cloud Monitoring and sorted by what it means
  (quiet, informational, pulled and stuck, a push endpoint refusing, in flight, small), before ten refused deliveries
  move a message to its dead letters. The host words each finding.
- `chuang_alerts.catalog` (0.2.0): an alert catalog's entry shape (name, broke, who, todo, next, variants, steps) and
  its lookups over the host's own tables: a job with its variant (a dev twin read as a rolled-back release), a
  schedule by id or prefix, a task action, a service with its error-line rules first, a dead letter, a build by tag.
  An unlisted system gets an entry that names it, with the host's words for what is unknown.

- `chuang_alerts.engine` (0.3.0): an alert watcher's engine: incident state, the fold and debounce, the Fixed and
  "quiet, not fixed" notes, the sweep and the dead-letter read, configured once by the host (`configure`, the
  watcher's own service name required so it never tells its own lines).

Nothing here names a project, a subscription or a person: those are the host's arguments.
