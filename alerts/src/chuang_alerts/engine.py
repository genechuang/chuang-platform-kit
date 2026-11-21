"""chuang_alerts.engine: an alert watcher's engine, one plain-English message per broken system (SMADPickleBot #1966).

The host's function is fed Cloud Logging entries by a log sink's Pub/Sub push, and Cloud Monitoring incidents on the
same topic, and calls `handle(payload, Context())` for each and `sweep(Context())` from a schedule. Everything the host
owns comes through one `configure(**kw)` call, before the first event: its project and region, its incident and pending
collections, the console address, its task queue, its own service's name (required: a watcher never alerts on its own
lines), its catalog's lookups, its zone, its logger, how a message is delivered, how an incident write is published,
how a dead-lettered message is described and how a task's name is read.

What each kind does (an incident per system in the incidents collection, held lines in the pending one; an incident
keeps the subject and body it was told with, for an issue filer):

  Cloud Run Job   An ERROR line is held under its execution. The execution's end (the audit entry's "Execution ... has
                  failed to complete") sends ONE message: that execution's ERROR lines and its step summary (a
                  `{"steps": {...}}` line) read back from the Logging API, the last successful execution of the same run
                  (the same arguments) from the Run Admin API. A later succeeded execution of that run sends "Fixed"; one
                  that ran only some of the failed steps (`--only`) says which are fixed. A deploy's smoke test (`--help`)
                  and a hand `python -c` are not alerts. Held lines whose end never comes are sent by the sweep after 15
                  minutes (it asks the Run Admin API first, so a finished run reads whole).
  Cloud Scheduler A failed attempt sends a message, then one per 30 minutes per schedule with the count of the rest; the
                  sweep closes it with "Fixed" from the attempt history once a later attempt passed. A schedule with
                  retries is held first, for its first backoff and two minutes: a retry that passes closes it with
                  nothing told, a retry that fails tells it at once with both attempts, and the sweep tells one whose
                  retry never answered.
  Services        The first ERROR line sends at once; more within 30 minutes are counted (in memory for a minute, then
                  in the store) and rolled into the next message; 30 quiet minutes, seen by the sweep, send "Fixed" when
                  a new revision serves, "quiet, not fixed" when the one that failed still does.
  Cloud Build     One message per failed build, its failing step and the build's ERROR lines.
  Tasks           The host's queue's last refused attempt: one message and one incident per task, never a Fixed (the
                  task is gone); it leaves "Also open" after STALE_HOURS.
  Dead letters    A Cloud Monitoring incident on a dead-letter topic: one message per opened incident, the queue, the
                  count and what the messages were (read without taking them), kept as an incident; a closed one closes
                  the kept incident and is not told. Another policy is told in its own words.

Delivery is the host's `deliver(subject, body, link)`, which answers the channels that took it; none is an error, so
the push is redelivered. Every line shown passes chuang_platform_kit.redact.
"""
import base64
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from chuang_platform_kit.redact import redact

DEBOUNCE = timedelta(minutes=30)     # one message per system per 30 minutes; the rest counted
QUIET = timedelta(minutes=30)        # a service quiet this long is Fixed
END_WAIT = timedelta(minutes=15)     # held job lines without an end are sent after this
RETRY_SLACK = timedelta(minutes=2)   # a held schedule waits its first backoff and this for its retry's answer
KEEP_PENDING = timedelta(days=2)     # sent or dropped holds are swept after this
MAX_LINES = 5
LINE_CHARS = 300
# SMADPickleBot #676: an open incident's repeats are counted in memory for FOLD_SECONDS after a counted write and
# answered at once, before any store read (fold); the first message after it writes them in (debounced). An instance
# that restarts loses at most that minute of counts, never a first message or a fix.
FOLD_SECONDS = 60
_FOLD = {}        # incident key: {'since': when the fold began, 'n', 'lines', 'last_at', 'revision'}
_JOB_FULL = set()  # executions whose held lines are full or told: a later line adds nothing (on_job_line)
FAILED_BUILD = ('FAILURE', 'TIMEOUT', 'INTERNAL_ERROR', 'EXPIRED')
SCHEDULER_FINISHED = 'type.googleapis.com/google.cloud.scheduler.logging.AttemptFinished'
SCHEDULER_AGENT = 'Google-Cloud-Scheduler'   # the user agent of every call a schedule makes (SMADPickleBot #1469)
STALE_HOURS = 36
DEAD_LETTER_WINDOW = timedelta(minutes=10)   # a message moved this long before the incident's start is the incident's:
                                             # the policy sums 5 minutes, and Monitoring opens a few minutes after
MAX_GIVEN_UP = 8                             # kinds of message listed in one alert; the rest counted

# ---------------------------------------------------------------------------------------------------------
# The host's settings
# ---------------------------------------------------------------------------------------------------------

_DEFAULTS = {
    'project': '',                     # the Google Cloud project the reads and console links name
    'region': '',                      # its region
    'incidents': 'alert_incidents',    # the store's collection of incidents, one per system
    'pending': 'alert_pending',        # and of held lines, sent marks and pending builds
    'console': 'https://console.cloud.google.com',
    'task_queue': '',                  # the one Cloud Tasks queue whose last refused attempt is told
    'service': None,                   # the watcher's own service: its lines are never told (required)
    'subject_prefix': 'Alert',         # every subject begins '<prefix>: '
    'one_off': '',                     # a label prefix of one-off runs, which never run again to say Fixed
    'task_kind': 'task',               # the incident kind of a task the queue gave up on
    'dead_letter_kind': 'dead_letter',  # the incident kind of a dead-letter topic's alert
    'dead_letter_command': '',         # the host's list/replay/drop command, '{queue}' its queue ('' for none)
    'jobs': {},                        # the catalog's job table, read for a job's step order and step words
    'dead_letters': {},                # the catalog's dead-letter table, by topic
    'job_entry': None,                 # job_entry(job, variant) -> entry
    'schedule_entry': None,            # schedule_entry(job_id) -> entry
    'task_entry': None,                # task_entry(action) -> entry
    'service_entry': None,             # service_entry(service, line) -> entry
    'line_rule': None,                 # line_rule(service, line) -> a rule's index or None
    'dead_letter_entry': None,         # dead_letter_entry(topic) -> entry
    'build_entry': None,               # build_entry(tag) -> entry
    'zone': timezone.utc,              # the zone every shown time is in
    'zone_label': '',                  # its label after a time ('PT'); '' for the zone's own abbreviation
    'logger': logging.getLogger('chuang_alerts.engine'),
    'deliver': None,                   # deliver(subject, body, link) -> the channels that took it
    'publish_incident': None,          # publish_incident(key, incident): every incident write, as an event
    'describe_dead_letter': None,      # describe_dead_letter(payload, attributes) -> {'what', 'source'} or a step's
    'describe_task': None,             # describe_task(task name) -> (its action, who or what it was for, as words)
    'firestore_client': None,          # a callable answering the store's Firestore client, when none is given
}
_CFG = dict(_DEFAULTS)


def configure(**kw) -> dict:
    """Set the host's settings (see _DEFAULTS for each name); called once at import by the host. The watcher's own
    service name is required, since a watcher that told its own lines would loop; an unknown name is a TypeError.
    The settings now in force."""
    unknown = sorted(set(kw) - set(_DEFAULTS))
    if unknown:
        raise TypeError(f"engine.configure: unknown setting(s) {', '.join(unknown)}")
    service = kw.get('service', _CFG.get('service'))
    if not service:
        raise ValueError("engine.configure: `service`, the watcher's own service name, is required")
    _CFG.update(kw)
    return dict(_CFG)


def _log():
    return _CFG['logger']


def _need(name):
    """A host lookup, or a RuntimeError naming it when the host never configured it."""
    fn = _CFG.get(name)
    if fn is None:
        raise RuntimeError(f'engine.configure was not given `{name}`')
    return fn


def _subject(text: str) -> str:
    return f"{_CFG['subject_prefix']}: {text}"


# ---------------------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------------------

def instant_now() -> datetime:
    """Now, as an aware UTC instant (stored instants; every shown time goes through pt())."""
    return datetime.now(timezone.utc)   # utc-ok: stored instants; every shown time goes through pt()


def parse_ts(text) -> datetime:
    """An RFC 3339 stamp (Logging's nanoseconds trimmed) as an aware datetime; None for nothing."""
    if not text:
        return None
    if isinstance(text, datetime):
        return text
    s = str(text).replace('Z', '+00:00')
    m = re.match(r'^(.*T\d\d:\d\d:\d\d)(\.\d+)?(.*)$', s)
    if m:
        frac = (m.group(2) or '')[:7]
        s = m.group(1) + frac + (m.group(3) or '+00:00')
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)   # utc-ok: a zoneless stamp from Google is UTC


def iso(dt: datetime) -> str:
    """A stored instant: ISO 8601 in UTC, to the second."""
    return dt.astimezone(timezone.utc).isoformat(timespec='seconds')   # utc-ok: a stored instant


