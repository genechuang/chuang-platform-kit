#!/usr/bin/env python3
"""image_keep: every image Cloud Run runs keeps a `keep-` tag in Artifact Registry, so a cleanup policy never deletes
a deployed one. google_rest is faked (nothing reaches Google).

Run:  python -B tests/test-image-keep.py
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import image_keep as K  # noqa: E402
from chuang_platform_kit.google_rest import GoogleError  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


PROJECT, REGION = 'example-project', 'us-east1'
REPO = f'{REGION}-docker.pkg.dev/{PROJECT}/images'
D = {n: 'sha256:' + (n * 64)[:64] for n in 'abcdef'}
VER = f'projects/{PROJECT}/locations/{REGION}/repositories/images/packages/{{p}}/versions/{{d}}'
BASE = f'https://run.googleapis.com/v2/projects/{PROJECT}/locations/{REGION}'


class Rest:
    """google_rest, faked: services, revisions, jobs and each package's keep tags."""

    def __init__(self, tags=None, refuse=()):
        self.calls, self.tags, self.refuse = [], tags or {}, refuse

    def list_all(self, url, key, token=None, **kw):
        self.calls.append(('LIST', url, None))
        if key == 'services':
            return [{'name': f'{BASE}/services/api', 'trafficStatuses': [
                        {'revision': 'api-r2', 'percent': 100}, {'revision': 'api-r3', 'tag': 'dev'},
                        {'revision': 'api-r1', 'percent': 0}]},
                    {'name': f'{BASE}/services/site', 'trafficStatuses': [{'revision': 'site-r9', 'percent': 100}]}]
        if key == 'jobs':
            return [{'name': f'{BASE}/jobs/weekly', 'template': {'template': {'containers': [{'image': f'{REPO}/daily@{D["d"]}'}]}}},
                    {'name': f'{BASE}/jobs/weekly-dev', 'template': {'template': {'containers': [{'image': f'{REPO}/daily@{D["e"]}'}]}}}]
        if key == 'packages':
            names = sorted({'api', 'daily', *self.tags})
            return [{'name': f'projects/{PROJECT}/locations/{REGION}/repositories/images/packages/{n}'} for n in names]
        if key == 'tags':
            pkg = url.split('/packages/')[1].split('/')[0]
            if pkg in self.refuse:
                raise RuntimeError('HTTP 403 denied')
            return [{'name': f'projects/x/packages/{pkg}/tags/{t}', 'version': v} for t, v in self.tags.get(pkg, {}).items()]
        raise AssertionError(url)

    def call(self, method, url, token=None, body=None, **kw):
        self.calls.append((method, url, body))
        if method == 'GET':
            images = {'api-r2': f'{REPO}/api@{D["a"]}', 'api-r3': f'{REPO}/api@{D["b"]}', 'site-r9': f'gcr.io/other/site@{D["c"]}'}
            return {'containers': [{'image': images[url.rsplit('/', 1)[-1]]}]}
        if 'refuse-write' in self.refuse:
            raise RuntimeError('HTTP 403 denied')
        if method == 'POST' and 'made-elsewhere' in self.refuse:
            raise GoogleError('POST tags: HTTP 409 already exists', status=409)
        if method == 'DELETE' and 'gone-already' in self.refuse:
            raise GoogleError('DELETE tags: HTTP 404 not found', status=404)
        return {}


def writes(rest):
    """The tag writes: (method, the path after /packages/, the version's digest head)."""
    return [(m, u.split('/packages/')[1], ((b or {}).get('version') or '').rsplit('/', 1)[-1][:9])
            for m, u, b in rest.calls if m in ('POST', 'PATCH', 'DELETE')]


ALL = ['api', 'api-dev', 'site', 'weekly', 'weekly-dev']

# The positive case: the serving and tagged revisions of a service and every job each get keep-<consumer>.
rest = Rest()
found = K.consumers(PROJECT, REGION, 't', rest=rest)
check("consumers: serving and tagged revisions and every job; a revision with no traffic and no tag is none", sorted(found), ALL)
lines = K.keep(PROJECT, REGION, 't', rest=rest)
check("each deployed image's tag is created on its digest", sorted(writes(rest)), sorted([
    ('POST', 'api/tags?tagId=keep-api', D['a'][:9]), ('POST', 'api/tags?tagId=keep-api-dev', D['b'][:9]),
    ('POST', 'daily/tags?tagId=keep-weekly', D['d'][:9]), ('POST', 'daily/tags?tagId=keep-weekly-dev', D['e'][:9])]))
check("the summary, and an image held elsewhere is left alone",
      (lines[-1], any('site runs gcr.io' in l for l in lines)),
      ('image keep: 5 image(s) Cloud Run runs; 4 tag(s) written, 0 already right', True))
check("the tags are asked for with Artifact Registry's own name filter",
      any(u.endswith('/tags?filter=name%3D%22projects%2Fexample-project%2Flocations%2Fus-east1%2Frepositories%2Fimages%2Fpackages%2Fapi%2Ftags%2Fkeep-%2A%22')
          for m, u, b in rest.calls if m == 'LIST'), True)

