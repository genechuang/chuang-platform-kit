#!/usr/bin/env python3
"""chuang_sdlc.cicd.build_once: build once, deploy the same bytes (twelve-factor V; SMADPickleBot's pipeline/shared/
build-once.py until #235, 11/8/26; Gene, 10/10/26 8:07 AM PT: "follow 1 build pipeline and 12 factors in their own
pipelines"). Every image and every Cloud Function source is named by a content key of exactly the files that go into
it; whichever path builds it first, the other deploys it by digest (an image) or by its object (a function's zip).

These pin the key (what changes it and what must not), the ignore rules (gcloudignore's), the staging, the registry and
bucket lookups with gcloud faked, the base images by digest, the marks, and the command line. The host's own wiring
(every image built through its one action, every function from its zip, its deploy scripts) stays in its suite.

Run:  python -B tests/test-sdlc-cicd-build-once.py
"""
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))

from chuang_sdlc.cicd import build_once as bo  # noqa: E402

# A synthetic base-images lock: the digests every FROM is pinned to. The host's real one is its own file (BASE_IMAGES_LOCK
# names it), so here every unlocked call reads this one.
LOCK = {'python:3.14-slim': 'sha256:' + 'a' * 64, 'python:3.15-slim': 'sha256:' + 'b' * 64}
bo.load_lock = lambda path=None: LOCK

fails = []


def eq(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


def write(root, rel, body):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path) or root, exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(body)


@contextlib.contextmanager
def fixture():
    """A tiny build context: a Dockerfile copying some of it, an ignore file, files outside."""
    tmp = tempfile.mkdtemp(prefix='build-once-')
    try:
        write(tmp, 'Dockerfile', 'FROM python:3.14-slim\nCOPY requirements.txt app.py ./\n'
                                 'COPY lib \\\n     ./lib\nCOPY --from=deps /x /y\n')
        write(tmp, 'requirements.txt', 'pytz\n')
        write(tmp, 'app.py', 'print(1)\n')
        write(tmp, 'lib/a.py', 'A = 1\n')
        write(tmp, 'lib/data.json', '{}\n')
        write(tmp, 'lib/keep.json', '{}\n')
        write(tmp, 'lib/__pycache__/a.cpython-314.pyc', 'x')
        write(tmp, 'lib/pkg-1.0.dist-info/direct_url.json', '{"url": "file:///home/runner/x"}')
        write(tmp, 'notcopied.py', 'Z = 0\n')
        write(tmp, '.ignore', '*.json\n!lib/keep.json\n')
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@contextlib.contextmanager
def fake_gcloud(responses):
    """bo._gcloud replaced: responses maps a tuple prefix of the argv to (returncode, stdout);
    every call is recorded."""
    calls = []
    real = bo._gcloud

    class R:
        def __init__(self, rc, out):
            self.returncode, self.stdout, self.stderr = rc, out, ''

    def fake(*argv):
        calls.append(argv)
        for prefix, (rc, out) in responses.items():
            if argv[:len(prefix)] == prefix:
                return R(rc, out)
        return R(1, '')
    bo._gcloud = fake
    try:
        yield calls
    finally:
        bo._gcloud = real


def plan(ctx, args=(), lock=None):
    return bo.image_plan(os.path.join(ctx, 'Dockerfile'), ctx, os.path.join(ctx, '.ignore'), list(args),
                         LOCK if lock is None else lock)


