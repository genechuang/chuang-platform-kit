#!/usr/bin/env python3
"""chuang_app_pipeline.gradle and chuang_app_pipeline.android: a local Android build's Gradle side and a device read, the
parts that stand on their own (SMADPickleBot #2310, step 4 of #2161).

Pinned (the positive case first in each): the edits to a prebuilt project's text, each refused or left alone when it must
be; the next version code; Gradle's arguments; a run that ends, goes silent, sits in one task or passes its bound; the
profile report read by kind; ccache's hits; two native input lists compared; a build's files; and what adb, uiautomator,
Maestro and the guest say.

Run:  python -B tests/test-app-android.py
"""
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'app-pipeline', 'src'))

from chuang_app_pipeline import android as A, gradle as G  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


def refused(fn, *a):
    try:
        fn(*a)
    except ValueError as e:
        return str(e)
    return ''


# ---- gradle: the text ----
check('the positive case: the daemon memory line replaced', G.with_jvmargs('a=1\norg.gradle.jvmargs=-Xmx2g\nb=2\n', '-Xmx4g'),
      'a=1\norg.gradle.jvmargs=-Xmx4g\nb=2\n')
check('  added when there is none', G.with_jvmargs('a=1', '-Xmx4g'), 'a=1\norg.gradle.jvmargs=-Xmx4g\n')
MANIFEST = '<manifest>\n  <application>\n    <meta-data android:name="expo.modules.updates.ENABLED" android:value="true"/>\n  </application>\n</manifest>\n'
out = G.with_update_channel(MANIFEST, 'production')
check('the channel goes beside the other updates settings, once', (out.index(G.HEADERS_KEY) < out.index('updates.ENABLED'), '&quot;production&quot;' in out,
      G.with_update_channel(out, 'other') == out), (True, True, True))
check('  no updates settings is refused', 'no expo-updates settings' in refused(G.with_update_channel, '<manifest/>', 'x'), True)
APPGRADLE = 'android {\n  versionCode 1\n  sentry {\n    org = "o"\n  }\n}\n'
check('the version code is set', G.with_version_code(APPGRADLE, 12).count('versionCode 12'), 1)
check('  a file without one is refused', refused(G.with_version_code, 'android {}', 2), 'app/build.gradle has no versionCode line')
quiet = G.with_no_sentry_telemetry(APPGRADLE)
check('telemetry is off in the sentry block, and said once', ('telemetry = false' in quiet, G.with_no_sentry_telemetry(quiet) == quiet,
      G.with_no_sentry_telemetry('android {\n}\n')), (True, True, 'android {\n}\n'))
check('the next version code is past the highest on any track', G.next_version_code([{'releases': [{'versionCodes': ['3', '7']}]}, {'releases': None}, {}]), 8)
check('  1 for an app with none', G.next_version_code([]), 1)

# ---- gradle: the run ----
check("gradle's arguments", G.args('assembleRelease', '-Xmx4g', abi='x86_64', extra=['-Px=1']),
      ['assembleRelease', '--console=plain', '--build-cache', '--profile', '-Dorg.gradle.jvmargs=-Xmx4g', '-Px=1', '-PreactNativeArchitectures=x86_64'])
check('  with neither', G.args('t', 'j')[-1], '-Dorg.gradle.jvmargs=j')
check('the last task a log names', (G.last_task('> Task :a:b\nx\n> Task :c:d UP-TO-DATE\n'), G.last_task('nothing')), (':c:d', ''))


class Proc:
    """A process that writes `writes[i]` to the log at its i-th look and ends after the last."""
    pid = 7

    def __init__(self, log, writes, code=0):
        self.log, self.writes, self.code, self.i, self.returncode = log, writes, code, 0, None

    def poll(self):
        if self.i >= len(self.writes):
            self.returncode = self.code
            return self.code
        with open(self.log, 'a', encoding='utf-8') as f:
            f.write(self.writes[self.i])
        self.i += 1
        return None


def watched(writes, step, **bounds):
    with tempfile.TemporaryDirectory() as d:
        log, t, killed, told = os.path.join(d, 'g.log'), [0], [], []

        def sleep(_s):
            t[0] += step
        got = G.watched_run(['gradle'], d, {}, log, kill=killed.append, tell=lambda s, task: told.append((s, task)), now=lambda: t[0], sleep=sleep,
                            popen=lambda *a, **k: Proc(log, writes), **{'stale_s': 600, 'task_max_s': 240, 'max_s': 2000, **bounds})
        return got, killed, told


