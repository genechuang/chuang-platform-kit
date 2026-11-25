#!/usr/bin/env python3
"""chuang_sdlc.cicd.release (SMADPickleBot #2432): a project's Cloud Run releases up one ladder, from a file in its own
repository. Pinned, positive case first: a service's image is built once, checked on the runner, deployed with no traffic
on the dev tag, smoked there, promoted and smoked again; a smoke that fails on dev leaves production where it was; a job
moves only after its checks; a component already on its content's image is left alone; a bad file is refused by name
before anything runs; nothing outside the listed files is in the image; no call names an environment, a secret or an
account.

Run:  python tests/test-sdlc-cicd-release.py      (prints PASS/FAIL per case and `N failure(s)`)
"""
import contextlib
import json
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'sdlc', 'src'))
sys.path.insert(0, os.path.join(HERE, '..', 'src'))
from chuang_sdlc.cicd import build_once, release as R  # noqa: E402

FAILURES = []
REG = 'us-west1-docker.pkg.dev/p/images'
REF = f'{REG}/api@sha256:' + 'a' * 64
CFG = '''
[release]
project = "p"
region = "us-west1"
registry = "us-west1-docker.pkg.dev/p/images"
base_images = "base.json"

[components.api]
kind = "service"
name = "my-api"
image = "api"
dockerfile = "cloud/Dockerfile"
files = { "src" = "src", "rules.md" = "docs/rules.md" }
smoke = { path = "/", code = 200, expect = "ok", private = true }
checks = [ { entrypoint = "python", args = ["-c", "import api"] } ]

[components.nightly]
kind = "job"
name = "my-nightly"
image = "api"
dockerfile = "cloud/Dockerfile"
files = { "src" = "src", "rules.md" = "docs/rules.md" }
checks = [ { args = ["--help"], exit = 2, expect = "usage: " } ]
'''


def eq(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ('' if ok else f'\n        got  {got!r}\n        want {want!r}'))
    if not ok:
        FAILURES.append(label)


@contextlib.contextmanager
def project(cfg=CFG):
    root = tempfile.mkdtemp()
    try:
        for name, text in {'release.toml': cfg, 'cloud/Dockerfile': 'FROM python:3.12-slim\nCOPY src ./src\nCOPY rules.md ./\n', 'src/a.py': 'x = 1\n',
                           'docs/rules.md': 'rules\n', '.env': 'SECRET=1\n', 'credentials/key.json': '{}',
                           'base.json': json.dumps({'images': {'python:3.12-slim': 'sha256:' + 'b' * 64}})}.items():
            path = os.path.join(root, *name.split('/'))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8', newline='\n') as f:
                f.write(text)
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


class Cloud:
    """Cloud Run and docker, faked: what a command changes, and every command asked."""

    def __init__(self, built=True, image='old:v1', dev_ok=True, takes=True, docker=(0, 'fine')):
        self.calls, self.image, self.job, self.dev, self.serving = [], image, 'old:v1', '', 'my-api-1'
        self.built, self.takes, self.docker, self.dev_ok = built, takes, docker, dev_ok

    def service(self):
        traffic = [{'revisionName': self.serving, 'percent': 100}] + ([{'revisionName': self.dev, 'tag': 'dev', 'url': 'https://dev---api'}] if self.dev else [])
        return {'spec': {'template': {'spec': {'containers': [{'image': self.image}]}}}, 'status': {'url': 'https://api', 'traffic': traffic}}

    def run(self, args, **kw):
        self.calls.append(args)
        ok = lambda out='': types.SimpleNamespace(returncode=0, stdout=out, stderr='')  # noqa: E731
        line = ' '.join(args)
        if args[0] == 'docker':
            code, said = self.docker if '--help' not in args else (2, 'usage: x')
            return types.SimpleNamespace(returncode=code, stdout=said, stderr='')
        if 'services describe' in line:
            return ok(json.dumps(self.service()))
        if 'jobs describe' in line:
            return ok(json.dumps({'spec': {'template': {'spec': {'template': {'spec': {'containers': [{'image': self.job}]}}}}}}))
        if 'run deploy' in line:
            self.image, self.dev = next(a for a in args if a.startswith('--image=')).split('=', 1)[1], 'my-api-2'
        if 'update-traffic' in line and self.takes:
            self.serving = self.dev
        if 'jobs update' in line:
            self.job = next(a for a in args if a.startswith('--image=')).split('=', 1)[1]
        return ok()

    def open(self, req, timeout=None):
        dev = 'dev---' in req.full_url
        body = b'{"ok": true}' if not dev or self.dev_ok else b'broken'
        return contextlib.nullcontext(types.SimpleNamespace(status=200 if not dev or self.dev_ok else 500, read=lambda n: body))

    def said(self, *words):
        return [' '.join(c[:4]) for c in self.calls if all(w in ' '.join(c) for w in words)]


