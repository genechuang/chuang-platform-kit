#!/usr/bin/env python3
"""chuang_sdlc.cicd.gh_deploy (SMADPickleBot #1285, lifted from its pipeline/shared/gh-deploy.py, now a shim): a host's
own names through configure(), a record on the configured repository, the host's signal after it, and a release never
stopped by a refusal. SMADPickleBot's tests/test-ci-console.py keeps the token order, the run-token fallback and the
standard-library path (#714) against the real host.

Run:  python tests/test-sdlc-cicd-gh-deploy.py      (prints PASS/FAIL per case and `N failure(s)`)
"""
import contextlib
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'sdlc', 'src'))
from chuang_sdlc.cicd import gh_deploy as G  # noqa: E402

FAILURES = []


def eq(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ('' if ok else f'\n        got  {got!r}\n        want {want!r}'))
    if not ok:
        FAILURES.append(label)


@contextlib.contextmanager
def host(**kw):
    keep, env = dict(G._SET), {k: os.environ.pop(k, None) for k in ('GITHUB_REPOSITORY', 'GITHUB_REPO')}
    G.configure(**kw)
    try:
        yield
    finally:
        G._SET.clear()
        G._SET.update(keep)
        for k, v in env.items():
            if v is not None:
                os.environ[k] = v


def test_a_start_records_on_the_configured_repository_and_bumps_the_hosts_signal():
    """the positive case: start posts the deployment on the configured repository, marks it in progress, prints its id and calls the host's bump once; finish posts the status there"""
    calls, bumps, out = [], [], []
    keep = G.call
    G.call = lambda method, path, body, tok, opener=None: calls.append((method, path, body.get('state') or body.get('environment'))) or {'id': 7}
    try:
        with host(repo='acme/app', bump=bumps.append):
            code = G.main(['start', 'api · production', '--ref', 'abc'], env={'GH_DEPLOY_TOKEN': 't'}, out=out.append)
            G.main(['finish', '7', 'success', '--url', 'https://x'], env={'GH_DEPLOY_TOKEN': 't'}, out=out.append)
    finally:
        G.call = keep
    eq('recorded', (code, out, calls, bumps),
       (0, ['7'], [('POST', '/repos/acme/app/deployments', 'api · production'),
                   ('POST', '/repos/acme/app/deployments/7/statuses', 'in_progress'),
                   ('POST', '/repos/acme/app/deployments/7/statuses', 'success')],
        ['gh-deploy start', 'gh-deploy finish']))


def test_the_process_repository_wins_and_none_records_nothing():
    """  GITHUB_REPOSITORY wins over the configured repository; with neither, one WARNING and exit 0, no call"""
    with host(repo='acme/app'):
        os.environ['GITHUB_REPOSITORY'] = 'other/repo'
        try:
            got = G.repo()
        finally:
            os.environ.pop('GITHUB_REPOSITORY', None)
    called, err = [], io.StringIO()
    keep = G.call
    G.call = lambda *a, **k: called.append(a) or {}
    try:
        with host(repo=''), contextlib.redirect_stderr(err):
            code = G.main(['start', 'x · dev'], env={'GH_DEPLOY_TOKEN': 't'}, out=lambda m: None)
    finally:
        G.call = keep
    eq('the repository', (got, code, called, 'no repository configured' in err.getvalue()), ('other/repo', 0, [], True))


def test_a_bump_that_raises_and_a_refusal_never_stop_the_release():
    """  the host's bump raising is swallowed; GitHub down is one WARNING and exit 0"""
    def boom(reason):
        raise RuntimeError('signal down')
    keep = G.call
    G.call = lambda *a, **k: {'id': 3}
    try:
        with host(repo='acme/app', bump=boom):
            code = G.main(['start', 'x · dev'], env={'GH_DEPLOY_TOKEN': 't'}, out=lambda m: None)
    finally:
        G.call = keep

    def down(*a, **k):
        raise OSError('unreachable')
    err = io.StringIO()
    G.call = down
    try:
        with host(repo='acme/app'), contextlib.redirect_stderr(err):
            refused = G.main(['finish', '3', 'failure'], env={'GH_DEPLOY_TOKEN': 't'}, out=lambda m: None)
    finally:
        G.call = keep
    eq('never stopped', (code, refused, 'OSError on the finish; the release goes on unrecorded' in err.getvalue()), (0, 0, True))


def test_the_plain_call_carries_the_hosts_user_agent_and_the_secret_is_configured():
    """  the standard-library call names the host's User-Agent; the token falls back to the configured secret in the configured project, and to no gcloud read when neither is set"""
    seen = {}

    class Answer:
        def __init__(self, body):
            self.body = body

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout=None):
        seen['ua'], seen['url'] = req.get_header('User-agent'), req.full_url
        return Answer(json.dumps({'id': 1}).encode())
    asked = []
    run = lambda args: asked.append(args) or ''   # noqa: E731
    with host(repo='acme/app', user_agent='acme-deploy', project='acme-prod', secret='GH'):
        got = G._plain('POST', '/repos/acme/app/deployments', {}, 't', opener)
        G.token({}, run)
    with host(repo='acme/app', project='', secret='GH'):
        G.token({}, run)
    gcloud = [a for a in asked if a[:1] == ['gcloud']]
    eq('the host', (got, seen['ua'], seen['url'], gcloud),
       ({'id': 1}, 'acme-deploy', 'https://api.github.com/repos/acme/app/deployments',
        [['gcloud', 'secrets', 'versions', 'access', 'latest', '--secret=GH', '--project=acme-prod']]))


def test_the_log_link_never_names_an_empty_repository():
    """  the run's page: LOG_URL wins; a run id with GITHUB_REPOSITORY empty in the call's env still names the configured repository (the original file wrote https://github.com//actions/runs/7)"""
    with host(repo='acme/app'):
        got = (G.log_url({'GITHUB_RUN_ID': '7', 'GITHUB_REPOSITORY': ''}), G.log_url({'GITHUB_RUN_ID': '7', 'GITHUB_REPOSITORY': 'o/r'}),
               G.log_url({'LOG_URL': 'https://l', 'GITHUB_RUN_ID': '7'}), G.log_url({}))
    eq('the link', got, ('https://github.com/acme/app/actions/runs/7', 'https://github.com/o/r/actions/runs/7', 'https://l', ''))


def test_configure_refuses_an_unknown_name():
    """  a setting the module does not have is refused, not ignored"""
    try:
        G.configure(repository='x')
        got = 'accepted'
    except TypeError as e:
        got = str(e)
    eq('refused', got, "gh_deploy.configure: unknown setting 'repository'")


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            print(fn.__doc__.strip().splitlines()[0])
            fn()
    print(f'{len(FAILURES)} failure(s)')
    sys.exit(1 if FAILURES else 0)
