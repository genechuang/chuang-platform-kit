"""The hourly WARNING digest's logic: a project's WARNING lines folded into kinds, and which kind a person should look at.

chuang-alerts 0.1.0 (SMADPickleBot #1964): moved unchanged from chuang_sdlc.warning_kinds, which keeps it for one
release. Lifted from SMAD PickleBot's picklebot_core/warning_digest.py (SMADPickleBot #1850, chuang-sdlc 0.39.0), where it runs
hourly in the watchdog with no Claude call. A WARNING line nobody reads is a breakage that reached nobody, and most
WARNING lines are noise, so each hour the host:

  reads    the last hour's WARNING lines (its own Logging read; request logs left out)
  folds    each into its kind (`kind`): the service and the message's first line with its numbers, ids and stamps
           stripped, counted (`fold`)
  keeps    each kind's record in a collection (`load`, `save`): the kind, a sample, first and last seen, the last
           HOURS hours' counts, its state and the investigator's reason
  fires    a kind when one case holds (`decide`): NEW, never seen; GROWING, this hour at least GROW_FACTOR times its
           hourly mean and at least GROW_MIN, once watched GROW_HISTORY hours; REPEATING, a kind kept as a one-off seen
           in at least REPEAT_HOURS of the last HOURS, at most once every REPEAT_EVERY seconds
  wakes    its investigator when anything fired (`wake_text`), who decides each kind (`decision`): promote (the line
           becomes an ERROR: a feature did not happen), demote (INFO: nobody needs it) or keep (a real one-off)

A fired kind waits for its decision and does not fire again meanwhile; a decided kind fires again only when it grows
(and a kept one on repeating). Nothing here names a project: the store's project and collection are the host's
arguments. Standard library, plus the kit's Firestore client, imported on the call.
"""
import hashlib
import json
import re
import urllib.error
import urllib.request

HOURS = 24            # the counts kept per kind
GROW_FACTOR = 4       # GROWING: this hour at least this many times the kind's hourly mean
GROW_MIN = 10         # ... and at least this many lines
GROW_HISTORY = 6      # ... once the kind has been watched this many hours
REPEAT_HOURS = 6      # REPEATING: a kept kind seen in at least this many of the last HOURS hours
REPEAT_EVERY = 24 * 3600   # ... told at most once a day
DECISIONS = ('promote', 'demote', 'keep')
CASES = ('new', 'growing', 'repeating')