def pt(dt) -> str:
    """'Sat 10/10/26 1:30 AM PT' in the configured zone (the year always), labelled with the configured `zone_label`,
    else the zone's own abbreviation."""
    d = parse_ts(dt).astimezone(_CFG['zone'])
    hour = d.hour % 12 or 12
    label = _CFG['zone_label'] or d.tzname() or 'UTC'
    return f"{d:%a} {d.month}/{d.day}/{d:%y} {hour}:{d:%M} {'AM' if d.hour < 12 else 'PM'} {label}"


def clean(line: str) -> str:
    """One error line as it is shown: redacted, one line, trimmed."""
    text = redact(' '.join(str(line or '').split()))
    return text if len(text) <= LINE_CHARS else text[:LINE_CHARS - 1] + '…'


def system_key(kind: str, *parts) -> str:
    """The store id of a system's incident: kind and name, no slashes."""
    raw = ':'.join([kind] + [p for p in parts if p])
    return re.sub(r'[^A-Za-z0-9_.:@=-]+', '_', raw)[:300]


def variant_of(command, args):
    """A job run's identity by its arguments (catalog variants): the arguments without the interpreter's
    -u, the script name and --only <steps>; None for a run that is not an alert: a deploy's smoke test
    (--help) or a hand `python -c`."""
    args = [str(a) for a in (args or [])]
    if '--help' in args or '-h' in args or args[:1] == ['-c']:
        return None
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a == '-u' or a.endswith('.py'):
            continue
        if a == '--only':
            skip = True
            continue
        if a.startswith('--only='):
            continue
        out.append(a)
    return ' '.join(out)


def only_steps(args) -> list:
    """The steps a run was limited to with --only (empty when it ran them all)."""
    args = [str(a) for a in (args or [])]
    for i, a in enumerate(args):
        if a == '--only' and i + 1 < len(args):
            return [s for s in args[i + 1].split(',') if s]
        if a.startswith('--only='):
            return [s for s in a.split('=', 1)[1].split(',') if s]
    return []


def entry_text(entry: dict) -> str:
    """A LogEntry's words: jsonPayload.message, textPayload, a request's method, path and status, or an
    audit entry's status message."""
    jp = entry.get('jsonPayload') or {}
    if jp.get('message'):
        return str(jp['message'])
    if entry.get('textPayload'):
        return str(entry['textPayload'])
    req = entry.get('httpRequest') or {}
    if req:
        url = re.sub(r'^https?://[^/]+', '', str(req.get('requestUrl', '')))
        return f"{req.get('requestMethod', '')} {url} answered {req.get('status', '?')}".strip()
    proto = entry.get('protoPayload') or {}
    if (proto.get('status') or {}).get('message'):
        return str(proto['status']['message'])
    return json.dumps(jp)[:LINE_CHARS] if jp else '(no message)'


STEP_LINE = re.compile(r'failed \(exit -?\d+\):\s*(.*)$')


def step_error(message: str) -> str:
    """'weekly costs-page failed (exit 1): <error>' -> '<error>' (a leading 'ERROR: ' dropped)."""
    m = STEP_LINE.search(message or '')
    text = m.group(1) if m else (message or '')
    return re.sub(r'^ERROR:\s*', '', text.strip())


def and_join(items: list) -> str:
    """'a', 'a and b', 'a, b and c' (empty items left out)."""
    items = [i for i in items if i]
    if len(items) <= 1:
        return ''.join(items)
    return ', '.join(items[:-1]) + ' and ' + items[-1]


# ---------------------------------------------------------------------------------------------------------
# The world: the store, Google's read APIs, the sender. Each is a seam the tests replace.
# ---------------------------------------------------------------------------------------------------------

class FirestoreStore:
    """The incidents and pending collections in Firestore (the host's client, or its configured `firestore_client`)."""

    def __init__(self, client=None):
        if client is None:
            client = _need('firestore_client')()
        self.db = client

    def get(self, coll, key):
        """The document's fields, or None when it does not exist."""
        snap = self.db.collection(coll).document(key).get()
        return snap.to_dict() if getattr(snap, 'exists', False) else None

    def set(self, coll, key, data):
        """Write the document whole; an incident's write is also published (the host's `publish_incident`)."""
        self.db.collection(coll).document(key).set(data)
        if coll == _CFG['incidents'] and _CFG['publish_incident'] is not None:
            _CFG['publish_incident'](key, data)

    def delete(self, coll, key):
        """Delete the document."""
        self.db.collection(coll).document(key).delete()

    def all(self, coll):
        """Every document of the collection as (id, fields)."""
        return [(d.id, d.to_dict()) for d in self.db.collection(coll).stream()]


class Google:
    """Logging, Run Admin, Cloud Build, Scheduler and Pub/Sub reads over REST with the default credentials."""

    def __init__(self, session=None, project=None, region=None):
        self._session = session
        self.project = project if project is not None else _CFG['project']
        self.region = region if region is not None else _CFG['region']

    def session(self):
        """The authorized session, made once: the process's own account (the kit's google_auth.authorized_session)."""
        if self._session is None:
            # No standard-library fallback: the reads pass `params=`, which only google-auth's AuthorizedSession takes.
            from chuang_platform_kit import google_auth
            session = google_auth.authorized_session('https://www.googleapis.com/auth/cloud-platform', fallback=False)
            if session is None:
                raise RuntimeError('no application default credentials (or no google-auth) for the Google reads')
            self._session = session
        return self._session

    def entries(self, filter_: str, order: str = 'timestamp asc', limit: int = 200) -> list:
        """Cloud Logging's entries matching the filter (entries.list, one page)."""
        r = self.session().post('https://logging.googleapis.com/v2/entries:list', timeout=30,
                                json={'resourceNames': [f'projects/{self.project}'], 'filter': filter_,
                                      'orderBy': order, 'pageSize': limit})
        r.raise_for_status()
        return r.json().get('entries', [])

    def execution_entries(self, job: str, execution: str, since: datetime) -> list:
        """The execution's ERROR lines and its step summary, oldest first."""
        return self.entries(
            f'resource.type="cloud_run_job" AND resource.labels.job_name="{job}" '
            f'AND labels."run.googleapis.com/execution_name"="{execution}" '
            f'AND (severity>=ERROR OR jsonPayload.steps:*) AND NOT logName:"cloudaudit.googleapis.com" '
            f'AND timestamp>="{iso(since)}"')

    def executions(self, job: str) -> list:
        """The job's recent executions, newest first (Run Admin API v2)."""
        r = self.session().get(f'https://run.googleapis.com/v2/projects/{self.project}/locations/{self.region}'
                               f'/jobs/{job}/executions', params={'pageSize': 100}, timeout=30)
        r.raise_for_status()
        return r.json().get('executions', [])

    def execution(self, job: str, name: str) -> dict:
        """One execution (Run Admin API v2)."""
        r = self.session().get(f'https://run.googleapis.com/v2/projects/{self.project}/locations/{self.region}'
                               f'/jobs/{job}/executions/{name}', timeout=30)
        r.raise_for_status()
        return r.json()

    def serving_revisions(self, service: str) -> set:
        """The revisions taking traffic on a Cloud Run service now (Run Admin API v2 trafficStatuses, percent > 0)."""
        r = self.session().get(f'https://run.googleapis.com/v2/projects/{self.project}/locations/{self.region}'
                               f'/services/{service}', timeout=30)
        r.raise_for_status()
        return {t.get('revision') for t in r.json().get('trafficStatuses') or [] if (t.get('percent') or 0) > 0 and t.get('revision')}

    def last_ok_attempt(self, job_id: str, before: datetime):
        """The schedule's last successful attempt before `before`, as a datetime, within 30 days; None."""
        got = self.entries(f'resource.type="cloud_scheduler_job" AND resource.labels.job_id="{job_id}" '
                           f'AND jsonPayload."@type"="{SCHEDULER_FINISHED}" AND severity<ERROR '
                           f'AND timestamp<"{iso(before)}" AND timestamp>="{iso(before - timedelta(days=30))}"',
                           order='timestamp desc', limit=1)
        return parse_ts(got[0]['timestamp']) if got else None

    def retry_config(self, job_id: str) -> dict:
        """The schedule's retryConfig from the Scheduler API ({} when it has none): retryCount and the
        minBackoffDuration its first retry waits."""
        r = self.session().get(f'https://cloudscheduler.googleapis.com/v1/projects/{self.project}/locations/'
                               f'{self.region}/jobs/{job_id}', timeout=30)
        r.raise_for_status()
        return r.json().get('retryConfig') or {}

    def _builds_urls(self) -> list:
        """Cloud Build's builds collection, global and in this region: a regional trigger's build answers 404 at the
        global address."""
        base = f'https://cloudbuild.googleapis.com/v1/projects/{self.project}'
        return [f'{base}/builds', f'{base}/locations/{self.region}/builds']

    def build(self, build_id: str) -> dict:
        """The build, looked up globally and then in this region; the last answer's error when neither has it."""
        r = None
        for url in self._builds_urls():
            r = self.session().get(f'{url}/{build_id}', timeout=30)
            if r.status_code != 404:
                break
        r.raise_for_status()
        return r.json()

    def build_errors(self, build_id: str) -> list:
        """The build's ERROR lines from Cloud Logging, as text."""
        return [entry_text(e) for e in self.entries(
            f'resource.type="build" AND resource.labels.build_id="{build_id}" AND textPayload=~"^ERROR"')]

    def dead_letters(self, subscription: str, limit: int = 100) -> list:
        """The messages waiting on a dead-letter subscription, read without taking them: one pull, then the lease
        handed straight back (ack deadline 0), so a later list or replay still sees every one. Each as {'id',
        'published', 'attributes', 'payload'}, the payload decoded (None when unreadable)."""
        from chuang_platform_kit import pubsub
        base = f'https://pubsub.googleapis.com/v1/projects/{self.project}/subscriptions/{subscription}'
        r = self.session().post(f'{base}:pull', json={'maxMessages': limit}, timeout=20)
        r.raise_for_status()
        got = r.json().get('receivedMessages') or []
        leases = [m['ackId'] for m in got if m.get('ackId')]
        if leases:
            try:
                self.session().post(f'{base}:modifyAckDeadline', json={'ackIds': leases, 'ackDeadlineSeconds': 0},
                                    timeout=20).raise_for_status()
            except Exception as e:   # noqa: BLE001 - the lease runs out by itself in the subscription's 60 s
                _log().warning(f'alert mailer: {subscription} lease not handed back ({type(e).__name__})')
        rows = []
        for m in got:
            msg = m.get('message') or {}
            try:
                payload = pubsub.decode(m)[0]
            except (ValueError, UnicodeDecodeError):
                payload = None
            rows.append({'id': msg.get('messageId', ''), 'published': msg.get('publishTime', ''),
                         'attributes': msg.get('attributes') or {}, 'payload': payload})
        return rows

    def last_ok_build(self, tag: str, before: datetime):
        """The finish time of the newest successful build with that tag (untagged for none) before `before`; None."""
        q = 'status="SUCCESS"' + (f' AND tags="{tag}"' if tag else '')
        best = None
        for url in self._builds_urls():
            r = self.session().get(url, params={'filter': q, 'pageSize': 20}, timeout=30)
            r.raise_for_status()
            for b in r.json().get('builds', []):
                done = parse_ts(b.get('finishTime'))
                if done and done < before and (tag or not b.get('tags')) and (best is None or done > best):
                    best = done
        return best


