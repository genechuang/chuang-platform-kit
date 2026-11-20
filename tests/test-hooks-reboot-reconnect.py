#!/usr/bin/env python3
"""reboot-reconnect.sh hands the first session that wakes after a reboot the Remote Control reconnect job,
once per boot (Gene, 10/19/26: "put this in a session awake/restart hook so I don't have to prompt it").

    python tests/test-hooks-reboot-reconnect.py      # prints PASS/FAIL per case and "N failure(s)"

In the plugin since SMADPickleBot #1810: it speaks only in a project whose `.claude/gene-hooks.toml` names a sessions
manifest that is there (CLAUDE_PROJECT_DIR stands in for the session's project), which a temp project supplies here.

Run on a copy in a temp folder with CLAUDE_REBOOT_UPTIME standing in for /proc/uptime and
CLAUDE_REBOOT_MARK_DIR for ~/.claude/reboot-reconnect, so the real marker folder is never touched.
"""
import json
import os
import shutil
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, '..', 'plugins', 'gene-hooks', 'hooks')
PROJECT = ''   # the temp project whose config names a sessions manifest (main sets it)
fails = []


def project(tmp, name, config, manifest=''):
    """A temp project folder with `.claude/gene-hooks.toml` holding `config` (None: no file), and the manifest file
    at `manifest` (relative to the project) when given; its path with forward slashes."""
    top = os.path.join(tmp, name)
    os.makedirs(os.path.join(top, '.claude'))
    if config is not None:
        with open(os.path.join(top, '.claude', 'gene-hooks.toml'), 'w', encoding='utf-8', newline='\n') as f:
            f.write(config)
    if manifest:
        path = os.path.normpath(os.path.join(top, manifest))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, 'w').close()
    return top.replace('\\', '/')


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


def run(script, event, uptime, mark_dir, extra_env=None):
    env = dict(os.environ, CLAUDE_REBOOT_UPTIME=str(uptime), CLAUDE_REBOOT_MARK_DIR=mark_dir,
               CLAUDE_PROJECT_DIR=PROJECT)
    env.pop('CLAUDE_NO_REBOOT_RECONNECT', None)
    env.update(extra_env or {})
    payload = json.dumps({'hook_event_name': event, 'session_id': 'x'})
    r = subprocess.run(['bash', script], input=payload, capture_output=True, text=True, timeout=30, env=env)
    out = r.stdout.strip()
    return r.returncode, (json.loads(out) if out else None)


def main() -> int:
    global PROJECT
    with tempfile.TemporaryDirectory() as tmp:
        # The script alone, without moved.lib.sh beside it, so this box's own settings never make it stand down.
        shutil.copy(os.path.join(HOOKS, 'reboot-reconnect.sh'), tmp)
        PROJECT = project(tmp, 'proj', '# ours\n[mail]\nscript = "scripts/mail.py"\n\n[sessions]\n'
                                       'manifest = "../shared/sessions.md"   # the map\n', '../shared/sessions.md')
        sh = os.path.join(tmp, 'reboot-reconnect.sh').replace('\\', '/')
        marks = os.path.join(tmp, 'marks').replace('\\', '/')

        # The marker is the boot time to the minute (now - uptime), and a real uptime grows with the clock. The two
        # wake-ups below are seconds apart, so their uptime is chosen to put the boot 25 s into its minute: until
        # 1.39.0 they were a fixed 300 and 320, boot times 20 s apart, which straddled a minute about one run in
        # three and read as two boots.
        import time as _time
        up = 300 + (int(_time.time()) - 300 - 25) % 60   # 300 to 359: still "5 minute(s) ago"
        rc, out = run(sh, 'SessionStart', up, marks)
        ctx =((out or {}).get('hookSpecificOutput') or {}).get('additionalContext', '')
        check("the positive case: 5 minutes after a boot, SessionStart hands the session the reconnect job",
              (rc, (out or {}).get('hookSpecificOutput', {}).get('hookEventName'), 'booted 5 minute(s) ago' in ctx,
               'list_sessions' in ctx, 'local_... sessionId' in ctx, 'NEVER its name' in ctx),
              (0, 'SessionStart', True, True, True, True))
        check("  the marker names the boot, so the job is claimed once per boot", len(os.listdir(marks)), 1)

        rc, out = run(sh, 'UserPromptSubmit', up + 2, marks)
        check("  a second session waking in the same boot gets nothing (the job is claimed)", (rc, out), (0, None))

        rc, out = run(sh, 'UserPromptSubmit', 500, os.path.join(tmp, 'marks2').replace('\\', '/'))
        check("  UserPromptSubmit fires it too, with its own event name",
              (rc, (out or {}).get('hookSpecificOutput', {}).get('hookEventName')), (0, 'UserPromptSubmit'))

        rc, out = run(sh, 'SessionStart', 3600, os.path.join(tmp, 'marks3').replace('\\', '/'))
        check("an hour after the boot nothing fires (not a fresh reboot)", (rc, out), (0, None))

        rc, out = run(sh, 'PreToolUse', 60, os.path.join(tmp, 'marks4').replace('\\', '/'))
        check("another event is ignored", (rc, out), (0, None))

        rc, out = run(sh, 'SessionStart', 60, os.path.join(tmp, 'marks5').replace('\\', '/'),
                      {'CLAUDE_NO_REBOOT_RECONNECT': '1'})
        check("CLAUDE_NO_REBOOT_RECONNECT turns it off", (rc, out), (0, None))

        rc, out = run(sh, 'SessionStart', 'junk', os.path.join(tmp, 'marks6').replace('\\', '/'))
        check("an unreadable uptime is a quiet exit 0, never a refusal", (rc, out), (0, None))

        # SMADPickleBot #1810: only a project that keeps a sessions manifest has sessions to reconnect
        quiet = []
        for n, (config, manifest) in enumerate((('[mail]\nscript = "scripts/mail.py"\n', ''),          # no [sessions]
                                                ('[sessions]\nmanifest = "../gone/sessions.md"\n', ''),  # no file
                                                ('[mail]\nmanifest = "sessions.md"\n', 'sessions.md'),   # another table's
                                                (None, ''))):                                           # no config
            PROJECT = project(tmp, f'quiet{n}', config, manifest)
            marks_n = os.path.join(tmp, f'quiet-marks{n}').replace('\\', '/')
            quiet.append(run(sh, 'SessionStart', 300, marks_n) + (os.path.isdir(marks_n),))
        check("no [sessions] manifest, a manifest that is not there, the key in another table, no config: exit 0, "
              "nothing said, no boot claimed", quiet, [(0, None, False)] * 4)

        PROJECT = project(tmp, 'named', "[sessions]\r\n  manifest = 'docs/sessions.md'\r\n", 'docs/sessions.md')
        deep = PROJECT + '/src/deep'
        os.makedirs(deep)
        PROJECT = deep
        rc, out = run(sh, 'SessionStart', 300, os.path.join(tmp, 'marks7').replace('\\', '/'))
        check("the configured manifest is the one looked for: a single-quoted path inside the project, CRLF lines, "
              "found from a subfolder",
              (rc, (out or {}).get('hookSpecificOutput', {}).get('hookEventName')), (0, 'SessionStart'))

    print(f"\n{len(fails)} failure(s)")
    return 1 if fails else 0


if __name__ == '__main__':
    raise SystemExit(main())
