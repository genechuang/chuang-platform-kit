"""An Expo app's native runtime, the parts of its guard that stand on their own (SMADPickleBot #2244, step 3b of #2161).

An installed store build takes only the over-the-air updates made for its own native runtime, so a pipeline must know,
before it publishes an update, whether the commit's native inputs are the ones its runtime was set from and whether
that runtime is the one the installed store build runs. A host's guard is mostly its own: how it reads a fingerprint,
where it runs Expo, what it tells its people to do. What is the same for any app is here, each a function of the
values it is handed, with no command run and no word printed:

- `guard_verdict`: the guard's decision alone;
- `install_stale`: whether a checkout's installed packages can be trusted for a fingerprint;
- `lock_hash` and `installed`: whether a folder's packages are the ones its lockfile names;
- `memo_get` and `memo_put`: a passing answer kept for a while under a key;
- `read_builds`: a host's read of its store builds, tried again on a failure;
- `record_inputs`: the row a runtime file keeps for a platform.

Standard library only.
"""
import hashlib
import json
import os
import time

__all__ = ['ReadFailed', 'guard_verdict', 'install_stale', 'installed', 'lock_hash', 'memo_get', 'memo_put', 'read_builds',
           'record_inputs']


def guard_verdict(inputs_now: str, recorded: dict, installed_runtime, allow: bool = False) -> str:
    """The guard's decision for one platform, with no I/O: `inputs_now` is the commit's native inputs, `recorded` the
    runtime file's row for the platform ({runtime, inputs}), `installed_runtime` the runtime the installed store build
    runs, `allow` the host's explicit override.

    - 'inputs_moved': the native inputs are not the ones the runtime was set from. Refused whatever `allow` says: the
      runtime must be set again (or the change undone) before anything is built.
    - 'ok': the inputs are unchanged and the commit's runtime is the installed store build's, so an update reaches it.
    - 'runtime_differs': the commit's runtime is not the installed store build's, and nobody said yes.
    - 'allowed': the same, with `allow`: the host goes on, having said so loudly.

    The host words each verdict and decides what an unread store answer means before it asks."""
    if inputs_now != recorded.get('inputs'):
        return 'inputs_moved'
    if installed_runtime and recorded.get('runtime') == installed_runtime:   # two unknowns are never a match
        return 'ok'
    return 'allowed' if allow else 'runtime_differs'


def install_stale(app: str, label: str = 'app'):
    """Why the packages installed under `app` cannot be trusted for a fingerprint, or None: node_modules missing, no npm
    install record in it (`.package-lock.json`), that record older than package-lock.json, or a package.json dependency
    not in node_modules. A fingerprint read from such a checkout is not the commit's. `label` is how the folder is
    named in the line (the path a person would type)."""
    lock = os.path.join(app, 'package-lock.json')
    marker = os.path.join(app, 'node_modules', '.package-lock.json')
    if not os.path.isdir(os.path.join(app, 'node_modules')):
        return f'{label}/node_modules is not installed: run npm ci in {label}/ first'
    if not os.path.isfile(marker):
        return f'{label}/node_modules has no npm install record (.package-lock.json): run npm ci in {label}/ first'
    if os.path.isfile(lock) and os.path.getmtime(marker) < os.path.getmtime(lock):
        return f'{label}/node_modules is older than {label}/package-lock.json: run npm ci in {label}/ first'
    try:
        with open(os.path.join(app, 'package.json'), encoding='utf-8') as f:
            pkg = json.load(f)
    except (OSError, ValueError):
        return None
    missing = [name for name in {**pkg.get('dependencies', {}), **pkg.get('devDependencies', {})}
               if not os.path.isfile(os.path.join(app, 'node_modules', *name.split('/'), 'package.json'))]
    if missing:
        return (f"{label}/node_modules lacks {', '.join(sorted(missing)[:3])}{' and more' if len(missing) > 3 else ''}: "
                f"run npm ci in {label}/ first")
    return None


