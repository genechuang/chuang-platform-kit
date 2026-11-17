"""Google Play's Developer API (androidpublisher v3), one client for every project (0.35.0, out of a host's own
status module of 11/4/26 and the edit calls of its Android upload; the CHANGELOG names the ticket).

The app's package name is a parameter of every call, and so is the token: `publisher_token` mints one for a keyless
publisher account (its email passed in) through `google_auth.impersonate`, from the caller's own token (a desktop's
sign-in, or a function's own account, which needs Token Creator on the publisher), for the androidpublisher scope.
Whose account that is, and what its Play Console rights are, is the host's business.

Play changes an app through an **edit**: opened, changed, then committed (or validated, or deleted). Play keeps one
open edit an app, so a commit of another edit deletes this one under a read; `tracks` opens a second edit once when
Play says so (EDIT_DELETED). The calls:

- `open_edit`, `commit_edit`, `validate_edit`, `delete_edit` (best effort, never raises);
- `edit_tracks` and `edit_track` (an open edit's tracks, or one), `set_track` (a track's releases put);
- `upload_bundle` and `upload_mapping` (an .aab's bytes, and its R8 mapping for a version code);
- `tracks`: every track, read in an edit that is always deleted after, so nothing changes in Play.

And the pure readers of Play's answers: `release` (a release to put), `version_codes`, `newest` (the release with
the highest version code), `fact` (a release as {track, version, codes, status, fraction}) and `newest_on` (the
newest release of the first of several tracks that holds one). Play's API gives a release's status (draft,
inProgress, halted, completed) and nothing of Google's review; the words a person reads are the host's.

A refusal is `PlayError` with Google's status and message, never a token. Standard library, over the kit's
`google_rest.call` (guard 'none': an upload opens and commits its edits from a desktop) and `google_auth`.
"""
import json
import urllib.error
import urllib.request

from . import google_auth, google_rest

SCOPE = 'https://www.googleapis.com/auth/androidpublisher'
API_ROOT = 'https://androidpublisher.googleapis.com/androidpublisher/v3/applications'
UPLOAD_ROOT = 'https://androidpublisher.googleapis.com/upload/androidpublisher/v3/applications'
CONSOLE_URL = 'https://play.google.com/console'
TIMEOUT = 60
EDIT_DELETED = 'has been deleted'   # Play's words for an edit another edit's commit closed ("This Edit has been deleted.")
NONE = 'none'                       # a fact's status when its tracks hold no release yet
STATUSES = ('draft', 'inProgress', 'halted', 'completed')   # Play's release statuses
NOTES_MAX = 500                     # Play's limit on a release's notes in one language


class PlayError(Exception):
    """A Play or IAM answer that stops the call, its text Google's own (never a token)."""


def api(package: str) -> str:
    """The Developer API's address for the app `package` (its Android package name)."""
    return f'{API_ROOT}/{package}'


def upload_api(package: str) -> str:
    """The Developer API's upload address for the app `package` (bundles and deobfuscation files go here)."""
    return f'{UPLOAD_ROOT}/{package}'


def iam_token_url(account: str) -> str:
    """The IAM Credentials address `publisher_token` calls for `account` (google_auth.impersonate's), for a suite or
    a log line that names it."""
    return google_auth.IAM_CREDENTIALS.format(account=account, method='generateAccessToken')


def call(method, url, token=None, body=None, data=None, content_type='application/json', opener=None,
         timeout=TIMEOUT, error=PlayError):
    """One HTTP call, its JSON answer as a dict ({} for an empty one); a refusal raised as `error` (PlayError) with
    Google's status and message. A JSON call is `google_rest.call` (guard 'none'); a raw upload (`data`, the file's
    bytes, with its `content_type`) is sent here, as Play's upload endpoint takes it."""
    if data is None:
        return google_rest.call(method, url, token or '', body, opener=opener, timeout=timeout, guard='none',
                                error=error)
    headers = {'Content-Type': content_type}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
            text = r.read().decode('utf-8')
            return json.loads(text) if text.strip() else {}
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode('utf-8') or '{}').get('error', {}).get('message', '')
        except (ValueError, OSError):
            msg = ''
        raise error(f'{method} {url.split("?")[0].rsplit("/", 2)[-2:]} answered HTTP {e.code}: {msg or e.reason}')


def publisher_token(account: str, source=None, opener=None, what: str = 'publisher', error=PlayError) -> str:
    """An access token, an hour, for the publisher service account `account` (its email), minted by IAM Credentials
    (`google_auth.impersonate`) for the androidpublisher scope from `source`'s token (a callable;
    `google_auth.access_token` by default). `what` names the account in the refusal: `error` (PlayError)
    "<what> token not minted: <why>" when none is minted."""
    try:
        return google_auth.impersonate(account, SCOPE, source=source, opener=opener)
    except google_auth.TokenFailed as e:
        raise error(f'{what} token not minted: {e}')


# --- an edit ---------------------------------------------------------------------------------------------------------

def open_edit(package: str, token: str, opener=None) -> str:
    """A new edit of `package`, its id."""
    return call('POST', f'{api(package)}/edits', token, {}, opener=opener)['id']


