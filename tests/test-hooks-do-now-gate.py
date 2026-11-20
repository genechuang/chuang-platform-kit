#!/usr/bin/env python3
"""plugins/gene-hooks/hooks/do-now-gate.py and its call from stamp-reply.py (1.37.0, SMADPickleBot #1917). Standard
library only; the positive case first. It checks one action in the ticket-activity log, never a reply's words."""
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, '..', 'plugins', 'gene-hooks', 'hooks')


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HOOKS, file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


os.environ.pop('CLAUDE_PROJECT_DIR', None)   # the session running this suite may be in a project with its own command
G = load('do_now_gate', 'do-now-gate.py')
S = load('stamp_reply', 'stamp-reply.py')
FAILS = []
SID = 'aaaa1111-bbbb-2222-cccc-333344445555'
OTHER = 'ffff9999-0000-1111-2222-333344445555'
T0 = 1_800_000_000.0


def check(label, got, want):
    print(('PASS  ' if got == want else 'FAIL  ') + label + ('' if got == want else f'\n        got  {got!r}\n        want {want!r}'))
    if got != want:
        FAILS.append(label)


def box(record=None, log=()):
    """A folder with the session's record (or none) and an activity log of `log` lines; (folder, log path, record path)."""
    folder = tempfile.mkdtemp(prefix='do-now-')
    path = G.record_path(SID, folder)
    if record is not None:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(record, f)
    logp = os.path.join(folder, 'activity.log')
    with open(logp, 'w', encoding='utf-8') as f:
        f.write('\n'.join(log) + ('\n' if log else ''))
    return folder, logp, path


def run(record=None, log=(), now=T0 + 600):
    folder, logp, path = box(record, log)
    out = G.check(SID, now=now, folder=folder, log=logp)
    left = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else None
    return (out or {}).get('decision'), left, (out or {}).get('reason') or '', (folder, logp, path)


# 1. The positive case: handed a ticket, no tool call on it since, the turn is held once and the ticket is named.
decision, left, reason, where = run({'ticket': '1917', 'at': T0},
                                    [f'{T0 - 50:.0f} {SID} 1917', f'{T0 + 20:.0f} {SID} 1916', f'{T0 + 30:.0f} {SID} stop'])
check('the positive case: a ticket handed over with no tool call on it since holds the turn, naming it',
      (decision, '#1917' in reason, (left or {}).get('held')), ('block', True, 1))
folder, logp, path = where
second = G.check(SID, now=T0 + 700, folder=folder, log=logp)
third = G.check(SID, now=T0 + 800, folder=folder, log=logp)
check('  1.41.0: it holds again while the ticket is untouched, HOLDS times in all, saying how many are left',
      ((second or {}).get('decision'), 'This holds 1 more time.' in second['reason'], (third or {}).get('decision'),
       'This is the last hold.' in third['reason'], 'This holds 2 more times.' in reason, G.HOLDS),
      ('block', True, 'block', True, True, 3))
check('  then the next end of turn is let through and the record is gone',
      (G.check(SID, now=T0 + 900, folder=folder, log=logp), os.path.exists(path)), (None, False))
check('  its words offer no note on the card as a way out',
      ('why it waits' in reason, 'A note on the card is not a start' in reason), (False, True))
check('  a record held once before 1.41.0 (held: true) has two holds left',
      [(G.check(SID, now=T0 + 60 + i, folder=f, log=lg) or {}).get('decision') for f, lg, _p in [box({'ticket': '1917', 'at': T0, 'held': True})]
       for i in range(3)], ['block', 'block', None])

# 2. A tool call on the ticket after the record releases it; one before the record, or another session's, does not.
decision, left, _, _ = run({'ticket': '1917', 'at': T0}, [f'{T0 + 20:.0f} {SID} 1917'])
check('a tool call stamped with the ticket after the record: released, the record removed', (decision, left), (None, None))
decision, left, _, _ = run({'ticket': '1917', 'at': T0}, [f'{T0 + 20:.0f} {SID} 1915, 1917'])
check('  the ticket among several a stamp names counts', (decision, left), (None, None))
check("  a call before the record, another session's call, a stop and an agent's line do not count",
      [run({'ticket': '1917', 'at': T0}, [line])[0] for line in (
          f'{T0 - 5:.0f} {SID} 1917', f'{T0 + 20:.0f} {OTHER} 1917', f'{T0 + 20:.0f} {SID} stop',
          f'{T0 + 20:.0f} {SID} agent a1917 -', f'{T0 + 20:.0f} {SID} 19170', f'{T0 + 20:.0f} {SID} none')],
      ['block'] * 6)