def lock_hash(app: str) -> str:
    """sha256 of `app`/package-lock.json, '' when there is none."""
    try:
        with open(os.path.join(app, 'package-lock.json'), 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ''


def installed(app: str, mark: str) -> bool:
    """Whether `app`'s packages are the ones its package-lock.json names: node_modules there, and the file `mark`
    holding the lock's hash, as the install that made them left it."""
    digest = lock_hash(app)
    try:
        with open(mark, encoding='utf-8') as f:
            kept = f.read().strip()
    except OSError:
        return False
    return bool(digest) and kept == digest and os.path.isdir(os.path.join(app, 'node_modules'))


def memo_get(path: str, key: str, seconds: float, now: float = None):
    """The answer kept for `key` in the JSON file `path` while it is under `seconds` old ({..., at}), else None; a
    missing or unreadable file is None."""
    try:
        hit = json.load(open(path, encoding='utf-8')).get(key)
    except (OSError, ValueError, AttributeError):
        return None
    if not hit or (now or time.time()) - float(hit.get('at') or 0) > seconds:
        return None
    return hit


def memo_put(path: str, key: str, answer: dict, seconds: float, now: float = None) -> None:
    """Keep `answer` under `key` in `path` with the time it was kept, dropping the entries past `seconds`; a failed
    write only costs the next asker its own read."""
    now = now or time.time()
    try:
        kept = json.load(open(path, encoding='utf-8'))
    except (OSError, ValueError):
        kept = {}
    kept = {k: v for k, v in kept.items() if isinstance(v, dict) and now - float(v.get('at') or 0) <= seconds}
    kept[key] = {**answer, 'at': now}
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(kept, f)
    except OSError:
        pass


class ReadFailed(Exception):
    """A host's read of its store builds failed on every try: the message is the last try's cause."""


def read_builds(ask, tries: int, pauses, need=None, sleep=time.sleep, on_retry=None) -> list:
    """A host's store builds, read with `ask()` (the text of a command that prints a JSON list, as `eas build:list
    --json` does, after any preamble) and tried `tries` times: on a command that exits (SystemExit), an answer that is
    not a list, or, with `need`, an answer holding no build it accepts. The builds `need` accepts when given, else
    all. Between tries it waits `pauses`' seconds (the last one repeated) after telling `on_retry(attempt, why,
    pause)`, the host's own line. ReadFailed with the last cause after the last try."""
    for attempt in range(1, tries + 1):
        try:
            out = ask()
            builds = json.loads(out[out.index('['):])
            if not isinstance(builds, list):
                raise ValueError('not a list of builds')
            if need is not None:
                builds = [b for b in builds if need(b)]
                if not builds:
                    raise IndexError('no build of the kind asked in the answer')
            return builds
        except (SystemExit, ValueError, IndexError, KeyError, AttributeError) as e:
            why = str(e)[-200:] if isinstance(e, SystemExit) else f'{type(e).__name__}: {str(e)[:120]}'
            if attempt == tries:
                raise ReadFailed(why) from None
            pause = pauses[min(attempt, len(pauses)) - 1]
            if on_retry:
                on_retry(attempt, why, pause)
            sleep(pause)
    return []


def record_inputs(data: dict, platform: str, inputs: str, keep: bool = False, since: str = '') -> tuple:
    """Record `inputs` as `platform`'s native inputs in a runtime file's `data` ({platform: {runtime, inputs, since}}),
    in place: (the runtime before, the row now). Unless `keep`, the inputs become the runtime too and `since` its
    stamp; with `keep` the runtime stays and only the inputs are re-recorded. A runtime that moved is the host's to
    yell about: every installed build of the platform stops taking updates until a store build carries it."""
    row = data.setdefault(platform, {})
    before = row.get('runtime')
    row['inputs'] = inputs
    if not keep:
        row['runtime'] = inputs
        row['since'] = since
    return before, row