@contextlib.contextmanager
def python_gcloud():
    """A fake gcloud the Python script can run on any OS (BUILD_ONCE_GCLOUD): nothing exists in
    the bucket, every upload succeeds; never the real gcloud."""
    tmp = tempfile.mkdtemp(prefix='fake-gcloud-py-')
    write(tmp, 'gcloud.py', 'import sys\nsys.exit(1 if sys.argv[1:4] == ["storage", "objects", "describe"] else 0)\n')
    try:
        yield f'"{sys.executable}" "{os.path.join(tmp, "gcloud.py")}"'.replace('\\', '/')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_stage_copies_exactly_the_image_files():
    """stage: exactly the files the COPY lines take, less the ignored, and the key"""
    with fixture() as ctx:
        out = os.path.join(ctx, '..', os.path.basename(ctx) + '-out')
        try:
            bo.main(['stage', '--dockerfile', os.path.join(ctx, 'Dockerfile'), '--context', ctx,
                     '--ignore', os.path.join(ctx, '.ignore'), '--out', out])
            staged = sorted(os.path.relpath(os.path.join(b, f), os.path.join(out, 'ctx')).replace('\\', '/')
                            for b, _d, fs in os.walk(os.path.join(out, 'ctx')) for f in fs)
            eq('the staged files', staged, ['app.py', 'lib/a.py', 'lib/keep.json', 'requirements.txt'])
            eq('the Dockerfile beside the context', os.path.exists(os.path.join(out, 'Dockerfile')), True)
        finally:
            shutil.rmtree(out, ignore_errors=True)


def test_key_follows_the_image_files_only():
    """the key moves with a copied file, the Dockerfile or a build argument, never with an uncopied file"""
    with fixture() as ctx:
        _f, k0, _p = plan(ctx)
        eq('stable', plan(ctx)[1], k0)
        write(ctx, 'notcopied.py', 'Z = 1\n')
        eq('an uncopied file changes nothing', plan(ctx)[1], k0)
        write(ctx, 'lib/data.json', '{"ignored": true}\n')
        eq('an ignored file changes nothing', plan(ctx)[1], k0)
        write(ctx, 'lib/__pycache__/b.pyc', 'y')
        eq('bytecode changes nothing', plan(ctx)[1], k0)
        write(ctx, 'lib/pkg-1.0.dist-info/direct_url.json', '{"url": "file:///workspace/x"}')
        eq("pip's install record (the checkout's path) changes nothing", plan(ctx)[1], k0)
        eq('a build argument changes it', plan(ctx, ['PYTHON_VERSION=3.14'])[1] != k0, True)
        write(ctx, 'lib/a.py', 'A = 2\n')
        k1 = plan(ctx)[1]
        eq('a copied file changes it', k1 != k0, True)
        with open(os.path.join(ctx, 'Dockerfile'), 'a', encoding='utf-8') as f:
            f.write('ENV X=1\n')
        eq('the Dockerfile changes it', plan(ctx)[1] != k1, True)
        eq('the key is 20 hex characters', bool(re.fullmatch(r'[0-9a-f]{20}', k1)), True)


def test_copy_sources_reads_the_dockerfile_forms():
    """COPY/ADD sources: continuations joined, flags dropped, --from stages and the JSON form handled"""
    df = ('FROM x AS deps\nCOPY --chown=a:b req.txt /tmp/\nADD one.py \\\n  two.py ./\n'
          'COPY --from=deps /install /usr/local\ncopy ["three.py", "four dir/", "/app/"]\nRUN echo COPY no.py .\n')
    eq('the sources', bo.copy_sources(df), ['req.txt', 'one.py', 'two.py', 'three.py', 'four dir/'])


def test_missing_copy_source_is_an_error():
    """a COPY source not in the context stops the build with ERROR"""
    with fixture() as ctx:
        write(ctx, 'Dockerfile', 'FROM python:3.14-slim\nCOPY gone.py ./\n')
        try:
            plan(ctx)
            eq('raised', False, True)
        except SystemExit as e:
            eq('the message', str(e).startswith('ERROR: the Dockerfile copies'), True)


