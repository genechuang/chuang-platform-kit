#!/usr/bin/env python3
"""Record a release's rung as a GitHub deployment, so the repository's Deployments view is the CI/CD console
(SMADPickleBot's pipeline/shared/gh-deploy.py, lifted here by #1285, phase 2 of #235; that file is now its shim).

    gh-deploy start ENVIRONMENT [--ref SHA] [--description TEXT]      prints the deployment id
    gh-deploy finish ID success|failure|inactive [--url LIVE_URL] [--description TEXT]

Every rung of every pipeline is an environment named `<component> · <rung>` (`api · dev`, `api · production`); an
environment ending in `· production` is recorded as a production environment, any other as transient. A host's deploy
scripts call this at a rung's start (in_progress) and end (success or failure), the run as the log link (GitHub's run,
or LOG_URL), the live address as the environment link.

A record must never stop a release: any refusal (no token, a 403, GitHub down) prints one WARNING line and exits 0,
and `finish` with an empty id does nothing. The token: GH_DEPLOY_TOKEN (a run's own token), else GITHUB_TOKEN /
GH_TOKEN, else the configured secret from Secret Manager through gcloud, else chuang_platform_kit.github's lookup (the
signed-in gh last) where the kit is installed. A GH_DEPLOY_TOKEN GitHub refuses (401, 403, 404) is tried once more
with the next source. The commit: --ref, else RELEASE_SHA, GITHUB_SHA, SHA, else `git rev-parse HEAD`. GitHub through
the kit's throttled client where the kit is installed, the standard library where it is not.

configure(repo=, project=, secret=, user_agent=, bump=) names the host once: the repository the deployments are
recorded on (GITHUB_REPOSITORY or GITHUB_REPO win at run time), the GCP project and secret the token falls back to, the
User-Agent of the standard-library calls, and `bump(reason)`, called after each record GitHub took (a host's live
signal; never raises into the release).
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

TIMEOUT = 15
STATES = ('success', 'failure', 'error', 'inactive', 'in_progress', 'queued', 'pending')
API = 'https://api.github.com'
_SET = {'repo': '', 'project': '', 'secret': 'GITHUB_TOKEN', 'user_agent': 'chuang-gh-deploy', 'bump': None}


def configure(**kw) -> None:
    """The host's names: repo ('owner/name'), project (the GCP project of the token's secret), secret (its name),
    user_agent and bump (a callable taking the record's reason). An unknown name is refused."""
    for k, v in kw.items():
        if k not in _SET:
            raise TypeError(f'gh_deploy.configure: unknown setting {k!r}')
        _SET[k] = v


def repo(env=None) -> str:
    """The repository the deployments are recorded on: the process's GITHUB_REPOSITORY, else GITHUB_REPO, else the
    configured one. Read from os.environ, never a call's `env`, which feeds the token, the commit and the log link."""
    return os.environ.get('GITHUB_REPOSITORY') or os.environ.get('GITHUB_REPO') or _SET['repo']


def _run(args):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=30, shell=(os.name == 'nt'))  # fixed argv
        return (p.stdout or '').strip() if p.returncode == 0 else ''
    except Exception:   # noqa: BLE001 - a missing CLI is no token
        return ''


def token(env=None, run=_run) -> str:
    """The first token that can be had, in the docstring's order; '' when none."""
    env = os.environ if env is None else env
    for k in ('GH_DEPLOY_TOKEN', 'GITHUB_TOKEN', 'GH_TOKEN'):
        if env.get(k):
            return env[k].strip()
    if _SET['project'] and _SET['secret']:
        t = run(['gcloud', 'secrets', 'versions', 'access', 'latest', f"--secret={_SET['secret']}",
                 f"--project={_SET['project']}"])
        if t:
            return t
    try:
        from chuang_platform_kit import github   # the kit's token lookup, the signed-in gh's last; `run` answers it
    except ImportError:   # no kit here: a runner has its job's own token above, so none is left
        return ''

    def answered(args, **_):
        got = run(args)
        return subprocess.CompletedProcess(args, 0 if got else 1, got, '')
    return github.token(env={}, run=answered)


def ref(given='', env=None, run=_run) -> str:
    """The commit recorded: --ref, else RELEASE_SHA, GITHUB_SHA, SHA, else `git rev-parse HEAD`."""
    env = os.environ if env is None else env
    for v in (given, env.get('RELEASE_SHA'), env.get('GITHUB_SHA'), env.get('SHA')):
        if v and v.strip():
            return v.strip()
    return run(['git', 'rev-parse', 'HEAD'])


def log_url(env=None) -> str:
    """The status's log link: LOG_URL, else this GitHub Actions run's page, else ''."""
    env = os.environ if env is None else env
    if env.get('LOG_URL'):
        return env['LOG_URL']
    if env.get('GITHUB_RUN_ID'):
        return f"{env.get('GITHUB_SERVER_URL', 'https://github.com')}/{env.get('GITHUB_REPOSITORY') or repo()}/actions/runs/{env['GITHUB_RUN_ID']}"
    return ''


