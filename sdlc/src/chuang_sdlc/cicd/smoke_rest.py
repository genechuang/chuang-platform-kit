#!/usr/bin/env python3
"""The backend smoke's reads in one process over REST, not a gcloud start per call (#1128, the CO #1124 release profile,
11/7/26: the production backend smoke took 35.9 s against CO #1091's 22.5 s, every component about 10 s slower, and the
time was the desktop's, before any request: each function smoke started four or five gcloud processes at once and a
token mint, so 15 side by side were about 60 gcloud starts, which take 15.6 s of wall time on this desktop where one
takes 2.1 s).

    python -m chuang_sdlc.cicd.smoke_rest reads DIR FUNCTION PROJECT REGION KIND [--revision R] [--bus] [--id-token]

In SMADPickleBot, the first host, pipeline/shared/smoke-test.sh runs it first, on every pipeline (the runner's action,
Cloud Build's deploy step, the dev rung of deploy-function.sh and the desktop's smoke, pipeline/desktop/desktop-smoke.py),
and it writes into DIR the same files the script's gcloud reads write, each beside its `.rc`, so the checks after it
read one shape whichever way the values came:

    one         a function's "STATE<tab>URI", or a Cloud Run service's URL (KIND run); a function that was a Cloud Run
                service from its first deploy is rc 1 with Google's NOT_FOUND, its URL in `svc_url`
    svc_ready   the service's Ready condition (a function whose record is not ACTIVE is judged by it, #790)
    two         "LATEST_CREATED<tab>LATEST_READY" revision names
    cond        --revision: that revision's Ready condition (the dev rung)
    endpoint    --bus: bot-reactions' push endpoint ('' while the desktop pulls it, #575)
    token       --bus: the access token the vote-writer's receipt is read with
    idtoken     --id-token: an identity token whose audience is the function's own URL; `idtoken.why` when none

The reads go side by side: the Cloud Functions v2 API for the function, the Cloud Run Admin API's v1 (Knative) surface
for the service and the revision (the shape gcloud's `run ... describe` formats read), and Pub/Sub for the
subscription. The identity token is IAM Credentials' generateIdToken as SMOKE_IMPERSONATE (the desktop's deploy
account) or the service account whose key is under GOOGLE_APPLICATION_CREDENTIALS, else the metadata server's own
account (Cloud Build: its metadata identity endpoint answers nothing, so the account mints its own, as smoke-test.sh's
id_token_self does, on its roles/iam.serviceAccountOpenIdTokenCreator on itself, infra/terraform/iam.tf), the
metadata identity endpoint last. The access token is CI_ACCESS_TOKEN (the desktop's smoke mints one for every component, a runner's
claim step one for the job), else the kit's token reader. All of it through chuang-platform-kit (google_auth,
google_rest), which the host's pipeline/shared/kit_loader.py puts on the path where the step has none.

Exit 0 once every file is written, with one line saying what was read and in how long. A refused read (no kit, no
token, any answer but the function's 404) writes nothing and exits 3 with Google's reason on its last line, and the
script reads with gcloud as before, saying so. No token is ever printed.
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)   # kit_loader beside this file, run as a script or imported by the desktop's smoke

FUNCTIONS_API = 'https://cloudfunctions.googleapis.com/v2'
PUBSUB_API = 'https://pubsub.googleapis.com/v1'
BUS_SUBSCRIPTION = 'bot-reactions'
KEY_ENV = 'GOOGLE_APPLICATION_CREDENTIALS'
TIMEOUT = 15        # seconds a read may take, as gcloud's own describe would wait about
ID_TIMEOUT = 5      # seconds the identity token's call may take
REFUSED = 3         # the exit smoke-test.sh reads as "read with gcloud"


class Refused(RuntimeError):
    """A REST read this process could not make: the kit missing, no token, or Google's refusal (its call and message).
    Never carries a token."""


def kit():
    """(google_auth, google_rest) of chuang-platform-kit. Refused when it is not importable: the host's shim puts the
    pinned kit on the path first where the step has none (SMADPickleBot's pipeline/shared/kit_loader.py `ensure()`
    before this module's main)."""
    try:
        from chuang_platform_kit import google_auth, google_rest
    except ImportError as e:
        raise Refused(f'chuang-platform-kit is not importable: {e}') from None
    return google_auth, google_rest


def access_token(env=None) -> str:
    """An access token for the reads: the kit's reader (CI_ACCESS_TOKEN first, then a key, the metadata server,
    gcloud); '' when none answers. Refused when the kit cannot be had."""
    google_auth, _rest = kit()
    return google_auth.access_token(env)


def run_v1(project: str, region: str, kind: str, name: str) -> str:
    """The Cloud Run Admin API's v1 (Knative) address of a service or revision (`kind` 'services' or 'revisions'): the
    surface gcloud's `run services describe` reads, so `status.url`, `status.latestCreatedRevisionName`,
    `status.latestReadyRevisionName`, `status.conditions` and `status.traffic` are the fields its formats name."""
    return f'https://{region}-run.googleapis.com/apis/serving.knative.dev/v1/namespaces/{project}/{kind}/{name}'


def ready(status: dict) -> str:
    """The Ready condition's status ('True', 'False', 'Unknown') of a v1 service or revision's `status`; the first
    condition's when none is named Ready (gcloud's `status.conditions[0]`), '' with none."""
    conds = (status or {}).get('conditions') or []
    named = next((c for c in conds if str(c.get('type') or '').lower() == 'ready'), None) or (conds[0] if conds else {})
    return str(named.get('status') or '')


def service(project: str, region: str, name: str, token: str, opener=None) -> dict:
    """One Cloud Run service as the v1 API gives it (its `status` with `traffic`, which the desktop's dev-rung read
    takes its dev tag from). Refused on Google's refusal or no answer."""
    _auth, rest = kit()
    try:
        return rest.call('GET', run_v1(project, region, 'services', name), token, opener=opener, timeout=TIMEOUT)
    except rest.GoogleError as e:
        raise Refused(str(e)) from None


def key_account(env=None) -> str:
    """The service account whose key is under GOOGLE_APPLICATION_CREDENTIALS (a file's path or the key's JSON itself),
    the account a runner mints its own identity token as; '' for anything else (no variable, a user's or an external
    account's credential)."""
    env = os.environ if env is None else env
    key = (env.get(KEY_ENV) or '').strip()
    try:
        if key.startswith('{'):
            doc = json.loads(key)
        elif key and os.path.isfile(key):
            with open(key, encoding='utf-8') as f:
                doc = json.load(f)
        else:
            return ''
    except (OSError, ValueError):
        return ''
    return str(doc.get('client_email') or '') if isinstance(doc, dict) and doc.get('type') == 'service_account' else ''


METADATA_EMAIL = 'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email'
EMAIL_TIMEOUT = 2   # seconds the metadata server may take to name its account (it answers in milliseconds on Google)


def metadata_account(opener=None) -> str:
    """The metadata server's own service account (a Cloud Build step's github-deploy), the account it mints its own
    identity token as when the metadata identity endpoint answers nothing; '' off Google Cloud, on any failure, or for
    an answer that is not a service account's email. Never raises."""
    import urllib.request
    req = urllib.request.Request(METADATA_EMAIL, headers={'Metadata-Flavor': 'Google'})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=EMAIL_TIMEOUT) as r:
            email = r.read().decode('utf-8').strip()
    except Exception:   # noqa: BLE001 - not on Google Cloud, a timeout, a refusal: no account
        return ''
    return email if email.endswith('.gserviceaccount.com') else ''


