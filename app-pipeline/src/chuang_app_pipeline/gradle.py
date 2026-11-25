"""A local Android build's Gradle side, the parts that stand on their own (SMADPickleBot #2310, step 4 of #2161).

A host prebuilds its Expo app's Android project and runs Gradle itself. What is the same for any app is here, each a
function of what it is handed:

- the edits made to the prebuilt project's text after prebuild, so neither platform's native inputs move: the daemon's
  memory, expo-updates' channel, the version code, the Sentry plugin's own telemetry off;
- `next_version_code`: the code after the highest a store lists;
- `args`, `last_task` and `watched_run`: Gradle's arguments, the task a log is in, and a run whose tree is killed when
  it goes silent, sits in one task or passes its bound;
- the `--profile` report read by task (`profile_tasks`, `task_kind`) and kept beside the log (`keep_profile`);
- `ccache_hits`, the comparison of two native input lists (`source_key`, `diff_sources`), and where a build's files are
  (`built`, `mapping_beside`), with the signing Gradle takes from its environment (`signing_env`).

Standard library only. Nothing here prints or names an app; a host words its own lines.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import time

__all__ = ['DURATION', 'HEADERS_KEY', 'PROFILE_ROW', 'TASK_KINDS', 'args', 'built', 'ccache_hits', 'diff_sources', 'keep_profile',
           'last_task', 'mapping_beside', 'next_version_code', 'profile_tasks', 'signing_env', 'source_key', 'task_kind',
           'watched_run', 'with_jvmargs', 'with_no_sentry_telemetry', 'with_update_channel', 'with_version_code']

HEADERS_KEY = 'expo.modules.updates.UPDATES_CONFIGURATION_REQUEST_HEADERS_KEY'


# ---- the prebuilt project's text -----------------------------------------------------------------------------------

def with_jvmargs(text: str, jvmargs: str) -> str:
    """gradle.properties with the daemon's memory set to `jvmargs`, the line replaced or added."""
    line = f'org.gradle.jvmargs={jvmargs}'
    if re.search(r'^org\.gradle\.jvmargs=.*$', text, re.M):
        return re.sub(r'^org\.gradle\.jvmargs=.*$', line, text, count=1, flags=re.M)
    return text.rstrip('\n') + '\n' + line + '\n'


def with_update_channel(manifest: str, channel: str) -> str:
    """The prebuilt AndroidManifest.xml with expo-updates' request headers naming `channel`. An EAS build adds
    `expo-channel-name` itself; a local build has none, so the update server cannot pick a channel and the app takes no
    update. A manifest that has the key is left as it is; one with no expo-updates settings is a ValueError."""
    if HEADERS_KEY in manifest:
        return manifest
    value = json.dumps({'expo-channel-name': channel}).replace('"', '&quot;')
    line = f'    <meta-data android:name="{HEADERS_KEY}" android:value="{value}"/>\n'
    at = manifest.find('<meta-data android:name="expo.modules.updates.')
    if at < 0:
        raise ValueError('the prebuilt AndroidManifest.xml has no expo-updates settings to put the channel beside')
    start = manifest.rfind('\n', 0, at) + 1
    return manifest[:start] + line + manifest[start:]


def with_version_code(gradle_text: str, code: int) -> str:
    """The prebuilt app/build.gradle with `versionCode code`; a ValueError when the file has no versionCode line."""
    if not re.search(r'^\s*versionCode\s+\d+\s*$', gradle_text, re.M):
        raise ValueError('app/build.gradle has no versionCode line')
    return re.sub(r'^(\s*versionCode\s+)\d+(\s*)$', rf'\g<1>{int(code)}\g<2>', gradle_text, count=1, flags=re.M)


def with_no_sentry_telemetry(gradle_text: str) -> str:
    """The prebuilt app/build.gradle with `telemetry = false` in its `sentry { }` block: Sentry's Android Gradle plugin
    reports every build to Sentry's own telemetry project unless told not to, and its Expo plugin has no option for it.
    A file with no sentry block, or one that already says telemetry, is left as it is."""
    if re.search(r'^\s*telemetry\s*=', gradle_text, re.M):
        return gradle_text
    block = re.search(r'^([ \t]*)sentry\s*\{[ \t]*$', gradle_text, re.M)
    if not block:
        return gradle_text
    return gradle_text[:block.end()] + f'\n{block.group(1)}    telemetry = false' + gradle_text[block.end():]