class Context:
    """One event's world: the store, Google's reads, the sender and the clock."""

    def __init__(self, store=None, google=None, send=None, now=None):
        self._store, self._google = store, google
        self.send = send or _CFG['deliver']
        self.now = now or instant_now()

    @property
    def store(self):
        """The store, made on first use."""
        if self._store is None:
            self._store = FirestoreStore()
        return self._store

    @property
    def google(self):
        """Google's reads, made on first use."""
        if self._google is None:
            self._google = Google()
        return self._google

    def tell(self, subject: str, body: str, link: str = '') -> list:
        """Send, or raise when neither channel took it (Pub/Sub redelivers the event)."""
        if self.send is None:
            raise RuntimeError('engine.configure was not given `deliver`, and the Context no sender')
        told = self.send(subject, body, link)
        if not told:
            raise RuntimeError(f'neither Slack nor email took the alert "{subject}"')
        _log().info(f"alert mailer: told {' and '.join(told)}: {subject}")
        return told


def stale(inc: dict, now) -> bool:
    """A one-off run's incident (its label begins with the host's `one_off`) with no failure for STALE_HOURS:
    nothing will ever run it again to say Fixed. A task the queue gave up on (on_task) is one too: its task is gone."""
    one = _CFG['one_off']
    if not ((one and str(inc.get('label') or '').startswith(one)) or inc.get('kind') == _CFG['task_kind']):
        return False
    try:
        last = datetime.fromisoformat(str(inc.get('last_at') or inc.get('first_at') or '').replace('Z', '+00:00'))
    except ValueError:
        return False
    return now - last > timedelta(hours=STALE_HOURS)


def others_open(ctx: Context, key: str) -> str:
    """'Also open: ...' for every other open incident, or the all-clear. A stale one-off run's incident is closed
    here instead (`stale`), said in the log, never listed."""
    incidents = _CFG['incidents']
    try:
        rows = [(k, d) for k, d in ctx.store.all(incidents) if d.get('open') and not d.get('held') and k != key]
        for k, d in rows:
            if stale(d, ctx.now):
                ctx.store.set(incidents, k, dict(d, open=False, closed_at=iso(ctx.now), closed_why='stale: a one-off run'))
                _log().info(f"alert mailer: closed {d.get('label', k)}: a one-off run with no failure in {STALE_HOURS} h")
        names = sorted(d.get('label', k) for k, d in rows if not stale(d, ctx.now))
    except Exception as e:   # noqa: BLE001 - a courtesy line; never the reason a message fails
        _log().warning(f'alert mailer: open incidents not read ({type(e).__name__})')
        return ''
    return f"Also open: {', '.join(names)}" if names else 'Still fine: no other alert is open'


# ---------------------------------------------------------------------------------------------------------
# Cloud Run Jobs
# ---------------------------------------------------------------------------------------------------------

def container(execution: dict) -> tuple:
    """(command, args) of an execution, from the audit entry's v1 response or a v2 Execution."""
    spec = (((execution.get('spec') or {}).get('template') or {}).get('spec') or {})
    conts = spec.get('containers') or (execution.get('template') or {}).get('containers') or [{}]
    c = conts[0] or {}
    return c.get('command') or [], c.get('args') or []


def in_step_order(job: str, names: list) -> list:
    """Step names in the job's own order (the catalog's): Logging hands the summary's steps back sorted by name."""
    order = list(((_CFG['jobs'].get(job) or {}).get('steps') or {}))
    return sorted(names, key=lambda s: (order.index(s) if s in order else len(order), s))


def summarize(entries: list) -> dict:
    """An execution's lines as {'steps': {step: code} or None, 'errors': [(step or '', text)]}."""
    steps, errors, seen = None, [], set()
    for e in entries:
        jp = e.get('jsonPayload') or {}
        if isinstance(jp.get('steps'), dict):
            steps = {str(k): int(v) for k, v in jp['steps'].items()}
            continue
        sev = str(e.get('severity', ''))
        if sev not in ('ERROR', 'CRITICAL', 'ALERT', 'EMERGENCY'):
            continue
        step = str(jp.get('step') or '')
        text = clean(step_error(entry_text(e)) if step else entry_text(e))
        if (step, text) in seen:
            continue
        seen.add((step, text))
        errors.append((step, text))
    return {'steps': steps, 'errors': errors}


def job_history(ctx: Context, job: str, variant: str, started: datetime) -> tuple:
    """(last good completion before `started` or None, recovered completion after it or None, the oldest
    run looked at) among the job's executions with the same variant."""
    try:
        runs = ctx.google.executions(job)
    except Exception as e:   # noqa: BLE001
        _log().warning(f'alert mailer: {job} executions not read ({type(e).__name__})')
        return None, None, None
    last_good = recovered = oldest = None
    for x in runs:
        cmd, args = container(x)
        if variant_of(cmd, args) != variant:
            continue
        created = parse_ts(x.get('createTime'))
        if created and (oldest is None or created < oldest):
            oldest = created
        done = parse_ts(x.get('completionTime'))
        if not (done and int(x.get('succeededCount') or 0) >= 1):
            continue
        if created and created > started:
            recovered = done if recovered is None or done < recovered else recovered
        elif last_good is None or done > last_good:
            last_good = done
    return last_good, recovered, oldest


def compose_job(job: str, variant: str, execution: str, started: datetime, summary: dict, condition: str,
                last_good, recovered, oldest, link: str, also: str = '', ended: bool = True) -> tuple:
    """(subject, body) for a failed job run."""
    entry = _need('job_entry')(job, variant)
    steps_cat = (_CFG['jobs'].get(job) or {}).get('steps') or {}
    steps = summary.get('steps')
    errors = summary.get('errors') or []
    failed = in_step_order(job, [s for s, c in (steps or {}).items() if c != 0])
    if not failed:
        failed = list(dict.fromkeys(s for s, _ in errors if s))
    ok = in_step_order(job, [s for s, c in (steps or {}).items() if c == 0])
    errors = sorted(errors, key=lambda e: failed.index(e[0]) if e[0] in failed else len(failed))
    system = job + (f' {variant}' if variant else '')

    effects = [steps_cat[s]['broke'] for s in failed if s in steps_cat and steps_cat[s].get('broke')]
    headline = and_join(effects) if effects else entry['broke']
    subject = _subject(f"{headline} ({system}, {pt(started)})")

    if steps:
        count = f"{len(failed)} of {len(steps)} steps failed."
    elif failed:
        count = f"{len(failed)} step(s) failed."
    else:
        count = clean(condition) if condition else 'It failed.'
    if not ended:
        count += ' It has logged errors for 15 minutes and has not finished.'
    lines = [f"What broke: {entry['name']}, run {execution} of {system}, started {pt(started)}. {count}"]
    shown = 0
    for step, text in errors[:MAX_LINES]:
        lines.append(f" - {step}: {text}" if step else f" - {text}")
        shown += 1
    if not shown and condition:
        lines.append(f" - {clean(condition)}")
    if ok:
        lines.append(f"Still fine: {', '.join(ok)}")
    elif also:
        lines.append(also)

    whos = [f"{steps_cat[s]['who']} ({s})" for s in failed if s in steps_cat and steps_cat[s].get('who')]
    lines.append(f"Who's affected: {'; '.join(whos) if whos else entry['who']}")

    if not ended:
        status = 'still running'
    elif recovered:
        status = f'already recovered ({pt(recovered)})'
    else:
        status = 'still broken'
    if last_good:
        good = pt(last_good)
    elif oldest:
        good = f'none on record (back to {pt(oldest)})'
    else:
        good = 'not known (the run history could not be read)'
    lines.append(f"Status: {status}. Last good run: {good}")

    todos = [f"{s}: {steps_cat[s]['todo']}" for s in failed if s in steps_cat and steps_cat[s].get('todo')]
    todo = ' '.join(todos) if todos else entry['todo']
    nxt = entry.get('next')
    if nxt and not recovered:
        todo += f" Otherwise it runs again {nxt}."
    lines.append(f"What to do: {todo}")
    return subject, '\n'.join(lines)