def mint_id_token(url: str, token: str, impersonate: str = '', env=None, opener=None) -> tuple:
    """(identity token for `url`, '' or why there is none). As SMOKE_IMPERSONATE's account or the key's (a runner's)
    through IAM Credentials' generateIdToken; with neither, as the metadata server's own account the same way (Cloud
    Build, the REST twin of smoke-test.sh's id_token_self), then the metadata identity endpoint."""
    google_auth, _rest = kit()
    if not url:
        return '', 'no URL to mint it for'
    account = impersonate or key_account(env)
    tried = []
    if not account:
        account = metadata_account(opener)
        if not account:
            tried.append('the metadata server named no account')
    if account:
        minted = google_auth.id_token(url, impersonate_account=account, source=lambda: token, opener=opener,
                                      timeout=ID_TIMEOUT)
        if minted:
            return minted, ''
        tried.append(f'IAM Credentials gave none as {account}')
    if not (impersonate or key_account(env)):
        minted = google_auth.id_token(url, opener=opener, timeout=ID_TIMEOUT)
        if minted:
            return minted, ''
        tried.append('the metadata server gave none')
    return '', f"{'; '.join(tried)} for {url}"


def reads(fn: str, project: str, region: str, kind: str = 'function', revision: str = '', bus: bool = False,
          id_token: bool = False, token: str = '', impersonate: str = '', env=None, opener=None) -> dict:
    """{file name: (text, rc)} of the smoke's reads (module docstring), made side by side on one token. Refused when no
    token answers or any read is refused, but a function's 404, which is a service that was never a Cloud Function."""
    env = os.environ if env is None else env
    google_auth, rest = kit()
    token = token or google_auth.access_token(env)
    if not token:
        raise Refused('no Google token (CI_ACCESS_TOKEN, a key, the metadata server and gcloud gave none)')
    urls = {'service': run_v1(project, region, 'services', fn)}
    if kind != 'run':
        urls['function'] = f'{FUNCTIONS_API}/projects/{project}/locations/{region}/functions/{fn}'
    if revision:
        urls['revision'] = run_v1(project, region, 'revisions', revision)
    if bus:
        urls['subscription'] = f'{PUBSUB_API}/projects/{project}/subscriptions/{BUS_SUBSCRIPTION}'

    def get(url):
        try:
            return rest.call('GET', url, token, opener=opener, timeout=TIMEOUT)
        except rest.GoogleError as e:
            return e
    with ThreadPoolExecutor(max_workers=len(urls)) as pool:
        got = dict(zip(urls, pool.map(get, urls.values())))
    for name, answer in got.items():
        if isinstance(answer, Exception) and not (name == 'function' and getattr(answer, 'status', 0) == 404):
            raise Refused(str(answer))
    svc = got['service'].get('status') or {}
    url = str(svc.get('url') or '')
    files = {}
    if kind == 'run':
        files['one'] = (url, 0)
    elif isinstance(got['function'], Exception):
        files['one'] = (f'function {fn} was not found (NOT_FOUND, status=[404])', 1)
        files['svc_url'] = (url, 0)
    else:
        uri = str((got['function'].get('serviceConfig') or {}).get('uri') or '')
        files['one'] = (f"{got['function'].get('state', '')}\t{uri}", 0)
        url = uri
    if kind != 'run':
        files['svc_ready'] = (ready(svc), 0)
    files['two'] = (f"{svc.get('latestCreatedRevisionName') or ''}\t{svc.get('latestReadyRevisionName') or ''}", 0)
    if revision:
        files['cond'] = (ready(got['revision'].get('status') or {}), 0)
    if bus:
        files['endpoint'] = (str((got['subscription'].get('pushConfig') or {}).get('pushEndpoint') or ''), 0)
        files['token'] = (token, 0)
    if id_token:
        minted, why = mint_id_token(url, token, impersonate, env, opener)
        if minted:
            files['idtoken'] = (minted, 0)
        else:
            files['idtoken.why'] = (why, 0)
    return files


