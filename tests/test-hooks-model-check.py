#!/usr/bin/env python3
"""plugins/gene-hooks/hooks/model-check.lib.sh (1.16.0; SMADPickleBot #423, Gene 11/7/26 11:37 AM PT: the model check
rides the prompt hook; 1.17.0, 11:58 AM PT: the all-models window at 99% starts the account handover). The library sourced on its own, and pacific-clock.sh's prompt branch carrying it, run through
bash as the host runs the hook. Standard library only; the positive case first.

    python tests/test-hooks-model-check.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, '..', 'plugins', 'gene-hooks', 'hooks')
LIB = os.path.join(HOOKS, 'model-check.lib.sh').replace('\\', '/')
CLOCK = os.path.join(HOOKS, 'pacific-clock.sh').replace('\\', '/')
BASH = shutil.which('bash') or 'bash'
FAILS = []


def check(label, got, want):
    print(('PASS  ' if got == want else 'FAIL  ') + label + ('' if got == want else f'\n        got  {got!r}\n        want {want!r}'))
    if got != want:
        FAILS.append(label)


def env_for(state, desktop=True, **extra):
    e = {k: v for k, v in os.environ.items() if not k.startswith('CLAUDE_MODEL_CHECK')}
    e.pop('CLAUDE_CODE_DESKTOP_APP_VERSION', None)
    if desktop:
        e['CLAUDE_CODE_DESKTOP_APP_VERSION'] = '1.0.0-test'
    e['CLAUDE_MODEL_CHECK_FILE'] = state.replace('\\', '/')
    e.update(extra)
    return e


def lib(state, **kw):
    """The function sourced on its own: prints MODEL_CHECK_TEXT and the function's exit code."""
    r = subprocess.run([BASH, '-c', f'. "{LIB}"; model_check; rc=$?; printf "%s\\n%s" "$rc" "$MODEL_CHECK_TEXT"'],
                       capture_output=True, text=True, encoding='utf-8', env=env_for(state, **kw), timeout=30)
    rc, _, text = r.stdout.partition('\n')
    return rc, text, r.returncode


def clock(state, event='UserPromptSubmit', **kw):
    """pacific-clock.sh as the host runs it: the one JSON object it prints."""
    r = subprocess.run([BASH, CLOCK], input=json.dumps({'hook_event_name': event, 'prompt': 'hello'}),
                       capture_output=True, text=True, encoding='utf-8', env=env_for(state, **kw), timeout=30)
    return r.stdout.strip(), r.returncode


def context(out):
    return json.loads(out)['hookSpecificOutput']['additionalContext'] if out else ''


def read(state):
    with open(state, encoding='utf-8') as f:
        return f.read().strip()


def main():
    d = tempfile.mkdtemp()
    state = os.path.join(d, 'model-check.last')

    rc, text, code = lib(state)
    check('the positive case: a desktop session with no check on record is handed the check, and the box is marked checked now',
          (rc, code, 'Model check, due now' in text, 'get_usage' in text, 'labeled Weekly / Fable' in text,
           os.path.isfile(state) and read(state).isdigit() and abs(int(read(state)) - time.time()) < 120),
          ('0', 0, True, True, True, True))
    check('  the check names the rule: 99 switches to the newest Opus, under it the newest Fable, the ids from the picker, a peer for your own',
          ('99 or more' in text, 'newest Opus' in text, 'newest Fable' in text, 'list-ids' in text,
           'peer session' in text, 'only when something was switched, refused or started' in text),
          (True, True, True, True, True, True))
    check('  since 1.17.0 the all-models window at 99 starts the account handover once (the record of one begun today stops a repeat), '
          'and the public hook names no session: the peer comes from the project manifest',
          ('Weekly / all models' in text, 'account handover starts now' in text, 'already begun for this account today' in text,
           'Black Widow' in text, 'Snow White' in text),
          (True, True, True, False, False))
    check('  the text carries nothing JSON would need escaped, and nothing outside ASCII (Windows consoles decode the rest)',
          ('"' in text, '\\' in text, '%' in text.replace('0%', ''), text.isascii()), (False, False, False, True))

    rc, text, code = lib(state)
    check('a second prompt within the hour: nothing handed, the mark untouched', (rc, text, code), ('0', '', 0))

    with open(state, 'w', encoding='utf-8') as f:
        f.write(f'{int(time.time()) - 3601}\n')
    rc, text, code = lib(state)
    check('an hour later the box is due again, and the mark moves to now',
          ('Model check, due now' in text, int(read(state)) > int(time.time()) - 120), (True, True))

    with open(state, 'w', encoding='utf-8') as f:
        f.write(f'{int(time.time()) - 100}\n')
    rc, text, code = lib(state, CLAUDE_MODEL_CHECK_EVERY='60')
    check('the interval is CLAUDE_MODEL_CHECK_EVERY seconds (60 here: 100 s old is due)', 'Model check, due now' in text, True)
    rc, text, code = lib(state, CLAUDE_MODEL_CHECK_EVERY='nonsense')
    check('  a value that is not a number is read as the default hour (just written: not due)', text, '')

    with open(state, 'w', encoding='utf-8') as f:
        f.write('garbage\n')
    rc, text, code = lib(state)
    check('a mark that is not an epoch counts as never checked', 'Model check, due now' in text, True)

    fresh = os.path.join(d, 'never.last')
    rc, text, code = lib(fresh, desktop=False)
    check('not a desktop-app session (no CLAUDE_CODE_DESKTOP_APP_VERSION): nothing handed and no mark written',
          (text, os.path.exists(fresh)), ('', False))
    rc, text, code = lib(fresh, CLAUDE_MODEL_CHECK_OFF='1')
    check('CLAUDE_MODEL_CHECK_OFF=1: nothing handed and no mark written', (text, os.path.exists(fresh)), ('', False))

    unwritable = os.path.join(d, 'a-folder')
    os.makedirs(unwritable)
    rc, text, code = lib(unwritable)
    check('a mark that cannot be written: nothing handed, the function still exits 0', (rc, text, code), ('0', '', 0))

    # Through the clock hook, as the host runs it.
    state2 = os.path.join(d, 'clock.last')
    out, code = clock(state2)
    ctx = context(out)
    check('pacific-clock.sh on a prompt carries the clock and, when due, the check in one JSON object',
          (code, ctx.startswith('Pacific clock: '), 'Model check, due now' in ctx, os.path.isfile(state2)), (0, True, True, True))
    out, code = clock(state2)
    ctx = context(out)
    check('  the next prompt carries the clock alone', (code, ctx.startswith('Pacific clock: '), 'Model check' in ctx), (0, True, False))
    with open(state2, 'w', encoding='utf-8') as f:
        f.write('0\n')
    out, code = clock(state2, event='PostToolUse')
    check('  a tool result never carries the check, and leaves the mark alone', ('Model check' in context(out), read(state2)), (False, '0'))
    out, code = clock(state2, desktop=False)
    check('  a session outside the desktop app gets the clock alone', ('Model check' in context(out), context(out).startswith('Pacific clock: ')), (False, True))

    shutil.rmtree(d, ignore_errors=True)
    print(f'\n{len(FAILS)} failure(s)')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
