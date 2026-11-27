"""A GitHub App's installation tokens: what a pipeline writes to GitHub with, so its work shows as the App, never as a
person (0.56.0; SMAD PickleBot #1280, Gene 11/9/26: "things wrote by sessions ... get confused as comments by me").

GitHub's flow, as its docs give it: the App signs an RS256 JWT with its private key (issued 60 s back for clock drift,
9 minutes long, 10 at most), looks up the installation on an account (`GET /orgs/{owner}/installation`, else
`/users/{owner}/installation`), and trades the JWT for an installation token (`POST
/app/installations/{id}/access_tokens`), good for one hour. Writes with it are attributed to `<app-slug>[bot]`, and
the installation has its own rate limit, which the kit's throttle keeps in its own lane (github.lane_of).

    from chuang_platform_kit import github_app
    tok = github_app.token('SMAD-PickleBot')     # '' when this process is given no App

The App is the process's own configuration (twelve-factor III): `GH_APP_ID` (GitHub refuses Variable names starting
GITHUB_) and `GITHUB_APP_PRIVATE_KEY` (the PEM, mounted from the secret manager) in the environment, and
`GH_APP_OWNER`, the account the installation is on, as token()'s default. A desktop session has none of them, so it
keeps its person's own sign-in. A token is kept per owner until 5 minutes before it ends. PyJWT with cryptography signs
the JWT (`pip install chuang-platform-kit[github-app]`), imported only when a token is minted. Nothing here prints or
logs a key or a token.
"""
import calendar
import os
import threading
import time

from . import github

ID_ENV, KEY_ENV, OWNER_ENV = 'GH_APP_ID', 'GITHUB_APP_PRIVATE_KEY', 'GH_APP_OWNER'
JWT_SECONDS = 540           # 9 minutes: GitHub takes 10 at most
EARLY = 300                 # a kept token is minted again 5 minutes before it ends
_lock = threading.Lock()
_kept = {}                  # owner -> (token, epoch seconds it ends)
_now = time.time            # the clock, a seam for the suite


def configured(env=None) -> bool:
    """True when this process is given an App: its id and its private key in the environment."""
    env = os.environ if env is None else env
    return bool((env.get(ID_ENV) or '').strip() and (env.get(KEY_ENV) or '').strip())


def app_jwt(pem: str, app_id: str, now=None) -> str:
    """The App's RS256 JWT: issued 60 s back, JWT_SECONDS long, its issuer the App's id."""
    import jwt   # PyJWT with cryptography (the github-app extra)
    at = int(now if now is not None else _now())
    return jwt.encode({'iat': at - 60, 'exp': at + JWT_SECONDS, 'iss': str(app_id)}, pem, algorithm='RS256')


def installation_id(app_token: str, owner: str, opener=None) -> int:
    """The App's installation on `owner`, an organization or a user; GitHubError 404 when it is installed on neither."""
    try:
        return int(github.get(app_token, f'/orgs/{owner}/installation', opener=opener)['id'])
    except github.GitHubError as e:
        if e.status != 404:
            raise
    return int(github.get(app_token, f'/users/{owner}/installation', opener=opener)['id'])


def mint(owner: str, app_id: str, pem: str, opener=None, now=None) -> tuple:
    """(token, epoch seconds it ends) for the App's installation on `owner`, minted now."""
    at = now if now is not None else _now()
    signed = app_jwt(pem, app_id, at)
    inst = installation_id(signed, owner, opener)
    _, got = github.request(signed, 'POST', f'/app/installations/{inst}/access_tokens', {}, opener)
    ends = at + 3600
    try:   # GitHub's own end, "2026-11-27T14:52:00Z"; an hour from now when it cannot be read
        ends = calendar.timegm(time.strptime(got['expires_at'], '%Y-%m-%dT%H:%M:%SZ'))
    except (KeyError, TypeError, ValueError):
        pass
    return got['token'], ends


def token(owner: str = '', env=None, opener=None) -> str:
    """The App's installation token on `owner` (GH_APP_OWNER by default), kept until EARLY seconds before it ends; ''
    when the process is given no App or no owner. A refused mint raises GitHubError: a misconfigured App is loud."""
    env = os.environ if env is None else env
    owner = owner or (env.get(OWNER_ENV) or '').strip()
    if not owner or not configured(env):
        return ''
    now = _now()
    with _lock:
        kept = _kept.get(owner)
        if kept and kept[1] - EARLY > now:
            return kept[0]
    pem = env[KEY_ENV].strip().replace('\\n', '\n')   # a key pasted on one line keeps its line breaks
    tok, ends = mint(owner, env[ID_ENV].strip(), pem, opener, now)
    with _lock:
        _kept[owner] = (tok, ends)
    return tok


def forget() -> None:
    """Drop every kept token (a suite's seam, and a process that rotated the key)."""
    with _lock:
        _kept.clear()