rest = Rest()
lines = K.keep(PROJECT, REGION, 't', rest=rest, found={'api': f'{REPO}/api@{D["a"]}'})
check("`found` given: Cloud Run is not read again",
      ([u for m, u, b in rest.calls if '/services' in u or '/jobs' in u], lines[-1]),
      ([], 'image keep: 1 image(s) Cloud Run runs; 1 tag(s) written, 0 already right'))

rest = Rest(tags={'api': {'keep-api': VER.format(p='api', d=D['a']), 'keep-api-dev': VER.format(p='api', d=D['f']),
                          'keep-old': VER.format(p='api', d=D['f'])},
                  'daily': {'keep-weekly': VER.format(p='daily', d=D['d']), 'keep-weekly-dev': VER.format(p='daily', d=D['e'])}})
lines = K.keep(PROJECT, REGION, 't', rest=rest)
check("a tag on its digest is not written, a moved one is patched, a gone consumer's is deleted", sorted(writes(rest)), sorted([
    ('PATCH', 'api/tags/keep-api-dev?updateMask=version', D['b'][:9]), ('DELETE', 'api/tags/keep-old', '')]))
check("its summary", lines[-1], 'image keep: 5 image(s) Cloud Run runs; 1 tag(s) written, 3 already right')

rest = Rest(tags={'gone': {'keep-gone': VER.format(p='gone', d=D['f'])}})
K.keep(PROJECT, REGION, 't', rest=rest)
check("a package nothing runs any more loses its keep tags",
      [w for w in writes(rest) if w[0] == 'DELETE'], [('DELETE', 'gone/tags/keep-gone', '')])
rest = Rest(refuse=('made-elsewhere',))
lines = K.keep(PROJECT, REGION, 't', rest=rest)
check("a create another run made first (409) is set with a PATCH, no WARNING",
      (sum(w[0] == 'PATCH' for w in writes(rest)), [l for l in lines if l.startswith('WARNING')]), (4, []))
rest = Rest(tags={'gone': {'keep-gone': VER.format(p='gone', d=D['f'])}}, refuse=('gone-already',))
lines = K.keep(PROJECT, REGION, 't', rest=rest)
check("a delete another run made first (404) is no WARNING", [l for l in lines if 'keep-gone' in l], [])

rest = Rest(refuse=('api',))
lines = K.keep(PROJECT, REGION, 't', rest=rest)
check("a package whose tags cannot be read is left as it is and the rest go on",
      ([w[1].split('/')[0] for w in writes(rest)], any(l.startswith('WARNING: image keep: the keep tags of api') for l in lines)),
      (['daily', 'daily'], True))
rest = Rest(refuse=('refuse-write',))
lines = K.keep(PROJECT, REGION, 't', rest=rest)
check("a refused write is a WARNING a tag", sum(l.startswith('WARNING: image keep: ') and 'not written' in l for l in lines), 4)


class Down(Rest):
    def list_all(self, *a, **k):
        raise RuntimeError('run.googleapis.com could not be reached')


check("Cloud Run unreadable: one WARNING, nothing written, never raised",
      K.keep(PROJECT, REGION, 't', rest=Down())[0].startswith('WARNING: image keep: Cloud Run could not be read'), True)


class Flaky(Rest):
    def __init__(self, fails, status=503):
        super().__init__()
        self.fails, self.status = fails, status

    def call(self, method, url, token=None, body=None, **kw):
        if method == 'GET' and url.endswith('api-r2') and self.fails:
            self.fails -= 1
            raise GoogleError('GET revisions: HTTP 503 The service is currently unavailable.', status=self.status)
        return super().call(method, url, token, body, **kw)


waits = []
found = K.consumers(PROJECT, REGION, 't', rest=Flaky(1), sleep=waits.append)
check("one 503 on a revision read is asked again after the first wait", (sorted(found), waits), (ALL, [K.RETRY_SECONDS[0]]))
waits = []
found = K.consumers(PROJECT, REGION, 't', rest=Flaky(2), sleep=waits.append, retry_seconds=(5, 7))
check("the caller's own waits", (sorted(found), waits), (ALL, [5, 7]))
try:
    K.consumers(PROJECT, REGION, 't', rest=Flaky(1, status=404), sleep=waits.append)
    got = 'no raise'
except GoogleError as e:
    got = e.status
check("a 404 is not retried", got, 404)
lines = K.keep(PROJECT, REGION, 't', rest=Flaky(9), retry_seconds=(0, 0))
check("a 503 that lasts past the retries is still the WARNING",
      lines[0].startswith('WARNING: image keep: Cloud Run could not be read'), True)

check("tag_name: the prefix, and only Artifact Registry's tag characters",
      (K.PREFIX, K.tag_name('api'), K.tag_name('a b/c')), ('keep-', 'keep-api', 'keep-a-b-c'))

print(f"\n{len(fails)} failure(s)")
if fails:
    sys.exit(1)
