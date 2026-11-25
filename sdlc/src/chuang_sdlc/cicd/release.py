#!/usr/bin/env python3
"""A project's Cloud Run releases, the same ladder for every project (SMADPickleBot #2432, phase (c) of its #1829): build
once by content, a dev rung with no traffic, its smoke, then all traffic. Driven by one file in the project's own
repository, run from its own workflow on any runner that has gcloud and docker:

    python -m chuang_sdlc.cicd.release --config config/release.toml                      every component whose image changed
    python -m chuang_sdlc.cicd.release --config config/release.toml --component NAME     that one alone
    python -m chuang_sdlc.cicd.release --config config/release.toml --plan               what it would do, nothing built

The file:

    [release]
    project = "my-project"
    region = "us-west1"
    registry = "us-west1-docker.pkg.dev/my-project/images"
    base_images = "config/base-images.json"        # every FROM by digest (build_once's lock)

    [components.api]                               # a Cloud Run service
    kind = "service"
    name = "my-api"                                # the service's own name
    image = "api"                                  # under `registry`; two components may share one
    dockerfile = "cloud/api.Dockerfile"
    files = { "pyproject.toml" = "pyproject.toml", "src" = "src" }   # the image's context: path there = path here
    smoke = { path = "/", code = 200, expect = "ok", private = true }
    checks = [ { entrypoint = "python", args = ["-c", "import api.handlers"] } ]

    [components.nightly]                           # a Cloud Run job
    kind = "job"
    name = "my-nightly"
    image = "nightly"
    dockerfile = "cloud/nightly.Dockerfile"
    files = { "src" = "src" }
    checks = [ { args = ["--help"], exit = 2, expect = "usage: " } ]

What a release changes is the image alone: a service's environment, secrets, account, concurrency and limits are never
named, so they stay as they are. First the image itself: each of the component's `checks` is a `docker run --rm`
of it on this runner, with no secret, no network and no cloud account, which must end with its exit code (0) and print
what it expects, so nothing that can deploy is asked until the image has started. A service: its revision is deployed with no traffic on the tag `dev`, smoke-tested at
the tag's own address, and only then takes every request; a revision that fails its smoke never serves one, the service
keeps the revision it had, and the exit is 1. Traffic moves in one step and ends on `latest`, never split. A job has no
traffic, so its checks are its whole proof, and it moves to the image for its next run. A component whose service already runs the image its files make is left alone (`--force`
releases it anyway). Nothing outside `files` is ever in an image: no .env, no credentials, no data.

Each rung is recorded as a GitHub deployment (gh_deploy, best effort: a record never stops a release) and each stage's
seconds are printed, the run's own baseline for the next. Standard library, gcloud and docker.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.request

from chuang_sdlc.cicd import build_once, gh_deploy, proc

SMOKE_TRIES = 4        # a new revision's first answer may be a cold start's
SMOKE_WAIT = 3
SMOKE_TIMEOUT = 60
DEV_TAG = 'dev'
KINDS = ('service', 'job')


class Refused(Exception):
    """The release stops here, in words: the message is the ERROR line."""


# ------------------------------------------------------------------ the project's file
def load(path: str) -> dict:
    """The release file, checked: {'project', 'region', 'registry', 'base_images', 'components': {name: {...}}}. A missing
    or unknown part is a Refused naming it, before anything is built."""
    try:
        with open(path, 'rb') as f:
            doc = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise Refused(f'{path} was not read: {e}')
    head = doc.get('release') or {}
    for key in ('project', 'region', 'registry'):
        if not head.get(key):
            raise Refused(f'{path}: [release] names no {key}')
    components = doc.get('components') or {}
    if not components:
        raise Refused(f'{path} names no component')
    for name, c in components.items():
        for key in ('kind', 'name', 'image', 'dockerfile', 'files'):
            if not c.get(key):
                raise Refused(f'{path}: [components.{name}] has no {key}')
        if c['kind'] not in KINDS:
            raise Refused(f"{path}: [components.{name}] kind {c['kind']!r} is none of {', '.join(KINDS)}")
        if c['kind'] == 'service' and not str((c.get('smoke') or {}).get('path') or '').startswith('/'):
            raise Refused(f'{path}: [components.{name}] a service needs a smoke with a path to ask')
        if c['kind'] == 'job' and not c.get('checks'):
            raise Refused(f'{path}: [components.{name}] a job needs checks: they are its whole proof')
        if any(not k.get('args') for k in c.get('checks') or []):
            raise Refused(f'{path}: [components.{name}] a check names no args: an image run bare does its real work')
    return {**head, 'components': components}


def stage(c: dict, root: str, out: str) -> tuple:
    """(context folder, Dockerfile path) for a component: the files it lists, copied from `root` to the places its
    Dockerfile reads them, and nothing else. A listed file that is missing, or that leaves the repository, is Refused."""
    ctx, base = os.path.join(out, 'ctx'), os.path.realpath(root)
    os.makedirs(ctx)
    for there, here in c['files'].items():
        src, dest = os.path.realpath(os.path.join(root, here)), os.path.realpath(os.path.join(ctx, there))
        if not src.startswith(base + os.sep) or not dest.startswith(os.path.realpath(ctx) + os.sep):
            raise Refused(f"{c['name']}: {here} -> {there} is outside the repository or its image's context")
        if os.path.isdir(src):
            shutil.copytree(src, dest, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        elif os.path.isfile(src):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(src, dest)
        else:
            raise Refused(f"{c['name']}: {here} is not in the repository")
    dockerfile = os.path.join(root, c['dockerfile'])
    if not os.path.isfile(dockerfile):
        raise Refused(f"{c['name']}: {c['dockerfile']} is not in the repository")
    return ctx, dockerfile


# ------------------------------------------------------------------ gcloud
def _gcloud(args: list, run=None) -> str:
    """One gcloud call's stdout; its failure is Refused with gcloud's own last words."""
    done = (run or proc.run)(['gcloud', *args])
    if done.returncode != 0:
        raise Refused(f"gcloud {' '.join(args[:4])} failed: {(done.stderr or '').strip()[-400:]}")
    return done.stdout or ''


def describe(cfg: dict, c: dict, run=None) -> dict:
    """The component's service or job as Cloud Run has it now."""
    what = ['run', 'services', 'describe'] if c['kind'] == 'service' else ['run', 'jobs', 'describe']
    got = _gcloud([*what, c['name'], f"--project={cfg['project']}", f"--region={cfg['region']}", '--format=json'], run)
    try:
        return json.loads(got)
    except ValueError:
        raise Refused(f"{c['name']}: gcloud's description did not read")


