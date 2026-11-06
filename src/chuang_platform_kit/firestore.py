"""Firestore over REST with the standard library: documents, commits, queries, create-only claims and TTL locks.

A host had grown its own Firestore calls one module at a time: hand-built REST requests, four value codecs, seven
create-only claims and two TTL locks (SMAD PickleBot #1015). This is the one copy, so no host needs
`google-cloud-firestore` and its gRPC stack to read a document or take a claim.

    from chuang_platform_kit import firestore
    db = firestore.client('my-project')
    db.get('config', 'league')                       # {'name': ...} or None
    if db.claim('sent', 'invite-42', ttl=86400):     # True once, False for every later caller
        ...
    if db.lock('locks', 'nightly-sync', ttl=120):    # an exclusive lease until it expires or is released
        try: ...
        finally: db.release('locks', 'nightly-sync')

Every call is one HTTPS request on the kit's kept-alive pool (`keepalive.urlopen`, urllib's own urlopen where requests
is not installed), or the `opener` given (urlopen's shape: a test's fake). The token is `token`: a string, a callable
asked when one is needed, or by default `google_auth.access_token` (a job's variable, a service-account key, the
metadata server, then gcloud), kept TOKEN_KEEP_SECONDS and dropped and asked again once on a 401.

What a caller sees:
  - `get` answers None for a missing document, `delete` and `release` are fine with one;
  - a create-only write (`create`, `claim`) answers False when the document is there; `lock` answers False while
    another holder's lease runs;
  - a precondition a `commit` names that does not hold raises Conflict;
  - anything else (no token, the network, a 5xx, a refusal) raises Unreachable, naming the HTTP status and
    Firestore's own status word or the exception's kind, never the token. The caller decides whether that fails open
    or closed. `retry_waits` tries a call that met Unreachable again after each wait (none by default).

Values (`encode`, `decode`): None, bool, int, float, str, bytes, an aware or naive-UTC datetime, and lists and dicts
of them, both ways; a timestamp decodes as its RFC 3339 text (`instant()` reads it, or a number of epoch seconds, as an
aware datetime). `to_fields` and `from_fields` do a whole document's `fields`.

Document ids are percent-encoded in every URL and resource name; a `collection` may be a path
('games/412/players') and is used as given. Nothing here names a project: the host passes it.
"""
import base64
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API = 'https://firestore.googleapis.com/v1'
TIMEOUT_SECONDS = 10       # every call's timeout unless the client is given one
TOKEN_KEEP_SECONDS = 240   # a default token is asked again after this (a metadata token lives longer than this)
COMMIT_LIMIT = 500         # Firestore's most writes in one commit
EXPIRES_FIELD = 'expires_at'   # the field `claim` and `lock` write a TTL into (a TTL policy deletes by it)
_CONFLICT = ('ALREADY_EXISTS', 'FAILED_PRECONDITION', 'ABORTED')
_OPS = {'==': 'EQUAL', '!=': 'NOT_EQUAL', '<': 'LESS_THAN', '<=': 'LESS_THAN_OR_EQUAL', '>': 'GREATER_THAN',
        '>=': 'GREATER_THAN_OR_EQUAL', 'in': 'IN', 'not-in': 'NOT_IN', 'array-contains': 'ARRAY_CONTAINS',
        'array-contains-any': 'ARRAY_CONTAINS_ANY'}


class Unreachable(Exception):
    """Firestore could not be read or written: no token, a network error, a refusal, a 5xx. The message names the
    HTTP status and Firestore's status word, or the exception's kind; never a token."""


class Conflict(Exception):
    """A commit's precondition did not hold (the document exists for a create-only write, or it changed since the read
    an update named): Firestore's ALREADY_EXISTS, FAILED_PRECONDITION or ABORTED."""


# ------------------------------------------------------------------ values
def _utc_text(when: datetime) -> str:
    when = when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when.astimezone(timezone.utc)
    return when.isoformat(timespec='microseconds').replace('+00:00', 'Z')


