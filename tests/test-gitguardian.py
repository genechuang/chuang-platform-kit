#!/usr/bin/env python3
"""chuang_platform_kit.gitguardian: a repository's incidents listed with their occurrences, ignored or resolved with a note, over a fake API.

Pinned (the positive case first): open_incidents() finds the source by full name, lists the open
states newest first and attaches each incident's occurrences; detector() and files() give the safe
parts; ignore() posts the note then the reason (a reason outside the list is refused before any
call); resolve() posts the note then secret_revoked; a 403 on a write is a PermissionError naming
the scope, while a 403 on a read propagates; an unknown repository is a RuntimeError.

Run:  python -B tests/test-gitguardian.py
"""
import io
import json
import os
import sys
import urllib.error

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import gitguardian as GG  # noqa: E402

fails = []


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


INCIDENTS = [{'id': 1, 'status': 'TRIGGERED', 'detector': {'display_name': 'Google API Key'}},
             {'id': 2, 'status': 'RESOLVED', 'detector': {'name': 'generic'}},
             {'id': 3, 'status': 'ASSIGNED', 'detector': {}}]
OCCURRENCES = {1: [{'filepath': 'a.py'}, {'filepath': 'a.py'}, {'filename': 'b.env'}], 3: []}


def fake(calls, writes_ok=True):
    def opener(req, timeout=None):
        path = req.full_url.replace(GG.API, '')
        body = json.loads(req.data.decode()) if req.data else None
        calls.append((req.get_method(), path, body, req.get_header('Authorization')))
        if path.startswith('/sources?'):
            return Resp(json.dumps([{'id': 9, 'full_name': 'Other/Repo'}, {'id': 42, 'full_name': 'genechuang/SMADPickleBot'}]).encode())
        if path.startswith('/sources/42/incidents/secrets'):
            return Resp(json.dumps(INCIDENTS).encode())
        if path.startswith('/occurrences/secrets?'):
            iid = int(path.split('incident_id=')[1].split('&')[0])
            if iid == 3:
                raise urllib.error.HTTPError(req.full_url, 500, 'boom', {}, None)
            return Resp(json.dumps(OCCURRENCES.get(iid, [])).encode())
        if req.get_method() == 'POST':
            if not writes_ok:
                raise urllib.error.HTTPError(req.full_url, 403, 'forbidden', {}, None)
            return Resp(b'{"ok": true}')
        raise urllib.error.HTTPError(req.full_url, 404, 'nope', {}, None)
    return opener


print("the positive case: open incidents by full name, newest first, with occurrences; the safe parts")
calls = []
found = GG.open_incidents('k', 'genechuang/SMADPickleBot', opener=fake(calls))
check("open", ([i['id'] for i in found], GG.detector(found[0]), GG.files(found[0]), GG.detector(found[1]), found[1].get('occurrences_error')),
      ([1, 3], 'Google API Key', ['a.py', 'b.env'], 'secret', 'HTTPError'))
check("the calls", [(m, p.split('?')[0]) for m, p, _b, _a in calls],
      [('GET', '/sources'), ('GET', '/sources/42/incidents/secrets'), ('GET', '/occurrences/secrets'), ('GET', '/occurrences/secrets')])
check("the token header", calls[0][3], 'Token k')
check("every state", [i['id'] for i in GG.incidents('k', 'genechuang/SMADPickleBot', states=(), opener=fake([]))], [1, 2, 3])

print("ignore() posts the note then the reason; a reason outside the list is refused before any call")
calls = []
GG.ignore('k', 1, 'false_positive', 'a label, not a value', opener=fake(calls))
check("ignored", [(m, p, b) for m, p, b, _a in calls],
      [('POST', '/incidents/secrets/1/notes', {'comment': 'a label, not a value'}), ('POST', '/incidents/secrets/1/ignore', {'ignore_reason': 'false_positive'})])
calls = []
try:
    GG.ignore('k', 1, 'because', opener=fake(calls))
    bad = 'no error'
except ValueError as e:
    bad = str(e)
check("bad reason", (bad, calls), ("ignore reason must be one of false_positive, test_credential, low_risk", []))

print("resolve() posts the note then secret_revoked; a 403 on a write is a PermissionError naming the scope; a read 403 propagates")
calls = []
GG.resolve('k', 3, 'rotated 10/8/26', opener=fake(calls))
check("resolved", [(m, p, b) for m, p, b, _a in calls],
      [('POST', '/incidents/secrets/3/notes', {'comment': 'rotated 10/8/26'}), ('POST', '/incidents/secrets/3/resolve', {'secret_revoked': True})])
try:
    GG.ignore('k', 1, 'test_credential', opener=fake([], writes_ok=False))
    denied = 'no error'
except PermissionError as e:
    denied = str(e)
check("read-only key", denied, 'GitGuardian refused POST /incidents/secrets/1/ignore (403): the key needs the incidents:write scope')
try:
    GG.source_id('k', 'nobody/nothing', opener=fake([]))
    unknown = 'no error'
except RuntimeError as e:
    unknown = str(e)
check("unknown repo", unknown, 'GitGuardian has no source named nobody/nothing')

print()
print(f"{len(fails)} failure(s)" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
