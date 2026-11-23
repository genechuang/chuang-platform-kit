"""What an Expo update channel serves a phone, checked after a release (SMADPickleBot #1891, moved here by #2236).

An update is run on a device before a release, on a lower channel (a simulator, an emulator), and the release then
publishes that same update to the channel the installed apps read. This asks the project's update server for a
channel's manifest of one platform and runtime, the request expo-updates makes, and passes when:

  - the branch is the released channel's and the runtime is the one the release's notice names;
  - the update was published with this release (its createdAt within `within_s` of the notice's published_at);
  - its launch bundle is the one the lower channel serves for that runtime, the files the device ran (a fresh upload
    after a refused republish must still be the same bytes); when the lower channel has since moved to a newer build,
    that is said and not judged;
  - the bundle downloads with the manifest's own asset headers and its sha256 is the manifest's hash.

The host passes its update server's address (its app.json's `updates.url`), the release's notice ({sha, runtime,
published_at, label}), its channels' names and how a commit's app tree is read; `check` answers one verdict, a dict
the host stores with its own. Standard library; the transfers are curl's, since Expo's CDN refuses Python's own client
(11/19/26: 403 to urllib, 200 to curl with the same headers).
"""
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone

__all__ = ['CLIENT', 'PUBLISHED_WITHIN_S', 'bundle', 'check', 'judge', 'line', 'manifest', 'parse', 'same_tree']

PUBLISHED_WITHIN_S = 120   # the manifest's createdAt against the notice's published_at (measured 0.4 s apart)
CLIENT = 'okhttp/4.12.0'   # a phone's client name; the server answers any, the CDN refuses Python's
FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def _curl(args: list, run=subprocess.run, timeout: int = 60):
    curl = shutil.which('curl') or 'curl'
    return run([curl, '-sS', '-m', str(timeout), '-A', CLIENT, *args], capture_output=True, timeout=timeout + 10,
               creationflags=FLAGS)


def parse(body: bytes) -> dict:
    """The server's multipart answer as {'manifest': {...}, 'headers': {asset key: {header: value}}}: the manifest
    part, and the extensions part's assetRequestHeaders, which each asset's download must carry. ValueError when the
    answer holds no manifest (a directive, an error page, an empty channel)."""
    parts = {}
    for chunk in re.split(rb'\r?\n?--+[-A-Za-z0-9_]*ExpoManifestBoundary[-A-Za-z0-9_]*(?:--)?\r?\n?', body):
        name = re.search(rb'name="([a-z]+)"', chunk[:300])
        cut = chunk.find(b'\r\n\r\n')
        if name and cut > 0:
            parts[name.group(1).decode()] = chunk[cut + 4:].strip()
    try:
        manifest = json.loads(parts['manifest'])
    except (KeyError, ValueError):
        raise ValueError('the channel answered no manifest') from None
    try:
        headers = json.loads(parts.get('extensions') or b'{}').get('assetRequestHeaders') or {}
    except ValueError:
        headers = {}
    return {'manifest': manifest, 'headers': headers}


def manifest(platform: str, runtime: str, channel: str, url: str, run=subprocess.run) -> dict:
    """What `channel` serves `platform` at `runtime` from the update server at `url`, asked as expo-updates asks:
    parse()'s answer. ValueError when the server refuses or answers no manifest."""
    got = _curl(['-H', 'accept: multipart/mixed,application/expo+json,application/json', '-H', 'expo-protocol-version: 1',
                 '-H', 'expo-api-version: 1', '-H', f'expo-platform: {platform}', '-H', f'expo-runtime-version: {runtime}',
                 '-H', f'expo-channel-name: {channel}', '-H', 'expo-updates-environment: BARE',
                 '-H', 'eas-client-id: 00000000-0000-4000-8000-000000000000', url], run, 30)
    if got.returncode != 0:
        raise ValueError(f"the update server could not be reached ({(got.stderr or b'').decode(errors='replace').strip()[:120]})")
    return parse(got.stdout)


def _instant(text: str):
    try:
        at = datetime.fromisoformat(str(text or '').replace('Z', '+00:00'))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)   # utc-ok: a stamp with no zone is the server's UTC


def judge(note: dict, prod: dict, dev: dict = None, channel: str = 'production', lower: str = 'dev',
          within_s: int = PUBLISHED_WITHIN_S) -> tuple:
    """(failures, notes) for the released channel's manifest `prod` against the release's notice `note` and, when
    given, the lower channel's manifest `dev` (the update the device ran). A failure is a reason the delivery is wrong;
    a note is said and not judged. `channel` and `lower` are the two channels' names, for the branch check and the
    words."""
    fails, notes = [], []
    meta = prod.get('metadata') or {}
    if meta.get('branchName') != channel:
        fails.append(f"the channel serves branch {meta.get('branchName') or 'unnamed'}, not {channel}")
    if str(prod.get('runtimeVersion') or '') != str(note.get('runtime') or ''):
        fails.append(f"the update's runtime is {str(prod.get('runtimeVersion'))[:10]}, the release's is {str(note.get('runtime'))[:10]}")
    made, published = _instant(prod.get('createdAt')), _instant(note.get('published_at'))
    if not made or not published:
        fails.append("the update's or the notice's publish time could not be read")
    elif abs((made - published).total_seconds()) > within_s:
        fails.append(f"{channel} serves an update published {prod.get('createdAt')}, not this release's "
                     f"({note.get('published_at')}): {round((made - published).total_seconds())} s apart")
    if dev is not None:
        dev_made = _instant(dev.get('createdAt'))
        same = (dev.get('launchAsset') or {}).get('hash') == (prod.get('launchAsset') or {}).get('hash')
        if same:
            notes.append(f'the bundle is the one the {lower} channel serves, the files the device ran')
        elif dev_made and made and dev_made > made:
            notes.append(f'the {lower} channel has moved on to a newer build, so the bundle is not compared with the one the device ran')
        else:
            fails.append(f"{channel}'s bundle is not the one the {lower} channel serves: not the files the device ran")
    return fails, notes


