#!/usr/bin/env python3
"""plugins/gene-hooks/hooks/model-check.lib.sh (1.16.0; SMADPickleBot #423, Gene 11/7/26 11:37 AM PT: the model check
rides the prompt hook; 1.17.0, 11:58 AM PT: the all-models window at 99% starts the account handover; 1.19.0, #1144,
6:04 PM PT: the hold until the Fable window's weekly reset, and model-check-hold.sh that records it). The library
sourced on its own, model-check-hold.sh run as a session runs it, and pacific-clock.sh's prompt branch carrying it, run through
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


HOLD = os.path.join(HOOKS, 'model-check-hold.sh').replace('\\', '/')
LIB_DIR = LIB.rsplit('/', 1)[0]


def env_for(state, desktop=True, hold=None, **extra):
    """The hold file is always a test path (state + '.hold' unless given), never the box's own under $HOME."""
    e = {k: v for k, v in os.environ.items() if not k.startswith('CLAUDE_MODEL_CHECK')}
    e.pop('CLAUDE_CODE_DESKTOP_APP_VERSION', None)
    if desktop:
        e['CLAUDE_CODE_DESKTOP_APP_VERSION'] = '1.0.0-test'
    e['CLAUDE_MODEL_CHECK_FILE'] = state.replace('\\', '/')
    e['CLAUDE_MODEL_CHECK_HOLD_FILE'] = (hold or state + '.hold').replace('\\', '/')
    e['CLAUDE_MODEL_CHECK_READING_FILE'] = (state + '.fable').replace('\\', '/')   # never the box's own reading
    e.update(extra)
    return e


def lib(state, source=None, cwd=None, **kw):
    """The function sourced on its own: prints MODEL_CHECK_TEXT and the function's exit code."""
    r = subprocess.run([BASH, '-c', f'. "{source or LIB}"; model_check; rc=$?; printf "%s\\n%s" "$rc" "$MODEL_CHECK_TEXT"'],
                       capture_output=True, text=True, encoding='utf-8', env=env_for(state, **kw), timeout=30, cwd=cwd)
    rc, _, text = r.stdout.partition('\n')
    return rc, text, r.returncode


