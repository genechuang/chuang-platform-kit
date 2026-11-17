"""The push decider's half of a two-path pipeline claim (SMADPickleBot #1681): what a webhook service does with a
push when a project can build on two paths (a CI provider's runners and a cloud build) and only one should.

The other half is the host's: the per-job claim each pipeline job makes as its first step, which must run with
nothing installed and so stays a standard-library file in the host's repository. That claim is handed in here as
`decide(path, pipeline, sha, store, wait=, polls=, now=)`, answering {run, reason}. This module is what runs only where
the package is installed, in the decider:

  changed_files      the paths a push payload names, or None when it cannot list them all
  pipelines_touched  which pipelines' path filters those paths match (the matcher is the host's, the one its bare
                     pipeline steps use, handed in)
  Decider            a project's names once, then: on_push (claim and start the cloud build now when it is the
                     preferred path, else record a pending check), sweep (each pending check past due: the provider
                     claimed, done; nobody did, the cloud build claims and starts, or the workflow is dispatched on the
                     provider's own runner), run_trigger, dispatch_workflow, workflow_ran, hand_run

Nothing here names a project: the cloud project, the region, the repository, the Firestore collections, the path
names and the words are the host's, given to Decider. Standard library only; the GitHub client is the kit's, handed in.
"""
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

DISPATCH = 'dispatch:'            # a pipeline with no cloud build trigger: `dispatch:<workflow file>`
TRIGGER_TIMEOUT = 30
FILES_PER_COMMIT = 3000           # GitHub's push payload lists at most this many files a commit
COMMITS_PER_PUSH = 20             # and this many commits


def changed_files(payload: dict):
    """The push payload's changed paths, or None when the payload cannot list them all: GitHub carries at most
    20 commits and 3,000 files a commit, and a forced push's `before` is not an ancestor."""
    commits = payload.get('commits') or []
    if payload.get('forced') or len(commits) >= COMMITS_PER_PUSH:
        return None
    out = []
    for c in commits:
        for key in ('added', 'modified', 'removed'):
            out.extend(c.get(key) or [])
        if any(len(c.get(key) or []) >= FILES_PER_COMMIT for key in ('added', 'modified', 'removed')):
            return None
    return out


def pipelines_touched(changed, pipelines: dict, match) -> dict:
    """{pipeline: trigger name} for every pipeline whose path filter (its `included_files`) matches one of the
    changed paths that none of its `ignored_files` matches. `match(glob, path)` is the host's path matcher, the one
    its triggers' filters are written for. `changed` None (the payload could not say) names every pipeline, as a
    trigger would start."""
    if changed is None:
        return {k: v['name'] for k, v in pipelines.items()}
    files = list(dict.fromkeys(changed))

    def starts(v, f):
        return any(match(g, f) for g in v.get('included_files') or []) \
            and not any(match(g, f) for g in v.get('ignored_files') or [])
    return {k: v['name'] for k, v in pipelines.items() if any(starts(v, f) for f in files)}