def next_version_code(tracks: list) -> int:
    """The version code after the highest a store's tracks hold ([{releases: [{versionCodes}]}], Google Play's answer),
    1 for an app with none: a code the store has seen is refused at the upload."""
    codes = [int(c) for t in tracks for r in (t.get('releases') or []) for c in (r.get('versionCodes') or [])]
    return max(codes, default=0) + 1


# ---- the run -------------------------------------------------------------------------------------------------------

def last_task(log_tail: str) -> str:
    """The last task a plain-console log's text names, '' for none."""
    tasks = re.findall(r'^> Task (\S+)', log_tail, re.M)
    return tasks[-1] if tasks else ''


def args(task: str, jvmargs: str, abi: str = '', extra=()) -> list:
    """Gradle's arguments: the task, plain output, the build cache (it keys tasks on their inputs, not the folder, so a
    fresh prebuild reuses them), its own task times (`--profile`), the daemon's memory, `extra`, and `abi` alone when
    asked (React Native's architectures)."""
    return ([task, '--console=plain', '--build-cache', '--profile', f'-Dorg.gradle.jvmargs={jvmargs}', *extra]
            + ([f'-PreactNativeArchitectures={abi}'] if abi else []))


def watched_run(command: list, cwd: str, env: dict, log: str, stale_s: int, task_max_s: int, max_s: int, kill, tell=None,
                now=time.time, sleep=time.sleep, popen=subprocess.Popen) -> tuple:
    """`command` run with its output live into `log`: (its exit code, ''), or (124, why) once it was stopped with
    `kill(pid)`: silent for `stale_s`, one task running for `task_max_s` (a task printing warnings is not a task making
    progress), or past `max_s` in all. `tell(seconds, task)` once a minute, the task '' before the first."""
    with open(log, 'w', encoding='utf-8') as f:
        p = popen(command, cwd=cwd, env=env, stdout=f, stderr=subprocess.STDOUT)
        start = seen = now()
        size, said, current, since = 0, start, '', start
        while p.poll() is None:
            sleep(5)
            t = now()
            grown = os.path.getsize(log)
            if grown != size:
                size, seen = grown, t
                with open(log, encoding='utf-8', errors='replace') as r:
                    r.seek(max(0, size - 20000))
                    running = last_task(r.read())
                if running != current:
                    current, since = running, t
            why = (f'printed nothing for {stale_s} s' if t - seen > stale_s
                   else f'sat in {current} for {int(t - since)} s' if current and t - since > task_max_s
                   else f'ran past {max_s} s' if t - start > max_s else '')
            if why:
                kill(p.pid)
                return 124, why
            if tell and t - said >= 60:
                said = t
                tell(int(t - start), current)
        return p.returncode, ''


# ---- what it took --------------------------------------------------------------------------------------------------

PROFILE_ROW = re.compile(r'<td[^>]*>(:[^<]+)</td>\s*<td class="numeric">([^<]+)</td>\s*<td>([^<]*)</td>')
DURATION = re.compile(r'^(?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?$')
TASK_KINDS = (('CMake', ('CMake', 'ExternalNative', 'NativeLibs', 'DebugSymbols')),
              ('JavaScript bundle', ('JsAndAssets',)),
              ('Kotlin and Java', ('Kotlin', 'JavaWithJavac', 'Kapt', 'Ksp')),
              ('R8 and dex', ('R8', 'minify', 'Dex', 'dex')),
              ('packaging', ('package', 'Bundle', 'sign', 'Sign', 'merge', 'Merge')))


def profile_tasks(html: str) -> list:
    """Gradle's --profile report's task rows, (task path, seconds, result), slowest first: the Task Execution table's
    rows whose result is not a project's `(total)`; a duration Gradle wrote as 1h2m3.45s, 1m3.45s or 0.123s."""
    rows = []
    for path, took, result in PROFILE_ROW.findall(html):
        m = DURATION.match(took.strip())
        if not m or result.strip() == '(total)' or not any(m.groups()):
            continue
        h, mi, s = m.groups()
        rows.append((path.strip(), int(h or 0) * 3600 + int(mi or 0) * 60 + float(s or 0), result.strip()))
    return sorted(rows, key=lambda r: -r[1])


def task_kind(path: str) -> str:
    """A task's kind by its name (the last part of its path): the first of TASK_KINDS whose words it holds, else other."""
    name = path.rsplit(':', 1)[-1]
    return next((kind for kind, words in TASK_KINDS if any(w in name for w in words)), 'other')


