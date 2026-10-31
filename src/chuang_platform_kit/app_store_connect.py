"""App Store Connect's API, one client for every project (SMAD PickleBot #570, out of its picklebot_core/app_store.py
of 10/9/26; Gene: "build this monitoring/notification into my submit pipeline, so it doesn't burn any tokens and I
have it in code").

The ES256 bearer from a team key (its id, the account's issuer id, the .p8), one call with Apple's own reason on a
refusal, and the readers an app wants, the app id a parameter: its builds with their TestFlight review states, its
App Store versions (with the build each carries and when the open review was sent), its internal testers, and the
three read side by side as one snapshot. Apple's state words are the caller's to translate; where the key is kept is
the host's business (`private_key` reads the environment, else Secret Manager).

PyJWT with its crypto extra signs the bearer (`pip install chuang-platform-kit[asc]`); the rest is the standard library.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

API = 'https://api.appstoreconnect.apple.com/v1'
TOKEN_SECONDS = 1200   # Apple's most: 20 minutes
TIMEOUT = 20


class Refused(RuntimeError):
    """Apple refused or did not answer: the line says the call, the status and Apple's own reason, never the key."""


def token(key: str, key_id: str, issuer_id: str, now=None, seconds: int = TOKEN_SECONDS) -> str:
    """App Store Connect's bearer: an ES256 JWT signed with the team key's .p8, `seconds` long (20 minutes, Apple's
    most), with the key id in its header and Apple's audience."""
    try:
        import jwt
    except ImportError as e:   # pragma: no cover - the extra is not installed
        raise ImportError('chuang_platform_kit.app_store_connect needs PyJWT with cryptography: '
                          'pip install chuang-platform-kit[asc]') from e
    now = int(now if now is not None else time.time())
    return jwt.encode({'iss': issuer_id.strip(), 'iat': now, 'exp': now + seconds, 'aud': 'appstoreconnect-v1'},
                      key, algorithm='ES256', headers={'kid': key_id.strip(), 'typ': 'JWT'})


def private_key(name: str, env=None, project: str = '', access=None) -> str:
    """The team key's .p8: the environment's `name` (a mounted secret), else Secret Manager's secret `name` in
    `project` (through `secrets.access`, or `access(project, name)` when given); '' when neither has it, and the
    caller says so. Never printed."""
    env = os.environ if env is None else env
    key = (env.get(name) or '').strip()
    if key or not project:
        return key
    try:
        if access is None:
            from . import secrets
            access = secrets.access
        return (access(project, name) or b'').decode('utf-8', 'replace').strip()
    except Exception:   # noqa: BLE001 - a key that cannot be read is no key
        return ''


def call(method: str, path: str, bearer: str, body=None, query=None, opener=None, timeout: int = TIMEOUT) -> dict:
    """One call under the API root, the JSON back ({} on an empty body); Refused with Apple's own reason when it
    refuses, or with the error's name when it does not answer."""
    url = API + path + ('?' + urllib.parse.urlencode(query) if query else '')
    data = json.dumps(body).encode() if body is not None else None
    headers = {'Authorization': f'Bearer {bearer}'}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw.decode()) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            detail = (json.loads(e.read().decode()).get('errors') or [{}])[0].get('detail', '')
        except Exception:   # noqa: BLE001 - a body that is not Apple's JSON
            detail = ''
        raise Refused(f'{method} {path} answered {e.code}' + (f': {detail}' if detail else ''))
    except (OSError, ValueError) as e:
        raise Refused(f'{method} {path} did not answer ({type(e).__name__})')


def get(path: str, bearer: str, query=None, opener=None, timeout: int = TIMEOUT) -> dict:
    """One GET; Refused carrying Apple's own reason when it refuses."""
    return call('GET', path, bearer, query=query, opener=opener, timeout=timeout)


