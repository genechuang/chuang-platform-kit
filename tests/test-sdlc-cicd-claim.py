#!/usr/bin/env python3
"""chuang_sdlc.cicd.claim (SMADPickleBot #1681): the push decider's half of a two-path pipeline claim, with a host's
names handed to Decider and its per-job claim handed in as `decide`. SMADPickleBot's tests/test-ci-claim.py and
tests/test-push-decider.py keep the real host's lines and collections against it.

Pinned (the positive case first): a push whose pipeline prefers the provider records one pending check a pipeline; a
pipeline that prefers the cloud build claims and starts its trigger at once; a dispatch-only pipeline is only ever
recorded; an unreachable store starts the build without a claim; the sweep deletes a check the provider claimed, starts
the trigger for one nobody claimed, and dispatches a dispatch-only pipeline unless a claim or a run is there; a start
or a dispatch that is refused is one ERROR line naming the hand run; the payload's files and the pipelines they touch;
the trigger's request; nothing in the module names a project.

Run:  python tests/test-sdlc-cicd-claim.py      (prints PASS/FAIL per case and `N failure(s)`)
"""
import io
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'sdlc', 'src'))
from chuang_sdlc.cicd import claim as C  # noqa: E402

FAILURES = []
NOW = datetime(2026, 11, 17, 21, 0, tzinfo=timezone.utc)
SHA = 'a' * 40


def eq(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ('' if ok else f'\n        got  {got!r}\n        want {want!r}'))
    if not ok:
        FAILURES.append(label)


class Down(Exception):
    """The host's store's own "could not be reached"."""


class Store:
    """A claim store in memory: {collection: {id: doc}}; `down` names the calls that raise."""

    def __init__(self, docs=None, down=()):
        self.docs, self.down, self.deleted = {k: dict(v) for k, v in (docs or {}).items()}, set(down), []

    def _check(self, call):
        if call in self.down:
            raise Down('URLError')

    def get(self, collection, doc_id):
        self._check('get')
        return self.docs.get(collection, {}).get(doc_id)

    def set(self, collection, doc_id, doc):
        self._check('set')
        self.docs.setdefault(collection, {})[doc_id] = doc

    def delete(self, collection, doc_id):
        self._check('delete')
        self.deleted.append(doc_id)
        self.docs.get(collection, {}).pop(doc_id, None)

    def due(self, collection, field, now):
        self._check('due')
        return [(k, v) for k, v in sorted(self.docs.get(collection, {}).items()) if v[field] <= now]


class GitHub:
    """The kit's GitHub client, in memory."""

    class GitHubError(Exception):
        def __init__(self, status=0, message=''):
            self.status, self.message = status, message

    def __init__(self, refuse=0, runs=0):
        self.calls, self.refuse, self.runs = [], refuse, runs

    def request(self, token, method, path, body=None, opener=None, timeout=None):
        self.calls.append((method, path, body))
        if self.refuse:
            raise self.GitHubError(self.refuse, 'no')

    def get(self, token, path, opener=None, timeout=None):
        self.calls.append(('GET', path, None))
        return {'total_count': self.runs}


def decider(github=None, taken=None, **kw):
    """A Decider for a made-up host; `taken` {pipeline: path} is who already holds each claim."""
    asked = []

    def decide(path, pipeline, sha, store, wait=0, polls=0, now=None):
        asked.append((path, pipeline, wait, polls))
        holder = (taken or {}).get(pipeline)
        if holder and holder != path:
            return {'run': False, 'reason': 'the other path took it'}
        return {'run': True, 'reason': 'claimed'}
    d = C.Decider(project='acme-prod', region='us-east1', repo='acme/widgets', decide=decide, unreachable=Down,
                  github=github, claims='claims', pending='pending', preferences='prefs', hint='the build box down?', **kw)
    return d, asked


def test_a_push_records_a_check_or_starts_the_cloud_build():
    """the positive case: a pipeline that prefers the provider gets one pending check due after the wait; one that prefers the cloud build claims with no wait and starts its trigger now; a dispatch-only pipeline is only recorded"""
    d, asked = decider()
    store = Store({'prefs': {'api': {'preferred': 'gcb'}}})
    started, said = [], []
    lines = d.on_push(SHA, {'api': 'api-deploy', 'tests': 'tests-run', 'app': 'dispatch:app.yml'}, store, 'tok',
                      runner=lambda real, sha, token: started.append((real, sha, token)) or 'build-1', now=NOW, out=said.append)
    pending = store.docs['pending']
    eq("recorded or started", (lines == said, sorted(pending), pending['tests__' + SHA]['due'] - NOW, pending['app__' + SHA]['real'],
                               started, asked, lines),
       (True, ['app__' + SHA, 'tests__' + SHA], timedelta(seconds=75), 'dispatch:app.yml', [('api-deploy', SHA, 'tok')],
        [('gcb', 'api', 0, 0)],
        ['api at aaaaaaa: claimed; started api-deploy: build build-1',
         'app at aaaaaaa: GitHub only; the sweep checks for its claim in 75s',
         'tests at aaaaaaa: GitHub Actions is preferred; the sweep checks for its claim in 75s']))