def test_ignore_reinclusion_idiom():
    """gcloudignore's re-include idiom: webhook/* then !webhook/site/ then the one file"""
    ig = bo.Ignore('webhook/*\n!webhook/site/\nwebhook/site/*\n!webhook/site/site_nav.py\n')
    eq('the kept file', ig.ignored('webhook/site/site_nav.py'), False)
    eq('its sibling', ig.ignored('webhook/site/main.py'), True)
    eq('another function', ig.ignored('webhook/picklebot/main.py'), True)
    eq('outside', ig.ignored('scripts/x.py'), False)


def test_ignore_everything_but():
    """'*' then '!dir/' and '!dir/file': only the named file under it survives"""
    ig = bo.Ignore('*\n!scripts/\n!scripts/deploy.sh\n!jobs/\n!jobs/w/\n!jobs/w/**\n')
    eq('the named script', ig.ignored('scripts/deploy.sh'), False)
    eq('another script', ig.ignored('scripts/other.py'), True)
    eq('a root file', ig.ignored('README.md'), True)
    eq('deep under a ** re-include', ig.ignored('jobs/w/a/b.py'), False)


def test_ignore_anchoring_and_dir_only():
    """a pattern with a slash is anchored, one without matches at any depth, a trailing slash means directories only"""
    ig = bo.Ignore('docs/\n*.log\n/top.txt\nbuild\n')
    eq('a dir pattern ignores what is under it', ig.ignored('docs/a.md'), True)
    eq('...but not a file of that name', bo.Ignore('docs/\n').ignored('x/docs', False), False)
    eq('an unanchored glob at depth', ig.ignored('a/b/c.log'), True)
    eq('anchored at the root', ig.ignored('top.txt'), True)
    eq('...not deeper', ig.ignored('a/top.txt'), False)
    eq('a bare name matches a directory at depth', ig.ignored('a/build/x.py'), True)
    eq('comments and blanks are not rules', bo.Ignore('# *.py\n\n').ignored('a.py'), False)


def test_base_images_are_built_by_digest():
    """every external FROM is rewritten to tag@digest from the lock, ARGs and build arguments applied, stages left alone"""
    df = ('ARG PYTHON_VERSION=3.14\nFROM python:${PYTHON_VERSION}-slim AS deps\nRUN pip install x\n'
          'FROM deps AS mid\nFROM python:$PYTHON_VERSION-slim\nFROM scratch\nFROM busybox@sha256:' + 'c' * 64 + '\n')
    pinned = bo.pin_dockerfile(df, [], LOCK)
    eq('the first stage', f"FROM python:3.14-slim@{LOCK['python:3.14-slim']} AS deps" in pinned, True)
    eq('the last python stage', f"\nFROM python:3.14-slim@{LOCK['python:3.14-slim']}\n" in pinned, True)
    eq('a stage of its own, scratch and a ref already by digest untouched',
       ('\nFROM deps AS mid\n' in pinned, '\nFROM scratch\n' in pinned, 'FROM busybox@sha256:' + 'c' * 64 in pinned), (True, True, True))
    eq('the build argument wins over the ARG default', f"python:3.15-slim@{LOCK['python:3.15-slim']}" in
       bo.pin_dockerfile(df, ['PYTHON_VERSION=3.15'], LOCK), True)
    eq('the images it names', bo.base_images(df), ['python:3.14-slim', 'python:3.14-slim'])


def test_unpinned_base_image_is_refused():
    """a base image the lock does not name stops the build with ERROR and the command that fixes it"""
    try:
        bo.pin_dockerfile('FROM node:22\n', [], LOCK)
        eq('raised', False, True)
    except SystemExit as e:
        eq('the message', ('ERROR: no pinned digest for the base image node:22' in str(e), 'base-images lock (' in str(e)), (True, True))


def test_a_new_base_digest_is_a_new_key():
    """the key moves when the lock's digest for a base image changes: a base refresh is a rebuild"""
    with fixture() as ctx:
        k0 = plan(ctx)[1]
        k1 = plan(ctx, lock=dict(LOCK, **{'python:3.14-slim': 'sha256:' + 'd' * 64}))[1]
        eq('changed', k1 != k0, True)
        eq('the staged Dockerfile names the digest', f"FROM python:3.14-slim@{LOCK['python:3.14-slim']}" in plan(ctx)[2], True)


