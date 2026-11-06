#!/usr/bin/env python3
"""firestore: documents, commits, queries, claims and TTL locks over Firestore's REST API with the standard library.

Offline: every client is given a fake opener (urlopen's shape) and a string or callable token, so nothing reaches
Google and no credential is read. Pinned:
  - the codec both ways (null, bool, int, float, str, bytes, timestamps, arrays, maps) and instant();
  - get / create / set / merge / delete / commit / batch_get / run_query send the request Firestore's REST reference
    names, and read its answers (a 404 is None, a 409 on create is False);
  - claim is one create-only commit (exists=false), with a TTL field and extra ids all or none; release never raises;
  - lock takes a free document create-only, refuses a running lease, takes over an expired one only on the
    updateTime it read, and loses a race as False;
  - failures: no token, a 5xx, a refusal and a dropped connection are Unreachable naming the status, never the token;
    retry_waits tries again; a 401 asks the default token again once.

Run:  python -B tests/test-firestore.py
"""
import io
import json
import os
import sys
import urllib.error
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import firestore as F  # noqa: E402

fails = []
NOW = datetime(2026, 11, 6, 8, 0, tzinfo=timezone.utc)   # utc-ok: an instant
ROOTNAME = 'projects/proj/databases/(default)/documents'
API = f'https://firestore.googleapis.com/v1/{ROOTNAME}'
SECRET = 'tok-SECRET-123'


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, status=''):
    body = json.dumps({'error': {'code': code, 'status': status, 'message': 'no'}}).encode()
    return urllib.error.HTTPError('https://x', code, 'err', {}, io.BytesIO(body))


def fake(*replies):
    """A fake urlopen answering each request in turn (the last answer repeats): a dict or list is a JSON body, an
    exception is raised. `seen` holds (method, url, body, headers)."""
    seen = []

    def opener(req, timeout):
        body = json.loads(req.data.decode()) if req.data else None
        seen.append((req.get_method(), req.full_url, body, dict(req.header_items())))
        r = replies[min(len(seen), len(replies)) - 1]
        if callable(r) and not isinstance(r, type):
            r = r(req)
        if isinstance(r, Exception):
            raise r
        return Resp(json.dumps(r).encode())
    return opener, seen


def db(*replies, **kw):
    opener, seen = fake(*replies)
    kw.setdefault('token', SECRET)
    return F.Client('proj', opener=opener, now=lambda: NOW, **kw), seen


# ------------------------------------------------------------------ the positive case
c, seen = db({'name': f'{ROOTNAME}/config/league', 'fields': {'name': {'stringValue': 'Ace'},
                                                             'courts': {'integerValue': '2'}}})
check("the positive case: get answers the document's fields as plain Python",
      c.get('config', 'league'), {'name': 'Ace', 'courts': 2})
check("  one GET of the document's URL", seen[0][:2], ('GET', f'{API}/config/league'))
check("  with the bearer token", seen[0][3].get('Authorization'), f'Bearer {SECRET}')

# ------------------------------------------------------------------ the codec
value = {'n': None, 'b': True, 'i': 7, 'f': 1.5, 's': 'x', 'raw': b'\x00\x01', 'when': NOW,
         'list': [1, 'a'], 'map': {'k': False}}
enc = F.to_fields(value)
check("encode: a bool is a booleanValue, never an integer", enc['b'], {'booleanValue': True})
check("encode: an int is integerValue text", enc['i'], {'integerValue': '7'})
check("encode: a datetime is a UTC timestampValue with a Z", enc['when'], {'timestampValue': '2026-11-06T08:00:00.000000Z'})
check("encode: a naive datetime is read as UTC", F.encode(datetime(2026, 11, 6, 8, 0)), enc['when'])
check("encode: a zoned datetime is put in UTC",
      F.encode(datetime(2026, 11, 6, 0, 0, tzinfo=timezone(timedelta(hours=-8)))), enc['when'])
check("encode: None is nullValue", enc['n'], {'nullValue': None})
check("encode: a map nests", enc['map'], {'mapValue': {'fields': {'k': {'booleanValue': False}}}})
back = F.from_fields({'name': 'x', 'fields': enc})
check("decode is encode's inverse but timestamps, which stay their text",
      back, {**value, 'when': '2026-11-06T08:00:00.000000Z'})
check("from_fields takes the fields map itself too", F.from_fields(enc)['i'], 7)
check("decode: a value that is not one is None", (F.decode({}), F.decode('x')), (None, None))
check("instant: Firestore's nine digits", F.instant('2026-11-06T08:00:00.123456789Z'),
      NOW.replace(microsecond=123456))