def job_log_link(job: str, execution: str) -> str:
    """The execution's logs page in the console."""
    return (f"{_CFG['console']}/run/jobs/executions/details/{_CFG['region']}/{execution}/logs?project={_CFG['project']}")


def handle_job_end(ctx: Context, job: str, execution: str, command, args, ok: bool, started, ended,
                   condition: str = '', link: str = '') -> str:
    """One execution's end: a failure's message, a success's Fixed, or nothing (a smoke test or a hand
    command, or a duplicate). What it did, for the log."""
    incidents, pending = _CFG['incidents'], _CFG['pending']
    variant = variant_of(command, args)
    held = ctx.store.get(pending, execution)
    if variant is None:
        if held:
            ctx.store.delete(pending, execution)
        return f'{execution}: a smoke test or hand command, not an alert'
    key = system_key('job', job, variant)
    system = job + (f' {variant}' if variant else '')
    started = parse_ts(started) or ctx.now
    if ok:
        if held:
            ctx.store.delete(pending, execution)
        inc = ctx.store.get(incidents, key)
        if not (inc and inc.get('open')):
            return f'{execution}: passed'
        failed = list(inc.get('failed_steps') or [])
        only = only_steps(args)
        if failed and only:
            fixed = [s for s in failed if s in only]
            left = [s for s in failed if s not in only]
            if fixed and left:
                ctx.tell(_subject(f"Partly fixed: {system} ({pt(ended or ctx.now)})"),
                         f"Fixed: {', '.join(fixed)} passed in run {execution} at {pt(ended or ctx.now)}. "
                         f"Still failing: {', '.join(left)}.", job_log_link(job, execution))
                ctx.store.set(incidents, key, dict(inc, failed_steps=left, last_at=iso(ctx.now)))
                return f'{execution}: partly fixed'
            if not fixed:
                return f'{execution}: passed, but ran none of the failed steps'
        ctx.tell(_subject(f"Fixed: {system} ({pt(ended or ctx.now)})"),
                 f"Fixed: {system} run {execution} at {pt(started)} passed. It had failed since "
                 f"{pt(inc['first_at'])} ({inc.get('count', 1)} failed run(s)).", job_log_link(job, execution))
        ctx.store.set(incidents, key, dict(inc, open=False, closed_at=iso(ctx.now)))
        return f'{execution}: Fixed sent'
    if held and held.get('sent'):
        return f'{execution}: already told'
    try:
        entries = ctx.google.execution_entries(job, execution, started - timedelta(minutes=5))
    except Exception as e:   # noqa: BLE001 - the held lines still say something
        _log().warning(f'alert mailer: {execution} lines not read ({type(e).__name__})')
        entries = (held or {}).get('entries') or []
    summary = summarize(entries)
    last_good, recovered, oldest = job_history(ctx, job, variant, started)
    subject, body = compose_job(job, variant, execution, started, summary, condition, last_good, recovered,
                                oldest, link, also=others_open(ctx, key), ended=ended is not None)
    ctx.tell(subject, body, link or job_log_link(job, execution))
    ctx.store.set(pending, execution, {'kind': 'job', 'job': job, 'sent': True, 'at': iso(ctx.now)})
    inc = ctx.store.get(incidents, key)
    failed = in_step_order(job, [s for s, c in (summary.get('steps') or {}).items() if c != 0]) or \
        list(dict.fromkeys(s for s, _ in summary['errors'] if s))
    if inc and inc.get('open'):
        inc = dict(inc, last_at=iso(ctx.now), count=int(inc.get('count', 1)) + 1, execution=execution,
                   failed_steps=in_step_order(job, sorted(set(inc.get('failed_steps') or []) | set(failed))),
                   body=redact(body), link=link or job_log_link(job, execution))
    else:
        inc = {'open': True, 'kind': 'job', 'label': system, 'first_at': iso(started), 'last_at': iso(ctx.now),
               'count': 1, 'execution': execution, 'failed_steps': failed, 'subject': redact(subject),
               'body': redact(body), 'link': link or job_log_link(job, execution)}
    if recovered:
        inc = dict(inc, open=False, closed_at=iso(ctx.now))
    ctx.store.set(incidents, key, inc)
    return f'{execution}: told ({subject})'


def on_job_event(ctx: Context, entry: dict) -> str:
    """A Cloud Run Job's audit entry for an execution's end."""
    proto = entry.get('protoPayload') or {}
    resp = proto.get('response') or {}
    job = (entry.get('resource') or {}).get('labels', {}).get('job_name', '')
    execution = str(proto.get('resourceName', '')).rsplit('/', 1)[-1]
    status = resp.get('status') or {}
    message = (proto.get('status') or {}).get('message', '')
    ok = 'completed successfully' in message or (int(status.get('succeededCount') or 0) >= 1 and not status.get('failedCount'))
    failed = 'failed' in message or int(status.get('failedCount') or 0) >= 1
    if not (ok or failed):
        return f'{execution}: not an end ({message[:80]})'
    cond = next((c.get('message', '') for c in status.get('conditions', []) if c.get('type') == 'Completed'), '')
    command, args = container(resp)
    started = (resp.get('metadata') or {}).get('creationTimestamp') or entry.get('timestamp')
    ended = parse_ts(status.get('completionTime') or entry.get('timestamp'))
    return handle_job_end(ctx, job, execution, command, args, ok and not failed, started, ended,
                          condition=cond or message, link=status.get('logUri', ''))


def on_job_line(ctx: Context, entry: dict) -> str:
    """A job's ERROR line before its end: held under its execution, never sent on its own."""
    pending = _CFG['pending']
    execution = (entry.get('labels') or {}).get('run.googleapis.com/execution_name', '')
    job = (entry.get('resource') or {}).get('labels', {}).get('job_name', '')
    if not execution:
        return 'a job line with no execution: ignored'
    if execution in _JOB_FULL:   # SMADPickleBot #676: its hold is full or told, so this line would change nothing
        return f'{execution}: held in full'
    held = ctx.store.get(pending, execution) or {'kind': 'job', 'job': job, 'sent': False,
                                                 'first_at': iso(parse_ts(entry.get('timestamp')) or ctx.now),
                                                 'entries': []}
    if held.get('sent'):
        _JOB_FULL.add(execution)
        return f'{execution}: already told'
    kept = held.get('entries') or []
    if len(kept) >= 20:   # full already (this instance restarted since): nothing to write
        _JOB_FULL.add(execution)
        return f'{execution}: held in full'
    kept.append({'severity': entry.get('severity'), 'jsonPayload': {k: v for k, v in (entry.get('jsonPayload') or {}).items()
                                                                   if k in ('message', 'step', 'steps', 'code')},
                 'textPayload': entry.get('textPayload')})
    if len(kept) >= 20:
        _JOB_FULL.add(execution)
    ctx.store.set(pending, execution, dict(held, entries=kept))
    return f'{execution}: held'


# ---------------------------------------------------------------------------------------------------------
# Cloud Scheduler and the services: debounced incidents
# ---------------------------------------------------------------------------------------------------------

def _rollup_body(entry: dict, inc: dict, what: str, also: str, status: str, good: str) -> str:
    lines = [what] + [f" - {line}" for line in (inc.get('lines') or [])[-MAX_LINES:]]
    if also:
        lines.append(also)
    lines.append(f"Who's affected: {entry['who']}")
    lines.append(f"Status: {status}. Last good run: {good}")
    lines.append(f"What to do: {entry['todo']}")
    return '\n'.join(lines)


def fold(ctx: Context, key: str, at: datetime, line: str, revision: str = '') -> str:
    """A repeat ERROR of an incident counted in memory (SMADPickleBot #676): within FOLD_SECONDS of a counted write
    (debounced made the fold), the line's count, its cleaned text, its time and its revision are kept here and the
    message answered at once, with no store read; the first message after FOLD_SECONDS takes the normal path, which
    writes them in. The line said, or '' when this message takes the normal path (no fold, or one too old)."""
    f = _FOLD.get(key)
    if not f or not 0 <= (ctx.now - f['since']).total_seconds() < FOLD_SECONDS:
        return ''
    f['n'] += 1
    f['lines'] = (f['lines'] + [clean(line)])[-MAX_LINES:]
    f['last_at'] = max(f['last_at'], at)
    if revision:
        f['revision'] = revision
    return f'{key}: folded ({f["n"]} in memory since {pt(f["since"])})'