@contextlib.contextmanager
def world(cloud):
    keep = (build_once.find_image, build_once.cmd_build, R.record)
    records = []
    build_once.find_image = lambda image, key: REF if cloud.built else ''
    build_once.cmd_build = lambda a: setattr(cloud, 'built', True) or 0
    R.record = lambda env, state, did='', description='', deploy=None: records.append((env, state)) or 'd1'
    try:
        yield records
    finally:
        build_once.find_image, build_once.cmd_build, R.record = keep


def go(cloud, root, name, **kw):
    said = []
    try:
        got = R.release(R.load(os.path.join(root, 'release.toml')), name, root, say=said.append, run=cloud.run,
                        **({'token': lambda aud: 't', 'opener': cloud.open, 'sleep': lambda s: None} if name == 'api' else {}), **kw)
    except R.Refused as e:
        got = f'refused: {e}'
    return got, said


def test_a_service_climbs_its_ladder():
    """the positive case: built when no image of its content exists, checked on the runner with no network, deployed with no traffic on the dev tag, smoked there, promoted to latest in one step, smoked again; recorded; the environment, the secrets and the account never named"""
    cloud = Cloud(built=False)
    with project() as root, world(cloud) as records:
        got, said = go(cloud, root, 'api')
    order = [w for c in cloud.calls for w in ('docker', 'deploy', 'update-traffic') if w in c]
    flat = ' '.join(' '.join(c) for c in cloud.calls)
    eq('released', (got, order, cloud.serving, cloud.image, records), ('released', ['docker', 'deploy', 'update-traffic'], 'my-api-2', REF,
                                                                       [('api · production', 'start'), ('api · production', 'success')]))
    eq('  the dev rung has no traffic, the check no network, the image alone changes',
       ('--no-traffic' in flat and '--tag=dev' in flat, '--network none' in flat, '--to-latest' in flat,
        [w for w in ('--set-env', '--update-env', 'secret', '--service-account', '--args', '--command') if w in flat]), (True, True, True, []))
    eq('  said', [s.split(' on ')[0] for s in said], ['api: 1 image check(s) passed', 'my-api: my-api-2 passed', 'api: live'])


def test_a_dev_rung_that_fails_leaves_production():
    """  a revision that fails its smoke on the dev tag is never promoted: the refusal names it and what serves, recorded as a failure"""
    cloud = Cloud(dev_ok=False)
    with project() as root, world(cloud) as records:
        got, _said = go(cloud, root, 'api')
    eq('production kept', (cloud.serving, cloud.said('update-traffic'), 'my-api-2 failed its smoke on the dev rung' in got, 'Production keeps my-api-1' in got, records[-1]),
       ('my-api-1', [], True, True, ('api · production', 'failure')))
    cloud = Cloud(takes=False)
    with project() as root, world(cloud):
        got, _said = go(cloud, root, 'api')
    eq('  a promotion that did not take is refused', 'was promoted and traffic goes to my-api-1' in got, True)


def test_an_image_that_does_not_start_deploys_nothing():
    """  an image check that ends wrong stops the release before any deploy, and no rung is recorded"""
    cloud = Cloud(docker=(1, 'ImportError: no module named api'))
    with project() as root, world(cloud) as records:
        got, _said = go(cloud, root, 'api')
    eq('refused', ("ended 1, not 0" in got and 'ImportError' in got, cloud.said('run', 'deploy'), records), (True, [], []))


def test_a_job_moves_after_its_checks_and_runs_nothing():
    """  a job: its check's exit code and words, then the image alone, nothing executed"""
    cloud = Cloud()
    with project() as root, world(cloud):
        got, _said = go(cloud, root, 'nightly')
    flat = ' '.join(' '.join(c) for c in cloud.calls)
    eq('moved', (got, cloud.job, [w for w in ('execute', '--args', '--wait') if w in flat]), ('released', REF, []))