def test_find_answers_the_digest():
    """find: IMAGE@sha256:... when the content tag is there, nothing when it is not"""
    with fake_gcloud({('artifacts', 'docker', 'images', 'describe'): (0, 'sha256:abc\n')}) as calls:
        eq('found', bo.find_image('reg/img', 'k1'), 'reg/img@sha256:abc')
        eq('asked for the content tag', calls[0][4], 'reg/img:bo-k1')
    with fake_gcloud({}):
        eq('missing', bo.find_image('reg/img', 'k1'), '')
    with fake_gcloud({('artifacts',): (0, 'not-a-digest')}):
        eq('an odd answer is missing, not a ref', bo.find_image('reg/img', 'k1'), '')


def test_resolve_fails_loudly_and_tags():
    """resolve: ERROR and 1 when the image is missing; the commit tag added when it is there"""
    import io
    buf = io.StringIO()
    with fake_gcloud({}), contextlib.redirect_stdout(buf):
        rc = bo.main(['resolve', '--image', 'reg/img', '--key', 'k1', '--tag', 'abc1234'])
    eq('missing exits 1', rc, 1)
    eq('with ERROR', buf.getvalue().startswith('ERROR:'), True)
    buf = io.StringIO()
    with fake_gcloud({('artifacts', 'docker', 'images', 'describe'): (0, 'sha256:d1'),
                      ('artifacts', 'docker', 'tags', 'add'): (0, '')}) as calls, contextlib.redirect_stdout(buf):
        rc = bo.main(['resolve', '--image', 'reg/img', '--key', 'k1', '--tag', 'abc1234'])
    eq('found exits 0', rc, 0)
    eq('prints the ref', buf.getvalue().strip(), 'reg/img@sha256:d1')
    eq('tags the digest with the commit', [c for c in calls if c[:3] == ('artifacts', 'docker', 'tags')][0][4:6],
       ('reg/img@sha256:d1', 'reg/img:abc1234'))


