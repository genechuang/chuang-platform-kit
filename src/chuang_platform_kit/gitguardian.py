"""GitGuardian's incidents for a repository, read and closed: list the open ones, attach their occurrences, ignore or resolve one with a note.

Lifted from SMAD PickleBot's `.github/scripts/gitguardian_alerts.py` (10/8/26, kit 0.5.0; Gene:
"both our sessions should clear our own alerts"). A workspace watches every repository the
GitHub app can see, so everything is by `repo` ("owner/name"): its source id is looked up once
(`source_id`), its incidents listed from that source. The REST API with a personal access
token (`key`): `incidents:read` and `sources:read` to list, **`incidents:write` to ignore or
resolve** — a read-only key answers 403 to those two, which `ignore()`/`resolve()` raise as
PermissionError naming the scope. `opener` is the seam (anything called as
urlopen(request, timeout=...)), so the suites run offline.

    from chuang_platform_kit import gitguardian as gg
    for inc in gg.open_incidents(key, 'genechuang/SMADPickleBot'):
        print(inc['id'], gg.detector(inc), gg.files(inc))
    gg.ignore(key, 12345, 'false_positive', 'a comment naming a token by its label, not its value')
    gg.resolve(key, 12346, 'the key was rotated on 10/8/26')

Nothing here prints; a message built from an incident goes through the host's own redaction
before it reaches a log or an issue (never the matched string: `files()` and `detector()`
are the safe parts).
"""
import json
import urllib.error
import urllib.parse
import urllib.request

API = 'https://api.gitguardian.com/v1'
OPEN_STATES = ('TRIGGERED', 'ASSIGNED')
IGNORE_REASONS = ('false_positive', 'test_credential', 'low_risk')
MAX_OCCURRENCES = 10


def _call(path, key, method='GET', body=None, opener=None, timeout=30):
    data = json.dumps(body).encode('utf-8') if body is not None else None
    headers = {'Authorization': f'Token {key}', 'Accept': 'application/json'}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(f'{API}{path}', data=data, method=method, headers=headers)
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
            text = r.read().decode('utf-8')
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as e:
        if e.code in (401, 403) and method != 'GET':
            raise PermissionError(f"GitGuardian refused {method} {path} ({e.code}): the key needs the incidents:write scope") from e
        raise


def source_id(key, repo, opener=None):
    """GitGuardian's id for `repo` ("owner/name"); RuntimeError when the workspace
    does not watch it."""
    q = urllib.parse.urlencode({'search': repo.split('/')[-1], 'per_page': 50})
    for s in _call(f'/sources?{q}', key, opener=opener) or []:
        if (s.get('full_name') or '').lower() == repo.lower():
            return s['id']
    raise RuntimeError(f'GitGuardian has no source named {repo}')


def incidents(key, repo, states=OPEN_STATES, opener=None, per_page=100):
    """The repository's secret incidents in `states` (the open ones by default),
    newest first, without occurrences (the list never embeds them)."""
    sid = source_id(key, repo, opener=opener)
    q = urllib.parse.urlencode({'per_page': per_page, 'ordering': '-date'})
    found = _call(f'/sources/{sid}/incidents/secrets?{q}', key, opener=opener) or []
    wanted = {s.upper() for s in states} if states else None
    return [i for i in found if not wanted or (i.get('status') or '').upper() in wanted]


def with_occurrences(key, incident, opener=None, limit=MAX_OCCURRENCES):
    """The incident with its occurrences attached (the file and line each match
    is in), read from the occurrences endpoint; the incident unchanged, with
    `occurrences_error`, when that read fails."""
    q = urllib.parse.urlencode({'incident_id': incident['id'], 'per_page': limit})
    try:
        incident['occurrences'] = _call(f'/occurrences/secrets?{q}', key, opener=opener)
    except Exception as e:   # noqa: BLE001 - the caller decides what to say, redacted
        incident['occurrences_error'] = type(e).__name__
    return incident


def open_incidents(key, repo, opener=None):
    """The repository's open incidents, each with its occurrences."""
    return [with_occurrences(key, i, opener=opener) for i in incidents(key, repo, opener=opener)]


def detector(incident):
    """The detector's display name ('Google API Key'), never the match."""
    d = incident.get('detector') or {}
    return d.get('display_name') or d.get('name') or 'secret'


def files(incident):
    """The files the incident's occurrences name, deduped, in order."""
    out = []
    for o in incident.get('occurrences') or []:
        name = o.get('filepath') or o.get('filename') or ''
        if name and name not in out:
            out.append(name)
    return out


def ignore(key, incident_id, reason, note='', opener=None):
    """Ignore an incident: `reason` one of IGNORE_REASONS, `note` why (the GitGuardian
    UI shows it). Needs incidents:write."""
    if reason not in IGNORE_REASONS:
        raise ValueError(f"ignore reason must be one of {', '.join(IGNORE_REASONS)}")
    body = {'ignore_reason': reason}
    if note:
        _call(f'/incidents/secrets/{incident_id}/notes', key, 'POST', {'comment': note}, opener=opener)
    return _call(f'/incidents/secrets/{incident_id}/ignore', key, 'POST', body, opener=opener)


def resolve(key, incident_id, note='', secret_revoked=True, opener=None):
    """Resolve an incident (the secret was rotated or removed), with a note. Needs
    incidents:write."""
    if note:
        _call(f'/incidents/secrets/{incident_id}/notes', key, 'POST', {'comment': note}, opener=opener)
    return _call(f'/incidents/secrets/{incident_id}/resolve', key, 'POST', {'secret_revoked': bool(secret_revoked)}, opener=opener)