def test_an_unreachable_store_starts_without_a_claim():
    """  the preference unreadable, or the check unwritable, starts the build now with no claim and says so; a dispatch-only pipeline that cannot be recorded is a WARNING; a refused start is one ERROR line naming the hand run"""
    d, _ = decider()
    started = []
    run = lambda real, sha, token: started.append(real) or 'b9'   # noqa: E731
    unread = d.on_push(SHA, {'api': 'api-deploy'}, Store(down=['get']), 'tok', runner=run, out=lambda t: None)
    unwritten = d.on_push(SHA, {'api': 'api-deploy', 'app': 'dispatch:app.yml'}, Store(down=['set']), 'tok', runner=run, out=lambda t: None)

    def refused(real, sha, token):
        raise RuntimeError('HTTP 403 denied')
    failed = d.on_push(SHA, {'api': 'api-deploy'}, Store(down=['get']), 'tok', runner=refused, out=lambda t: None)
    eq("without a claim", (started, unread[0].startswith('api at aaaaaaa: Firestore unreachable (URLError): starting without a claim'),
                           unwritten[0].startswith('api at aaaaaaa: Firestore unreachable while recording the check'),
                           unwritten[1].startswith('WARNING: push-decider: app at aaaaaaa: the check could not be recorded'),
                           failed[0]),
       (['api-deploy', 'api-deploy'], True, True, True,
        'ERROR: push-decider: Cloud Build holds the claim for api at aaaaaaa but its build api-deploy did not start (HTTP 403 denied); '
        'GitHub stepped aside, so run the trigger by hand: gcloud builds triggers run api-deploy --region=us-east1 '
        f'--sha={SHA} --substitutions=_BACKEND=gcb'))


def test_the_sweep():
    """  a check past due whose claim the provider holds is deleted and nothing starts; one nobody claimed starts its trigger; one not yet due is left; a broken document is deleted; an unreadable queue is one WARNING"""
    d, asked = decider(taken={'tests': 'gha'})
    store = Store({'pending': {
        'tests__' + SHA: {'pipeline': 'tests', 'real': 'tests-run', 'sha': SHA, 'due': NOW - timedelta(seconds=5)},
        'api__' + SHA: {'pipeline': 'api', 'real': 'api-deploy', 'sha': SHA, 'due': NOW - timedelta(seconds=1)},
        'site__' + SHA: {'pipeline': 'site', 'real': 'site-deploy', 'sha': SHA, 'due': NOW + timedelta(seconds=30)},
        'broken': {'due': NOW - timedelta(days=1)}}})
    started = []
    lines = d.sweep(store, 'tok', runner=lambda real, sha, token: started.append(real) or 'b2', now=NOW, out=lambda t: None)
    down = d.sweep(Store(down=['due']), 'tok', now=NOW, out=lambda t: None)
    eq("swept", (lines, started, sorted(store.docs['pending']), sorted(store.deleted), down),
       (['api at aaaaaaa: claimed; started api-deploy: build b2', 'tests at aaaaaaa: the other path took it'], ['api-deploy'],
        ['site__' + SHA], ['api__' + SHA, 'broken', 'tests__' + SHA],
        ['WARNING: push-decider: the pending checks could not be read (URLError); the next sweep tries again']))