def image_of(doc: dict, kind: str) -> str:
    """The image a described service or job runs."""
    spec = doc.get('spec', {}).get('template', {}).get('spec', {})
    if kind == 'job':
        spec = spec.get('template', {}).get('spec', {})
    return ((spec.get('containers') or [{}])[0]).get('image') or ''


def tagged(doc: dict, tag: str = DEV_TAG) -> tuple:
    """(revision, URL) on a described service's traffic tag; ('', '') with none."""
    row = next((t for t in doc.get('status', {}).get('traffic') or [] if t.get('tag') == tag), {})
    return row.get('revisionName') or '', row.get('url') or ''


def serving(doc: dict) -> list:
    """The revisions a described service sends traffic to."""
    return [t.get('revisionName') for t in doc.get('status', {}).get('traffic') or [] if (t.get('percent') or 0) > 0]


def running(doc: dict, kind: str) -> str:
    """The image that serves: a job's own; a service's newest revision's only when that revision takes every request, ''
    while traffic is elsewhere (a release that stopped on its dev rung leaves its image in the spec, serving nothing)."""
    status = doc.get('status', {})
    newest = status.get('latestCreatedRevisionName') or status.get('latestReadyRevisionName')
    return image_of(doc, kind) if kind == 'job' or serving(doc) == [newest] else ''


# ------------------------------------------------------------------ the build
def image_ref(cfg: dict, c: dict, root: str, build=True) -> tuple:
    """(IMAGE@sha256:..., its key): the image this component's files make, found in the registry by its content key, or
    built and pushed when no build of that content exists; ('', key) with `build` off and none there."""
    image = f"{cfg['registry']}/{c['image']}"
    keep = build_once.LOCK_PATH
    build_once.LOCK_PATH = os.path.join(root, cfg.get('base_images') or 'config/base-images.json')
    try:
        with tempfile.TemporaryDirectory() as tmp:
            ctx, dockerfile = stage(c, root, tmp)
            _files, key, _pinned = build_once.image_plan(dockerfile, ctx, None, [])
            ref = build_once.find_image(image, key)
            if ref or not build:
                return ref, key
            code = build_once.cmd_build(argparse.Namespace(dockerfile=dockerfile, context=ctx, ignore=None, arg=[], image=image,
                                                           mark='', force=False, verbose=True, registry_cache=False))
            if code != 0:
                raise Refused(f"{c['name']}: the image did not build")
            return build_once.find_image(image, key), key
    except SystemExit as e:   # build_once's own refusal (a base image the lock does not hold)
        raise Refused(f"{c['name']}: {e}")
    finally:
        build_once.LOCK_PATH = keep