check("instant: epoch seconds", F.instant(NOW.timestamp()), NOW)
check("instant: text that is not a time is None", F.instant('soon'), None)

# ------------------------------------------------------------------ documents
c, seen = db(http_error(404, 'NOT_FOUND'))
check("get: a missing document is None", c.get('config', 'nope'), None)
c, seen = db({'name': 'n', 'fields': {}, 'updateTime': '2026-11-06T08:00:00Z'})
check("get raw: Firestore's document with its updateTime", c.get('a', 'b', raw=True)['updateTime'], '2026-11-06T08:00:00Z')
c, seen = db({})
c.get('a', 'x/y z')
check("an id is percent-encoded in the URL", seen[0][1], f'{API}/a/x%2Fy%20z')

c, seen = db({'name': f'{ROOTNAME}/c/d'})
check("create: True when this call made it", c.create('c', 'd', {'at': NOW}), True)
check("  a POST with documentId", seen[0][:2], ('POST', f'{API}/c?documentId=d'))
check("  the fields encoded", seen[0][2], {'fields': {'at': {'timestampValue': '2026-11-06T08:00:00.000000Z'}}})
c, seen = db(http_error(409, 'ALREADY_EXISTS'))
check("create: False when the document is there", c.create('c', 'd', {}), False)
c, seen = db({'name': f'{ROOTNAME}/usage/AbC123'})
check("create with no id: Firestore's new id", c.create('usage', None, {'n': 1}), 'AbC123')
check("  posted to the collection alone", seen[0][1], f'{API}/usage')

c, seen = db({'writeResults': [{}]})
c.set('c', 'd', {'x': 1})
check("set: one commit of a whole update, no mask, no precondition", seen[0][2],
      {'writes': [{'update': {'name': f'{ROOTNAME}/c/d', 'fields': {'x': {'integerValue': '1'}}}}]})
check("  to :commit", seen[0][:2], ('POST', f'{API}:commit'))

c, seen = db({})
c.merge('live', 'g1', {'at': NOW, 'what': 'vote'}, increment={'version': 1})
w = seen[0][2]['writes'][0]
check("merge: the mask is data's keys", w['updateMask'], {'fieldPaths': ['at', 'what']})
check("merge: an increment is a server-side transform", w['updateTransforms'],
      [{'fieldPath': 'version', 'increment': {'integerValue': '1'}}])
c, seen = db({})
c.merge('plans', 'p', append={'held': ['B']})
w = seen[0][2]['writes'][0]
check("merge with append alone: an empty mask and appendMissingElements",
      (w['updateMask'], w['updateTransforms']),
      ({'fieldPaths': []}, [{'fieldPath': 'held', 'appendMissingElements': {'values': [{'stringValue': 'B'}]}}]))
c, seen = db({})
c.merge('live', 'g', {'steps': {'ann': 5}}, paths=['steps.`ann`'])
check("merge: `paths` names the mask into a map", seen[0][2]['writes'][0]['updateMask'], {'fieldPaths': ['steps.`ann`']})

c, seen = db(http_error(404, 'NOT_FOUND'))
c.delete('c', 'gone')
check("delete: a missing document is fine", seen[0][:2], ('DELETE', f'{API}/c/gone'))

c, seen = db({'commitTime': 't'})
got = c.commit([F.update('chat/x', {'a': 1}), F.delete_write('chat/y'),
                {'transform': {'document': f'{ROOTNAME}/k/1', 'fieldTransforms': []}}])
check("commit: Firestore's answer", got, {'commitTime': 't'})
check("commit: relative names made full, full ones kept",
      [seen[0][2]['writes'][0]['update']['name'], seen[0][2]['writes'][1]['delete'],
       seen[0][2]['writes'][2]['transform']['document']],
      [f'{ROOTNAME}/chat/x', f'{ROOTNAME}/chat/y', f'{ROOTNAME}/k/1'])
try:
    c.commit([F.delete_write('a/b')] * 501)
    over = None
except ValueError as e:
    over = str(e)
check("commit: past 500 writes is a ValueError", over, '501 writes: a commit takes at most 500')

c, seen = db([{'found': {'name': f'{ROOTNAME}/a/2', 'fields': {'v': {'integerValue': '2'}}}},
              {'missing': f'{ROOTNAME}/a/1'}])