# 3. Nothing to hold: no record, a stale one, one with no ticket, an unreadable one, no session id.
check('no record, no hold', run(None)[0], None)
decision, left, _, _ = run({'ticket': '1917', 'at': T0}, now=T0 + G.MAX_AGE + 1)
check('a record older than MAX_AGE holds nothing and is removed', (decision, left), (None, None))
check('a record with no ticket, and a file that is not JSON, hold nothing',
      (run({'at': T0})[0], run({'ticket': 'none', 'at': T0})[0]), (None, None))
folder, logp, path = box()
with open(path, 'w', encoding='utf-8') as f:
    f.write('{not json')
check('  an unreadable record lets the turn end', G.check(SID, now=T0, folder=folder, log=logp), None)
check('  no session id, nothing checked', G.check('', now=T0, folder=folder, log=logp), None)
folder, _logp, _path = box({'ticket': '1917', 'at': T0})
check('  a missing activity log is no tool call: held',
      (G.check(SID, now=T0 + 60, folder=folder, log=os.path.join(folder, 'no-such.log')) or {}).get('decision'), 'block')

# 4. From the Stop hook: after the stamp and the evidence, for the session alone, never a helper.
folder, logp, path = box({'ticket': '1917', 'at': T0})
keep = dict(os.environ)
os.environ['DO_NOW_DIR'], os.environ['TICKET_ACTIVITY_LOG'] = folder, logp
try:
    import time
    real = time.time
    time.time = lambda: T0 + 60
    helper = S.do_now({'session_id': SID, 'agent_id': 'a1'})
    held = S.do_now({'session_id': SID})
    again = S.do_now({'session_id': SID})
    time.time = real
finally:
    os.environ.clear()
    os.environ.update(keep)
check("the Stop hook: a helper's end is never judged; the session's is held, and held again while the ticket is untouched",
      (helper, (held or {}).get('decision'), (again or {}).get('decision')), (None, 'block', 'block'))
check('  the reason speaks of the action, names no project, and says how many holds are left',
      ('made no tool call' in held['reason'], 'This holds 2 more times.' in held['reason'], 'SMAD' in held['reason']),
      (True, True, False))

# 5. The log lagging a change of ticket (1.40.0, SMADPickleBot #1929): on 11/19/26 the first calls after a session
#    changed tickets were logged under the ticket of the turn before, and the gate held a turn that had done its work.
from datetime import datetime, timezone   # noqa: E402