def bundle(prod: dict, headers: dict, run=subprocess.run, work: str = None) -> str:
    """'' when the launch bundle downloads with its own asset headers and its sha256 is the manifest's hash; else why
    not, in one line."""
    asset = prod.get('launchAsset') or {}
    if not asset.get('url') or not asset.get('hash'):
        return 'the manifest names no launch bundle'
    folder = work or tempfile.mkdtemp(prefix='channel-check-')
    path = os.path.join(folder, 'bundle.bin')
    try:
        args = []
        for name, value in (headers.get(asset.get('key')) or {}).items():
            args += ['-H', f'{name}: {value}']
        got = _curl([*args, '-o', path, '-w', '%{http_code}', asset['url']], run, 90)
        code = (got.stdout or b'').decode(errors='replace').strip()
        if got.returncode != 0 or code != '200':
            return f'the bundle did not download (HTTP {code or "no answer"})'
        digest = hashlib.sha256()
        with open(path, 'rb') as f:
            for block in iter(lambda: f.read(1 << 20), b''):
                digest.update(block)
        mine = base64.urlsafe_b64encode(digest.digest()).decode().rstrip('=')
        return '' if mine == asset['hash'] else "the downloaded bundle's sha256 is not the manifest's hash"
    finally:
        if work is None:
            shutil.rmtree(folder, ignore_errors=True)


def same_tree(sha: str, named: str, tree) -> bool:
    """Whether the notice's commit `named` holds the same app tree as the release `sha`, by the host's own reader
    `tree(rev)` (the app folder's tree id, '' when unread): a rebuild of one app tree reuses its artifact, so a release
    publishes the update built at an earlier commit, whose notice names that commit. Both read and equal; anything
    unread is False."""
    mine, theirs = tree(sha), tree(named)
    return bool(mine) and mine == theirs


def check(sha: str, platform: str, note: dict, url: str, run=subprocess.run, same=None, channel: str = 'production',
          lower: str = 'dev', by: str = 'expo_channel') -> dict:
    """One platform's verdict for the release `sha`: {sha, platform, passed, checked (False when the release published
    no update for it), detail [lines], update, group, published_at, label, seconds, at, by}. The release's update is
    the notice's when the notice names `sha`, or, with `same(sha, named)` given, a commit holding the same app tree.
    `note` is the release's notice for the platform ({} or None when it could not be read)."""
    began = time.monotonic()
    out = {'sha': sha, 'platform': platform, 'passed': False, 'checked': True, 'detail': [],
           'at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'by': by}   # utc-ok: an instant, never a day

    def done(**more):
        return {**out, **more, 'seconds': round(time.monotonic() - began, 1)}
    named = str((note or {}).get('sha') or '')
    if not note or not named:
        return done(checked=False, passed=True, detail=[f"no {platform} update was published for {sha[:7]} (no notice); nothing to check"])
    if not str(sha).startswith(named) and not (same and same(sha, named)):
        return done(checked=False, passed=True,
                    detail=[f"no {platform} update was published for {sha[:7]} (the notice names {named}, whose app tree is "
                            f"not this release's); nothing to check"])
    try:
        prod = manifest(platform, note['runtime'], channel, url, run)
    except ValueError as e:
        return done(detail=[str(e)])
    try:
        dev = manifest(platform, note['runtime'], lower, url, run)['manifest']
    except ValueError:
        dev = None
    fails, notes = judge(note, prod['manifest'], dev, channel, lower)
    if dev is None:
        notes.append(f'the {lower} channel could not be read, so the bundle is not compared with the one the device ran')
    if not fails:
        why = bundle(prod['manifest'], prod['headers'], run)
        if why:
            fails.append(why)
        else:
            notes.append('the bundle downloads and its sha256 is the manifest\'s')
    meta = prod['manifest'].get('metadata') or {}
    return done(passed=not fails, detail=fails or notes, update=prod['manifest'].get('id'), group=meta.get('updateGroup'),
                published_at=prod['manifest'].get('createdAt'), label=note.get('label'))


def line(v: dict, channel: str = 'production') -> str:
    """One verdict in one line, an ERROR when it failed."""
    name = {'ios': 'iPhone', 'android': 'Android'}.get(v['platform'], v['platform'])
    if not v.get('checked'):
        return f"channel check: {name}: {v['detail'][0]}"
    if v['passed']:
        return f"channel check: {name}: {channel} serves {v.get('label') or v['sha'][:7]} ({v['seconds']} s): {'; '.join(v['detail'])}"
    return f"ERROR: channel check: {name} update of {v['sha'][:7]}: {'; '.join(v['detail'])}"