def test_build_from_the_desktop_pushes_once_by_content():
    """build: stops when the key is in the registry; else docker-builds the pinned Dockerfile with the build arguments, pushes IMAGE:bo-KEY and :latest, and prints the ref; a failed build is ERROR and 1"""
    import io
    import subprocess
    with fixture() as ctx:
        dockerfile = os.path.join(ctx, 'Dockerfile')
        calls = []

        def fake_docker(*argv, **kw):
            calls.append(argv)
            rc = 2 if (argv[:2] == ('buildx', 'build') and os.environ.get('FAKE_BUILD_FAILS')) else 0
            return subprocess.CompletedProcess(argv, rc, stdout='', stderr='boom' if rc else '')
        real = bo._docker
        bo._docker = fake_docker
        try:
            args = ['build', '--image', 'reg/x', '--dockerfile', dockerfile, '--context', ctx, '--ignore', os.path.join(ctx, '.ignore'),
                    '--arg', 'PYTHON_VERSION=3.14']
            with fake_gcloud({('artifacts', 'docker', 'images', 'describe'): (0, 'sha256:had\n')}), contextlib.redirect_stdout(io.StringIO()) as out:
                rc = bo.main(args)
            eq('in the registry already: nothing built, the ref printed', (rc, calls, out.getvalue().strip()), (0, [], 'reg/x@sha256:had'))
            describes = iter([(1, ''), (0, 'sha256:new\n')])
            with fake_gcloud({}) as gcalls, contextlib.redirect_stdout(io.StringIO()) as out:
                bo._gcloud = lambda *argv: (gcalls.append(argv) or type('R', (), {'returncode': (r := next(describes))[0], 'stdout': r[1], 'stderr': ''})()) if argv[:4] == ('artifacts', 'docker', 'images', 'describe') else type('R', (), {'returncode': 0, 'stdout': '1048576\n', 'stderr': ''})()
                rc = bo.main(args)
            verbs = [c[0] for c in calls]
            build = next(c for c in calls if c[:2] == ('buildx', 'build'))
            eq('  built with every stage cached in the registry (#651: cache-to IMAGE:cache mode=max, cache-from the same and :latest, no pull first), both tags pushed, the new ref printed',
               (rc, verbs, '--build-arg' in build and 'PYTHON_VERSION=3.14' in build, '-t' in build and 'reg/x:bo-' in ' '.join(build) and 'reg/x:latest' in build,
                'type=registry,ref=reg/x:cache,mode=max' in build and build[build.index('--cache-to') + 1].startswith('type=registry'),
                [build[i + 1] for i, w in enumerate(build) if w == '--cache-from'], 'BUILDKIT_INLINE_CACHE=1' in build,
                [c[2] for c in calls if c[0] == 'push'][1], out.getvalue().strip()),
               (0, ['buildx', 'push', 'push'], True, True, True, ['type=registry,ref=reg/x:cache', 'reg/x:latest'], False, 'reg/x:latest', 'reg/x@sha256:new'))
            calls.clear()
            os.environ['FAKE_BUILD_FAILS'] = '1'
            try:
                with fake_gcloud({}), contextlib.redirect_stdout(io.StringIO()) as out:
                    rc = bo.main(args)
            finally:
                del os.environ['FAKE_BUILD_FAILS']
            eq('  a failed build: ERROR, 1, nothing pushed', (rc, 'ERROR: x did not build' in out.getvalue(), [c[0] for c in calls if c[0] == 'push']), (1, True, []))
            calls.clear()
            describes = iter([(1, ''), (0, 'sha256:new\n')])
            with fake_gcloud({}) as gcalls, contextlib.redirect_stdout(io.StringIO()):
                bo._gcloud = lambda *argv: (gcalls.append(argv) or type('R', (), {'returncode': (r := next(describes))[0], 'stdout': r[1], 'stderr': ''})()) if argv[:4] == ('artifacts', 'docker', 'images', 'describe') else type('R', (), {'returncode': 0, 'stdout': '1\n', 'stderr': ''})()
                bo.main([*args, '--mark', 'desktop'])
            eq("  --mark desktop: the desktop's tag built and pushed beside bo- and latest",
               ('reg/x:desktop-' in ' '.join(next(c for c in calls if c[:2] == ('buildx', 'build'))), [c[2] for c in calls if c[0] == 'push'][-1].startswith('reg/x:desktop-')), (True, True))
            with fake_gcloud({('artifacts', 'docker', 'images', 'describe'): (0, 'sha256:had\n')}) as gcalls, contextlib.redirect_stdout(io.StringIO()) as out:
                bo.main(['find', '--image', 'reg/x', '--key', 'k1', '--mark', 'desktop'])
            eq("  find --mark asks for the desktop tag and answers true/false", (out.getvalue().strip(), gcalls[-1][4]), ('true', 'reg/x:desktop-k1'))
        finally:
            bo._docker = real