def holder(hold_file, *args):
    """model-check-hold.sh as a session runs it: (exit code, stdout, stderr)."""
    e = {k: v for k, v in os.environ.items() if not k.startswith('CLAUDE_MODEL_CHECK')}
    e['CLAUDE_MODEL_CHECK_HOLD_FILE'] = hold_file.replace('\\', '/')
    r = subprocess.run([BASH, HOLD, *args], capture_output=True, text=True, encoding='utf-8', env=e, timeout=30)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def write(path, text):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


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
    check('  the check names the rule: since 1.24.0 (SMADPickleBot #1475) 95, or the rate carried forward an hour reaching 99, switches to the newest Opus, '
          'under it the newest Fable, the ids from the picker, a peer for your own',
          ('95 or more' in text, 'carried forward one hour' in text, 'reaches 99' in text, '99 or more' in text.split('(2)')[0],
           'newest Opus' in text, 'newest Fable' in text, 'list-ids' in text,
           'peer session' in text, 'only when something was switched, refused or started, or the check was late' in text),
          (True, True, True, False, True, True, True, True, True))
    check('  with no reading on record the text says the 95 rule stands alone, and the check ends by noting this reading, the script named',
          ('No earlier reading is on record' in text, f"(4) The reading: run bash '{LIB_DIR}/model-check-hold.sh' note" in text,
           'late by' in text),
          (True, True, False))
    check('  since 1.17.0 the all-models window at 99 starts the account handover once (the record of one begun today stops a repeat), '
          'and the public hook names no session: the peer comes from the project manifest',
          ('Weekly / all models' in text, 'account handover starts now' in text, 'already begun for this account today' in text,
           'Black Widow' in text, 'Snow White' in text),
          (True, True, True, False, False))
    check('  since 1.18.0 a session on another machine is messaged to switch itself, session mail when it cannot be reached, and counted only once it answers',
          ('another machine (a Remote Control row)' in text, 'ListAgents' in text, 'session mail instead' in text,
           'only for those that answered' in text),
          (True, True, True, True))
    check('  the text carries nothing JSON would need escaped, and nothing outside ASCII (Windows consoles decode the rest)',
          ('"' in text, '\\' in text, '%' in text.replace('0%', ''), text.isascii()), (False, False, False, True))
    named = f"bash '{LIB_DIR}/model-check-hold.sh'"
    check('  since 1.19.0 (SMADPickleBot #1144) the full text ends with the hold: set at the Fable resetsAt once every session is on Opus, clear on the switch to Fable, '
          'the script named by its absolute path beside the library',
          ('(3) The hold' in text, f'{named} set with the Fable window' in text, f'{named} clear' in text,
           'every session on another machine answered' in text, os.path.isfile(HOLD)),
          (True, True, True, True, True))
    named_full = text

    # The hold (1.19.0): a future epoch hands the all-models read alone; anything else is no hold.
    hstate = os.path.join(d, 'held.last')
    hfile = os.path.join(d, 'model-check.hold')
    write(hfile, f'{int(time.time()) + 86400}\n')
    rc, text, code = lib(hstate, hold=hfile)
    check('a hold still ahead of now: the due check is the short text, the all-models window alone, and the mark still moves',
          (rc, code, text.startswith('Model check, held'), 'Weekly / all models' in text, 'account handover starts now' in text,
           'Switch no model this turn' in text, 'Weekly / Fable' in text, 'set_session_model' in text,
           'model-check-hold.sh\' set' in text, os.path.isfile(hstate) and read(hstate).isdigit()),
          ('0', 0, True, True, True, True, False, False, False, True))
    check('  the short text is shorter than the full one, and carries nothing JSON would need escaped',
          (len(text) < len(named_full) / 2, '"' in text, '\\' in text, '%' in text, text.isascii()), (True, False, False, False, True))
    rc, text, code = lib(hstate, hold=hfile)
    check('  the cadence still applies under a hold: a second prompt within the hour hands nothing', (rc, text, code), ('0', '', 0))

    write(hfile, f'{int(time.time()) - 60}\n')
    rc, text, code = lib(os.path.join(d, 'past.last'), hold=hfile)
    check('a hold already past is spent: the full check runs, with the hold instruction naming the script',
          (text.startswith('Model check, due now'), 'Weekly / Fable' in text, f'{named} set' in text), (True, True, True))
    write(hfile, 'not-an-epoch\n')
    rc, text, code = lib(os.path.join(d, 'garbled.last'), hold=hfile)
    check('a garbled hold file is no hold: the full check', (text.startswith('Model check, due now'), '(3) The hold' in text), (True, True))
    with open(hfile, 'w', encoding='utf-8', newline='') as f:
        f.write(f'{int(time.time()) + 86400}\r\n')
    rc, text, code = lib(os.path.join(d, 'crlf.last'), hold=hfile)
    check('a hold line ending in CRLF (a Windows editor) is still read as the hold', text.startswith('Model check, held'), True)
    write(hfile, '')
    rc, text, code = lib(os.path.join(d, 'empty.last'), hold=hfile)
    check('an empty hold file is no hold', text.startswith('Model check, due now'), True)
    rc, text, code = lib(os.path.join(d, 'nohold.last'), hold=os.path.join(d, 'absent.hold'))
    check('a missing hold file is no hold', text.startswith('Model check, due now'), True)

    rc, text, code = lib(os.path.join(d, 'relative.last'), source='./model-check.lib.sh', cwd=HOOKS)
    m = text.split("bash '", 1)[1].split("'", 1)[0] if "bash '" in text else ''
    check('the library sourced by a relative path still names the hold script absolutely, and that path is the script',
          (m.startswith('/') or m[1:3] == ':/', m.endswith('/model-check-hold.sh'), '\\' in m), (True, True, False))
    if os.name == 'nt':   # a backslash is a path separator only on Windows; on Linux it is part of a file's name
        rc, text, code = lib(os.path.join(d, 'backslash.last'), source=LIB.replace('/', '\\\\'))
        m2 = text.split("bash '", 1)[1].split("'", 1)[0] if "bash '" in text else ''
        check('  sourced by a Windows path with backslashes, the named path has forward slashes alone and is the same script',
              (m2, '\\' in text), (f'{LIB_DIR}/model-check-hold.sh', False))
    found = subprocess.run([BASH, '-c', f'[ -f "{m}" ]'], capture_output=True, text=True, timeout=30).returncode
    check('  bash finds the named script there', found, 0)

    # model-check-hold.sh: set, show, clear, and a refusal that writes nothing.
    hf = os.path.join(d, 'sub', 'rt.hold')
    # A reset two days ahead, at a whole hour, as get_usage gives one (a weekly window's resetsAt is within 7 days).
    soon = (int(time.time()) // 3600 + 48) * 3600
    soon_iso = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(soon))
    rc, out, err = holder(hf, 'set', soon_iso + 'Z')
    check('model-check-hold.sh set writes the reset as an epoch (its folder made), and says so',
          (rc, os.path.isfile(hf) and read(hf), f'held until epoch {soon}' in out), (0, str(soon), True))
    rc, out, err = holder(hf, 'show')
    check('  show prints the epoch', (rc, out), (0, str(soon)))
    rc, out, err = holder(hf, 'set', soon_iso + '.000Z')
    check('  set takes fractional seconds as get_usage may give them', (rc, read(hf)), (0, str(soon)))
    rc, out, err = holder(hf, 'set', soon_iso + '+00:00')
    check('  set takes an offset', (rc, read(hf)), (0, str(soon)))
    os.remove(hf)
    e = {k: v for k, v in os.environ.items() if not k.startswith('CLAUDE_MODEL_CHECK')}
    e['CLAUDE_MODEL_CHECK_HOLD_FILE'] = hf.replace('\\', '/')
    r = subprocess.run([BASH, '-c', f'date() {{ return 1; }}; export -f date; bash "{HOLD}" set {soon_iso}.000Z'],
                       capture_output=True, text=True, encoding='utf-8', env=e, timeout=60)
    check('  with no date -d (BSD date), set falls back to python and writes the same epoch', (r.returncode, read(hf)), (0, str(soon)))
    rc, text, code = lib(os.path.join(d, 'rt.last'), hold=hf)
    check('  the library reads what set wrote as a hold', text.startswith('Model check, held'), True)
    rc, out, err = holder(hf, 'clear')
    check('  clear removes the file', (rc, os.path.exists(hf)), (0, False))
    rc, out, err = holder(hf, 'show')
    check('  show after clear says there is no hold', (rc, out), (0, 'no hold'))
    rc, out, err = holder(hf, 'clear')
    check('  clear with no hold is still exit 0', rc, 0)

    far_iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(int(time.time()) + 30 * 86400))
    for bad in ('garbage', '', 'tomorrow', '2099-13-45T99:99:99Z', '12345', far_iso, '2099-01-01T00:00:00Z'):
        rc, out, err = holder(hf, 'set', bad)
        check(f'set refuses {bad!r}: exit 2, one line, and nothing written (a time past 8 days is past any weekly reset)',
              (rc, len(err.splitlines()), os.path.exists(hf)), (2, 1, False))
    write(hf, f'{soon}\n')
    rc, out, err = holder(hf, 'set', 'garbage')
    check('  a refused set leaves an existing hold as it was', (rc, read(hf)), (2, str(soon)))
    write(hf, f'{int(time.time()) + 30 * 86400}\n')
    rc, text, code = lib(os.path.join(d, 'far.last'), hold=hf)
    check('a hold file more than 8 days ahead (a hand edit, a wrong year) is no hold: the full check runs',
          text.startswith('Model check, due now'), True)
    write(hf, '4070908800\n')
    rc, text, code = lib(os.path.join(d, 'far2.last'), hold=hf)
    check('  nor does a hold in 2099', text.startswith('Model check, due now'), True)
    write(hf, f'{int(time.time()) + 86400}\n')
    rc, text, code = lib(os.path.join(d, 'handover.last'), hold=hf)
    check('the held text has the session clear the hold before a handover, naming the script, so the new account checks at once',
          (f'{named} clear so the new account' in text, text.index(f'{named} clear') < text.index("skill's part (a)")), (True, True))
    rc, out, err = holder(hf, 'bogus')
    check('an unknown command is a usage line and exit 2', (rc, err.startswith('usage:')), (2, True))

    # 1.24.0 (SMADPickleBot #1475): the reading the next check carries, and the late line.
    rstate = os.path.join(d, 'reading.last')
    rfile = rstate + '.fable'
    e = {k: v for k, v in os.environ.items() if not k.startswith('CLAUDE_MODEL_CHECK')}
    e['CLAUDE_MODEL_CHECK_READING_FILE'] = rfile.replace('\\', '/')
    r = subprocess.run([BASH, HOLD, 'note', '97'], capture_output=True, text=True, encoding='utf-8', env=e, timeout=30)
    parts = read(rfile).split()
    check('the positive case: note writes "<epoch> <percent>" and says so',
          (r.returncode, len(parts), parts[1], abs(int(parts[0]) - time.time()) < 120, 'Fable at 97' in r.stdout), (0, 2, '97', True, True))
    for bad in ('', 'ninety', '101', '-3', '9.5'):
        r = subprocess.run([BASH, HOLD, 'note', bad], capture_output=True, text=True, encoding='utf-8', env=e, timeout=30)
        check(f'  note refuses {bad!r}: exit 2 and the reading left as it was', (r.returncode, read(rfile).split()[1]), (2, '97'))
    write(rfile, f'{int(time.time()) - 80 * 60} 96\n')
    rc, text, code = lib(rstate)
    check('the next check carries the last reading and its age in minutes, and no longer says the 95 rule stands alone',
          ('The last reading was Fable at 96 percent, 80 minutes ago.' in text, 'No earlier reading' in text), (True, False))
    for i, (label, body) in enumerate((('from the future', f'{int(time.time()) + 3600} 50'), ('over a week old', f'{int(time.time()) - 8 * 86400} 50'),
                                       ('garbled', 'yesterday ninety'), ('a percent of four digits', f'{int(time.time()) - 600} 1000'))):
        write(rfile, body + '\n')
        rc, text, code = lib(os.path.join(d, f'reading-{i}.last'), CLAUDE_MODEL_CHECK_READING_FILE=rfile.replace('\\', '/'))
        check(f'  a reading {label} is no reading', ('No earlier reading is on record' in text, 'The last reading' in text), (True, False))
    with open(rfile, 'w', encoding='utf-8', newline='') as f:
        f.write(f'{int(time.time()) - 600} 90\r\n')
    rc, text, code = lib(os.path.join(d, 'reading-crlf.last'), CLAUDE_MODEL_CHECK_READING_FILE=rfile.replace('\\', '/'))
    check('  a reading line ending in CRLF is still read', 'Fable at 90 percent, 10 minutes ago' in text, True)

    lstate = os.path.join(d, 'late.last')
    write(lstate, f'{int(time.time()) - 3600 - 76 * 60}\n')
    rc, text, code = lib(lstate)
    check('a mark two intervals old or more: the check opens late by the minutes past due and asks the session to say so',
          (text.startswith('Model check, late by 76 minutes'), 'say so to Gene' in text, 'Model check, due now' in text), (True, True, True))
    write(lstate, f'{int(time.time()) - 3600 - 20 * 60}\n')
    rc, text, code = lib(lstate)
    check('  a check due within its second interval is not late', (text.startswith('Model check, due now'), 'late by' in text), (True, False))
    write(lstate, f'{int(time.time()) - 3 * 3600}\n')
    write(hfile, f'{int(time.time()) + 86400}\n')
    rc, text, code = lib(lstate, hold=hfile)
    check('  a late check under a hold says late too, then the held text', (text.startswith('Model check, late by'), 'Model check, held' in text), (True, True))
    rc, text, code = lib(os.path.join(d, 'never-late.last'))
    check('  a box never checked is not late', 'late by' in text, False)
    with open(lstate, 'w', encoding='utf-8', newline='') as f:
        f.write(f'{int(time.time()) - 3 * 3600}\r\n')
    rc, text, code = lib(lstate)
    check('  a mark line ending in CRLF is still the mark (a Windows program wrote it): late by 120 minutes, not never checked',
          text.startswith('Model check, late by 120 minutes'), True)
    check('  the late and reading texts carry nothing JSON would need escaped, and stay ASCII',
          ('"' in text, '\\' in text, text.isascii()), (False, False, True))

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
