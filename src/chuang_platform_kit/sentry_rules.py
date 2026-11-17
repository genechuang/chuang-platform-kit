"""Which Sentry issues are alarms: one definition for every reader of a host's Sentry issues (a webhook that posts
to a channel, a sweep that files a ticket), so both call the same things alarms.

An issue is an alarm when its level is error or fatal, its latest event's environment is production, and the event
did not come from a developer's machine: a `service` tag of `local` (what a host's config sets off its cloud
runtime), or a browser event whose page's host is a loopback (localhost, 127.0.0.1) or one of the host's own
development hosts, which the caller names in `dev_hosts` (a rung below production: dev.example.com, qa.example.com).
The environment is `prod`, with `production` still read as production. A process's `server_name` never counts by
being a loopback: every container reports `localhost`; it counts only when it names one of `dev_hosts`.

Two widenings: a Sentry performance issue (is_performance) is an alarm at any level, since Sentry files those at
info; and an emulator or simulator event is an alarm in any environment, so a test run's failure is caught before
production. A developer's own machine stays out of both.

When the event cannot be read, the level decides: missing a production error is worse than one extra post.
Standard library only.
"""
from urllib.parse import urlparse

ALARM_LEVELS = ('error', 'fatal')
PRODUCTION = 'prod'
PRODUCTION_NAMES = (PRODUCTION, 'production')
LOCAL_SERVICES = ('local',)
EMULATORS = ('sdk_gphone', 'emulator', 'generic_x86')   # the Android emulator's device names
LOOPBACKS = ('localhost', '127.0.0.1')   # a container's own hostname, never a sign of a developer


def tags(event) -> dict:
    """An event's tags as {key: value}: the API's [{key, value}] list or a webhook's [[key, value]] pairs."""
    out = {}
    for t in (event or {}).get('tags') or []:
        if isinstance(t, dict):
            out[str(t.get('key'))] = str(t.get('value'))
        elif isinstance(t, (list, tuple)) and len(t) == 2:
            out[str(t[0])] = str(t[1])
    return out


PERFORMANCE_TITLES = ('N+1 Query', 'Consecutive HTTP', 'Consecutive DB Queries', 'N+1 API Call', 'Slow DB Query',
                      'Large HTTP payload', 'Large Render Blocking Asset', 'File IO on Main Thread', 'Uncompressed Asset')


def is_performance(issue) -> bool:
    """Whether a Sentry issue is one of its performance detectors' (issueCategory `performance`, an issueType starting
    `performance_`, or one of their titles when an older answer carries neither)."""
    issue = issue or {}
    return (str(issue.get('issueCategory') or '').lower() == 'performance'
            or str(issue.get('issueType') or '').lower().startswith('performance_')
            or str(issue.get('title') or '') in PERFORMANCE_TITLES)


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or '').lower()
    except ValueError:
        return ''


def is_alarm(issue, event=None, dev_hosts=()):
    """(True, '') when the issue is an alarm; (False, why) when it is not. `event` is its latest event, or
    None/{} when it could not be read. `dev_hosts` are the host's own development host names (whole names, any
    case), whose pages and processes are a developer's; the loopbacks always are for a page."""
    level = str((issue or {}).get('level') or (event or {}).get('level') or '').lower()
    # A performance issue (N+1 Query, Consecutive HTTP, ...) is an alarm whatever its level: Sentry files them at
    # info, so the level rule alone would hide them.
    if level not in ALARM_LEVELS and not is_performance(issue):
        return False, f'level {level or "unknown"}, not error or fatal'
    if not event:
        return True, ''
    t = tags(event)
    device = t.get('device', '').lower()
    # An emulator or simulator test run is an alarm in any environment, a test build's dev rung included.
    test_device = device.startswith(EMULATORS) or 'true' in (t.get('emulator', '').lower(), t.get('simulator', '').lower())
    env = str(event.get('environment') or t.get('environment') or PRODUCTION).lower()
    if env not in PRODUCTION_NAMES and not test_device:
        return False, f'environment {env}, not prod'
    if t.get('service', '').lower() in LOCAL_SERVICES:
        return False, "a developer's machine (service tag local)"
    # A page's host names a developer's machine at localhost; a process's server_name never does by being `localhost`:
    # every container reports that. A developer's box is told by its `service` tag (local, above); server_name counts
    # only when it names a development host.
    developer = set(LOOPBACKS) | {str(h).lower() for h in dev_hosts or ()}
    server = str(t.get('server_name', '')).lower()
    for host in (_host(t.get('url', '')), _host(((event.get('request') or {}).get('url')) or ''),
                 '' if server in LOOPBACKS else server):
        if host and host in developer:
            return False, f"a developer's machine ({host})"
    return True, ''