def test_zip_uploads_once_by_content():
    """zip: the source keyed and uploaded once; the helper files, bytecode and install records left out"""
    import io
    with fixture() as ctx:
        write(ctx, '.deploy.sh', 'gcloud functions deploy x\n')
        uploaded = {}

        def run(responses):
            buf = io.StringIO()
            with fake_gcloud(responses) as calls, contextlib.redirect_stdout(buf):
                real_cp = bo._gcloud

                def cp(*argv):
                    if argv[:2] == ('storage', 'cp'):
                        with zipfile.ZipFile(argv[3]) as z:
                            uploaded['names'] = sorted(z.namelist())
                    return real_cp(*argv)
                bo._gcloud = cp
                rc = bo.main(['zip', '--dir', ctx, '--function', 'fn', '--bucket', 'b', '--exclude', '.deploy.sh'])
            return rc, buf.getvalue().strip().splitlines()[-1], calls

        rc, url, calls = run({('storage', 'cp'): (0, '')})
        eq('built', rc, 0)
        eq('the url', bool(re.fullmatch(r'gs://b/function-sources/fn/[0-9a-f]{20}\.zip', url)), True)
        eq('uploaded without clobbering', [c for c in calls if c[:2] == ('storage', 'cp')][0][2], '--no-clobber')
        eq('what the zip holds', uploaded['names'],
           ['.ignore', 'Dockerfile', 'app.py', 'lib/a.py', 'lib/data.json', 'lib/keep.json', 'notcopied.py', 'requirements.txt'])
        write(ctx, '.deploy.sh', 'gcloud functions deploy y\n')
        rc2, url2, calls2 = run({('storage', 'objects', 'describe'): (0, 'name')})
        eq('a helper file does not change the key', url2, url)
        eq('already there: nothing uploaded', [c for c in calls2 if c[:2] == ('storage', 'cp')], [])


def test_the_registry_reads_through_the_real_seams():
    """  _gcloud and _docker run the command BUILD_ONCE_GCLOUD / BUILD_ONCE_DOCKER names (the tests' fakes; gcloud.cmd found through PATHEXT on Windows), as text; zip_marked and image_marked read through it"""
    with python_gcloud() as fake:
        keep = dict(os.environ)
        os.environ['BUILD_ONCE_GCLOUD'], os.environ['BUILD_ONCE_DOCKER'] = fake, fake
        try:
            eq("  _gcloud and _docker run the command BUILD_ONCE_GCLOUD / BUILD_ONCE_DOCKER names (the tests' fakes; gcloud.cmd found through PATHEXT on Windows), as text; zip_marked and image_marked read through it",
               (bo._gcloud('storage', 'objects', 'describe', 'x').returncode, bo._gcloud('artifacts', 'list').returncode,
                bo._docker('version', capture_output=True).returncode, bo.zip_marked('gs://b/f/k.zip', 'desktop'), bo.image_marked('reg/x', 'k', 'desktop')),
               (1, 0, 0, False, False))
        finally:
            os.environ.clear()
            os.environ.update(keep)


def test_key_stage_and_find_from_the_command_line():
    """  `key` prints the key alone; `stage` replaces an out folder that is there; `find` without --mark prints the ref or nothing"""
    import io
    with fixture() as ctx:
        keep = bo.load_lock
        bo.load_lock = lambda path=None: LOCK
        try:
            args = ['--dockerfile', os.path.join(ctx, 'Dockerfile'), '--context', ctx, '--ignore', os.path.join(ctx, '.ignore')]
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                bo.main(['key', *args])
            out = os.path.join(ctx, 'out')
            os.makedirs(os.path.join(out, 'ctx', 'stale'))
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                bo.main(['stage', *args, '--out', out])
            eq("  `key` prints the key alone; `stage` replaces an out folder that is there; `find` without --mark prints the ref or nothing",
               (buf.getvalue().strip() == plan(ctx)[1], os.path.exists(os.path.join(out, 'ctx', 'stale')), os.path.exists(os.path.join(out, 'ctx', 'app.py'))),
               (True, False, True))
        finally:
            bo.load_lock = keep
    buf = io.StringIO()
    with fake_gcloud({('artifacts', 'docker', 'images', 'describe'): (0, 'sha256:abc\n')}), contextlib.redirect_stdout(buf):
        rc = bo.main(['find', '--image', 'reg/img', '--key', 'k1'])
    found = buf.getvalue()
    buf = io.StringIO()
    with fake_gcloud({}), contextlib.redirect_stdout(buf):
        rc2 = bo.main(['find', '--image', 'reg/img', '--key', 'k1'])
    eq("  find", (rc, found, rc2, buf.getvalue()), (0, 'reg/img@sha256:abc\n', 0, ''))