def write(folder: str, files: dict) -> None:
    """Each file and its `.rc` into `folder`, LF-ended (a Windows interpreter's CRLF would leave a '\\r' in the
    script's `$(...)`)."""
    for name, (text, rc) in files.items():
        for path, body in ((os.path.join(folder, name), text), (os.path.join(folder, name + '.rc'), str(rc))):
            with open(path, 'w', encoding='utf-8', newline='\n') as f:
                f.write(f'{body}\n')


SAID = {'one': 'the function', 'two': 'the service', 'cond': 'the revision', 'endpoint': 'the bus subscription',
        'idtoken': 'an identity token'}


def main(argv=None, out=print) -> int:
    """`reads DIR FUNCTION PROJECT REGION KIND [--revision R] [--bus] [--id-token]`: the files written and 0, or 3 with
    the refusal's reason on the last line and nothing written."""
    p = argparse.ArgumentParser(prog='smoke_rest.py')
    sub = p.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('reads')
    for name in ('dir', 'function', 'project', 'region', 'kind'):
        r.add_argument(name)
    r.add_argument('--revision', default='')
    r.add_argument('--bus', action='store_true')
    r.add_argument('--id-token', action='store_true')
    a = p.parse_args(argv)
    t0 = time.monotonic()
    try:
        files = reads(a.function, a.project, a.region, a.kind, a.revision, a.bus, a.id_token,
                      impersonate=os.environ.get('SMOKE_IMPERSONATE', ''))
    except Refused as e:
        out(str(e)[:300])
        return REFUSED
    except Exception as e:   # noqa: BLE001 - any surprise is a refusal: the script reads with gcloud
        out(f'{type(e).__name__}: {str(e)[:240]}')
        return REFUSED
    write(a.dir, files)
    what = [SAID[n] for n in ('one', 'two', 'cond', 'endpoint', 'idtoken') if n in files]
    if a.kind == 'run':
        what[0] = 'the service'
        what = list(dict.fromkeys(what))
    out(f"smoke: read over REST in {time.monotonic() - t0:.1f}s, one process ({', '.join(what)})")
    return 0


if __name__ == '__main__':
    sys.exit(main())