def test_a_dispatch_only_pipeline_is_dispatched_only_when_nobody_took_it():
    """  with a claim, or with a run already on GitHub for the push, nothing is dispatched; with neither, the workflow is dispatched on the provider's own runner with the host's ref and hint; no token, or a refusal, is one ERROR line naming the hand run"""
    due = {'pending': {'app__' + SHA: {'pipeline': 'app', 'real': 'dispatch:app.yml', 'sha': SHA, 'due': NOW - timedelta(seconds=1)}}}
    env = {'GITHUB_DISPATCH_TOKEN': 'ghp_x'}
    keep = dict(os.environ)
    os.environ.update(env)
    try:
        gh = GitHub()
        d, _ = decider(gh, ref='trunk')
        claimed = d.sweep(Store(dict(due, claims={'app__' + SHA: {'path': 'gha'}})), 'tok', now=NOW, out=lambda t: None)
        ran_gh = GitHub(runs=1)
        ran = decider(ran_gh)[0].sweep(Store(due), 'tok', now=NOW, out=lambda t: None)
        sent = d.sweep(Store(due), 'tok', now=NOW, out=lambda t: None)
        refused = decider(GitHub(refuse=422))[0].sweep(Store(due), 'tok', now=NOW, out=lambda t: None)
        os.environ.pop('GITHUB_DISPATCH_TOKEN')
        no_token = decider(GitHub())[0].sweep(Store(due), 'tok', now=NOW, out=lambda t: None)
    finally:
        os.environ.clear()
        os.environ.update(keep)
    eq("dispatched once", (claimed, ran, [c for c in ran_gh.calls if c[0] == 'POST'], sent,
                           [c for c in gh.calls if c[0] == 'POST'], refused[0], no_token[0]),
       (['app at aaaaaaa: GitHub Actions took this push; nothing to dispatch'],
        ['app at aaaaaaa: no claim, but GitHub has a app.yml run for this push (its claim was not written); nothing to dispatch'], [],
        ["app at aaaaaaa: no runner took it within the wait (the build box down?); dispatched app.yml on GitHub's runner"],
        [('POST', '/repos/acme/widgets/actions/workflows/app.yml/dispatches', {'ref': 'trunk', 'inputs': {'runner': 'github'}})],
        "ERROR: push-decider: no runner took app at aaaaaaa and the dispatch to GitHub's runner was refused (HTTP 422); "
        'run it by hand: gh workflow run app.yml -f runner=github',
        "ERROR: push-decider: no runner took app at aaaaaaa and the dispatch to GitHub's runner was refused "
        '(no GITHUB_DISPATCH_TOKEN on the decider); run it by hand: gh workflow run app.yml -f runner=github'))


def test_the_payload_and_the_pipelines_it_touches():
    """  the payload's added, modified and removed paths; None for a forced push, 20 commits or 3,000 files; a pipeline is touched by a path its filter includes and does not ignore, with the host's matcher; None names every pipeline"""
    payload = {'commits': [{'added': ['api/a.py'], 'modified': ['docs/x.md'], 'removed': ['app/old.ts']}, {'modified': ['api/a.py']}]}
    pipelines = {'api': {'name': 'api-deploy', 'included_files': ['api/*']},
                 'app': {'name': 'dispatch:app.yml', 'included_files': ['app/*'], 'ignored_files': ['app/old*']},
                 'site': {'name': 'site-deploy', 'included_files': ['site/*']}}
    match = lambda glob, path: re.fullmatch(glob.replace('*', '.*'), path) is not None   # noqa: E731
    files = C.changed_files(payload)
    eq("touched", (files, C.pipelines_touched(files, pipelines, match), C.pipelines_touched(None, pipelines, match),
                   C.changed_files({'forced': True, 'commits': [{}]}), C.changed_files({'commits': [{}] * 20}),
                   C.changed_files({'commits': [{'added': ['f'] * 3000}]})),
       (['api/a.py', 'docs/x.md', 'app/old.ts', 'api/a.py'], {'api': 'api-deploy'},
        {'api': 'api-deploy', 'app': 'dispatch:app.yml', 'site': 'site-deploy'}, None, None, None))


def test_the_trigger_request_and_no_project_in_the_module():
    """  run_trigger posts the commit with _GATED and _SHA to the host's project and region and answers the build's id; a refusal is a RuntimeError with the status; the module names no project of its own"""
    d, _ = decider()
    seen = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout=None):
        seen.append((req.full_url, json.loads(req.data), req.headers.get('Authorization')))
        return Resp(json.dumps({'metadata': {'build': {'id': 'b-77'}}}).encode())
    build = d.run_trigger('api-deploy', SHA, 'tok', opener=opener, substitutions={'_EXTRA': '1'})
    try:
        d.run_trigger('api-deploy', SHA, 'tok', opener=lambda req, timeout=None: Resp(b'{}'))
        empty = ''
    except RuntimeError as e:
        empty = str(e)
    src = open(C.__file__, encoding='utf-8').read().lower()
    # the header may cite the ticket it came from, as every kit module does; no project id, repository or session
    eq("the request", (build, seen, empty, [w for w in ('smad-pickleball', 'picklebot_core', 'genechuang', 'snow white') if w in src]),
       ('b-77', [('https://cloudbuild.googleapis.com/v1/projects/acme-prod/locations/us-east1/triggers/api-deploy:run',
                  {'source': {'commitSha': SHA, 'substitutions': {'_GATED': 'gcb', '_SHA': SHA, '_EXTRA': '1'}}}, 'Bearer tok')],
        'no build in the answer', []))


if __name__ == '__main__':
    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            print(fn.__doc__.strip().splitlines()[0])
            fn()
    print(f'{len(FAILURES)} failure(s)')
    sys.exit(1 if FAILURES else 0)