def test_a_component_on_its_contents_image_is_left_alone():
    """  the service already runs the image its files make: unchanged, no check, no deploy; --force releases it; a plan builds and deploys nothing"""
    cloud = Cloud(image=REF)
    with project() as root, world(cloud):
        got, _ = go(cloud, root, 'api')
        quiet = list(cloud.calls)
        forced, _ = go(cloud, root, 'api', force=True)
        planned = Cloud(built=False)
        with world(planned):
            plan, words = go(planned, root, 'api', plan=True)
    eq('unchanged', (got, [c[:3] for c in quiet], forced, plan, words[0].split(' (')[1], planned.built, len(planned.calls)),
       ('unchanged', [['gcloud', 'run', 'services']], 'released', 'planned', 'to build); it runs old:v1', False, 1))


def test_nothing_outside_the_listed_files_is_in_the_image():
    """  the context holds the listed files where the Dockerfile reads them, and no .env or credentials; a listed path that leaves the repository or is missing is refused"""
    with project() as root, tempfile.TemporaryDirectory() as out:
        cfg = R.load(os.path.join(root, 'release.toml'))
        ctx, _df = R.stage(cfg['components']['api'], root, out)
        found = sorted(os.path.relpath(os.path.join(a, f), ctx).replace(os.sep, '/') for a, _d, fs in os.walk(ctx) for f in fs)
        refused = []
        for files in ({'x': '../outside'}, {'../x': 'src'}, {'x': 'nope.txt'}):
            with tempfile.TemporaryDirectory() as o:
                try:
                    R.stage({**cfg['components']['api'], 'files': files}, root, o)
                except R.Refused as e:
                    refused.append('outside' if 'outside' in str(e) else 'missing')
    eq('the context', (found, refused), (['rules.md', 'src/a.py'], ['outside', 'outside', 'missing']))


def test_a_bad_file_is_refused_by_name():
    """  a missing part, an unknown kind, a service with no path, a job with no checks and a check with no args are each refused before anything runs; main answers 1 with one ERROR line; an unknown component is named"""
    bad = {'no project': CFG.replace('project = "p"', ''), 'kind': CFG.replace('kind = "job"', 'kind = "cron"'),
           'no path': CFG.replace('path = "/", ', ''), 'no checks': CFG.replace('checks = [ { args = ["--help"], exit = 2, expect = "usage: " } ]', ''),
           'no args': CFG.replace('args = ["--help"], ', ''), 'not toml': 'x ='}
    said = {}
    for why, text in bad.items():
        with project(text) as root:
            out = []
            said[why] = (R.main(['--config', os.path.join(root, 'release.toml')], say=out.append, run=lambda *a, **k: 1 / 0), out[0][:15])
    eq('refused', said, {why: (1, 'ERROR: release:') for why in bad})
    with project() as root:
        out = []
        code = R.main(['--config', os.path.join(root, 'release.toml'), '--component', 'web'], say=out.append)
    eq('  an unknown component', (code, 'has no component web (it has api, nightly)' in out[0]), (1, True))


def test_a_refusal_of_gclouds_is_the_error():
    """  gcloud's own last words are the refusal's, and a base image the lock does not hold is one too"""
    with project() as root, world(Cloud()):
        down = lambda args, **kw: types.SimpleNamespace(returncode=1, stdout='', stderr='PERMISSION_DENIED: run.services.get')  # noqa: E731
        out = []
        code = R.main(['--config', os.path.join(root, 'release.toml'), '--component', 'api', '--root', root], say=out.append, run=down)
        with open(os.path.join(root, 'base.json'), 'w') as f:
            f.write('{"images": {}}')
        out2 = []
        code2 = R.main(['--config', os.path.join(root, 'release.toml'), '--component', 'api', '--root', root], say=out2.append, run=down)
    eq('words', (code, 'PERMISSION_DENIED' in out[0], code2, 'no pinned digest for the base image python:3.12-slim' in out2[0]), (1, True, 1, True))


def main():
    for name, fn in list(globals().items()):
        if name.startswith('test_'):
            print(fn.__doc__.strip())
            fn()
    print(f'\n{len(FAILURES)} failure(s)')
    return 1 if FAILURES else 0


if __name__ == '__main__':
    raise SystemExit(main())