def entry(seconds, kind, text=''):
    """One assistant entry of a transcript, `seconds` after T0: a text block or a tool call, as Claude Code writes them."""
    stamp = datetime.fromtimestamp(T0 + seconds, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')  # utc-ok: a transcript entry's own stamp is a UTC instant
    block = {'type': 'text', 'text': text} if kind == 'text' else {'type': 'tool_use', 'id': 'toolu_1', 'name': 'Bash', 'input': {}}
    return json.dumps({'type': 'assistant', 'timestamp': stamp, 'message': {'role': 'assistant', 'content': [block]}})


def held_with(lines, log=()):
    """The gate's decision for a record of #1883 at T0, the log given, and a transcript of `lines`."""
    folder, logp, _path = box({'ticket': '1883', 'at': T0}, log)
    transcript = os.path.join(folder, 'session.jsonl')
    with open(transcript, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    return (G.check(SID, now=T0 + 600, folder=folder, log=logp, transcript=transcript) or {}).get('decision')


STALE_LOG = [f'{T0 + 205:.0f} {SID} 1868', f'{T0 + 207:.0f} {SID} 1868', f'{T0 + 214:.0f} {SID} stop']
NEW = '11/19/26 6:02 PM PT · ticket: #1883: reading its body.'
OLD = '11/19/26 5:55 PM PT · ticket: #1868: done.'
check("the 11/19/26 shape: the log names the ticket of the turn before, the transcript shows the new ticket's stamp and "
      "then a tool call, so the turn is not held",
      held_with([entry(200, 'text', NEW), entry(205, 'tool')], STALE_LOG), None)
check('  a stamp naming the ticket with no tool call after it is words: held',
      held_with([entry(200, 'text', NEW)], STALE_LOG), 'block')
check('  a call under another ticket, a stamped call from before the record, and ticket: none do not count',
      [held_with([entry(200, 'text', OLD), entry(205, 'tool')], STALE_LOG),
       held_with([entry(-30, 'text', NEW), entry(-20, 'tool')], STALE_LOG),
       held_with([entry(200, 'text', NEW), entry(202, 'text', '11/19/26 6:03 PM PT · ticket: none: a question.'), entry(205, 'tool')], STALE_LOG)],
      ['block'] * 3)
check('  a later unstamped line changes nothing: the call after it is still charged to the last stamp',
      held_with([entry(200, 'text', NEW), entry(202, 'text', 'Running the suite.'), entry(205, 'tool')], STALE_LOG), None)
check('  one stamp naming two tickets counts for each', held_with([entry(200, 'text', '11/19/26 6:02 PM PT · ticket: #1868, #1883: both.'), entry(205, 'tool')]), None)
check('  an unreadable or missing transcript is no tool call, and never an error',
      [G.worked('', '1883', T0), G.worked(os.path.join(tempfile.gettempdir(), 'no-such-transcript.jsonl'), '1883', T0),
       held_with(['{not json', 'null', '[]'], STALE_LOG)], [False, False, 'block'])
folder, logp, path = box({'ticket': '1883', 'at': T0}, STALE_LOG)
tpath = os.path.join(folder, 'session.jsonl')
with open(tpath, 'w', encoding='utf-8') as f:
    f.write(entry(200, 'text', NEW) + '\n' + entry(205, 'tool') + '\n')
keep = dict(os.environ)
os.environ['DO_NOW_DIR'], os.environ['TICKET_ACTIVITY_LOG'] = folder, logp
try:
    import time
    real = time.time
    time.time = lambda: T0 + 600
    through_hook = S.do_now({'session_id': SID, 'transcript_path': tpath})
    time.time = real
finally:
    os.environ.clear()
    os.environ.update(keep)
check("  the Stop hook hands the gate the turn's transcript: not held, the record gone", (through_hook, os.path.exists(path)), (None, False))

# 6. 1.41.0 (SMADPickleBot #2066): with no record, the project is asked for the session's next card, and a card named
#    holds the turn; a hold answered with no tool call is let go.
print('the project\'s next card (1.41.0)')


def project(next_cmd=('python', 'next.py'), script='import json; print(json.dumps({"ticket": "2066", "title": "No idling", "how": "tool start 2066"}))'):
    """A project folder with a .do-now.json naming `next_cmd`, and a next.py that prints `script`'s answer."""
    root = tempfile.mkdtemp(prefix='do-now-project-')
    if next_cmd is not None:
        with open(os.path.join(root, G.CONFIG), 'w', encoding='utf-8') as f:
            json.dump({'next': list(next_cmd)} if not isinstance(next_cmd, str) else next_cmd, f)
    with open(os.path.join(root, 'next.py'), 'w', encoding='utf-8') as f:
        f.write(script + '\n')
    os.makedirs(os.path.join(root, 'deep', 'er'))
    return root


root = project()
folder, logp, _ = box()
named = lambda *_a: {'ticket': '2066', 'title': 'No idling', 'how': 'tool start 2066'}  # noqa: E731
first = G.check(SID, now=T0, folder=folder, log=logp, cwd=root, ask=named)
check('the positive case: no record, the project names a card: the turn is held, naming it and how to start it',
      ((first or {}).get('decision'), '#2066 No idling' in first['reason'], '(tool start 2066)' in first['reason'],
       'A note on a card is not work' in first['reason'], json.load(open(G.idle_path(SID, folder), encoding='utf-8'))),
      ('block', True, True, True, {'at': T0, 'ticket': '2066'}))
check('  the reason names no project', 'SMAD' in first['reason'], False)
check('  a hold answered with no tool call (a continued turn, nothing in the log since) is let go, its mark removed',
      (G.check(SID, now=T0 + 30, folder=folder, log=logp, cwd=root, active=True, ask=named), os.path.exists(G.idle_path(SID, folder))),
      (None, False))
folder, logp, _ = box(log=[f'{T0 + 10:.0f} {SID} 2066'])
G.check(SID, now=T0, folder=folder, log=logp, cwd=root, ask=named)
again = G.check(SID, now=T0 + 30, folder=folder, log=logp, cwd=root, active=True, ask=named)
check('  a hold answered with work holds again while the project still names a card, even in a continued turn',
      ((again or {}).get('decision'), json.load(open(G.idle_path(SID, folder), encoding='utf-8'))['at']), ('block', T0 + 30))
check("  another session's call, a stop and a helper's line are not this session's work",
      [G.called_since([ln], SID, T0) for ln in (f'{T0 + 5:.0f} {OTHER} 2066', f'{T0 + 5:.0f} {SID} stop',
                                               f'{T0 + 5:.0f} {SID} agent a1 -', f'{T0 - 5:.0f} {SID} 2066', f'{T0 + 5:.0f} {SID} none')],
      [False, False, False, False, True])
folder, logp, _ = box()
G.check(SID, now=T0, folder=folder, log=logp, cwd=root, ask=named)
check('  a fresh end of turn (not a continued one) holds whatever the last hold met: a new turn is a new answer',
      (G.check(SID, now=T0 + 900, folder=folder, log=logp, cwd=root, active=False, ask=named) or {}).get('decision'), 'block')
check('  the project names none: the turn ends, and an old mark is removed',
      (G.check(SID, now=T0 + 950, folder=folder, log=logp, cwd=root, ask=lambda *_a: {}), os.path.exists(G.idle_path(SID, folder))),
      (None, False))
folder, logp, _ = box({'ticket': '1917', 'at': T0})
both = G.check(SID, now=T0 + 60, folder=folder, log=logp, cwd=root, ask=named)
check("  a record's own ticket comes first: its hold, not the project's card", '#1917' in both['reason'] and '#2066' not in both['reason'], True)
check('the command: found from a folder below the project, python run under this interpreter, the rest as written',
      (G.project_command(os.path.join(root, 'deep', 'er')), G.project_command(root, {'CLAUDE_PROJECT_DIR': root})[0]),
      ((root, [sys.executable, 'next.py']), root))
check('  no file, a file that holds no object, one with an empty command and one with an empty word: no command',
      [G.project_command(project(next_cmd=None)), G.project_command(project(next_cmd='{nope')),
       G.project_command(project(next_cmd=())), G.project_command(project(next_cmd=('ok', '')))], [None] * 4)
folder, logp, _ = box()
real_run = G.check(SID, now=T0, folder=folder, log=logp, cwd=root)
check('  run for real: its last line of JSON read, the session id handed to it',
      ((real_run or {}).get('decision'), '#2066 No idling' in (real_run or {}).get('reason', '')), ('block', True))
echo = project(script='import sys, json; print("noise"); print(json.dumps({"ticket": sys.argv[sys.argv.index("--session-id") + 1][:4]}))')
check('  the command is given --session-id', G.ask_project(echo, [sys.executable, 'next.py'], '2066abcd'), {'ticket': '2066'})
check('  a command that fails, prints no JSON, prints a list or runs past its time answers nothing: the turn ends',
      [G.ask_project(project(script=s), [sys.executable, 'next.py'], SID) for s in
       ('raise SystemExit(3)', 'print("not json")', 'print("[1]")', 'import time; time.sleep(8)')], [{}] * 4)
check('  and no project at all asks nobody', G.check(SID, now=T0, folder=folder, log=logp, cwd=tempfile.gettempdir(), ask=named), None)
off_folder, off_log, _ = box()
open(os.path.join(off_folder, G.OFF), 'w').close()
asked_off = []
check("the owner's switch: a file named idle-off in the folder, or DO_NOW_IDLE_OFF, and the project is not even asked",
      (G.check(SID, now=T0, folder=off_folder, log=off_log, cwd=root, ask=lambda *a: asked_off.append(a) or named()),
       G.check(SID, now=T0, folder=folder, log=logp, cwd=root, env={'DO_NOW_IDLE_OFF': '1', 'CLAUDE_PROJECT_DIR': root},
               ask=lambda *a: asked_off.append(a) or named()), asked_off), (None, None, []))
off_rec, off_rec_log, _ = box({'ticket': '1917', 'at': T0})
open(os.path.join(off_rec, G.OFF), 'w').close()
check("  the record's own hold is not switched by it",
      (G.check(SID, now=T0 + 60, folder=off_rec, log=off_rec_log, cwd=root, ask=named) or {}).get('decision'), 'block')
keep = dict(os.environ)
folder, logp, _ = box()
os.environ['DO_NOW_DIR'], os.environ['TICKET_ACTIVITY_LOG'] = folder, logp
try:
    cont = S.decide({'session_id': SID, 'stop_hook_active': True, 'cwd': root}, lambda p: [])
    helper = S.decide({'session_id': SID, 'stop_hook_active': True, 'cwd': root, 'agent_id': 'a1'}, lambda p: [])
finally:
    os.environ.clear()
    os.environ.update(keep)
check("the Stop hook: a continued turn is still asked (the stamp is not), a helper's never",
      ((cont or {}).get('decision'), helper), ('block', None))

print(f'\n{len(FAILS)} failure(s)' + (': ' + '; '.join(FAILS) if FAILS else ''))
sys.exit(1 if FAILS else 0)