def test_the_size_line_and_image_size_read_the_registry():
    """  size_line: this version's compressed size and the one before it (or 'no earlier version'); '' when the registry cannot be read, answers oddly or lacks the digest; image_size likewise in bytes or None; resolve prints the size line and says when a tag could not be added"""
    rows = json.dumps([{'version': 'sha256:new', 'createTime': '2026-10-11T02:00:00', 'metadata': {'imageSizeBytes': str(399 * 1048576)}},
                       {'version': 'sha256:old', 'createTime': '2026-10-10T02:00:00', 'metadata': {'imageSizeBytes': str(396 * 1048576)}},
                       {'version': 'sha256:none', 'createTime': '2026-10-09T02:00:00', 'metadata': {}}])
    with fake_gcloud({('artifacts', 'docker', 'images', 'list'): (0, rows)}):
        both = (bo.size_line('reg/smad-daily', 'reg/smad-daily@sha256:new'), bo.size_line('reg/smad-daily', 'reg/smad-daily@sha256:old'),
                bo.size_line('reg/smad-daily', 'reg/smad-daily@sha256:gone'), bo.image_size('reg/smad-daily', 'reg/smad-daily@sha256:old'),
                bo.image_size('reg/smad-daily', 'reg/smad-daily@sha256:none'), bo.image_size('reg/smad-daily', 'reg/smad-daily@sha256:gone'))
    with fake_gcloud({('artifacts', 'docker', 'images', 'list'): (0, 'nope')}):
        odd = (bo.size_line('reg/x', 'reg/x@sha256:new'), bo.image_size('reg/x', 'reg/x@sha256:new'))
    with fake_gcloud({}):
        unread = (bo.size_line('reg/x', 'reg/x@sha256:new'), bo.image_size('reg/x', 'reg/x@sha256:new'))
    eq("  size_line: this version's compressed size and the one before it (or 'no earlier version'); '' when the registry cannot be read, answers oddly or lacks the digest; image_size likewise in bytes or None; resolve prints the size line and says when a tag could not be added",
       (both, odd, unread),
       (('size: smad-daily 399 MB compressed (last 396 MB)', 'size: smad-daily 396 MB compressed (no earlier version)', '', 396 * 1048576, None, None),
        ('', None), ('', None)))
    import io
    out, err = io.StringIO(), io.StringIO()
    with fake_gcloud({('artifacts', 'docker', 'images', 'describe'): (0, 'sha256:new'), ('artifacts', 'docker', 'images', 'list'): (0, rows),
                      ('artifacts', 'docker', 'tags', 'add'): (1, '')}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = bo.main(['resolve', '--image', 'reg/smad-daily', '--key', 'k1', '--tag', 'abc1234'])
    eq("  resolve", (rc, out.getvalue().strip(), '(could not tag abc1234' in err.getvalue(), 'size: smad-daily 399 MB compressed (last 396 MB)' in err.getvalue()),
       (0, 'reg/smad-daily@sha256:new', True, True))


def test_build_marks_an_image_built_elsewhere_and_fails_loudly_on_a_push():
    """  build --mark on an image CI built before the desktop had it: the mark tag alone, nothing built; a push that fails is ERROR and 1; an image missing from the registry after the push is ERROR and 1"""
    import io
    with fixture() as ctx:
        args = ['build', '--image', 'reg/x', '--dockerfile', os.path.join(ctx, 'Dockerfile'), '--context', ctx, '--ignore', os.path.join(ctx, '.ignore'),
                '--mark', 'desktop']
        keep_lock, keep_docker = bo.load_lock, bo._docker
        bo.load_lock = lambda path=None: LOCK
        calls = []

        def docker(*argv, **kw):
            calls.append(argv)
            rc = 1 if argv[0] == 'push' and os.environ.get('FAKE_PUSH_FAILS') else 0
            return subprocess.CompletedProcess(argv, rc, stdout='', stderr='denied' if rc else '')
        bo._docker = docker
        try:
            describes = iter([(0, 'sha256:had\n'), (1, '')])   # bo- there, desktop- not
            with fake_gcloud({}) as gcalls, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                bo._gcloud = lambda *argv: (gcalls.append(argv) or type('R', (), {'returncode': (r := next(describes, (0, '')))[0], 'stdout': r[1], 'stderr': ''})())
                rc = bo.main(args)
            tag_add = [c for c in gcalls if c[:4] == ('artifacts', 'docker', 'tags', 'add')]
            eq("  build --mark on an image CI built before the desktop had it: the mark tag alone, nothing built; a push that fails is ERROR and 1; an image missing from the registry after the push is ERROR and 1",
               (rc, calls, len(tag_add), tag_add[0][5].startswith('reg/x:desktop-')), (0, [], 1, True))
            os.environ['FAKE_PUSH_FAILS'] = '1'
            try:
                out = io.StringIO()
                with fake_gcloud({}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                    rc = bo.main(args)
            finally:
                del os.environ['FAKE_PUSH_FAILS']
            eq("  a failed push", (rc, out.getvalue().startswith('ERROR: reg/x:bo-'), 'could not be pushed: denied' in out.getvalue()), (1, True, True))
            calls.clear()
            out = io.StringIO()
            with fake_gcloud({}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                rc = bo.main(args)
            eq("  not in the registry after the push", (rc, out.getvalue().startswith('ERROR: reg/x:bo-'), 'is not in Artifact Registry after the push' in out.getvalue(),
                                                       [c[0] for c in calls]), (1, True, True, ['buildx', 'push', 'push', 'push']))
        finally:
            bo.load_lock, bo._docker = keep_lock, keep_docker


def test_zip_marks_and_says_when_the_upload_failed():
    """  zip --mark: the marker object <key>.<mark> written beside a zip, built now or already there; a failed upload is ERROR and 1"""
    import io
    with fixture() as ctx:
        args = ['zip', '--dir', ctx, '--function', 'fn', '--bucket', 'b', '--mark', 'desktop']
        with fake_gcloud({('storage', 'cp'): (0, '')}) as calls, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = bo.main(args)
        cps = [c for c in calls if c[:2] == ('storage', 'cp')]
        marker = json.load(open(cps[1][2], encoding='utf-8')) if os.path.exists(cps[1][2]) else {'by': '(removed)'}
        eq("  zip --mark: the marker object <key>.<mark> written beside a zip, built now or already there; a failed upload is ERROR and 1",
           (rc, len(cps), cps[0][2], cps[1][3].endswith('.desktop'), cps[1][3][:-8] + '.zip' == cps[0][4], marker.get('by') in ('desktop', '(removed)')),
           (0, 2, '--no-clobber', True, True, True))
        with fake_gcloud({('storage', 'objects', 'describe'): (0, 'name'), ('storage', 'cp'): (0, '')}) as calls, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = bo.main(args)
        cps = [c for c in calls if c[:2] == ('storage', 'cp')]
        eq("  already there: the marker alone", (rc, len(cps), cps[0][3].endswith('.desktop')), (0, 1, True))
        out = io.StringIO()
        with fake_gcloud({}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = bo.main(args)
        eq("  a failed upload", (rc, out.getvalue().startswith('ERROR: the function source could not be uploaded to gs://b/function-sources/fn/')), (1, True))


if __name__ == '__main__':
    for _name, _fn in list(globals().items()):
        if _name.startswith('test_') and callable(_fn):
            print(_fn.__doc__.strip().splitlines()[0] if _fn.__doc__ else _name)
            _fn()
    print(f'{len(fails)} failure(s)')
    sys.exit(1 if fails else 0)