_STAMP = re.compile(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?Z?|\d{1,2}:\d{2}(:\d{2})?\s*(AM|PM|PDT|PST|UTC)?', re.I)
_HEX = re.compile(r'\b(?=[0-9a-f]*\d)[0-9a-f]{6,}\b', re.I)
_SPACE = re.compile(r'\s+')
_NUM = re.compile(r'[\w-]*\d[\w.-]*')   # any word with a digit in it: 812, APP-1C, v1, 4f9a1c (12 and 1C are one kind)


def kind(service: str, message: str) -> str:
    """The kind a WARNING line is counted under: its service and its message's first line with stamps, hex ids and
    numbers stripped, cut to 140 characters. Two lines that differ only in a number, an id or a time are one kind."""
    first = (message or '').strip().splitlines()[0] if (message or '').strip() else '(no message)'
    text = _NUM.sub('N', _HEX.sub('H', _STAMP.sub('T', first)))
    text = _SPACE.sub(' ', text).strip()[:140]
    return f"{service or 'unknown'}: {text}"


def kind_id(k: str) -> str:
    """The kind's document id: the first 20 hex characters of its SHA-1."""
    return hashlib.sha1(k.encode('utf-8')).hexdigest()[:20]


def service_of(entry: dict) -> str:
    """The emitting service of a Cloud Logging entry: its Cloud Run service, job or function, else its resource type."""
    labels = (entry.get('resource') or {}).get('labels') or {}
    return (labels.get('service_name') or labels.get('job_name') or labels.get('function_name')
            or (entry.get('resource') or {}).get('type') or 'unknown')


def message_of(entry: dict) -> str:
    """A log entry's text: the JSON payload's message, else the text payload."""
    return str((entry.get('jsonPayload') or {}).get('message') or entry.get('textPayload') or '')


def fold(entries: list) -> dict:
    """{kind: {'count', 'service', 'sample'}} for one hour's entries; the sample is the kind's first line, cut."""
    out = {}
    for e in entries:
        svc, msg = service_of(e), message_of(e)
        k = kind(svc, msg)
        rec = out.setdefault(k, {'count': 0, 'service': svc, 'sample': msg.strip()[:300]})
        rec['count'] += 1
    return out


def hour_label(epoch: float) -> str:
    """The UTC hour an epoch second falls in, as the records key it: '2026-10-27T22:00Z'."""
    import datetime as dt
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime('%Y-%m-%dT%H:00Z')   # utc-ok: a key, never a day


def _hours_before(hour: str, n: int) -> str:
    """The hour label `n` hours before `hour`."""
    import datetime as dt
    t = dt.datetime.strptime(hour, '%Y-%m-%dT%H:00Z').replace(tzinfo=dt.timezone.utc)   # utc-ok: a key's own zone
    return (t - dt.timedelta(hours=n)).strftime('%Y-%m-%dT%H:00Z')


def decide(counts: dict, records: dict, hour: str, now: float) -> tuple:
    """(fired, records) for one hour. `counts` is fold's answer, `records` {kind_id: record} as kept (missing kinds are
    new), `hour` the hour's label, `now` epoch seconds. Each kind seen gets this hour's count; a kind fires once by the
    case it meets (CASES' order), recorded as `fired` with its case. Pure: nothing is read or written."""
    records = {i: dict(r) for i, r in records.items()}
    fired = []
    for k, c in counts.items():
        i = kind_id(k)
        rec = records.get(i)
        if rec is None:
            rec = {'kind': k, 'service': c['service'], 'sample': c['sample'], 'first_seen': hour, 'hours': {},
                   'state': 'watching', 'reason': '', 'fired_at': 0, 'case': ''}
            case = 'new'
        else:
            case = ''
        past = dict(rec.get('hours') or {})
        history = [v for h, v in sorted(past.items()) if h != hour]
        mean = (sum(history) / len(history)) if history else 0
        past[hour] = past.get(hour, 0) + c['count']
        cutoff = _hours_before(hour, HOURS)
        rec['hours'] = {h: v for h, v in sorted(past.items()) if h > cutoff}
        rec['last_seen'], rec['sample'] = hour, c['sample'] or rec.get('sample', '')
        state = rec.get('state') or 'watching'
        if not case and state != 'fired':
            if len(history) >= GROW_HISTORY and c['count'] >= max(GROW_MIN, GROW_FACTOR * mean):
                case = 'growing'
            elif (state == 'kept' and sum(1 for v in rec['hours'].values() if v) >= REPEAT_HOURS
                  and now - float(rec.get('fired_at') or 0) >= REPEAT_EVERY):
                case = 'repeating'
        if case:
            rec.update(state='fired', case=case, fired_at=now)
            fired.append({'id': i, 'kind': k, 'case': case, 'count': c['count'], 'service': c['service'],
                          'sample': rec['sample']})
        records[i] = rec
    return fired, records


def decision(record: dict, verdict: str, why: str) -> dict:
    """The record after the investigator's verdict (promote, demote or keep) and reason; ValueError for any other
    verdict or an empty reason."""
    if verdict not in DECISIONS:
        raise ValueError(f'a verdict is one of {", ".join(DECISIONS)}, not {verdict!r}')
    if not (why or '').strip():
        raise ValueError('a verdict carries its reason')
    state = {'promote': 'promoted', 'demote': 'demoted', 'keep': 'kept'}[verdict]
    return dict(record, state=state, reason=why.strip(), case='')


def wake_text(fired: list) -> str:
    """One line naming what fired, for the investigator's wake and the job's log."""
    parts = [f"{f['case']} {f['kind'][:80]} ({f['count']} this hour)" for f in fired[:5]]
    more = f' and {len(fired) - 5} more' if len(fired) > 5 else ''
    return f'warnings: {len(fired)} kind(s) to decide: ' + '; '.join(parts) + more


# -- the kinds' store, on the kit's Firestore client and the caller's token ----------------------------------------

def to_plain(record: dict) -> dict:
    """A record as the document's plain values: a dict (`hours`) as its JSON string, None as ''."""
    return {k: json.dumps(v, sort_keys=True) if isinstance(v, dict) else '' if v is None else v for k, v in record.items()}


def from_plain(fields: dict) -> dict:
    """A document's plain values as a record; `hours` back to a dict."""
    rec = dict(fields or {})
    try:
        rec['hours'] = json.loads(rec.get('hours') or '{}')
    except (TypeError, ValueError):
        rec['hours'] = {}
    return rec


def _firestore(project: str, token: str, opener, call):
    """`call` on the kit's Firestore client for `project` on the caller's token; a Firestore it cannot reach raised as
    urllib's URLError, the failure an hourly job already catches."""
    from chuang_platform_kit import firestore
    try:
        return call(firestore.Client(project, token=token, opener=opener, timeout=30))
    except firestore.Unreachable as e:
        raise urllib.error.URLError(f'Firestore: {e}') from None


def load(project: str, collection: str, token: str, opener=urllib.request.urlopen) -> dict:
    """Every kind's record in `collection`, {kind_id: record}: the whole collection, one query."""
    return {kid: from_plain(f) for kid, f in _firestore(project, token, opener, lambda db: db.run_query(collection))}


def save(project: str, collection: str, token: str, kid: str, record: dict, opener=urllib.request.urlopen) -> None:
    """One kind's record written whole."""
    _firestore(project, token, opener, lambda db: db.set(collection, kid, to_plain(record)))