def encode(value) -> dict:
    """One Python value as a Firestore REST value, decode's inverse: None, bool, int, float, str, bytes, a datetime
    (a timestampValue in UTC; a naive one is read as UTC), lists or tuples and dicts of them; anything else as its
    str()."""
    if value is None:
        return {'nullValue': None}
    if isinstance(value, bool):
        return {'booleanValue': value}
    if isinstance(value, int):
        return {'integerValue': str(value)}
    if isinstance(value, float):
        return {'doubleValue': value}
    if isinstance(value, datetime):
        return {'timestampValue': _utc_text(value)}
    if isinstance(value, (bytes, bytearray)):
        return {'bytesValue': base64.b64encode(bytes(value)).decode('ascii')}
    if isinstance(value, (list, tuple)):
        return {'arrayValue': {'values': [encode(v) for v in value]}}
    if isinstance(value, dict):
        return {'mapValue': {'fields': {str(k): encode(v) for k, v in value.items()}}}
    return {'stringValue': str(value)}


def decode(value: dict):
    """One Firestore REST value as plain Python: integers and doubles as numbers, arrays and maps in kind, null as
    None, bytes as bytes, a timestamp, a reference or a geo point as Firestore wrote it (text, or a {latitude,
    longitude} dict). None for anything that is not a value."""
    if not isinstance(value, dict) or not value:
        return None
    kind, v = next(iter(value.items()))
    if kind == 'integerValue':
        return int(v)
    if kind == 'doubleValue':
        return float(v)
    if kind == 'booleanValue':
        return bool(v)
    if kind == 'arrayValue':
        return [decode(x) for x in (v or {}).get('values') or []]
    if kind == 'mapValue':
        return {k: decode(x) for k, x in ((v or {}).get('fields') or {}).items()}
    if kind == 'bytesValue':
        return base64.b64decode(v or '')
    if kind == 'nullValue':
        return None
    return v   # stringValue, timestampValue, referenceValue, geoPointValue


def to_fields(data: dict) -> dict:
    """A plain dict as a document body's `fields` (encode() on each value)."""
    return {str(k): encode(v) for k, v in (data or {}).items()}


def from_fields(doc: dict) -> dict:
    """A document's fields as a plain dict (decode() on each): `doc` is the document ({'fields': ...}) or its
    `fields` map itself (told apart by the document's own keys, so pass the document when its only field is named
    `fields`)."""
    doc = doc or {}
    is_doc = 'name' in doc or (doc and doc.keys() <= {'fields', 'createTime', 'updateTime'})
    fields = doc.get('fields') if is_doc else doc
    return {k: decode(v) for k, v in (fields or {}).items()}


