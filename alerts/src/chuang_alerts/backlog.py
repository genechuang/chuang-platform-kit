"""A Pub/Sub subscription stuck before its dead letters: each subscription's backlog read from Cloud Monitoring and
sorted by what it means, before ten refused deliveries move a message to a dead-letter topic.

Lifted from SMAD PickleBot's watchdog (jobs/watchdog/run.py `check_subscriptions`, 10/18/26, after a push
subscription refused every delivery for 45 minutes with nothing told; chuang-alerts 0.1.0, SMADPickleBot #1964). The
host reads with `backlogs`, sorts with `classify`, and words each finding itself (who a subscription serves, which
command lists its dead letters): nothing here names a project, a subscription or a person.

`classify`'s kinds, each subscription at most one:
  quiet      a subscription the host calls quiet by name prefix (a CI bus: never the pager), any size
  info       a subscription the host calls informational by prefix (one a person pulls themselves), any size
  pulled     one the host pulls itself (no push endpoint), stuck: older than `age_s` or over `count` waiting
  refusing   a push subscription stuck the same way: its endpoint refuses or does not answer
  in_flight  a smaller backlog whose oldest message is under `in_flight_s` (being delivered, not stuck)
  small      any other smaller backlog
An empty subscription is no finding, and a dead-letter hold (`is_dead_letter_hold`) is never read.
"""
import datetime as dt
import json
import urllib.parse
import urllib.request

MONITORING_URL = 'https://monitoring.googleapis.com/v3/projects/{}/timeSeries'
AGE_S = 300           # a message older than this is stuck
COUNT = 20            # more than this many waiting is stuck
IN_FLIGHT_S = 60      # a smaller backlog younger than this is in flight
STUCK = ('pulled', 'refusing')


def is_dead_letter_hold(name: str) -> bool:
    """A dead-letter topic's own holding subscription (`...dead-letter`, `...-dlq`): read by its own tools, never here."""
    return name.endswith('dead-letter') or name.endswith('-dlq')


def _get(url: str, token: str, opener=urllib.request.urlopen) -> dict:
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
    with opener(req, timeout=30) as resp:
        return json.loads(resp.read() or b'{}')


def read_series(project: str, token: str, metric: str, now, opener=urllib.request.urlopen) -> dict:
    """{subscription: its newest value} for a pubsub_subscription `metric` over the ten minutes before `now`, every
    page of Cloud Monitoring's answer."""
    start = (now - dt.timedelta(minutes=10)).strftime('%Y-%m-%dT%H:%M:%SZ')
    end = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    query = {'filter': f'metric.type="pubsub.googleapis.com/subscription/{metric}" AND resource.type="pubsub_subscription"',
             'interval.startTime': start, 'interval.endTime': end}
    out, page = {}, ''
    while True:
        q = dict(query, pageToken=page) if page else query
        data = _get(MONITORING_URL.format(project) + '?' + urllib.parse.urlencode(q), token, opener=opener)
        for series in data.get('timeSeries') or []:
            sub = ((series.get('resource') or {}).get('labels') or {}).get('subscription_id', '')
            points = series.get('points') or []
            if sub and points:   # newest first
                v = points[0].get('value') or {}
                out[sub] = int(float(v.get('int64Value') or v.get('doubleValue') or 0))
        page = data.get('nextPageToken') or ''
        if not page:
            return out


def backlogs(project: str, token: str, now, opener=urllib.request.urlopen) -> dict:
    """{subscription: {'undelivered': n, 'oldest_s': seconds}} for every subscription of `project` but the
    dead-letter holds."""
    count = read_series(project, token, 'num_undelivered_messages', now, opener=opener)
    age = read_series(project, token, 'oldest_unacked_message_age', now, opener=opener)
    return {s: {'undelivered': count.get(s, 0), 'oldest_s': age.get(s, 0)}
            for s in sorted(set(count) | set(age)) if not is_dead_letter_hold(s)}


def minutes(seconds: int) -> str:
    """'7 min' from two minutes on, else '45 s': how a backlog's age is said."""
    return f'{seconds // 60} min' if seconds >= 120 else f'{seconds} s'


def classify(found: dict, quiet=(), info=(), pulled=(), age_s: int = AGE_S, count: int = COUNT,
             in_flight_s: int = IN_FLIGHT_S) -> list:
    """[(kind, subscription, undelivered, oldest seconds)] in subscription order for `found` (backlogs' answer), one
    finding a subscription that holds anything; `quiet` and `info` are name prefixes, `pulled` names. The kinds are
    the module's; the host says each at its own level and in its own words."""
    out = []
    for sub, b in sorted(found.items()):
        n, age = int(b.get('undelivered') or 0), int(b.get('oldest_s') or 0)
        if n <= 0 and age <= 0:
            continue
        stuck = age > age_s or n > count
        if any(sub.startswith(p) for p in quiet):
            kind = 'quiet'
        elif any(sub.startswith(p) for p in info):
            kind = 'info'
        elif stuck:
            kind = 'pulled' if sub in set(pulled) else 'refusing'
        elif age < in_flight_s:
            kind = 'in_flight'
        else:
            kind = 'small'
        out.append((kind, sub, n, age))
    return out