def commit_edit(package: str, edit: str, token: str, opener=None) -> dict:
    """The edit committed: its changes are Play's now. Play's answer."""
    return call('POST', f'{api(package)}/edits/{edit}:commit', token, opener=opener)


def validate_edit(package: str, edit: str, token: str, opener=None) -> dict:
    """The edit checked as a commit would be, without committing it; a refusal is the PlayError a commit would be."""
    return call('POST', f'{api(package)}/edits/{edit}:validate', token, opener=opener)


def delete_edit(package: str, edit: str, token: str, opener=None) -> bool:
    """The edit deleted, so nothing of it stays in Play; False when Play refused (an edit already gone). Never raises
    PlayError: it is the clean-up after a call that may itself have failed."""
    try:
        call('DELETE', f'{api(package)}/edits/{edit}', token, opener=opener)
        return True
    except PlayError:
        return False


def edit_tracks(package: str, edit: str, token: str, opener=None) -> list:
    """Every track the open edit lists, each {'track', 'releases': [...]}."""
    return call('GET', f'{api(package)}/edits/{edit}/tracks', token, opener=opener).get('tracks') or []


def edit_track(package: str, edit: str, track: str, token: str, opener=None) -> dict:
    """One track of the open edit, {'track', 'releases': [...]}."""
    return call('GET', f'{api(package)}/edits/{edit}/tracks/{track}', token, opener=opener)


def set_track(package: str, edit: str, track: str, releases: list, token: str, opener=None) -> dict:
    """`track`'s releases put in the open edit (they replace the track's own); in Play once the edit is committed."""
    return call('PUT', f'{api(package)}/edits/{edit}/tracks/{track}', token, {'track': track, 'releases': releases},
                opener=opener)


def upload_bundle(package: str, edit: str, data: bytes, token: str, opener=None, timeout: float = 900) -> dict:
    """An Android App Bundle's bytes uploaded into the open edit; Play's answer, whose `versionCode` is the bundle's."""
    return call('POST', f'{upload_api(package)}/edits/{edit}/bundles?uploadType=media', token, data=data,
                content_type='application/octet-stream', opener=opener, timeout=timeout)


def upload_mapping(package: str, edit: str, code, data: bytes, token: str, opener=None, timeout: float = 300) -> dict:
    """The R8 / ProGuard mapping for version code `code` uploaded into the open edit, so Play's crash reports read."""
    return call('POST', f'{upload_api(package)}/edits/{edit}/apks/{code}/deobfuscationFiles/proguard?uploadType=media',
                token, data=data, content_type='application/octet-stream', opener=opener, timeout=timeout)


def tracks(package: str, token: str, opener=None) -> list:
    """Every track Play lists for `package`, read in an edit that is deleted after, so nothing changes in Play. Play
    keeps one open edit an app: a commit of another deletes this one under the read, so an edit Play says has been
    deleted (EDIT_DELETED) is opened again once and read; a second failure is the caller's PlayError."""
    for attempt in (1, 2):
        edit = open_edit(package, token, opener)
        try:
            return edit_tracks(package, edit, token, opener)
        except PlayError as e:
            if attempt == 1 and EDIT_DELETED in str(e):
                continue
            raise
        finally:
            delete_edit(package, edit, token, opener)


# --- Play's answers, read ----------------------------------------------------------------------------------------------

def release(code, status: str = 'draft', name: str = '', notes: str = '', language: str = 'en-US') -> dict:
    """A release to put on a track (`set_track`): version code `code`, Play's `status`, named `name` (the code when
    none), with `notes` in `language` cut to Play's limit (NOTES_MAX) when given."""
    out = {'name': name or str(code), 'versionCodes': [str(code)], 'status': status}
    if notes:
        out['releaseNotes'] = [{'language': language, 'text': notes[:NOTES_MAX]}]
    return out


def version_codes(releases: list) -> list:
    """Every version code the releases carry, as ints (anything that is not a number left out)."""
    return [int(c) for r in releases or [] for c in r.get('versionCodes') or [] if str(c).isdigit()]


def newest(releases: list):
    """The release with the highest version code (Play's newest), or None; a release with no code is skipped."""
    rows = [r for r in releases or [] if r.get('versionCodes')]
    return max(rows, key=lambda r: max(version_codes([r]), default=0)) if rows else None


def fact(track: str, rel: dict) -> dict:
    """A release as the fact a status page keeps: {track, version (its name), codes (strings), status, fraction (a
    staged rollout's userFraction, else None)}."""
    return {'track': track, 'version': rel.get('name') or '', 'codes': [str(c) for c in rel.get('versionCodes') or []],
            'status': rel.get('status') or '', 'fraction': rel.get('userFraction')}


def newest_on(listed: list, names) -> dict:
    """The newest release's fact on the first of the tracks `names` (in the order given) that holds a release, from
    `tracks`' answer; when none does, a fact with status NONE and track '' (or the one name when `names` is a single
    track)."""
    names = (names,) if isinstance(names, str) else tuple(names)
    by = {t.get('track'): t.get('releases') or [] for t in listed or []}
    for name in names:
        got = newest(by.get(name))
        if got:
            return fact(name, got)
    return fact(names[0] if len(names) == 1 else '', {'status': NONE})
