#!/usr/bin/env python3
"""chuang_platform_kit.gcs: Cloud Storage over its JSON API, through google_rest's one call.

Pinned (the positive case first, offline: a stand-in transport recording every request):

- list: every page by pageToken, {name, size} sorted by name, the bucket and prefix in the address
- an upload with no cache_control is a media upload with the content type the file name says; one with it is one
  multipart request carrying {name, cacheControl, contentType} and the bytes
- download returns the bytes; download_text decodes them; copy names both objects server side
- delete: True when removed, False on a 404, any other refusal raised as GoogleError with Google's status
- download_prefix writes only the objects directly under the prefix; upload_dir uploads only the folder's own files
- the token is sent as a bearer; google_rest.call's JSON callers are unchanged (its own suite)

Run:  python -B tests/test-gcs.py
"""
import io
import json
import os
import sys
import tempfile
import urllib.error

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import gcs, google_rest  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class Answer:
    def __init__(self, body=b'{}'):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode('utf-8')

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Opener:
    """Answers each request from `routes` [(method, url substring, answer or status)], first match; records them."""

    def __init__(self, routes):
        self.routes, self.requests = routes, []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        for method, part, answer in self.routes:
            if req.get_method() == method and part in req.full_url:
                if isinstance(answer, int):
                    raise urllib.error.HTTPError(req.full_url, answer, 'x', {}, io.BytesIO(b'{"error": {"message": "no"}}'))
                return Answer(answer)
        raise urllib.error.HTTPError(req.full_url, 500, 'x', {}, io.BytesIO(b'{}'))


op = Opener([('GET', 'pageToken=p2', {'items': [{'name': 'r/a.json', 'size': '5'}]}),
             ('GET', '/o?', {'items': [{'name': 'r/c.json', 'size': '7'}, {'name': 'r/b.json'}], 'nextPageToken': 'p2'})])
c = gcs.Client('tok', op)
check('list: every page, {name, size} by name, the bucket and prefix asked, the bearer sent',
      (c.list('my-bucket', 'r/'), '/b/my-bucket/o?' in op.requests[0].full_url, 'prefix=r%2F' in op.requests[0].full_url,
       op.requests[0].get_header('Authorization')),
      ([{'name': 'r/a.json', 'size': 5}, {'name': 'r/b.json', 'size': 0}, {'name': 'r/c.json', 'size': 7}], True, True,
       'Bearer tok'))

with tempfile.TemporaryDirectory() as tmp:
    page = os.path.join(tmp, 'page.html')
    with open(page, 'wb') as f:
        f.write(b'<html></html>')
    op = Opener([('POST', 'uploadType=media', {'name': 'site/page.html'}), ('POST', 'uploadType=multipart', {'name': 'x'})])
    c = gcs.Client('tok', op)
    c.upload_file('b', 'site/page.html', page)
    media = op.requests[0]
    c.upload_file('b', 'site/page.html', page, cache_control='no-cache')
    multi = op.requests[1]
    meta = json.loads(multi.data.split(b'\r\n\r\n', 1)[1].split(b'\r\n', 1)[0])
    check('a media upload takes the name\'s content type; a cache_control upload is one multipart request',
          (media.get_header('Content-type'), media.data, 'name=site%2Fpage.html' in media.full_url,
           multi.get_header('Content-type').startswith('multipart/related; boundary='), meta, b'<html></html>' in multi.data),
          ('text/html', b'<html></html>', True, True,
           {'name': 'site/page.html', 'cacheControl': 'no-cache', 'contentType': 'text/html'}, True))

op = Opener([('GET', 'alt=media', b'\xe2\x9c\x93 bytes'), ('POST', '/copyTo/', {'done': True})])
c = gcs.Client('tok', op)
check('download returns bytes, download_text decodes, copy names both objects server side',
      (c.download('b', 'dir/x.bin'), c.download_text('b', 'dir/x.bin'), c.copy('b', 'daily/a.dump', 'monthly/a.dump'),
       '/o/daily%2Fa.dump/copyTo/b/b/o/monthly%2Fa.dump' in op.requests[-1].full_url),
      (b'\xe2\x9c\x93 bytes', '✓ bytes', {'done': True}, True))

op = Opener([('DELETE', '/o/there', b''), ('DELETE', '/o/gone', 404), ('DELETE', '/o/locked', 403)])
c = gcs.Client('tok', op)
try:
    c.delete('b', 'locked')
    locked = 'no error'
except google_rest.GoogleError as e:
    locked = e.status
check('delete: True when removed, False on a 404, another refusal raised with its status',
      (c.delete('b', 'there'), c.delete('b', 'gone'), locked), (True, False, 403))

with tempfile.TemporaryDirectory() as tmp:
    op = Opener([('GET', 'alt=media', b'data'),
                 ('GET', '/o?', {'items': [{'name': 'ev/a.pdf'}, {'name': 'ev/sub/b.pdf'}, {'name': 'ev/'}]})])
    got = gcs.Client('tok', op).download_prefix('b', 'ev/', os.path.join(tmp, 'out'))
    written = sorted(os.listdir(os.path.join(tmp, 'out')))
    src = os.path.join(tmp, 'src')
    os.makedirs(os.path.join(src, 'deeper'))
    for n in ('two.json', 'one.json'):
        with open(os.path.join(src, n), 'w') as f:
            f.write('{}')
    op2 = Opener([('POST', 'uploadType=media', {})])
    up = gcs.Client('tok', op2).upload_dir('b', 'pulls/', src)
    check('download_prefix writes only the direct objects; upload_dir uploads only the folder\'s own files',
          (got, written, up, ['name=pulls%2Fone.json' in r.full_url or 'name=pulls%2Ftwo.json' in r.full_url
                              for r in op2.requests]),
          (['a.pdf'], ['a.pdf'], ['one.json', 'two.json'], [True, True]))

op = Opener([('POST', 'uploadType=multipart', {'name': 'h.png'}),
             ('GET', '/o/heat%2Fh.png', {'name': 'heat/h.png', 'metadata': {'data_hash': 'abc'}, 'updated': '2026-11-20T01:00:00Z'}),
             ('GET', '/o/none', 404)])
c = gcs.Client('tok', op)
c.upload_bytes('b', 'heat/h.png', b'png', 'image/png', metadata={'data_hash': 'abc', 'response_count': 7})
meta = json.loads(op.requests[0].data.split(b'\r\n\r\n', 1)[1].split(b'\r\n', 1)[0])
check('custom metadata goes up in the multipart upload as text; stat reads an object, None for one not there',
      (meta, c.stat('b', 'heat/h.png')['metadata'], c.stat('b', 'none')),
      ({'name': 'heat/h.png', 'contentType': 'image/png', 'metadata': {'data_hash': 'abc', 'response_count': '7'}},
       {'data_hash': 'abc'}, None))

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