def _plain(method, path, body, tok, opener=None):
    """The same call with the standard library, where the kit is not installed (a runner image without it: every
    deploy there went unrecorded until SMADPickleBot #714). A refusal is urllib's own HTTPError, which main reads."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f'{API}{path}', data=data, method=method, headers={
        'Authorization': f'Bearer {tok}', 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': _SET['user_agent'], 'Content-Type': 'application/json'})
    with (opener or urllib.request.urlopen)(req, timeout=TIMEOUT) as r:
        got = json.loads(r.read() or b'{}')
    return got if isinstance(got, dict) else {}


def call(method, path, body, tok, opener=None):
    """One GitHub call through chuang_platform_kit.github's throttled client, or the standard library's where the kit is
    not installed (_plain); the JSON. A refusal is raised as urllib's HTTPError with GitHub's status, so main's token
    fallback reads it; no answer, or a quota the kit refuses to spend, as URLError."""
    try:
        from chuang_platform_kit import github
    except ImportError:
        return _plain(method, path, body, tok, opener)
    try:
        got = github.request(tok, method, path, body, opener=opener, timeout=TIMEOUT)[1]
    except github.RateLimited as e:
        raise urllib.error.URLError(e.message) from None
    except github.GitHubError as e:
        if e.status:
            raise urllib.error.HTTPError(f'{github.API}{path}', e.status, e.message, {}, None) from None
        raise urllib.error.URLError(e.message) from None
    return got if isinstance(got, dict) else {}


def start(environment, sha, description, tok, opener=None, env=None) -> str:
    """Create the deployment (no merge, no required checks: a record, not a gate) and mark it in progress; its id."""
    production = environment.endswith('· production')
    d = call('POST', f'/repos/{repo(env)}/deployments',
             {'ref': sha, 'environment': environment, 'description': description[:140], 'auto_merge': False,
              'required_contexts': [], 'production_environment': production, 'transient_environment': not production},
             tok, opener)
    did = str(d.get('id') or '')
    if not did:
        raise RuntimeError(d.get('message') or 'no deployment id in the answer')
    status(did, 'in_progress', '', description, tok, opener, env)
    return did


def status(did, state, url, description, tok, opener=None, env=None):
    """A status on deployment `did`: its state, the description, the log link and the live address when given."""
    body = {'state': state, 'description': description[:140], 'auto_inactive': state == 'success'}
    if log_url(env):
        body['log_url'] = log_url(env)
    if url:
        body['environment_url'] = url
    call('POST', f'/repos/{repo(env)}/deployments/{did}/statuses', body, tok, opener)


def _no_bump(reason: str) -> bool:
    return False


def main(argv=None, env=None, opener=None, run=_run, out=print, bump=None) -> int:
    """The command line; always 0, a refusal one WARNING line on stderr."""
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('start')
    s.add_argument('environment')
    s.add_argument('--ref', default='')
    s.add_argument('--description', default='')
    f = sub.add_parser('finish')
    f.add_argument('id')
    f.add_argument('state', choices=STATES)
    f.add_argument('--url', default='')
    f.add_argument('--description', default='')
    a = ap.parse_args(argv)
    if a.cmd == 'finish' and not a.id.strip():
        return 0
    if not repo(env):
        print('WARNING: gh-deploy: no repository configured; this rung is not recorded in the Deployments view', file=sys.stderr)
        return 0
    tok = token(env, run)
    if not tok:
        print('WARNING: gh-deploy: no GitHub token; this rung is not recorded in the Deployments view', file=sys.stderr)
        return 0

    def record(t):
        if a.cmd == 'start':
            sha = ref(a.ref, env, run)
            out(start(a.environment, sha, a.description or f'{a.environment} at {sha[:7]}', t, opener, env))
        else:
            status(a.id.strip(), a.state, a.url, a.description or a.state, t, opener, env)

    try:
        try:
            record(tok)
        except urllib.error.HTTPError as e:
            # The run's own token (GH_DEPLOY_TOKEN) refused, a job without `deployments: write` say: once more with
            # the next source, the secret before it.
            src = os.environ if env is None else env
            if e.code not in (401, 403, 404) or tok != (src.get('GH_DEPLOY_TOKEN') or '').strip():
                raise
            rest = {k: v for k, v in src.items() if k not in ('GH_DEPLOY_TOKEN', 'GITHUB_TOKEN', 'GH_TOKEN')}
            fallback = token(rest, run)
            if not fallback or fallback == tok:
                raise
            print(f'WARNING: gh-deploy: the run token was refused ({e.code}); recording with the automation token', file=sys.stderr)
            record(fallback)
        try:
            (bump or _SET['bump'] or _no_bump)(f'gh-deploy {a.cmd}')
        except Exception:   # noqa: BLE001 - the host's signal never stops a release
            pass
    except urllib.error.HTTPError as e:
        hint = ' (the token needs "Deployments: read and write" on this repository)' if e.code in (403, 404) else ''
        print(f'WARNING: gh-deploy: GitHub answered {e.code} to the {a.cmd}{hint}; the release goes on unrecorded', file=sys.stderr)
    except Exception as e:   # noqa: BLE001 - a record never stops a release
        print(f'WARNING: gh-deploy: {type(e).__name__} on the {a.cmd}; the release goes on unrecorded', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