def instant(value):
    """An aware UTC datetime from a decoded timestamp (RFC 3339 text), a number of epoch seconds or a datetime (naive
    read as UTC); None for anything else."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip().replace('Z', '+00:00')
        if '.' in text:   # Firestore writes up to nine digits; fromisoformat takes six
            head, _, rest = text.partition('.')
            digits = ''.join(c for c in rest if c.isdigit())
            text = f'{head}.{(digits + "000000")[:6]}{rest[len(digits):]}'
        try:
            when = datetime.fromisoformat(text)
        except ValueError:
            return None
        return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when
    return None


def _seconds(ttl) -> float:
    return ttl.total_seconds() if isinstance(ttl, timedelta) else float(ttl)


# ------------------------------------------------------------------ the client
def _default_opener():
    import importlib.util
    if importlib.util.find_spec('requests') is None:   # keepalive's one dependency
        return urllib.request.urlopen
    from . import keepalive
    return keepalive.urlopen


class Client:
    """Firestore's REST API for one project and database. `token` is a string, a callable answering one, or None for
    `google_auth.access_token` (kept TOKEN_KEEP_SECONDS); `opener` is urlopen-shaped (default the kit's kept-alive
    pool); `retry_waits` are the seconds before each further try of a call that met Unreachable, `sleep` the wait;
    `now` the clock claims and locks stamp by (a callable answering an aware datetime)."""

    def __init__(self, project: str, database: str = '(default)', token=None, opener=None,
                 timeout: float = TIMEOUT_SECONDS, retry_waits=(), sleep=time.sleep, now=None):
        if not project:
            raise ValueError('a Firestore client needs a project')
        self.project, self.database, self.timeout = project, database, timeout
        self.root = f'projects/{project}/databases/{database}/documents'
        self.opener = opener or _default_opener()
        self.retry_waits, self.sleep = tuple(retry_waits or ()), sleep
        self.now = now or (lambda: datetime.now(timezone.utc))   # utc-ok: instants, never a calendar day
        self._token, self._kept, self._kept_at = token, '', 0.0

    # -- plumbing
    def name(self, collection: str, doc_id: str) -> str:
        """The document's full resource name, 'projects/p/databases/(default)/documents/<collection>/<id>' (the id
        percent-encoded)."""
        return f'{self.root}/{collection.strip("/")}/{urllib.parse.quote(str(doc_id), safe="")}'

    def _full(self, name: str) -> str:
        return name if name.startswith('projects/') else f'{self.root}/{name.lstrip("/")}'

    def _bearer(self, fresh: bool = False) -> str:
        tok = self._token
        if isinstance(tok, str):
            return tok
        if callable(tok):
            return tok() or ''
        if fresh or not self._kept or time.monotonic() - self._kept_at > TOKEN_KEEP_SECONDS:
            from . import google_auth
            self._kept, self._kept_at = google_auth.access_token(), time.monotonic()
        return self._kept

    def _once(self, method: str, url: str, body, fresh: bool):
        token = self._bearer(fresh)
        if not token:
            raise Unreachable('no access token')
        data = json.dumps(body).encode('utf-8') if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header('Authorization', f'Bearer {token}')
        if data is not None:
            req.add_header('Content-Type', 'application/json')
        with self.opener(req, timeout=self.timeout) as resp:
            raw = resp.read().decode('utf-8')
        return json.loads(raw) if raw.strip() else {}

    def _call(self, method: str, url: str, body=None, missing_ok: bool = False):
        """(status, answer): 200 and the JSON, or 404 and {} when `missing_ok`. Conflict on a failed precondition,
        Unreachable for the rest after `retry_waits`."""
        waits = list(self.retry_waits)
        tries, refreshed = 0, False
        while True:
            tries += 1
            try:
                return 200, self._once(method, url, body, fresh=refreshed)
            except urllib.error.HTTPError as e:
                status = _status_word(e)
                if e.code == 404 and missing_ok:
                    return 404, {}
                if e.code == 401 and not refreshed and self._token is None:
                    refreshed = True   # the kept token expired early: ask once more, not counted as a try
                    tries -= 1
                    continue
                if e.code == 409 or status in _CONFLICT:
                    raise Conflict(f'HTTP {e.code} {status}'.strip()) from None
                why = f'HTTP {e.code} {status}'.strip()
                if e.code < 500 and e.code != 429:
                    raise Unreachable(why) from None
            except Unreachable:
                raise
            except Exception as e:   # noqa: BLE001 - the network, a timeout, an unreadable answer
                why = type(e).__name__
            if not waits:
                raise Unreachable(f'{why}; {tries} tries' if tries > 1 else why)
            self.sleep(waits.pop(0))

    def _doc_url(self, collection: str, doc_id: str) -> str:
        return f'{API}/{self.name(collection, doc_id)}'

    # -- documents
    def get(self, collection: str, doc_id: str, raw: bool = False):
        """The document's fields as a plain dict, or None when it does not exist; with `raw`, Firestore's document
        itself ({name, fields, createTime, updateTime})."""
        code, doc = self._call('GET', self._doc_url(collection, doc_id), missing_ok=True)
        if code == 404:
            return None
        return doc if raw else from_fields(doc)

    def create(self, collection: str, doc_id, data: dict):
        """Create the document unless it exists: True when this call created it, False when it was there. `doc_id`
        None lets Firestore choose the id, and the answer is that id (a meter's reading, an append-only log)."""
        url = f'{API}/{self.root}/{collection.strip("/")}'
        if doc_id is not None:
            url += '?' + urllib.parse.urlencode({'documentId': str(doc_id)})
        try:
            _, doc = self._call('POST', url, {'fields': to_fields(data)})
        except Conflict:
            return False
        return (doc.get('name') or '').rsplit('/', 1)[-1] if doc_id is None else True

    def set(self, collection: str, doc_id: str, data: dict) -> None:
        """Write the document whole: created, or replaced with exactly these fields."""
        self.commit([update(self.name(collection, doc_id), data)])

    def merge(self, collection: str, doc_id: str, data: dict = None, paths=None, increment=None, append=None,
              remove=None) -> None:
        """Write these fields alone, the rest of the document kept (a missing document is created with them): `data`'s
        top-level keys are the mask unless `paths` names it (dotted paths into a map, `a.b`, with backticks round a
        segment that is not a plain name). `increment` {field: n} adds server-side, so two writers never lose one;
        `append` / `remove` {field: [values]} add missing elements to / take them out of an array."""
        self.commit([update(self.name(collection, doc_id), data or {}, mask=list(data or {}) if paths is None else paths,
                            increment=increment, append=append, remove=remove)])

    def delete(self, collection: str, doc_id: str) -> None:
        """Delete the document; a missing one is fine."""
        self._call('DELETE', self._doc_url(collection, doc_id), missing_ok=True)

    def commit(self, writes: list) -> dict:
        """One atomic commit of `writes` (REST Write objects, `update()`, `delete_write()` or hand-built), all or none:
        a name in them may be relative to the database's documents ('chat_days/x') or full. Firestore's answer
        ({writeResults, commitTime}). Conflict when a precondition fails; ValueError past COMMIT_LIMIT writes."""
        writes = list(writes or [])
        if len(writes) > COMMIT_LIMIT:
            raise ValueError(f'{len(writes)} writes: a commit takes at most {COMMIT_LIMIT}')
        full = []
        for w in writes:
            w = json.loads(json.dumps(w))
            if 'update' in w:
                w['update']['name'] = self._full(w['update']['name'])
            if 'transform' in w:
                w['transform']['document'] = self._full(w['transform']['document'])
            if 'delete' in w:
                w['delete'] = self._full(w['delete'])
            full.append(w)
        _, answer = self._call('POST', f'{API}/{self.root}:commit', {'writes': full})
        return answer

    def batch_get(self, refs) -> list:
        """Several documents in one round trip: each (collection, doc_id) in `refs` as its fields, None for one that
        does not exist, in `refs`' order."""
        refs = list(refs or [])
        if not refs:
            return []
        names = [self.name(c, d) for c, d in refs]
        _, answers = self._call('POST', f'{API}/{self.root}:batchGet', {'documents': names})
        found = {}
        for a in answers if isinstance(answers, list) else []:
            doc = (a or {}).get('found') if isinstance(a, dict) else None
            if doc and doc.get('name'):
                fields = from_fields(doc)
                found[doc['name']] = fields
                found[urllib.parse.unquote(doc['name'])] = fields
        return [found.get(n, found.get(urllib.parse.unquote(n))) for n in names]

    def run_query(self, collection: str, where=(), order_by=(), limit=None, select=None, all_descendants=False,
                  query=None, raw: bool = False) -> list:
        """The documents of `collection` that match, as [(doc_id, fields)] (the id unquoted); with `raw`, Firestore's
        documents themselves. `where` is (field, op, value) triples ANDed, `op` Firestore's word ('EQUAL') or its
        symbol ('==', '>=', 'in', 'array-contains'); `order_by` field names, '-field' descending; `select` the fields to
        return; `all_descendants` every collection of that id at any depth. `query` is a whole structuredQuery, used as
        given. No `where` reads the whole collection (Firestore pages it on the server)."""
        if query is None:
            query = {'from': [{'collectionId': collection.rsplit('/', 1)[-1], **({'allDescendants': True} if all_descendants else {})}]}
            filters = [{'fieldFilter': {'field': {'fieldPath': f}, 'op': _OPS.get(op, op), 'value': encode(v)}}
                       for f, op, v in where or ()]
            if len(filters) == 1:
                query['where'] = filters[0]
            elif filters:
                query['where'] = {'compositeFilter': {'op': 'AND', 'filters': filters}}
            if order_by:
                query['orderBy'] = [{'field': {'fieldPath': o.lstrip('-')},
                                     'direction': 'DESCENDING' if o.startswith('-') else 'ASCENDING'} for o in order_by]
            if select is not None:
                query['select'] = {'fields': [{'fieldPath': f} for f in select]}
            if limit is not None:
                query['limit'] = int(limit)
        parent = self.root
        if '/' in collection.strip('/'):   # a subcollection: the query's parent is the document above it
            parent = f'{self.root}/{collection.strip("/").rsplit("/", 1)[0]}'
        _, rows = self._call('POST', f'{API}/{parent}:runQuery', {'structuredQuery': query})
        out = []
        for row in rows if isinstance(rows, list) else []:
            doc = (row or {}).get('document') if isinstance(row, dict) else None
            if not doc or not doc.get('name'):
                continue
            out.append(doc if raw else (urllib.parse.unquote(doc['name'].rsplit('/', 1)[-1]), from_fields(doc)))
        return out

    # -- claims and locks
    def claim(self, collection: str, doc_id: str, data: dict = None, ttl=None, more_ids=()) -> bool:
        """Take `collection/doc_id` once: a create-only commit (precondition exists=false). True when this call made it,
        False when it was already there, so two deliveries of one message act once. `ttl` (seconds or a timedelta) adds
        EXPIRES_FIELD for Firestore's TTL policy; `more_ids` are taken in the same commit, all or none. Unreachable for
        anything else, so a caller never acts on a claim it may not hold unless it chooses to."""
        fields = dict(data or {})
        if ttl is not None:
            fields[EXPIRES_FIELD] = self.now() + timedelta(seconds=_seconds(ttl))
        writes = [update(self.name(collection, d), fields, exists=False) for d in (doc_id, *more_ids)]
        try:
            self.commit(writes)
        except Conflict:
            return False
        return True

    def release(self, collection: str, doc_id: str) -> bool:
        """Give back a claim or a lock (delete it) so a retry can take it at once: True when deleted (or already
        gone), False, never an exception, when Firestore could not be reached (a TTL then frees it)."""
        try:
            self.delete(collection, doc_id)
            return True
        except Unreachable:
            return False

    def lock(self, collection: str, doc_id: str, ttl, data: dict = None) -> bool:
        """An exclusive lease on `collection/doc_id` for `ttl` (seconds or a timedelta): True when this call holds it,
        False while another holder's lease runs. A free document is taken create-only; an expired one is taken over
        only if unchanged since it was read (an updateTime precondition), so two callers never both win. It holds
        `locked_at`, EXPIRES_FIELD (a timestamp; a number of epoch seconds is read too) and `data`. Unreachable when
        Firestore cannot be read or written: a caller that must fail open catches it."""
        now = self.now()
        doc = self.get(collection, doc_id, raw=True)
        fields = {**(data or {}), 'locked_at': now, EXPIRES_FIELD: now + timedelta(seconds=_seconds(ttl))}
        if doc is None:
            write = update(self.name(collection, doc_id), fields, exists=False)
        else:
            expires = instant(from_fields(doc).get(EXPIRES_FIELD))
            if expires is not None and expires > now:
                return False
            write = update(self.name(collection, doc_id), fields, update_time=doc.get('updateTime'))
        try:
            self.commit([write])
        except Conflict:
            return False
        return True


def _status_word(err) -> str:
    try:
        body = json.loads(err.read().decode('utf-8') or '{}')
        return str((body.get('error') or {}).get('status') or '') if isinstance(body, dict) else ''
    except Exception:   # noqa: BLE001 - no body, or not JSON
        return ''


# ------------------------------------------------------------------ writes for commit()
def update(name: str, data: dict = None, mask=None, exists=None, update_time=None, increment=None, append=None,
           remove=None) -> dict:
    """A commit's update Write for document `name` (full, or relative to the database's documents): `data` encoded;
    `mask` the field paths to change (None replaces the document whole); `exists` True / False a precondition that
    the document is / is not there; `update_time` that it is unchanged since that read; `increment`, `append`,
    `remove` server-side transforms ({field: n} / {field: [values]})."""
    write = {'update': {'name': name, 'fields': to_fields(data or {})}}
    if mask is not None:
        write['updateMask'] = {'fieldPaths': list(mask)}
    transforms = [{'fieldPath': f, 'increment': encode(n)} for f, n in (increment or {}).items()]
    transforms += [{'fieldPath': f, 'appendMissingElements': {'values': [encode(v) for v in vs]}}
                   for f, vs in (append or {}).items()]
    transforms += [{'fieldPath': f, 'removeAllFromArray': {'values': [encode(v) for v in vs]}}
                   for f, vs in (remove or {}).items()]
    if transforms:
        write['updateTransforms'] = transforms
    if exists is not None:
        write['currentDocument'] = {'exists': bool(exists)}
    elif update_time:
        write['currentDocument'] = {'updateTime': update_time}
    return write


def delete_write(name: str, exists=None) -> dict:
    """A commit's delete Write for document `name` (full, or relative); `exists` True a precondition that it is
    there."""
    write = {'delete': name}
    if exists is not None:
        write['currentDocument'] = {'exists': bool(exists)}
    return write


# ------------------------------------------------------------------ module level: one client per project
_CLIENTS = {}
_CLIENTS_LOCK = threading.Lock()


def client(project: str = None, **kw) -> Client:
    """The process's client for `project` (GOOGLE_CLOUD_PROJECT, then GCP_PROJECT, when None), kept and shared; with
    any keyword (`token`, `opener`, `database`, `timeout`, `retry_waits`, `sleep`, `now`) a new one, not kept."""
    project = project or os.environ.get('GOOGLE_CLOUD_PROJECT') or os.environ.get('GCP_PROJECT') or ''
    if kw:
        return Client(project, **kw)
    with _CLIENTS_LOCK:
        if project not in _CLIENTS:
            _CLIENTS[project] = Client(project)
        return _CLIENTS[project]


def reset() -> None:
    """Forget the kept clients (tests, and a process whose credentials changed)."""
    with _CLIENTS_LOCK:
        _CLIENTS.clear()


def get(project: str, collection: str, doc_id: str, raw: bool = False):
    """client(project).get: the document's fields, or None when it does not exist."""
    return client(project).get(collection, doc_id, raw=raw)


def create(project: str, collection: str, doc_id, data: dict):
    """client(project).create: True when created, False when it was there (doc_id None: the new id)."""
    return client(project).create(collection, doc_id, data)


def set(project: str, collection: str, doc_id: str, data: dict) -> None:   # noqa: A001 - Firestore's own verb
    """client(project).set: the document written whole."""
    client(project).set(collection, doc_id, data)


def merge(project: str, collection: str, doc_id: str, data: dict = None, **kw) -> None:
    """client(project).merge: these fields alone (and `paths`, `increment`, `append`, `remove`)."""
    client(project).merge(collection, doc_id, data, **kw)


def delete(project: str, collection: str, doc_id: str) -> None:
    """client(project).delete: the document deleted; a missing one is fine."""
    client(project).delete(collection, doc_id)


def commit(project: str, writes: list) -> dict:
    """client(project).commit: `writes` in one atomic commit."""
    return client(project).commit(writes)


def batch_get(project: str, refs) -> list:
    """client(project).batch_get: each (collection, doc_id) as its fields or None, in order."""
    return client(project).batch_get(refs)


def run_query(project: str, collection: str, **kw) -> list:
    """client(project).run_query: [(doc_id, fields)] that match `where` (and `order_by`, `limit`, `select`, ...)."""
    return client(project).run_query(collection, **kw)


def claim(project: str, collection: str, doc_id: str, data: dict = None, ttl=None, more_ids=()) -> bool:
    """client(project).claim: True once for the first caller, False for every later one."""
    return client(project).claim(collection, doc_id, data, ttl=ttl, more_ids=more_ids)


def release(project: str, collection: str, doc_id: str) -> bool:
    """client(project).release: the claim or lock deleted; False, never raising, when Firestore is unreachable."""
    return client(project).release(collection, doc_id)


def lock(project: str, collection: str, doc_id: str, ttl, data: dict = None) -> bool:
    """client(project).lock: True when this call holds the lease, False while another holder's runs."""
    return client(project).lock(collection, doc_id, ttl, data)