check("batch_get: in refs' order, None for a missing one", c.batch_get([('a', '1'), ('a', '2')]), [None, {'v': 2}])
check("  one batchGet of full names", seen[0][2], {'documents': [f'{ROOTNAME}/a/1', f'{ROOTNAME}/a/2']})
check("batch_get of nothing asks nothing", (db({})[0].batch_get([]),), ([],))

c, seen = db([{'readTime': 't'}, {'document': {'name': f'{ROOTNAME}/u/a%20b', 'fields': {'d': {'stringValue': '2026-11-01'}}}}])
rows = c.run_query('u', where=[('d', '>=', '2026-11-01'), ('kind', '==', 'x')], order_by=['-d'], limit=5, select=['d'])
check("run_query: (id unquoted, fields), rows without a document skipped", rows, [('a b', {'d': '2026-11-01'})])
q = seen[0][2]['structuredQuery']
check("  the filters ANDed with Firestore's words", q['where'],
      {'compositeFilter': {'op': 'AND', 'filters': [
          {'fieldFilter': {'field': {'fieldPath': 'd'}, 'op': 'GREATER_THAN_OR_EQUAL', 'value': {'stringValue': '2026-11-01'}}},
          {'fieldFilter': {'field': {'fieldPath': 'kind'}, 'op': 'EQUAL', 'value': {'stringValue': 'x'}}}]}})
check("  order, select and limit", (q['orderBy'], q['select'], q['limit']),
      ([{'field': {'fieldPath': 'd'}, 'direction': 'DESCENDING'}], {'fields': [{'fieldPath': 'd'}]}, 5))
check("  posted to :runQuery", seen[0][1], f'{API}:runQuery')
c, seen = db([])
c.run_query('games/412/players', where=[('in', 'EQUAL', True)])
check("run_query on a subcollection: the parent document's :runQuery and the last id",
      (seen[0][1], seen[0][2]['structuredQuery']['from'], 'compositeFilter' in seen[0][2]['structuredQuery']['where']),
      (f'{API}/games/412:runQuery', [{'collectionId': 'players'}], False))
c, seen = db([{'document': {'name': f'{ROOTNAME}/u/z', 'fields': {}}}])
check("run_query raw: Firestore's documents; no where reads the collection",
      (c.run_query('u', raw=True), 'where' in seen[0][2]['structuredQuery']),
      ([{'name': f'{ROOTNAME}/u/z', 'fields': {}}], False))

# ------------------------------------------------------------------ claims
c, seen = db({'writeResults': [{}, {}]})
check("claim: True for the first caller", c.claim('sent', 'invite-42', {'by': 'api'}, ttl=60, more_ids=['invite-43']), True)
ws = seen[0][2]['writes']
check("  one commit, every id create-only", [w['currentDocument'] for w in ws], [{'exists': False}] * 2)
check("  the TTL field a minute on", ws[0]['update']['fields']['expires_at'],
      {'timestampValue': '2026-11-06T08:01:00.000000Z'})
check("  the data beside it", ws[1]['update']['fields']['by'], {'stringValue': 'api'})
for code, status in ((409, 'ALREADY_EXISTS'), (400, 'FAILED_PRECONDITION')):
    c, seen = db(http_error(code, status))
    check(f"claim: False when it is there (HTTP {code} {status})", c.claim('sent', 'x'), False)
c, seen = db(http_error(400, 'INVALID_ARGUMENT'))
try:
    c.claim('sent', 'x')
    why = None
except F.Unreachable as e:
    why = str(e)
check("claim: any other refusal raises Unreachable naming it", why, 'HTTP 400 INVALID_ARGUMENT')
c, seen = db({})
check("release: True when deleted", c.release('sent', 'x'), True)
c, seen = db(urllib.error.URLError('down'))
check("release: False, never raising, when unreachable", c.release('sent', 'x'), False)

# ------------------------------------------------------------------ locks
c, seen = db(http_error(404, 'NOT_FOUND'), {'writeResults': [{}]})
check("lock: a free document is taken", c.lock('locks', 'sync', ttl=120, data={'by': 'job'}), True)
w = seen[1][2]['writes'][0]
check("  create-only, with locked_at, expires_at and the data",
      (w['currentDocument'], sorted(w['update']['fields']), w['update']['fields']['expires_at']),
      ({'exists': False}, ['by', 'expires_at', 'locked_at'], {'timestampValue': '2026-11-06T08:02:00.000000Z'}))
held = {'name': f'{ROOTNAME}/locks/sync', 'updateTime': 'U1',
        'fields': {'expires_at': {'timestampValue': '2026-11-06T08:01:00Z'}}}