def keep_profile(android: str, log: str) -> str:
    """Gradle's newest --profile report under android/build/reports/profile copied beside the build's log as
    `<log less .log>-profile.html`, and one line of its task seconds by kind and its ten slowest tasks. The seconds are
    each task's own, run in parallel, so they add up to more than the wall. Never fails a build: a missing or unreadable
    report is said as such."""
    try:
        reports = glob.glob(os.path.join(android, 'build', 'reports', 'profile', 'profile-*.html'))
        if not reports:
            return 'no Gradle profile report (build/reports/profile is empty)'
        src = max(reports, key=os.path.getmtime)
        dest = (log[:-4] if log.endswith('.log') else log) + '-profile.html'
        shutil.copyfile(src, dest)
        with open(src, encoding='utf-8', errors='replace') as f:
            rows = profile_tasks(f.read())
        if not rows:
            return f'Gradle profile kept at {dest}, but no task rows were read from it'
        kinds = {}
        for path, secs, _ in rows:
            kinds[task_kind(path)] = kinds.get(task_kind(path), 0) + secs
        by_kind = ', '.join(f'{k} {v:.0f} s' for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]))
        slowest = ', '.join(f'{p} {s:.1f} s' for p, s, _ in rows[:10])
        return (f'Gradle profile, task seconds run in parallel ({len(rows)} tasks): {by_kind}; slowest: {slowest}; '
                f'kept at {dest}')
    except Exception as e:   # telemetry, never the build's verdict
        return f'Gradle profile not read ({type(e).__name__}: {str(e)[:120]})'


CCACHE_STAT = re.compile(r'^(direct_cache_hit|preprocessed_cache_hit|cache_miss)\t(\d+)$', re.M)


def ccache_hits(tsv: str) -> tuple:
    """(hits, misses) from `ccache --print-stats`' tab-separated counters."""
    seen = {k: int(v) for k, v in CCACHE_STAT.findall(tsv)}
    return seen.get('direct_cache_hit', 0) + seen.get('preprocessed_cache_hit', 0), seen.get('cache_miss', 0)


# ---- its inputs and its files --------------------------------------------------------------------------------------

def source_key(s: dict) -> str:
    """A native fingerprint source's name: its type and its path, or its id."""
    return f"{s.get('type')}:{s.get('filePath') or s.get('id') or ''}"


def diff_sources(a: list, b: list) -> list:
    """The sources two fingerprints disagree on: (key, 'added'|'removed'|'changed'), sorted."""
    ha = {source_key(s): s.get('hash') for s in a}
    hb = {source_key(s): s.get('hash') for s in b}
    out = [(k, 'removed') for k in ha if k not in hb] + [(k, 'added') for k in hb if k not in ha]
    out += [(k, 'changed') for k in ha if k in hb and ha[k] != hb[k]]
    return sorted(out)


def built(android: str, aab: bool) -> str:
    """Where a release build leaves its file: the bundle (`aab`) or the APK."""
    return os.path.join(android, 'app', 'build', 'outputs', *(('bundle', 'release', 'app-release.aab') if aab else
                                                              ('apk', 'release', 'app-release.apk')))


def mapping_beside(android: str, dest: str) -> str:
    """The build's R8 mapping (app/build/outputs/mapping/release/mapping.txt) copied beside the bundle as
    `<dest>.mapping.txt` for a store's deobfuscation upload; the line to say. A build that did not minify writes none,
    and the line says so."""
    src = os.path.join(android, 'app', 'build', 'outputs', 'mapping', 'release', 'mapping.txt')
    if not os.path.exists(src):
        return 'no R8 mapping (the build did not minify); Play gets no deobfuscation file'
    shutil.copyfile(src, dest + '.mapping.txt')
    return f'R8 mapping kept beside the bundle, {os.path.getsize(src) / 1e6:.1f} MB, for the Play upload'


def signing_env(store_file: str, store_password: str, alias: str, key_password: str) -> dict:
    """The Android Gradle plugin's injected signing (what Android Studio passes), as Gradle project properties in the
    environment, so no password is on a command line or in Gradle's log; it overrides the release build type's own
    signing config."""
    p = 'ORG_GRADLE_PROJECT_android.injected.signing.'
    return {p + 'store.file': store_file, p + 'store.password': store_password, p + 'key.alias': alias,
            p + 'key.password': key_password}