check('the positive case: a run that ends is its exit code', watched(['> Task :a:b\n', '> Task :c:d\n'], 30)[0], (0, ''))
check('  told each minute, with its task', watched(['> Task :a:b\n', '> Task :c:d\n', 'x\n'], 30)[2], [(60, ':c:d')])
check('a silent run is killed', watched([''] * 40, 30)[:2], ((124, 'printed nothing for 600 s'), [7]))
check('a task past its bound is killed, named, though it prints', watched(['> Task :app:minify\n'] + ['warning\n'] * 20, 30)[:2],
      ((124, 'sat in :app:minify for 270 s'), [7]))
check('a run past its bound is killed', watched([f'> Task :t:{i}\n' for i in range(99)], 30, max_s=300)[:2], ((124, 'ran past 300 s'), [7]))

# ---- gradle: what it took ----
HTML = ('<td class="indentPath">:app:buildCMakeRelWithDebInfo</td>\n<td class="numeric">1m3.5s</td>\n<td>Did work</td>'
        '<td>:app</td><td class="numeric">9m0s</td><td>(total)</td>'
        '<td>:app:minifyReleaseWithR8</td><td class="numeric">12.25s</td><td></td>')
check('the profile report by task, slowest first, no project totals', G.profile_tasks(HTML),
      [(':app:buildCMakeRelWithDebInfo', 63.5, 'Did work'), (':app:minifyReleaseWithR8', 12.25, '')])
check('  an hour, and nothing to read', (G.profile_tasks('<td>:a:b</td><td class="numeric">1h2m3.5s</td><td>x</td>')[0][1], G.profile_tasks('')), (3723.5, []))
check('a task kind', [G.task_kind(p) for p in (':a:buildCMake', ':a:minifyReleaseWithR8', ':a:whatever')], ['CMake', 'R8 and dex', 'other'])
with tempfile.TemporaryDirectory() as d:
    check('no report is said', G.keep_profile(d, os.path.join(d, 'b.log')), 'no Gradle profile report (build/reports/profile is empty)')
    os.makedirs(os.path.join(d, 'build', 'reports', 'profile'))
    with open(os.path.join(d, 'build', 'reports', 'profile', 'profile-1.html'), 'w', encoding='utf-8') as f:
        f.write(HTML)
    line = G.keep_profile(d, os.path.join(d, 'b.log'))
    check('  kept beside the log, its seconds by kind', ('CMake 64 s, R8 and dex 12 s' in line, os.path.exists(os.path.join(d, 'b-profile.html'))), (True, True))
check("ccache's hits and misses", G.ccache_hits('direct_cache_hit\t5\npreprocessed_cache_hit\t2\ncache_miss\t3\nother\t9\n'), (7, 3))

# ---- gradle: inputs and files ----
a = [{'type': 'file', 'filePath': 'x', 'hash': '1'}, {'type': 'contents', 'id': 'y', 'hash': '2'}]
b = [{'type': 'file', 'filePath': 'x', 'hash': '9'}, {'type': 'dir', 'filePath': 'z', 'hash': '3'}]
check('two input lists compared', G.diff_sources(a, b), [('contents:y', 'removed'), ('dir:z', 'added'), ('file:x', 'changed')])
check('  the same list is no difference', G.diff_sources(a, list(a)), [])
check("where a build's file is", (G.built('w', True).endswith('app-release.aab'), G.built('w', False).endswith('app-release.apk')), (True, True))
with tempfile.TemporaryDirectory() as d:
    check('no mapping is said', G.mapping_beside(d, os.path.join(d, 'x.aab')), 'no R8 mapping (the build did not minify); Play gets no deobfuscation file')
    src = os.path.join(d, 'app', 'build', 'outputs', 'mapping', 'release')
    os.makedirs(src)
    with open(os.path.join(src, 'mapping.txt'), 'w', encoding='utf-8') as f:
        f.write('a -> b')
    G.mapping_beside(d, os.path.join(d, 'x.aab'))
    check('  kept beside the bundle', os.path.exists(os.path.join(d, 'x.aab.mapping.txt')), True)