c, seen = db(held)
check("lock: refused while another holder's lease runs", c.lock('locks', 'sync', ttl=120), False)
check("  and nothing written", len(seen), 1)
expired = dict(held, fields={'expires_at': {'timestampValue': '2026-11-06T07:59:00Z'}})
c, seen = db(expired, {'writeResults': [{}]})
check("lock: an expired lease is taken over", c.lock('locks', 'sync', ttl=timedelta(minutes=2)), True)
check("  only on the updateTime it read", seen[1][2]['writes'][0]['currentDocument'], {'updateTime': 'U1'})
epoch = dict(held, fields={'expires_at': {'doubleValue': NOW.timestamp() + 30}})
c, seen = db(epoch)
check("lock: an epoch-seconds expiry is read too", c.lock('locks', 'sync', ttl=120), False)
c, seen = db(expired, http_error(400, 'FAILED_PRECONDITION'))
check("lock: a takeover that lost the race is False", c.lock('locks', 'sync', ttl=120), False)
c, seen = db(http_error(503, 'UNAVAILABLE'))
try:
    c.lock('locks', 'sync', ttl=120)
    why = None
except F.Unreachable as e:
    why = str(e)
check("lock: unreachable raises, for the caller to fail open or closed", why, 'HTTP 503 UNAVAILABLE')

# ------------------------------------------------------------------ failures and tokens
c, seen = db({}, token='')
try:
    c.get('a', 'b')
    why = None
except F.Unreachable as e:
    why = str(e)
check("no token: Unreachable, nothing sent", (why, len(seen)), ('no access token', 0))
c, seen = db(urllib.error.URLError('reset'))
try:
    c.get('a', 'b')
    why = None
except F.Unreachable as e:
    why = str(e)
check("a dropped connection names its kind", why, 'URLError')
check("  never the token", SECRET in (why or ''), False)
waits = []
c, seen = db(http_error(503, 'UNAVAILABLE'), http_error(500), {'fields': {'v': {'integerValue': '1'}}},
             retry_waits=(1, 3), sleep=waits.append)
check("retry_waits: tried again after each wait until it answers", (c.get('a', 'b'), waits), ({'v': 1}, [1, 3]))
c, seen = db(lambda req: http_error(503, 'UNAVAILABLE'), retry_waits=(1,), sleep=lambda s: None)
try:
    c.get('a', 'b')
    why = None
except F.Unreachable as e:
    why = str(e)
check("  the last miss names the tries", why, 'HTTP 503 UNAVAILABLE; 2 tries')
c, seen = db(http_error(403, 'PERMISSION_DENIED'), retry_waits=(1,), sleep=lambda s: None)
try:
    c.get('a', 'b')
    why = None
except F.Unreachable as e:
    why = str(e)
check("  a refusal (4xx) is not tried again", (why, len(seen)), ('HTTP 403 PERMISSION_DENIED', 1))
asked = []
c, seen = db({}, token=lambda: asked.append(1) or 'from-callable')
c.get('a', 'b')
check("a callable token is asked at the call", (len(asked), seen[0][3]['Authorization']), (1, 'Bearer from-callable'))

# the default token: google_auth's, kept, and asked again once on a 401
from chuang_platform_kit import google_auth  # noqa: E402
minted = []
real = google_auth.access_token
google_auth.access_token = lambda *a, **k: minted.append(1) or f'default-{len(minted)}'
try:
    opener, seen = fake(http_error(401, 'UNAUTHENTICATED'), {'fields': {}}, {'fields': {}})
    c = F.Client('proj', opener=opener)
    c.get('a', 'b')
    c.get('a', 'c')
    check("default token: a 401 asks google_auth again once and the call goes through",
          [h[3]['Authorization'] for h in seen], ['Bearer default-1', 'Bearer default-2', 'Bearer default-2'])
    check("  then the token is kept between calls", len(minted), 2)
finally:
    google_auth.access_token = real

# ------------------------------------------------------------------ module level
F.reset()
check("client(project) is kept per project", F.client('p1') is F.client('p1'), True)
opener, seen = fake({'name': f'{ROOTNAME}/c/d'})
check("client(project, **kw) is a new one", F.client('p1', opener=opener, token='t') is F.client('p1'), False)
try:
    F.Client('')
    why = None
except ValueError as e:
    why = str(e)
check("a client needs a project", why, 'a Firestore client needs a project')
F.reset()

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