# ------------------------------------------------------------------ the image's own checks
def check_image(c: dict, ref: str, run=None) -> None:
    """Each of the component's `checks`, a run of the image on this machine with no network and no environment of ours:
    its exit code and, when it names one, the words it prints. Refused names the one that did not."""
    for k in c.get('checks') or []:
        entry = ['--entrypoint', k['entrypoint']] if k.get('entrypoint') else []
        done = (run or proc.run)(['docker', 'run', '--rm', '--network', 'none', *entry, ref, *k['args']])
        want, said = int(k.get('exit') or 0), f"{done.stdout or ''}{done.stderr or ''}"
        if done.returncode != want or str(k.get('expect') or '') not in said:
            raise Refused(f"{c['name']}: the image's check {' '.join(k['args'])[:80]!r} ended {done.returncode}, not {want}"
                          + (f" with {k['expect']!r}" if k.get('expect') else '') + f": {said.strip()[-300:]}")


# ------------------------------------------------------------------ the smoke
def ask(url: str, token: str = '', opener=None) -> tuple:
    """(HTTP code, body) of one GET; (0, why) when nothing answered."""
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'} if token else {})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=SMOKE_TIMEOUT) as r:
            return r.status, r.read(65536).decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read(65536).decode('utf-8', 'replace')
    except (urllib.error.URLError, OSError) as e:
        return 0, str(getattr(e, 'reason', e))


def id_token(audience: str, env=None, run=None, opener=None) -> str:
    """An identity token for a private service. With RELEASE_ACCOUNT (a runner signed in with no key), that account's, from
    IAM Credentials with the runner's own access token, since gcloud mints none from such a sign-in; else gcloud's."""
    account = (env if env is not None else os.environ).get('RELEASE_ACCOUNT', '')
    if not account:
        return _gcloud(['auth', 'print-identity-token', f'--audiences={audience}'], run).strip().splitlines()[-1]
    access = _gcloud(['auth', 'print-access-token'], run).strip().splitlines()[-1]
    req = urllib.request.Request(f'https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{account}:generateIdToken', method='POST',
                                 data=json.dumps({'audience': audience, 'includeEmail': True}).encode(),
                                 headers={'Authorization': f'Bearer {access}', 'Content-Type': 'application/json'})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=SMOKE_TIMEOUT) as r:
            return json.loads(r.read())['token']
    except urllib.error.HTTPError as e:
        raise Refused(f'no identity token as {account}: HTTP {e.code} {e.read(400).decode("utf-8", "replace").strip()}')
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        raise Refused(f'no identity token as {account}: {type(e).__name__}')


def smoke(c: dict, url: str, bearer: str = '', opener=None, sleep=time.sleep) -> str:
    """A service's smoke at `url`: its path answers the code it names and its body holds what it expects. '' when it
    passes, else what it answered, after SMOKE_TRIES tries."""
    want, said = int(c['smoke'].get('code') or 200), ''
    for i in range(SMOKE_TRIES):
        if i:
            sleep(SMOKE_WAIT)
        code, body = ask(url.rstrip('/') + c['smoke']['path'], bearer, opener)
        if code == want and str(c['smoke'].get('expect') or '') in body:
            return ''
        said = f"{c['smoke']['path']} answered {code or 'nothing'} ({body.strip()[:160]}), not {want}" + (
            f" with {c['smoke']['expect']!r}" if c['smoke'].get('expect') else '')
    return said


# ------------------------------------------------------------------ the ladder
def release_service(cfg: dict, c: dict, ref: str, say=print, run=None, token=None, opener=None, sleep=time.sleep) -> None:
    """The service up its ladder: a revision of `ref` at no traffic on the dev tag, its smoke, every request, and the
    smoke once more at the service's own address. Refused leaves production on the revision it had, named."""
    place = [f"--project={cfg['project']}", f"--region={cfg['region']}", '--quiet']
    before = describe(cfg, c, run)
    was, home = serving(before), before.get('status', {}).get('url') or ''
    bearer = (token or id_token)(home) if c['smoke'].get('private') else ''   # before anything is deployed: no smoke, no revision
    _gcloud(['run', 'deploy', c['name'], f'--image={ref}', '--no-traffic', f'--tag={DEV_TAG}', *place], run)
    doc = describe(cfg, c, run)
    revision, url = tagged(doc)
    if not revision or image_of(doc, 'service') != ref:
        raise Refused(f"{c['name']}: the dev revision was not made from {ref}; production keeps {', '.join(was)}")
    failed = smoke(c, url, bearer, opener, sleep)
    if failed:
        raise Refused(f"{c['name']}: {revision} failed its smoke on the dev rung: {failed}. Production keeps {', '.join(was)}")
    say(f"{c['name']}: {revision} passed on the dev rung")
    _gcloud(['run', 'services', 'update-traffic', c['name'], '--to-latest', *place], run)
    now = serving(describe(cfg, c, run))
    if now != [revision]:
        raise Refused(f"{c['name']}: {revision} was promoted and traffic goes to {', '.join(map(str, now)) or 'nothing'}")
    failed = smoke(c, home, bearer, opener, sleep)
    if failed:
        raise Refused(f"{c['name']}: {revision} serves and failed its smoke: {failed}. Back: gcloud run services update-traffic "
                      f"{c['name']} --to-revisions={was[0] if was else '<the one before>'}=100 {' '.join(place[:2])}")


