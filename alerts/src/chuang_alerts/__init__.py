"""chuang-alerts: a project's alerting, project-agnostic. The hourly WARNING digest (`digest`) and a Pub/Sub
subscription's backlog before its dead letters (`backlog`) since 0.1.0; the alert catalog's shape and lookups
(`catalog`) since 0.2.0 (SMADPickleBot #1965); the alert watcher's engine (`engine`: incident state, the fold and
debounce, the Fixed and quiet notes, the sweep and the dead-letter read, configured once by the host) (#1966). A distribution of its own
beside chuang-platform-kit and chuang-sdlc in the same repository (alerts/, tags alerts-v<version>), pinned only where
it runs."""
__version__ = '0.3.0'