class Decider:
    """One project's push decider. `decide` is the host's per-job claim; `unreachable` the exception its store raises
    when the claim store cannot be reached; `github` the kit's GitHub client module (request, get, GitHubError), or
    None where it is not installed (a dispatch is then refused plainly). `names` {path: its words} for the lines;
    `hint` what a line says when no runner took a push; `prefix` the service's name on WARNING and ERROR lines."""

    def __init__(self, *, project: str, region: str, repo: str, decide, unreachable=Exception, github=None,
                 claims: str = 'ci_claims', pending: str = 'ci_pending', preferences: str = 'ci_paths',
                 paths=('gha', 'gcb'), names=None, default_preferred: str = 'gha', cloud_path: str = 'gcb',
                 claim_wait: float = 75, pending_ttl_days: int = 1, dispatch_token_env: str = 'GITHUB_DISPATCH_TOKEN',
                 ref: str = 'main', prefix: str = 'push-decider', hint: str = 'its own runners down?',
                 trigger_timeout: int = TRIGGER_TIMEOUT):
        self.project, self.region, self.repo = project, region, repo
        self.decide, self.unreachable, self.github = decide, unreachable, github
        self.claims, self.pending, self.preferences = claims, pending, preferences
        self.paths, self.names = tuple(paths), dict(names or {'gha': 'GitHub Actions', 'gcb': 'Cloud Build'})
        self.default_preferred, self.cloud_path = default_preferred, cloud_path
        self.claim_wait, self.pending_ttl_days = claim_wait, pending_ttl_days
        self.dispatch_token_env, self.ref = dispatch_token_env, ref
        self.prefix, self.hint, self.trigger_timeout = prefix, hint, trigger_timeout

    # ------------------------------------------------------------------ the real build
    def run_trigger(self, name: str, sha: str, token: str, opener=None, substitutions=None) -> str:
        """Start trigger `name` on commit `sha` (Cloud Build's triggers.run with a RepoSource: the commit and the
        substitutions); the started build's id. `_GATED`, not `_BACKEND`: the real build skips the claim (the decider
        made it) but still asks its dependency gate whether the push changes what it deploys. Raises RuntimeError with
        the refusal."""
        opener = opener or urllib.request.urlopen
        url = f'https://cloudbuild.googleapis.com/v1/projects/{self.project}/locations/{self.region}/triggers/{name}:run'
        body = {'source': {'commitSha': sha,
                           'substitutions': dict({'_GATED': self.cloud_path, '_SHA': sha}, **(substitutions or {}))}}
        req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), method='POST',
                                     headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
        try:
            with opener(req, timeout=self.trigger_timeout) as resp:
                out = json.loads(resp.read().decode('utf-8') or '{}')
        except urllib.error.HTTPError as e:
            detail = ''
            try:
                detail = e.read().decode('utf-8')[:300]
            except Exception:   # noqa: BLE001 - the status alone then
                pass
            raise RuntimeError(f'HTTP {e.code} {detail}'.strip()) from None
        except Exception as e:   # noqa: BLE001 - a network error, a bad answer
            raise RuntimeError(type(e).__name__) from None
        build = ((out.get('metadata') or {}).get('build') or {}).get('id') or out.get('name') or ''
        if not build:
            raise RuntimeError('no build in the answer')
        return build

    def dispatch_workflow(self, real: str, sha: str, token: str = '', opener=None, env=None, repo: str = '',
                          inputs=None) -> str:
        """Dispatch the workflow a `dispatch:<file>` entry names on the ref with runner=github, or with `inputs`.
        GitHub's REST API with the token in the dispatch token's environment variable; `token`, the cloud one the
        triggers take, is not used. 'dispatched'; raises RuntimeError with the refusal."""
        env = os.environ if env is None else env
        workflow = real[len(DISPATCH):]
        gh = (env.get(self.dispatch_token_env) or '').strip()
        if not gh:
            raise RuntimeError(f'no {self.dispatch_token_env} on the decider')
        repo = repo or env.get('GITHUB_REPO') or self.repo
        if self.github is None:
            raise RuntimeError('chuang-platform-kit is not installed')
        try:
            self.github.request(gh, 'POST', f'/repos/{repo}/actions/workflows/{workflow}/dispatches',
                                {'ref': self.ref, 'inputs': inputs or {'runner': 'github'}}, opener=opener,
                                timeout=self.trigger_timeout)
        except self.github.GitHubError as e:
            raise RuntimeError(f'HTTP {e.status}' if e.status else (e.message or 'no answer').split(':')[0]) from None
        except Exception as e:   # noqa: BLE001 - a network error
            raise RuntimeError(type(e).__name__) from None
        return 'dispatched'

    def hand_run(self, real: str, sha: str) -> str:
        """The command that runs the real trigger by hand, for an ERROR line when the decider could not."""
        if real.startswith(DISPATCH):
            return f'gh workflow run {real[len(DISPATCH):]} -f runner=github'
        return (f'gcloud builds triggers run {real} --region={self.region} --sha={sha} '
                f'--substitutions=_BACKEND={self.cloud_path}')

    def workflow_ran(self, real: str, sha: str, env=None, opener=None, repo: str = '') -> bool:
        """Whether GitHub already has a run of the `dispatch:<file>` workflow for `sha` (any state): the run that took
        the push but could not write its claim. One REST read with the dispatch token; False when it cannot be read,
        so the fallback stands."""
        env = os.environ if env is None else env
        gh = (env.get(self.dispatch_token_env) or '').strip()
        if not gh or self.github is None:
            return False
        repo = repo or env.get('GITHUB_REPO') or self.repo
        try:
            got = self.github.get(gh, f'/repos/{repo}/actions/workflows/{real[len(DISPATCH):]}/runs?head_sha={sha}&per_page=5',
                                  opener=opener, timeout=self.trigger_timeout)
            return int((got if isinstance(got, dict) else {}).get('total_count') or 0) > 0
        except Exception:   # noqa: BLE001 - unread (a refusal, the quota, no answer): the fallback dispatches as before
            return False

    # ------------------------------------------------------------------ the push and the sweep
    def record_pending(self, pipeline: str, real: str, sha: str, store, wait=None, now=None) -> dict:
        """Write <pending>/<pipeline>__<sha> {pipeline, real, sha, due, expires_at}, the check the sweep makes `wait`
        seconds from now; the document written. A re-delivered push writes it again, harmlessly."""
        wait = self.claim_wait if wait is None else wait
        now = now or datetime.now(timezone.utc)
        doc = {'pipeline': pipeline, 'real': real, 'sha': sha, 'at': now, 'due': now + timedelta(seconds=wait),
               'expires_at': now + timedelta(days=self.pending_ttl_days)}
        store.set(self.pending, f'{pipeline}__{sha}', doc)
        return doc

    def on_push(self, sha: str, touched: dict, store, token: str, runner=None, wait=None, now=None, out=print) -> list:
        """The decider's answer to one push: for each touched pipeline, claim and start the real build now when the
        cloud build is preferred (`decide` with no wait), else record the pending check for the sweep. The claim store
        unreachable: start every touched build now, without a claim (the double run). The lines written, one a
        pipeline."""
        runner = runner or self.run_trigger
        wait = self.claim_wait if wait is None else wait
        cloud, other = self.names.get(self.cloud_path, 'the cloud build'), self.names.get(self.default_preferred, 'the provider')
        lines = []
        for pipeline, real in sorted(touched.items()):
            if real.startswith(DISPATCH):
                # The provider is the only path: no preference to read and nothing to start now; the sweep looks for the claim.
                try:
                    self.record_pending(pipeline, real, sha, store, wait=wait, now=now)
                    lines.append(_line(f'{pipeline} at {sha[:7]}: GitHub only; the sweep checks for its claim in {wait:.0f}s', out))
                except self.unreachable as e:
                    lines.append(_line(f'WARNING: {self.prefix}: {pipeline} at {sha[:7]}: the check could not be recorded ({e}); '
                                       f'nothing will dispatch it if its runners are down', out))
                continue
            try:
                pref = store.get(self.preferences, pipeline) or {}
                preferred = pref.get('preferred') if pref.get('preferred') in self.paths else self.default_preferred
            except self.unreachable as e:
                lines.append(self._start(pipeline, real, sha, token, runner,
                                         f'Firestore unreachable ({e}): starting without a claim (GitHub may run it too)', out))
                continue
            if preferred == self.cloud_path:
                d = self.decide(self.cloud_path, pipeline, sha, store, wait=0, polls=0, now=now)
                lines.append(self._start(pipeline, real, sha, token, runner, d['reason'], out) if d['run']
                             else _line(f'{pipeline} at {sha[:7]}: {d["reason"]}', out))
                continue
            try:
                self.record_pending(pipeline, real, sha, store, wait=wait, now=now)
                lines.append(_line(f'{pipeline} at {sha[:7]}: {other} is preferred; the sweep checks for its claim in {wait:.0f}s', out))
            except self.unreachable as e:
                lines.append(self._start(pipeline, real, sha, token, runner,
                                         f'Firestore unreachable while recording the check ({e}): starting without a claim', out))
        del cloud
        return lines

    def sweep(self, store, token: str, runner=None, now=None, out=print, dispatcher=None, ran=None) -> list:
        """Every pending check past due: the provider's claim is there, the check is deleted; none, the cloud build
        claims (the failover) and the real trigger is started, then the check is deleted. A start that fails is an
        ERROR line naming the hand run, and the check is deleted so the sweep does not alarm every minute. The lines
        written."""
        runner = runner or self.run_trigger
        dispatcher = dispatcher or self.dispatch_workflow
        now = now or datetime.now(timezone.utc)
        lines = []
        try:
            due = store.due(self.pending, 'due', now)
        except self.unreachable as e:
            return [_line(f'WARNING: {self.prefix}: the pending checks could not be read ({e}); the next sweep tries again', out)]
        for doc_id, doc in due:
            pipeline, real, sha = doc.get('pipeline') or '', doc.get('real') or '', doc.get('sha') or ''
            if not (pipeline and real and sha):
                store.delete(self.pending, doc_id)
                continue
            if real.startswith(DISPATCH):
                lines.append(self._redispatch(pipeline, real, sha, store, dispatcher, out, ran))
            else:
                d = self.decide(self.cloud_path, pipeline, sha, store, wait=0, polls=0, now=now)
                lines.append(self._start(pipeline, real, sha, token, runner, d['reason'], out) if d['run']
                             else _line(f'{pipeline} at {sha[:7]}: {d["reason"]}', out))
            try:
                store.delete(self.pending, doc_id)
            except self.unreachable as e:
                lines.append(_line(f'WARNING: {self.prefix}: {doc_id} could not be deleted ({e}); the next sweep sees it again', out))
        return lines

    def _redispatch(self, pipeline, real, sha, store, dispatcher, out, ran=None):
        """A dispatch entry's check: the provider's claim is there (a runner took the push), nothing to do; no claim
        but GitHub already has a run of the workflow for the push (its claim could not be written), nothing to do;
        neither, the workflow is dispatched on GitHub's runner. No claim is taken here: the dispatched run claims it."""
        try:
            holder = store.get(self.claims, f'{pipeline}__{sha}')
        except self.unreachable as e:
            return _line(f'WARNING: {self.prefix}: {pipeline} at {sha[:7]}: the claim could not be read ({e}); not dispatched', out)
        if holder:
            return _line(f'{pipeline} at {sha[:7]}: {self.names.get(holder.get("path"), "a runner")} took this push; nothing to dispatch', out)
        if (ran or self.workflow_ran)(real, sha):
            return _line(f'{pipeline} at {sha[:7]}: no claim, but GitHub has a {real[len(DISPATCH):]} run for this push '
                         f'(its claim was not written); nothing to dispatch', out)
        try:
            dispatcher(real, sha, '')
        except RuntimeError as e:
            return _line(f'ERROR: {self.prefix}: no runner took {pipeline} at {sha[:7]} and the dispatch to GitHub\'s runner was '
                         f'refused ({e}); run it by hand: {self.hand_run(real, sha)}', out)
        return _line(f'{pipeline} at {sha[:7]}: no runner took it within the wait ({self.hint}); dispatched '
                     f'{real[len(DISPATCH):]} on GitHub\'s runner', out)

    def _start(self, pipeline, real, sha, token, runner, why, out):
        try:
            build = runner(real, sha, token)
        except RuntimeError as e:
            return _line(f'ERROR: {self.prefix}: {self.names.get(self.cloud_path, "The cloud build")} holds the claim for {pipeline} at '
                         f'{sha[:7]} but its build {real} did not start ({e}); GitHub stepped aside, so run the trigger by hand: '
                         f'{self.hand_run(real, sha)}', out)
        return _line(f'{pipeline} at {sha[:7]}: {why}; started {real}: build {build}', out)


def _line(text, out):
    out(text)
    return text