def release_job(cfg: dict, c: dict, ref: str, say=print, run=None) -> None:
    """The job moved to `ref`, its image checked already, read back: its next run is the new image, nothing is executed."""
    _gcloud(['run', 'jobs', 'update', c['name'], f'--image={ref}', f"--project={cfg['project']}", f"--region={cfg['region']}", '--quiet'], run)
    if image_of(describe(cfg, c, run), 'job') != ref:
        raise Refused(f"{c['name']}: the job does not run {ref} after its update")
    say(f"{c['name']}: its next run is the new image")


def record(environment: str, state: str, did: str = '', description: str = '', deploy=None) -> str:
    """A rung's GitHub deployment, best effort: started (its id) or finished; '' when GitHub kept none."""
    try:
        tok, d = gh_deploy.token(), deploy or gh_deploy
        if not tok:
            return ''
        if state == 'start':
            return d.start(environment, d.ref(), description, tok)
        if did:
            d.status(did, state, '', description, tok)
    except Exception as e:   # noqa: BLE001 - a record never stops a release
        print(f'WARNING: release: {environment} was not recorded ({type(e).__name__})')
    return did


def release(cfg: dict, name: str, root: str, plan=False, force=False, say=print, run=None, clock=time.monotonic, **smokes) -> str:
    """One component's release: 'unchanged', 'released', or with `plan` what it would be. Refused stops it."""
    c, began = cfg['components'][name], clock()
    ref, key = image_ref(cfg, c, root, build=not plan)
    built = clock() - began
    current = running(describe(cfg, c, run), c['kind'])
    if ref and current == ref and not force:
        say(f'{name}: unchanged (it runs the image of this content, {key})')
        return 'unchanged'
    if plan:
        say(f"{name}: would release {key} ({'already built' if ref else 'to build'}); it runs {current or 'nothing read'}")
        return 'planned'
    if not ref:
        raise Refused(f'{name}: no image for {key}')
    check_image(c, ref, run)
    say(f"{name}: {len(c.get('checks') or [])} image check(s) passed")
    did = record(f'{name} · production', 'start', description=f'{name} {key}')
    try:
        (release_job if c['kind'] == 'job' else release_service)(cfg, c, ref, say, run, **(smokes if c['kind'] == 'service' else {}))
    except Exception as e:   # noqa: BLE001 - whatever stopped it, the record says so and the line is an ERROR, never a traceback
        record(f'{name} · production', 'failure', did, f'{name} {key}')
        raise e if isinstance(e, Refused) else Refused(f'{name}: {type(e).__name__}: {e}') from e
    record(f'{name} · production', 'success', did, f'{name} {key} is live')
    say(f'{name}: live on {ref} (image {built:.0f} s, ladder {clock() - began - built:.0f} s)')
    return 'released'


def main(argv=None, say=print, run=None) -> int:
    """The command line above; 0 when every component asked for was released or unchanged, 1 at the first that was not."""
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--config', required=True)
    ap.add_argument('--component', default='', help='one component; every one by default')
    ap.add_argument('--root', default='.', help="the repository's root")
    ap.add_argument('--plan', action='store_true')
    ap.add_argument('--force', action='store_true', help='release a component whose image did not change')
    a = ap.parse_args(argv)
    try:
        cfg = load(a.config)
        names = [a.component] if a.component else list(cfg['components'])
        unknown = [n for n in names if n not in cfg['components']]
        if unknown:
            raise Refused(f"{a.config} has no component {unknown[0]} (it has {', '.join(cfg['components'])})")
        for name in names:
            release(cfg, name, a.root, a.plan, a.force, say, run)
    except Refused as e:
        say(f'ERROR: release: {e}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