check('signing is injected as project properties', sorted(G.signing_env('f', 'p', 'a', 'k').values()), ['a', 'f', 'k', 'p'])

# ---- android: the screen and the app ----
XML = '<hierarchy><node text="Sign in" content-desc=""/><node text="" content-desc="Home"/><node text="Wait" bounds="[10,20][30,60]"/></hierarchy>'
check('the positive case: the texts on a screen dump', A.screen_texts(XML), ['Sign in', 'Home', 'Wait'])
check('  one that does not parse is none', (A.screen_texts('<hierarchy><node'), A.screen_texts('ERROR: null root node')), ([], []))
check('a not-responding dialog, and its Wait', (A.anr(["Pixel Launcher isn't responding"]), A.anr(['Games']), A.wait_button(XML), A.wait_button('')),
      ("Pixel Launcher isn't responding", '', (20, 40), None))
check("  a host's own words as well", A.anr(['the dump hung'], A.ANR_WORDS + ('the dump hung',)), 'the dump hung')
CRASH = 'x\nE FATAL EXCEPTION: main\nE Process: com.example.app, PID: 1\nE java.lang.X\nE at y\n'
check("the app's own crash, and another app's", (len(A.crash_lines(CRASH, 'com.example.app')), A.crash_lines(CRASH, 'com.other')), (4, []))
check('the launch time', (A.launch_ms('TotalTime: 1834\n'), A.launch_ms('Error')), (1834, None))
check("an install's refusal", (A.install_error('Performing Streamed Install\nFailure [INSTALL_FAILED_X: y]'), A.install_error('java.lang.IllegalStateException: no\n  at z'),
      A.install_error('')), ('Failure [INSTALL_FAILED_X: y]', 'java.lang.IllegalStateException: no', 'adb said nothing'))
check("a flow's failure from its last plain lines", A.maestro_failure('no-such-file', 'Running\n┌── Maestro Cloud\nAssertion failed', 1), 'Running | Assertion failed')
check('  and from nothing', A.maestro_failure('no-such-file', '', 3), 'maestro exited 3 and printed nothing')
check('a reopen is due once past each time, never past the wait', (A.reopen_due(20.5, 0, 150, (20, 45)), A.reopen_due(30, 1, 150, (20, 45)), A.reopen_due(46, 1, 40, (20, 45))),
      (True, False, False))
check("adb's own server down", (A.adb_server_down('cannot connect to daemon'), A.adb_server_down('error: device offline')), (True, False))

# ---- android: the guest ----
MEM = 'MemTotal: 3000000 kB\nMemAvailable: 1024000 kB\nSwapTotal: 2048000 kB\nSwapFree: 1024000 kB\n'
check('the positive case: up past its bound is worn, its swap said', A.worn(MEM, '90000.1 1', 21600), 'up 25 h, 1000 of 2000 MB swap in use')
check('  up less is fresh, and an unreadable uptime is not judged', (A.worn(MEM, '600 1', 21600), A.worn('', 'cat: no such file', 1)), ('', ''))
check('seconds up and load', (A.guest_up_s('1201.7 4000.1'), A.guest_up_s(''), A.guest_load('1201.7 4000.1\n26.10 5.00 1.00 3/900 1\n')), (1202, None, 26.1))
check("/data's free space", (A.data_free_kb('Filesystem 1K Used Available\n/dev/block/dm-1 100 40 60 40% /data\n'), A.data_free_kb('no')), (60, None))
check("the guest's facts", A.guest_facts(MEM + '3600.0 1.0\n0.50 0.40 0.30 1/900 5\n'), {'guest_free_mb': 1000, 'guest_swap_mb': '1000/2000', 'guest_up_h': 1.0, 'guest_load': 0.5})
check("the box's", (A.host_facts('37 10240 32768'), A.host_facts('')), ({'box_cpu': 37, 'box_free_mb': 10240, 'box_total_mb': 32768}, {}))
check('the watched processes, summed', A.process_mb('"java.exe","1","C","1","2,048 K"\n"java.exe","2","C","1","1,024 K"\n"x","3"\n', [('gradle', 'java')]), {'gradle': 3})
check("the AVD's settings, the last line read", A.avd_config('hw.ramSize=2G\nhw.ramSize=3072\nvm.heapSize = 512\n'), {'ram': '3072', 'heap': '512'})

print(f'\n{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