def debounced(ctx: Context, key: str, kind: str, name: str, entry: dict, line: str, at: datetime,
              good: str, link: str, revision: str = '') -> str:
    """An error for a schedule or a service: sent at once when nothing is open or the last message is 30
    minutes old, else counted for the next one. A service's error names its revision, kept on the incident so
    the sweep can tell a fix (a new revision serving) from a pause. A held schedule's (on_scheduler) attempts are
    the first message's, its first attempt the incident's start; `line` None tells the held ones."""
    incidents = _CFG['incidents']
    inc = ctx.store.get(incidents, key)
    held = inc if inc and inc.get('open') and inc.get('held') else None
    line = clean(line) if line is not None else None
    folded = _FOLD.pop(key, None)   # SMADPickleBot #676: what fold() counted in memory since the last write
    if inc and inc.get('open') and not held:
        extra = folded['n'] if folded else 0
        last = max(at, folded['last_at']) if folded and folded['n'] else at
        lines = ((inc.get('lines') or []) + ((folded or {}).get('lines') or []) + [line])[-MAX_LINES:]
        revision = revision or ((folded or {}).get('revision') or '')
        inc = dict(inc, last_at=iso(last), count=int(inc.get('count', 1)) + 1 + extra,
                   unsent=int(inc.get('unsent', 0)) + 1 + extra,
                   lines=lines, last_line=line, **({'revision': revision} if revision else {}))
        if at - parse_ts(inc['last_sent_at']) < DEBOUNCE:
            ctx.store.set(incidents, key, inc)
            if kind == 'service':   # its next repeats fold in memory for FOLD_SECONDS (fold)
                _FOLD[key] = {'since': ctx.now, 'n': 0, 'lines': [], 'last_at': at, 'revision': ''}
            return f'{key}: counted ({inc["unsent"]} since the last message)'
        return send_rollup(ctx, key, inc, link)
    first = parse_ts(held['first_at']) if held else at
    lines = ((held or {}).get('lines') or []) + ([line] if line is not None else [])
    count = len(lines) if held else 1
    if kind != 'schedule':
        what = f"What broke: {entry['name']} ({name}) logged an error at {pt(at)}."
    elif not held:
        what = f"What broke: {entry['name']} ({name}) could not start its run at {pt(at)}."
    elif line is not None:
        what = (f"What broke: {entry['name']} ({name}) could not start its run at {pt(first)}, and its retry at "
                f"{pt(at)} failed too.")
    else:
        what = (f"What broke: {entry['name']} ({name}) could not start its run at {pt(first)}, and its retry had not "
                f"answered by {pt(ctx.now)}.")
    previous = held.get('previous_closed_at') if held else (inc or {}).get('closed_at')
    inc = {'open': True, 'kind': kind, 'label': name, 'first_at': iso(first), 'last_at': iso(at), 'count': count,
           'unsent': 0, 'lines': lines[-MAX_LINES:], 'last_line': lines[-1] if lines else '', 'last_sent_at': iso(ctx.now),
           'link': link, 'previous_closed_at': previous, **({'revision': revision} if revision else {})}
    status = ('still broken' if kind == 'schedule'
              else "still broken (a 'Fixed' follows after 30 minutes without an error)")
    body = _rollup_body(entry, inc, what, others_open(ctx, key), status, good)
    subject = _subject(f"{entry['broke']} ({name}, {pt(first)})")
    ctx.tell(subject, body, link)
    # What was told, kept for an issue filer.
    ctx.store.set(incidents, key, dict(inc, subject=redact(subject), body=redact(body)))
    return f'{key}: told'


def send_rollup(ctx: Context, key: str, inc: dict, link: str = '') -> str:
    """The errors counted since the last message, as one 'still failing' message."""
    kind, name = inc['kind'], inc['label']
    entry = (_need('schedule_entry')(name) if kind == 'schedule'
             else _need('service_entry')(name, inc.get('last_line', '')))
    what = (f"What broke: {entry['name']} ({name}) failed {inc['unsent']} more time(s) since the last message "
            f"at {pt(inc['last_sent_at'])}; {inc['count']} in all since {pt(inc['first_at'])}, the latest at "
            f"{pt(inc['last_at'])}.")
    body = _rollup_body(entry, inc, what, '', 'still broken', f"before {pt(inc['first_at'])}")
    ctx.tell(_subject(f"still failing: {entry['broke']} ({name}, {pt(inc['last_at'])})"), body, link or inc.get('link', ''))
    ctx.store.set(_CFG['incidents'], key, dict(inc, unsent=0, last_sent_at=iso(ctx.now)))
    return f'{key}: roll-up told'


def schedule_passed(ctx: Context, key: str, inc: dict, job_id: str, at: datetime, link: str = '') -> str:
    """An open schedule incident whose schedule ran well at `at`: closed. A held one (its own retry passed) tells
    nobody, since nothing was told; a told one gets its Fixed."""
    incidents = _CFG['incidents']
    if inc.get('held'):
        # The schedule's own retry did its job: nothing was told, so nothing is fixed in anyone's inbox.
        ctx.store.set(incidents, key, dict(inc, open=False, held=False, closed_at=iso(ctx.now), retried_ok=iso(at)))
        _log().info(f'alert mailer: {job_id}: its retry at {pt(at)} passed after the failure at '
                    f'{pt(inc["first_at"])}; nothing told')
        return f'{job_id}: retry passed, nothing told'
    ctx.tell(_subject(f"Fixed: schedule {job_id} ({pt(at)})"),
             f"Fixed: schedule {job_id} started its run at {pt(at)}. It had failed since {pt(inc['first_at'])} "
             f"({inc.get('count', 1)} failed attempt(s)).", link)
    ctx.store.set(incidents, key, dict(inc, open=False, closed_at=iso(ctx.now)))
    return f'{job_id}: Fixed sent'


def passed_since(ctx: Context, inc: dict):
    """When an open schedule incident's schedule last ran well after its last failure, from the attempt history; None
    when it has not, or the history cannot be read (the next sweep asks again). The sweep's way to a schedule's Fixed
    (SMADPickleBot #1395: a sink that delivers every success costs an invocation and an incident read for each)."""
    last = parse_ts(inc.get('last_at'))
    try:
        ok = ctx.google.last_ok_attempt(inc['label'], ctx.now)
    except Exception as e:   # noqa: BLE001 - an unread history never closes or holds an incident
        _log().warning(f"alert mailer: {inc.get('label')} attempts not read ({type(e).__name__}); asked again at the next sweep")
        return None
    return ok if ok and last and ok > last else None


def on_scheduler(ctx: Context, entry: dict) -> str:
    """A Cloud Scheduler AttemptFinished: a failure's message (debounced). A success reaches this only when one is
    still delivered; the sweep closes an open incident from the attempt history (passed_since)."""
    incidents = _CFG['incidents']
    job_id = (entry.get('resource') or {}).get('labels', {}).get('job_id', '')
    jp = entry.get('jsonPayload') or {}
    at = parse_ts(entry.get('timestamp')) or ctx.now
    key = system_key('schedule', job_id)
    failed = str(entry.get('severity', '')) in ('ERROR', 'CRITICAL', 'ALERT', 'EMERGENCY') or bool(jp.get('status'))
    link = f"{_CFG['console']}/cloudscheduler/jobs/edit/{_CFG['region']}/{job_id}?project={_CFG['project']}"
    if not failed:
        # A success still delivered (a sink that leaves them out, with one in flight, or a sink not yet applied).
        inc = ctx.store.get(incidents, key)
        if not (inc and inc.get('open')):
            return f'{job_id}: ok'
        return schedule_passed(ctx, key, inc, job_id, at, link)
    entry_cat = _need('schedule_entry')(job_id)
    target = str(jp.get('url') or jp.get('targetType') or '')
    target = re.sub(r'^https://run\.googleapis\.com/v2/projects/[^/]+/locations/[^/]+/jobs/([^:]+):run$',
                    r'the Cloud Run Job \1', target)
    line = ' '.join(p for p in (f"{jp.get('status') or 'refused'}:", str(jp.get('debugInfo') or ''),
                                 f'(target: {target})' if target else '') if p)
    try:
        last = ctx.google.last_ok_attempt(job_id, at)
        good = pt(last) if last else 'none in the last 30 days'
    except Exception as e:   # noqa: BLE001
        _log().warning(f'alert mailer: {job_id} attempts not read ({type(e).__name__})')
        good = 'not known (the attempt history could not be read)'
    inc = ctx.store.get(incidents, key)
    if inc and inc.get('open') and inc.get('kind') == 'schedule':
        # A pass between the last failure and this one, which nothing delivered: the open incident ends first (a held
        # one silently, a told one with its Fixed), so this failure is a new one, never "its retry failed too".
        ok = passed_since(ctx, inc)
        if ok and ok < at:
            schedule_passed(ctx, key, inc, job_id, ok, link)
            inc = ctx.store.get(incidents, key)
    if not (inc and inc.get('open')):
        window = retry_window(ctx, job_id)
        if window:
            ctx.store.set(incidents, key, {
                'open': True, 'held': True, 'kind': 'schedule', 'label': job_id, 'first_at': iso(at), 'last_at': iso(at),
                'count': 1, 'lines': [clean(line)], 'last_line': clean(line), 'hold_until': iso(at + window),
                'good': good, 'link': link, 'previous_closed_at': (inc or {}).get('closed_at')})
            return f'{job_id}: held for its retry until {pt(at + window)}'
    return debounced(ctx, key, 'schedule', job_id, entry_cat, line, at, good, link)


