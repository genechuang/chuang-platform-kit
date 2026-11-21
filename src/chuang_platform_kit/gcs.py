"""Cloud Storage over its JSON API, one client for every project: list, upload, download, copy and delete objects, and a
folder's worth of them, standard library only (google_rest's one call, its live guard and its error).

    from chuang_platform_kit import gcs
    c = gcs.Client()                                         # the token from google_auth, or token= a string or callable
    c.upload_file('my-bucket', 'site/page.html', 'out/page.html', cache_control='no-cache')
    for o in c.list('my-bucket', 'reports/'): ...             # {'name', 'size'}
    c.download_prefix('my-bucket', 'evidence/', 'data/')      # the names written
    c.delete('my-bucket', 'old.json')                        # True when it removed one, False when there was none

An upload with no `content_type` takes the one its file name says (mimetypes), else application/octet-stream. One with
`cache_control` (a page a CDN or a browser must not keep) is a multipart upload that sets the object's metadata with
its bytes in one request; one without is a plain media upload. A refusal is google_rest.GoogleError (Google's status
and message; 0 for no answer), a write off Cloud Run its RefusedHere unless the project's opt-in variable says a run by
hand (google_rest's live guard). `opener` (urlopen's shape) is the test seam.
"""
import json
import mimetypes
import os
import urllib.parse
import uuid

from . import google_rest

API = 'https://storage.googleapis.com'
UPLOAD_TIMEOUT = 300
TIMEOUT = 60
OCTET = 'application/octet-stream'


def _q(name: str) -> str:
    return urllib.parse.quote(name, safe='')


def content_type_for(path: str) -> str:
    """The content type a file's name says (mimetypes), else application/octet-stream."""
    return mimetypes.guess_type(path)[0] or OCTET


class Client:
    """One Cloud Storage client: `token` a string, a callable asked per call, or None for google_auth's; `opener` the
    transport (urlopen's shape), the real one by default; `guard` google_rest's live guard ('writes' by default; 'none'
    for a run by hand whose own flag is the ask to write, said where it makes the client)."""

    def __init__(self, token=None, opener=None, timeout: float = TIMEOUT, guard: str = 'writes'):
        self.token, self.opener, self.timeout, self.guard = token, opener, timeout, guard

    def _call(self, method: str, url: str, timeout: float = None, **kw):
        return google_rest.call(method, url, self.token, opener=self.opener, timeout=timeout or self.timeout,
                                guard=self.guard, what='Cloud Storage call', **kw)

    def list(self, bucket: str, prefix: str = '') -> list:
        """The bucket's live objects under `prefix`, [{name, size}], by name, every page."""
        out, page = [], ''
        while True:
            query = urllib.parse.urlencode({'prefix': prefix, 'fields': 'items(name,size),nextPageToken',
                                            **({'pageToken': page} if page else {})})
            got = self._call('GET', f'{API}/storage/v1/b/{_q(bucket)}/o?{query}')
            out += [{'name': o['name'], 'size': int(o.get('size') or 0)} for o in got.get('items') or []]
            page = got.get('nextPageToken') or ''
            if not page:
                return sorted(out, key=lambda o: o['name'])

    def upload_bytes(self, bucket: str, name: str, data: bytes, content_type: str = OCTET,
                     cache_control: str = None) -> dict:
        """`data` as the object `name`, with its `content_type` and, when given, its `cache_control`; the object's
        metadata as Google answers it."""
        content_type = content_type or OCTET
        if not cache_control:
            query = urllib.parse.urlencode({'uploadType': 'media', 'name': name})
            return self._call('POST', f'{API}/upload/storage/v1/b/{_q(bucket)}/o?{query}', data=data,
                              content_type=content_type, timeout=UPLOAD_TIMEOUT)
        boundary = f'gcs-{uuid.uuid4().hex}'
        meta = json.dumps({'name': name, 'cacheControl': cache_control, 'contentType': content_type}).encode('utf-8')
        body = (f'--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n'.encode('utf-8') + meta +
                f'\r\n--{boundary}\r\nContent-Type: {content_type}\r\n\r\n'.encode('utf-8') + data +
                f'\r\n--{boundary}--\r\n'.encode('utf-8'))
        return self._call('POST', f'{API}/upload/storage/v1/b/{_q(bucket)}/o?uploadType=multipart', data=body,
                          content_type=f'multipart/related; boundary={boundary}', timeout=UPLOAD_TIMEOUT)

    def upload_file(self, bucket: str, name: str, path: str, content_type: str = None, cache_control: str = None) -> dict:
        """The file at `path` as the object `name` (upload_bytes), its content type from its name when not given."""
        with open(path, 'rb') as f:
            data = f.read()
        return self.upload_bytes(bucket, name, data, content_type or content_type_for(path), cache_control)

    def download(self, bucket: str, name: str) -> bytes:
        """The object's bytes."""
        return self._call('GET', f'{API}/storage/v1/b/{_q(bucket)}/o/{_q(name)}?alt=media', raw=True)

    def download_text(self, bucket: str, name: str, encoding: str = 'utf-8') -> str:
        """The object's text."""
        return self.download(bucket, name).decode(encoding)

    def copy(self, bucket: str, source: str, target: str, target_bucket: str = None) -> dict:
        """One object copied to another name (in `target_bucket`, else the same bucket), server side."""
        return self._call('POST', f'{API}/storage/v1/b/{_q(bucket)}/o/{_q(source)}/copyTo/b/'
                                  f'{_q(target_bucket or bucket)}/o/{_q(target)}', data=b'')

    def delete(self, bucket: str, name: str) -> bool:
        """The object removed: True when it was there, False when there was none (a 404)."""
        try:
            self._call('DELETE', f'{API}/storage/v1/b/{_q(bucket)}/o/{_q(name)}', raw=True)
            return True
        except google_rest.GoogleError as e:
            if getattr(e, 'status', 0) == 404:
                return False
            raise

    def download_prefix(self, bucket: str, prefix: str, dest: str) -> list:
        """Every object directly under `prefix` (none in a deeper folder) into `dest`, by the name after the prefix;
        the names written, sorted."""
        os.makedirs(dest, exist_ok=True)
        names = []
        for o in self.list(bucket, prefix):
            rel = o['name'][len(prefix):]
            if not rel or '/' in rel:
                continue
            with open(os.path.join(dest, rel), 'wb') as f:
                f.write(self.download(bucket, o['name']))
            names.append(rel)
        return sorted(names)

    def upload_dir(self, bucket: str, prefix: str, src: str, cache_control: str = None) -> list:
        """Every file directly in `src` to `<prefix><name>`; the names uploaded, sorted."""
        names = sorted(n for n in os.listdir(src) if os.path.isfile(os.path.join(src, n)))
        for n in names:
            self.upload_file(bucket, prefix + n, os.path.join(src, n), cache_control=cache_control)
        return names
