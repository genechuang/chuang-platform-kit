"""An Expo app's build artifact, the parts any app's pipeline needs (SMADPickleBot #2237, step 3a of #2161): the
content key a build is stored and found under, what an export ships by kind, whether an export is fit to release and
is this build's, whether the installed packages are the lockfile's, the phone exports of each platform joined into one
bundle, and what a build's manifest says was tested.

Each is a pure function of the folder or the values it is handed: the app folder, the release API's address, the hosts
a release must never name and the runtime file's path are the host's arguments, so nothing here names a project.
Standard library only.
"""
import hashlib
import json
import os
import shutil
import tarfile

__all__ = ['JOINED_JSON', 'KINDS', 'JoinError', 'bundle_has', 'checks_to_run', 'config_files', 'config_hash', 'expo_error',
           'join_exports', 'key', 'mb', 'pack', 'posix_metadata', 'release_check', 'runtime_versions', 'sha256', 'size_line',
           'sizes', 'stale_packages', 'stamp_check', 'tested_here']

# What an export ships, by the kind a reader of a size line cares about; anything else is 'other'.
KINDS = (('fonts', ('.ttf', '.otf', '.woff', '.woff2')), ('javascript', ('.js', '.hbc')),
         ('images', ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.svg')))
JOINED_JSON = ('metadata.json', 'assetmap.json')   # the two files every platform's export writes its own half of


class JoinError(ValueError):
    """Two platforms' exports disagree on a file that is neither one of the joined JSON files nor the same bytes."""


def config_hash(values: list) -> str:
    """The public config a bundle carries as one short hash: the values joined by '|', sha256, the first 8 hex
    digits. A host's workflow that computes the same key must join the same values in the same order."""
    return hashlib.sha256('|'.join(values).encode()).hexdigest()[:8]


def key(tree: str, values: list) -> str:
    """A build artifact's key: the app folder's git tree, its first 12, and the config hash. The content of the app and
    its public config, never the commit, so a rebase that leaves the app alone still finds its build and a changed
    config value never reuses a stale one."""
    return f'{tree[:12]}-{config_hash(values)}'


def sha256(path: str) -> str:
    """A file's sha256 as hex, read a megabyte at a time."""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def pack(src: str, dest: str) -> None:
    """The folder `src` as the gzipped tar `dest`, under its own name, at the lightest compression (the upload's time is
    the transfer's, not the packing's)."""
    with tarfile.open(dest, 'w:gz', compresslevel=1) as t:
        t.add(src, arcname=os.path.basename(src))


def release_check(folder: str, api: str, non_prod) -> list:
    """What makes an export unfit to ship, read from its JavaScript (source maps left out): a host `non_prod` (a
    compiled pattern of the hosts a release must never name) finds, or no mention of the release API `api` at all.
    Empty when it is a release build."""
    text = ''
    for root, _, names in os.walk(folder):
        for n in names:
            if n.endswith(('.js', '.hbc', '.bundle')) and not n.endswith('.map'):
                with open(os.path.join(root, n), encoding='utf-8', errors='replace') as f:
                    text += f.read()
    bad = [f'it names the non-production host {h}' for h in sorted(set(non_prod.findall(text)))]
    if api not in text:
        bad.append(f'it never names {api}')
    return bad


def stamp_check(folder: str, stamp: str) -> list:
    """What says an export is not this build's (a bundler cache kept between builds): its JavaScript must carry this
    build's stamp as a quoted string. Empty when it does."""
    for root, _, names in os.walk(folder):
        for n in names:
            if n.endswith('.js') and not n.endswith('.map'):
                with open(os.path.join(root, n), encoding='utf-8', errors='replace') as f:
                    if f'"{stamp}"' in f.read():
                        return []
    return [f"it does not carry this build's stamp {stamp} (a stale cache?)"]


def stale_packages(app: str, limit: int = 5) -> list:
    """The installed packages under `app` that differ from its package-lock.json, as "name: lock X, installed Y" (at
    most `limit`); [] when they match. A release must be built from the committed lock, not whatever an older install
    left. Optional packages, platform builds a machine never installs, and links are skipped."""
    lock = json.load(open(os.path.join(app, 'package-lock.json'), encoding='utf-8')).get('packages') or {}
    try:
        have = json.load(open(os.path.join(app, 'node_modules', '.package-lock.json'), encoding='utf-8')).get('packages') or {}
    except OSError:
        return ['node_modules is not installed']
    out = []
    for path, meta in lock.items():
        if not path or meta.get('optional') or meta.get('link'):
            continue
        got = (have.get(path) or {}).get('version')
        if got != meta.get('version'):
            out.append(f"{path.replace('node_modules/', '')}: lock {meta.get('version')}, installed {got or 'none'}")
            if len(out) >= limit:
                break
    return out


def sizes(out: str, kinds=KINDS) -> dict:
    """{kind: bytes} of what an export ships: fonts, javascript, images and other; source maps left out, since they go
    to the error tracker and never to a site or a phone."""
    got: dict = {}
    for root, _, names in os.walk(out):
        for n in names:
            if n.endswith('.map'):
                continue
            kind = next((k for k, ends in kinds if n.lower().endswith(ends)), 'other')
            got[kind] = got.get(kind, 0) + os.path.getsize(os.path.join(root, n))
    return got


def mb(n) -> str:
    """Bytes as megabytes to two places, '?' for none."""
    return '?' if n is None else f'{n / 1e6:.2f} MB'


def size_line(shipped: dict, archive: int, last) -> str:
    """'ships 5.21 MB (javascript 3.94 MB, images 0.40 MB, fonts 0.85 MB), archive 5.97 MB (last 10.40 MB)'."""
    parts = ', '.join(f'{k} {mb(v)}' for k, v in sorted(shipped.items(), key=lambda kv: -kv[1]) if v)
    return f"ships {mb(sum(shipped.values()))} ({parts}), archive {mb(archive)} (last {mb(last) if last else 'not on record'})"


def runtime_versions(path: str) -> dict:
    """An app's runtime file: {platform: {runtime, inputs, since}}, the runtime each platform's builds and updates
    carry and the native inputs it was set from."""
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def expo_error(text: str) -> str:
    """Expo's own words for a failed read: the first line naming a PluginError, or `expo config`'s error, from the
    command's output; '' when it names neither, so the caller keeps its own line."""
    lines = [ln.strip() for ln in (text or '').splitlines() if ln.strip()]
    for ln in lines:
        if 'PluginError' in ln:
            return ln[ln.index('PluginError'):][:300]
    for i, ln in enumerate(lines):
        if 'expo config' in ln.lower() and ('fail' in ln.lower() or 'error' in ln.lower()):
            return ' '.join(lines[i:i + 2])[:300]
    return ''


def config_files(config: dict) -> list:
    """Every local file an app.json's `expo` section names (a "./" string anywhere in it: icons, the splash, a
    service's config file, a plugin's image), relative to the app folder, sorted."""
    found = set()

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str) and v.startswith('./'):
            found.add(v[2:])

    walk(config.get('expo', config))
    return sorted(found)


def join_exports(parts: list, out: str) -> str:
    """The phone bundle's one folder `out` made from each platform's own export in `parts`: the first becomes `out`,
    and every later one's files are moved in beside it. A file both hold is one file when its bytes are the same (the
    images and fonts, named by their content); metadata.json's `fileMetadata` and assetmap.json are each the halves
    laid together; anything else two halves disagree on is a JoinError, never one silently winning.

    Checked 11/19/26 against one `expo export` call for both platforms: the same files, Android's bundle and every
    asset byte for byte, the asset map equal, and the iPhone bundle the same modules with the same source in another
    order."""
    shutil.move(parts[0], out)
    for part in parts[1:]:
        for folder, _, names in os.walk(part):
            for name in names:
                src = os.path.join(folder, name)
                rel = os.path.relpath(src, part)
                dest = os.path.join(out, rel)
                if not os.path.exists(dest):
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    shutil.move(src, dest)
                elif rel in JOINED_JSON:
                    with open(dest, encoding='utf-8') as f:
                        have = json.load(f)
                    with open(src, encoding='utf-8') as f:
                        more = json.load(f)
                    if rel == 'metadata.json':
                        have['fileMetadata'] = {**(have.get('fileMetadata') or {}), **(more.get('fileMetadata') or {})}
                    else:
                        have = {**have, **more}
                    with open(dest, 'w', encoding='utf-8') as f:
                        json.dump(have, f)
                elif sha256(src) != sha256(dest):
                    raise JoinError(f'the phone exports disagree on {rel.replace(os.sep, "/")}; nothing joined')
        shutil.rmtree(part, ignore_errors=True)
    return out


def bundle_has(out: str, platform: str) -> bool:
    """Whether the phone export at `out` carries `platform`'s half (its metadata.json's fileMetadata)."""
    try:
        with open(os.path.join(out, 'metadata.json'), encoding='utf-8') as f:
            return bool((json.load(f).get('fileMetadata') or {}).get(platform))
    except (OSError, ValueError, AttributeError):
        return False


def tested_here(manifest: dict) -> bool:
    """Whether a build's manifest says the typecheck and every suite passed for it and carries their results file:
    only then may a later stage skip its own (an older build, or one tested only by the suites related to its change,
    is tested again)."""
    checks_ = manifest.get('checks') or {}
    return bool(checks_.get('passed')) and checks_.get('scope') != 'related' and 'app-test-results.json' in (manifest.get('files') or {})


def checks_to_run(full: bool, wanted: list, old: dict | None) -> str | None:
    """Which checks a build runs beside its exports: 'full' for a ship whose web build is new or was tested only for a
    lower rung (a phone bundle added to such a build must not keep its related-only pass), 'related' for a new web
    build that is not a ship, None for a phone bundle added to a build already fully tested."""
    if full and ('web' in wanted or not (old and tested_here(old))):
        return 'full'
    return 'related' if 'web' in wanted else None


def posix_metadata(out: str) -> None:
    """The phone export's metadata.json with forward slashes in its asset paths. On Windows Expo writes backslashes,
    and `eas update` on a Linux runner then cannot find a single asset ("Failed to upload")."""
    meta = os.path.join(out, 'metadata.json')
    if not os.path.exists(meta):
        return
    with open(meta, encoding='utf-8') as f:
        data = json.load(f)
    for platform in (data.get('fileMetadata') or {}).values():
        platform['bundle'] = str(platform.get('bundle', '')).replace('\\', '/')
        for asset in platform.get('assets') or []:
            asset['path'] = str(asset.get('path', '')).replace('\\', '/')
    with open(meta, 'w', encoding='utf-8') as f:
        json.dump(data, f)