def _seconds(text) -> float:
    """A Scheduler duration ('60s', '5.500s') in seconds; 0 for nothing."""
    m = re.match(r'^\s*([\d.]+)s\s*$', str(text or ''))
    return float(m.group(1)) if m else 0.0


def retry_window(ctx: Context, job_id: str):
    """How long a failed attempt is held for the schedule's own retry: its first backoff and RETRY_SLACK. None
    (told at once) for a schedule without retries, or when its settings cannot be read."""
    try:
        rc = ctx.google.retry_config(job_id)
    except Exception as e:   # noqa: BLE001 - an unread setting never holds an alert back
        _log().warning(f'alert mailer: {job_id} retry settings not read ({type(e).__name__}); told at once')
        return None
    if int(rc.get('retryCount') or 0) <= 0:
        return None
    return timedelta(seconds=_seconds(rc.get('minBackoffDuration'))) + RETRY_SLACK


def release_hold(ctx: Context, key: str, inc: dict) -> str:
    """A held schedule whose retry never answered: its first message, from the sweep."""
    name = inc['label']
    return debounced(ctx, key, 'schedule', name, _need('schedule_entry')(name), None, parse_ts(inc['last_at']),
                     inc.get('good') or 'not known', inc.get('link', ''))


def on_task(ctx: Context, entry: dict) -> str:
    """The host's queue's last refused attempt (an ERROR attemptResponseLog with no retryTime): one message and one
    incident (kind `task_kind`) per task, told once, the action and what it was for read from the task's name by the
    host's `describe_task`. Cloud Tasks has deleted the task, so nothing says Fixed: the incident leaves "Also open"
    after STALE_HOURS (stale)."""
    incidents = _CFG['incidents']
    jp = entry.get('jsonPayload') or {}
    log = jp.get('attemptResponseLog') or {}
    task = str(jp.get('task') or '').rsplit('/', 1)[-1]
    refused = (str(entry.get('severity', '')) in ('ERROR', 'CRITICAL', 'ALERT', 'EMERGENCY')
               or str(log.get('status') or 'OK') != 'OK')
    if not log or log.get('retryTime') or not refused:
        return f'{task or "a task"}: not a last refused attempt: ignored'
    key = system_key(_CFG['task_kind'], task)
    if ctx.store.get(incidents, key):
        return f'{key}: already told'
    at = parse_ts(entry.get('timestamp')) or ctx.now
    describe = _CFG['describe_task']
    action, whose = describe(task) if describe else ('', '')
    cat = _need('task_entry')(action)
    target = re.sub(r'^POST https://run\.googleapis\.com/v2/projects/[^/]+/locations/[^/]+/jobs/([^:]+):run$',
                    r'the Cloud Run Job \1', str(log.get('targetAddress') or ''))
    line = clean(f"{log.get('status') or 'refused'}: {target or 'its job'} would not start "
                 f"({log.get('dispatchCount') or '?'} attempt(s))")
    link = f"{_CFG['console']}/cloudtasks/queue/{_CFG['region']}/{_CFG['task_queue']}?project={_CFG['project']}"
    body = '\n'.join([f"What broke: {cat['name']} ({task}{', ' + whose if whose else ''}) could not start its job: "
                      f"Cloud Tasks gave up at {pt(at)}.", f" - {line}", others_open(ctx, key),
                      f"Who's affected: {cat['who']}",
                      'Status: not run, and it will not run by itself (Cloud Tasks deleted the task after its last '
                      'attempt).',
                      f"What to do: {cat['todo']}"])
    subject = _subject(f"{cat['broke']} ({task}, {pt(at)})")
    ctx.tell(subject, body, link)
    # Kept as an incident, so its event can file an issue.
    ctx.store.set(incidents, key, {
        'open': True, 'kind': _CFG['task_kind'], 'label': task, 'first_at': iso(at), 'last_at': iso(at),
        'count': 1, 'unsent': 0, 'lines': [line], 'last_line': line, 'last_sent_at': iso(ctx.now), 'link': link,
        'subject': redact(subject), 'body': redact(body)})
    return f'{key}: told'


def on_service(ctx: Context, entry: dict) -> str:
    """An ERROR line from a Cloud Run service or function. A 5xx Cloud Run wrote for a call Cloud Scheduler made is that
    schedule's failed attempt, which its own AttemptFinished tells (held for its retry, on_scheduler), so the request
    line tells nobody (SMADPickleBot #1469)."""
    labels = (entry.get('resource') or {}).get('labels', {})
    service = labels.get('service_name') or labels.get('function_name') or 'unknown'
    http = entry.get('httpRequest') or {}
    if SCHEDULER_AGENT in str(http.get('userAgent') or '') and str(http.get('status') or '').startswith('5'):
        return f"{service}: a {http.get('status')} to Cloud Scheduler's call: its schedule's attempt tells it"
    at = parse_ts(entry.get('timestamp')) or ctx.now
    line = entry_text(entry)
    # An error the catalog tells apart by its text (a line rule) is its own incident, never folded into another.
    rule = _need('line_rule')(service, line)
    key = system_key('service', service) if rule is None else system_key('service', service, f'rule{rule}')
    folded = fold(ctx, key, at, line, labels.get('revision_name', ''))
    if folded:
        return folded
    inc = ctx.store.get(_CFG['incidents'], key)
    prev = (inc or {}).get('closed_at') if not (inc and inc.get('open')) else None
    good = f'no errors from {pt(prev)} until this one' if prev else 'no earlier incident on record'
    link = f"{_CFG['console']}/run/detail/{_CFG['region']}/{service}/logs?project={_CFG['project']}"
    return debounced(ctx, key, 'service', service, _need('service_entry')(service, line), line, at, good, link,
                     revision=labels.get('revision_name', ''))


# ---------------------------------------------------------------------------------------------------------
# Cloud Build
# ---------------------------------------------------------------------------------------------------------

def compose_build(build: dict, errors: list, last_good, also: str = '') -> tuple:
    """(subject, body) for a failed build: its failing step, the steps that passed, its ERROR lines."""
    tag = (build.get('tags') or [''])[0]
    entry = _need('build_entry')(tag)
    bid = str(build.get('id', ''))
    when = build.get('finishTime') or build.get('createTime') or instant_now()
    steps = build.get('steps') or []
    bad = [s.get('id') or s.get('name', '?') for s in steps if s.get('status') in FAILED_BUILD]
    ok = [s.get('id') or s.get('name', '?') for s in steps if s.get('status') == 'SUCCESS']
    detail = clean(((build.get('failureInfo') or {}).get('detail')) or build.get('statusDetail') or build.get('status', ''))
    label = f"Cloud Build {tag or 'deploy'}"
    subject = _subject(f"{entry['broke']} ({label}, {pt(when)})")
    lines = [f"What broke: {entry['name']} (build {bid[:8]}, {pt(when)}). "
             f"{('Step ' + ', '.join(bad) + ' failed: ') if bad else ''}{detail}"]
    for text in list(dict.fromkeys(clean(e) for e in errors))[:MAX_LINES]:
        lines.append(f" - {text}")
    lines.append(f"Still fine: {', '.join(ok)}" if ok else (also or 'Still fine: no step passed'))
    lines.append(f"Who's affected: {entry['who']}")
    lines.append(f"Status: still broken until the next build. Last good run: "
                 f"{pt(last_good) if last_good else 'none among the recent builds'}")
    lines.append(f"What to do: {entry['todo']}")
    return subject, '\n'.join(lines)


def handle_build(ctx: Context, build_id: str, line: str = '') -> str:
    """A build's ERROR line or the sweep's look: one message per failed build, nothing for one that passed."""
    pending = _CFG['pending']
    key = f'build-{build_id}'
    held = ctx.store.get(pending, key)
    if held and held.get('sent'):
        return f'{key}: already told or passed'
    try:
        build = ctx.google.build(build_id)
    except Exception as e:   # noqa: BLE001 - an unreadable build must not hold its alert back
        _log().warning(f'alert mailer: build {build_id} not read ({type(e).__name__}: {e}); told from its line alone')
        build = {'id': build_id, 'status': 'FAILURE', 'statusDetail': clean(line) if line else
                 'the build could not be read, so its steps are not known'}
    status = build.get('status', '')
    if status == 'SUCCESS' or status == 'CANCELLED':
        ctx.store.set(pending, key, {'kind': 'build', 'sent': True, 'at': iso(ctx.now), 'status': status})
        return f'{key}: {status.lower()}, nothing to tell'
    if status not in FAILED_BUILD:
        if not held:
            ctx.store.set(pending, key, {'kind': 'build', 'build': build_id, 'sent': False, 'first_at': iso(ctx.now)})
        return f'{key}: {status.lower() or "unknown"}, held'
    try:
        errors = ctx.google.build_errors(build_id)
    except Exception as e:   # noqa: BLE001
        _log().warning(f'alert mailer: build {build_id} lines not read ({type(e).__name__})')
        errors = [line] if line else []
    tag = (build.get('tags') or [''])[0]
    try:
        last_good = ctx.google.last_ok_build(tag, parse_ts(build.get('createTime')) or ctx.now)
    except Exception as e:   # noqa: BLE001
        _log().warning(f'alert mailer: earlier builds not read ({type(e).__name__})')
        last_good = None
    subject, body = compose_build(build, errors, last_good)
    ctx.tell(subject, body, build.get('logUrl', ''))
    ctx.store.set(pending, key, {'kind': 'build', 'sent': True, 'at': iso(ctx.now), 'status': status})
    return f'{key}: told'