def builds(app_id: str, bearer: str, limit: int = 5, opener=None) -> list:
    """The app's latest builds, newest first: [{number, version, uploaded, processing, beta_review, submitted,
    external, internal}]."""
    reply = get('/builds', bearer, {
        'filter[app]': app_id, 'sort': '-uploadedDate', 'limit': str(limit),
        'include': 'buildBetaDetail,betaAppReviewSubmission,preReleaseVersion',
    }, opener)
    inc = {(i['type'], i['id']): i.get('attributes', {}) for i in reply.get('included', [])}

    def rel(b, name):
        d = ((b.get('relationships') or {}).get(name) or {}).get('data')
        return inc.get((d['type'], d['id']), {}) if d else {}

    out = []
    for b in reply.get('data', []):
        a = b.get('attributes', {})
        detail, review, pre = rel(b, 'buildBetaDetail'), rel(b, 'betaAppReviewSubmission'), rel(b, 'preReleaseVersion')
        out.append({'number': a.get('version'), 'version': pre.get('version'), 'uploaded': a.get('uploadedDate'),
                    'processing': a.get('processingState'), 'beta_review': review.get('betaReviewState'),
                    'submitted': review.get('submittedDate'),
                    'external': detail.get('externalBuildState'), 'internal': detail.get('internalBuildState')})
    return out


def store_versions(app_id: str, bearer: str, opener=None, with_builds: bool = False):
    """The app's App Store versions' states, {versionString: state}, newest first; empty until the app is first sent
    to the store. `with_builds`: (that, {versionString: the build number attached to it}, when the open review was
    submitted or None), the build read in the same request."""
    query = {'limit': '3', 'fields[appStoreVersions]': 'versionString,appStoreState,appVersionState,build'}
    if with_builds:
        query.update({'include': 'build', 'fields[builds]': 'version'})
    reply = get(f'/apps/{app_id}/appStoreVersions', bearer, query, opener)
    numbers = {i['id']: (i.get('attributes') or {}).get('version') for i in reply.get('included', []) if i.get('type') == 'builds'}
    out, built = {}, {}
    for v in reply.get('data', []):
        a = v.get('attributes', {})
        if a.get('versionString'):
            out[a['versionString']] = a.get('appVersionState') or a.get('appStoreState')
            d = ((v.get('relationships') or {}).get('build') or {}).get('data')
            if d and numbers.get(d.get('id')):
                built[a['versionString']] = numbers[d['id']]
    if not with_builds:
        return out
    # When the open review was sent (the newest submission still with Apple), Apple's own time.
    subs = get(f'/apps/{app_id}/reviewSubmissions', bearer,
               {'limit': '5', 'fields[reviewSubmissions]': 'state,submittedDate,platform'}, opener).get('data', [])
    open_ = [s.get('attributes') or {} for s in subs
             if (s.get('attributes') or {}).get('state') in ('WAITING_FOR_REVIEW', 'IN_REVIEW', 'UNRESOLVED_ISSUES')]
    submitted = max((s.get('submittedDate') or '' for s in open_), default='') or None
    return out, built, submitted


def internal_testers(app_id: str, bearer: str, opener=None) -> dict:
    """Everyone in the app's internal TestFlight groups, {tester id: "First Last"}; an internal tester gets every
    build before Apple reviews it, one dropped to an external group only the reviewed ones."""
    out = {}
    for g in get(f'/apps/{app_id}/betaGroups', bearer, {'limit': '50'}, opener).get('data', []):
        if not (g.get('attributes') or {}).get('isInternalGroup'):
            continue
        for t in get(f"/betaGroups/{g['id']}/betaTesters", bearer, {'limit': '200'}, opener).get('data', []):
            a = t.get('attributes') or {}
            out[t['id']] = ' '.join(p for p in (a.get('firstName'), a.get('lastName')) if p) or 'a tester'
    return out


def snapshot(app_id: str, bearer: str, limit: int = 5, opener=None) -> dict:
    """What Apple says now: {'builds': {number: {version, beta_review, external, uploaded, processing, submitted}},
    'store': {version: state}, 'store_builds': {version: build number}, 'store_submitted': when the open review was
    sent, 'internal': {tester id: name}}. The three reads are made side by side (none needs another's answer, and one
    after another they were a watch's "Consecutive HTTP" hint); a read that fails still raises its own Refused."""
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix='app-store-read') as pool:
        rows = pool.submit(builds, app_id, bearer, limit, opener)
        store = pool.submit(store_versions, app_id, bearer, opener, True)
        internal = pool.submit(internal_testers, app_id, bearer, opener)
        rows, (store, store_builds, store_submitted), internal = rows.result(), store.result(), internal.result()
    return {'builds': {str(r['number']): {'version': r['version'], 'beta_review': r['beta_review'],
                                          'external': r['external'], 'uploaded': r.get('uploaded'),
                                          'processing': r.get('processing'), 'submitted': r.get('submitted')}
                       for r in rows if r.get('number')},
            'store': store,
            'store_builds': store_builds,
            'store_submitted': store_submitted,
            'internal': internal}