# ---------------------------------------------------------------------------------------------------------
# The sweep (every 15 minutes) and the dispatcher
# ---------------------------------------------------------------------------------------------------------

def same_revision(ctx: Context, inc: dict) -> bool:
    """True when the revision that logged the incident's errors is still serving: a quiet spell, not a fix. False
    when a new revision took over, when the incident names none, or when the service cannot be read (then the
    old 'Fixed' stands)."""
    rev = inc.get('revision')
    if not rev:
        return False
    try:
        return rev in ctx.google.serving_revisions(inc['label'])
    except Exception as e:   # noqa: BLE001 - an unreadable service keeps the old behavior
        _log().warning(f"alert mailer: {inc['label']}'s serving revision not read ({type(e).__name__})")
        return False


def flush_folds(ctx: Context) -> list:
    """Every fold holding counts written into its open incident before the sweep reads them (SMADPickleBot #676): the
    sweep runs in the same one instance, so a flood that stopped within a fold's minute is still counted in full. A
    fold whose incident is no longer open is dropped. The lines said."""
    incidents = _CFG['incidents']
    said = []
    for key in list(_FOLD):
        f = _FOLD.pop(key)
        if not f['n']:
            continue
        inc = ctx.store.get(incidents, key)
        if not (inc and inc.get('open')) or inc.get('held'):
            continue
        lines = ((inc.get('lines') or []) + f['lines'])[-MAX_LINES:]
        ctx.store.set(incidents, key, dict(inc, count=int(inc.get('count', 1)) + f['n'],
                                           unsent=int(inc.get('unsent', 0)) + f['n'], lines=lines,
                                           last_line=f['lines'][-1] if f['lines'] else inc.get('last_line', ''),
                                           last_at=iso(max(f['last_at'], parse_ts(inc.get('last_at')) or f['last_at'])),
                                           **({'revision': f['revision']} if f['revision'] else {})))
        said.append(f'{key}: {f["n"]} folded error(s) written in')
    return said


def sweep(ctx: Context) -> list:
    """Quiet services' Fixed, counted errors' roll-ups, held job lines whose end never came, pending
    builds, and old holds cleared. What it did."""
    incidents, pending = _CFG['incidents'], _CFG['pending']
    done = [line for line in flush_folds(ctx) if line]
    for key, inc in ctx.store.all(incidents):
        if not inc.get('open') or inc.get('kind') not in ('service', 'schedule'):
            continue
        last = parse_ts(inc.get('last_at'))
        try:
            if inc['kind'] == 'schedule':
                # A pass first: a held one whose retry passed tells nobody, a told one gets its Fixed.
                ok = passed_since(ctx, inc)
                if ok:
                    done.append(schedule_passed(ctx, key, inc, inc['label'], ok, inc.get('link', '')))
                    continue
            if inc.get('held'):
                if ctx.now >= parse_ts(inc['hold_until']):
                    done.append(release_hold(ctx, key, inc))
            elif inc['kind'] == 'service' and last and ctx.now - last >= QUIET:
                extra = (f" {inc['unsent']} more error(s) after the last message, the latest: {inc.get('last_line', '')}"
                         if inc.get('unsent') else '')
                if same_revision(ctx, inc):
                    # A quiet spell between two requests is no fix while the revision that failed still serves.
                    ctx.tell(_subject(f"quiet, not fixed: {inc['label']} ({pt(ctx.now)})"),
                             f"Quiet: {inc['label']} has logged no error for 30 minutes since {pt(last)}, but the revision "
                             f"that failed ({inc['revision']}) is still the one serving, so nothing has been fixed: the cause "
                             f"is likely still there and the next request that reaches it fails again. It had "
                             f"{inc.get('count', 1)} error(s) since {pt(inc['first_at'])}.{extra}", inc.get('link', ''))
                    done.append(f'{key}: quiet sent (same revision)')
                else:
                    ctx.tell(_subject(f"Fixed: {inc['label']} ({pt(ctx.now)})"),
                             f"Fixed: {inc['label']} has logged no error for 30 minutes since {pt(last)}. It had "
                             f"{inc.get('count', 1)} error(s) since {pt(inc['first_at'])}.{extra}", inc.get('link', ''))
                    done.append(f'{key}: Fixed sent')
                ctx.store.set(incidents, key, dict(inc, open=False, closed_at=iso(last)))
            elif inc.get('unsent') and ctx.now - parse_ts(inc['last_sent_at']) >= DEBOUNCE:
                done.append(send_rollup(ctx, key, inc))
        except Exception as e:   # noqa: BLE001 - one incident never stops the rest; the next sweep tries again
            _log().error(f'alert mailer: sweep could not tell {key}: {type(e).__name__}: {e}')
    for key, held in ctx.store.all(pending):
        try:
            at = parse_ts(held.get('at') or held.get('first_at')) or ctx.now
            if held.get('sent'):
                if ctx.now - at >= KEEP_PENDING:
                    ctx.store.delete(pending, key)
                continue
            if held.get('kind') == 'build':
                # A build's ERROR lines come before its status is final; each sweep looks again.
                done.append(handle_build(ctx, held.get('build') or key[len('build-'):]))
                continue
            if ctx.now - at < END_WAIT:
                continue
            done.append(sweep_job(ctx, key, held))
        except Exception as e:   # noqa: BLE001
            _log().error(f'alert mailer: sweep could not finish {key}: {type(e).__name__}: {e}')
    return done


def sweep_job(ctx: Context, execution: str, held: dict) -> str:
    """Held job lines 15 minutes old with no end seen: the execution as the Run Admin API has it, finished
    or not."""
    job = held.get('job', '')
    try:
        x = ctx.google.execution(job, execution)
    except Exception as e:   # noqa: BLE001
        _log().warning(f'alert mailer: {execution} not read ({type(e).__name__}); telling what is held')
        x = {}
    command, args = container(x)
    done = parse_ts(x.get('completionTime'))
    ok = int(x.get('succeededCount') or 0) >= 1 and not int(x.get('failedCount') or 0)
    cond = next((c.get('message', '') for c in x.get('conditions', []) if c.get('type') == 'Completed'), '')
    started = x.get('createTime') or held.get('first_at')
    if not x:
        args = ['(unknown)']
    return handle_job_end(ctx, job, execution, command, args, ok and done is not None, started, done,
                          condition=cond, link=x.get('logUri', ''))


# ---------------------------------------------------------------------------------------------------------
# Cloud Monitoring incidents (the dead-letter policies)
# ---------------------------------------------------------------------------------------------------------

def _incident_topic(inc: dict) -> str:
    """The topic an incident is about: its resource's topic_id, else the condition's "published to <topic>"."""
    topic = (((inc.get('resource') or {}).get('labels') or {}).get('topic_id') or '').strip()
    if topic:
        return topic
    m = re.search(r'published to (\S+)', str(inc.get('condition_name') or ''))
    return m.group(1) if m else ''


def _count(value) -> str:
    """Monitoring's observed value ('1.000') as a count, or '' when it says none."""
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return ''
    return str(n) if n > 0 else ''


def given_up(rows: list, since: datetime) -> dict:
    """What a dead-letter incident's messages were, from the messages waiting (Google.dead_letters): each moved to the
    queue since `since` described (the host's `describe_dead_letter`), in the order they were moved there, a run's step
    requests of one run and day on one line; older ones only counted. {'headline': the subject's words ('' when none
    was read), 'lines': the body's, 'older': how many older ones wait}."""
    describe = _need('describe_dead_letter')
    steps, others, older, oldest = {}, {}, 0, None
    for r in sorted(rows or [], key=lambda r: str(r.get('published') or '')):
        moved = parse_ts(r.get('published'))
        if moved is not None and moved < since:
            older += 1
            oldest = moved if oldest is None or moved < oldest else oldest
            continue
        item = describe(r.get('payload'), r.get('attributes'))
        if 'step' in item:
            got = steps.setdefault((item['run'], item['day']), {'steps': [], 'sources': set()})
            if item['step'] not in got['steps']:
                got['steps'].append(item['step'])
            got['sources'].add(item['source'])
        else:
            got = others.setdefault(item['what'], {'n': 0, 'sources': set()})
            got['n'] += 1
            got['sources'].add(item['source'])

    def by(sources):
        named = sorted(s for s in sources if s)
        return f" (refused by {and_join(named)})" if named else ''

    heads, lines = [], []
    for (run, day), got in steps.items():
        names = got['steps']
        said = f"the {and_join(names)} step{'s' if len(names) > 1 else ''} of {run} for {day}"
        heads.append(f'{said} did not run')
        lines.append(f' - {said}: never ran{by(got["sources"])}')
    whats = list(others)
    for what in whats[:MAX_GIVEN_UP]:
        n = others[what]['n']
        lines.append(f" - {what}{f' ({n} messages)' if n > 1 else ''}{by(others[what]['sources'])}")
    if len(whats) > MAX_GIVEN_UP:
        lines.append(f' - and {len(whats) - MAX_GIVEN_UP} more kinds of message')
    if whats:
        total = sum(o['n'] for o in others.values())
        heads.append(f"{and_join(whats[:3]) if len(whats) <= 3 else ', '.join(whats[:3]) + ' and more'} "
                     f"{'was' if total == 1 else 'were'} given up on")
    if older:
        lines.append(f" - and {older} older message{'s' if older > 1 else ''} still waiting, the oldest moved there "
                     f"{pt(oldest)}")
    return {'headline': '; '.join(heads), 'lines': lines, 'older': older}


def compose_dead_letter(topic: str, count: str, at: datetime, given: dict = None, unread: str = '') -> tuple:
    """(subject, body, link) for messages moved to a dead-letter topic: what they were (given_up), the queue, the
    count, who it affects and the host's command lines that list, replay and drop them (`dead_letter_command`).
    `unread` is why the messages could not be read, said in the body; nothing read keeps the queue's own words."""
    entry = _need('dead_letter_entry')(topic)
    many = bool(count) and count != '1'
    what = (f"What broke: {count or 'A'} message{'s' if many else ''} {'were' if many else 'was'} given up on and "
            f"moved to {topic or 'a dead-letter topic'} in the 5 minutes to {pt(at)}: {entry['name']} refused "
            f"{'them' if many else 'it'} on every delivery.")
    template = _CFG['dead_letter_command']
    cmd = template.format(queue=entry['queue']) if template and entry.get('queue') else ''
    lines = [what]
    if unread:
        lines.append(f"What they were: not read here ({unread})" + ("; the See it line below lists them." if cmd else "."))
    elif given and given.get('lines'):
        lines += ['What they were:'] + given['lines']
    lines += [f"Who's affected: {entry['who']}",
              f"Status: waiting on {topic or 'the dead-letter topic'} for a week; nothing retries it by itself.",
              f"What to do: {entry['todo']}"]
    if cmd:
        lines += [f" - See it: `{cmd} list` (each message, its kind and the last reason)",
                  f" - Once the cause is fixed, put it back: `{cmd} replay <message id>` (or `replay --all`); "
                  f"drop it: `{cmd} drop <message id>`"]
    headline = (given or {}).get('headline') or entry['broke']
    return _subject(f"{headline} ({topic or 'dead letter'}, {pt(at)})"), '\n'.join(lines), entry.get('log', '')


def read_given_up(ctx: Context, topic: str, at: datetime) -> tuple:
    """(given_up of the topic's waiting messages or None, '' or why they could not be read). The dead-letter
    subscription is named as its topic."""
    try:
        rows = ctx.google.dead_letters(topic)
    except Exception as e:   # noqa: BLE001 - the alert still goes, saying the messages were not read
        _log().warning(f'alert mailer: the messages on {topic} not read ({type(e).__name__})')
        return None, type(e).__name__
    return given_up(rows, at - DEAD_LETTER_WINDOW), ''


def is_dead_letter_topic(topic: str) -> bool:
    """A topic an incident names that is a dead-letter queue: catalogued, or named as one."""
    return topic in _CFG['dead_letters'] or 'dead-letter' in topic or topic.endswith('-dlq')


def close_dead_letter(ctx: Context, topic: str, iid: str) -> str:
    """Monitoring closed a dead-letter incident: the kept one closed too, so 'Also open' stops listing it. An issue
    filed for it stays open, since the messages still wait."""
    incidents = _CFG['incidents']
    key = system_key(_CFG['dead_letter_kind'], topic)
    kept = ctx.store.get(incidents, key)
    if not (kept and kept.get('open')) or (iid and kept.get('incident_id') and kept['incident_id'] != iid):
        return ''
    ctx.store.set(incidents, key, dict(kept, open=False, closed_at=iso(ctx.now),
                                       closed_why=f'Cloud Monitoring closed its incident; the messages still wait on {topic} '
                                                  'until replayed or dropped'))
    return f'; {key} closed, its issue left open'


def on_incident(ctx: Context, payload: dict) -> str:
    """A Cloud Monitoring incident on the watcher's channel: an opened dead-letter incident is one message (once per
    incident id, held in the pending collection as sent) and an incident of kind `dead_letter_kind`; a closed one is
    not told (the messages still wait on the queue until replayed) and closes the kept incident; any other policy is
    told in its own words."""
    incidents, pending, dead_kind = _CFG['incidents'], _CFG['pending'], _CFG['dead_letter_kind']
    inc = payload.get('incident') or {}
    iid = str(inc.get('incident_id') or '')
    topic = _incident_topic(inc)
    dead = is_dead_letter_topic(topic)
    if str(inc.get('state') or '').lower() != 'open':
        closed = close_dead_letter(ctx, topic, iid) if dead else ''
        return f'incident {iid or "?"}: {inc.get("state") or "no state"}, not told{closed}'
    key = f'incident-{iid}' if iid else ''
    if key and (ctx.store.get(pending, key) or {}).get('sent'):
        return f'{key}: already told'
    try:
        at = datetime.fromtimestamp(int(inc.get('started_at')), timezone.utc)
    except (TypeError, ValueError, OverflowError):
        at = ctx.now
    count = _count(inc.get('observed_value'))
    if dead:
        given, unread = read_given_up(ctx, topic, at)
        subject, body, link = compose_dead_letter(topic, count, at, given, unread)
    else:
        doc = ((inc.get('documentation') or {}).get('content') or '').strip()
        subject = _subject(f"{inc.get('policy_name') or 'a Cloud Monitoring alert'} ({pt(at)})")
        body = '\n'.join(p for p in (f"What broke: {inc.get('summary') or inc.get('policy_name') or 'see the incident'}",
                                     doc) if p)
        link = ''
    ctx.tell(subject, body, link or inc.get('url') or '')
    if dead:
        # Kept as an incident, so its event can file an issue; a new opening of the topic is a new one.
        ctx.store.set(incidents, system_key(dead_kind, topic), {
            'open': True, 'kind': dead_kind, 'label': topic, 'first_at': iso(at), 'last_at': iso(ctx.now),
            'count': int(count) if count else 1, 'incident_id': iid, 'subject': redact(subject), 'body': redact(body),
            'link': link or inc.get('url') or ''})
    if key:
        ctx.store.set(pending, key, {'kind': 'incident', 'sent': True, 'at': iso(ctx.now)})
    return f'{key or "incident"}: told ({subject})'


def decode(envelope: dict) -> dict:
    """The pushed Pub/Sub message's JSON (a LogEntry from the sink); {} when unreadable."""
    raw = ((envelope or {}).get('message') or {}).get('data', '')
    try:
        return json.loads(base64.b64decode(raw).decode('utf-8')) if raw else {}
    except (ValueError, UnicodeDecodeError):
        return {}


def handle(payload: dict, ctx: Context) -> str:
    """One LogEntry by its resource type, or a Cloud Monitoring incident. What it did, for the log."""
    if isinstance(payload.get('incident'), dict):
        return on_incident(ctx, payload)
    rtype = (payload.get('resource') or {}).get('type', '')
    labels = (payload.get('resource') or {}).get('labels', {})
    # Its own line would be an alert about the watcher sent by the watcher.
    if labels.get('service_name') == _need('service'):
        return 'my own line: ignored (the backstop policy covers it)'
    if rtype == 'cloud_run_job':
        if 'cloudaudit.googleapis.com' in str(payload.get('logName', '')):
            if '/executions/' not in str((payload.get('protoPayload') or {}).get('resourceName', '')):
                return 'a job event that is not an execution: ignored'
            # An old execution cleared at a job's redeploy ("Execution … has been deleted", /Jobs.ReplaceJob) is no
            # run's end.
            if (payload.get('protoPayload') or {}).get('methodName') == '/Jobs.ReplaceJob':
                return 'an execution deleted at a redeploy: ignored'
            return on_job_event(ctx, payload)
        return on_job_line(ctx, payload)
    if rtype == 'cloud_scheduler_job':
        if (payload.get('jsonPayload') or {}).get('@type') != SCHEDULER_FINISHED:
            return 'a scheduler line that is not an AttemptFinished: ignored'
        return on_scheduler(ctx, payload)
    if rtype in ('cloud_run_revision', 'cloud_function'):
        return on_service(ctx, payload)
    if rtype == 'cloud_tasks_queue':
        if labels.get('queue_id') != _CFG['task_queue']:
            return f"the queue {labels.get('queue_id') or '?'} is not {_CFG['task_queue'] or 'the task queue'}: ignored"
        return on_task(ctx, payload)
    if rtype == 'build':
        return handle_build(ctx, labels.get('build_id', ''), entry_text(payload))
    return f'resource type {rtype or "?"}: ignored'
