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
      (decision, '#1917' in reason, (left or {}).get('held')), ('block', True, True))
folder, logp, path = where
check('  held once: the very next end of turn is let through and the record is gone',
      (G.check(SID, now=T0 + 700, folder=folder, log=logp), os.path.exists(path)), (None, False))

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
check("the Stop hook: a helper's end is never judged; the session's is held once, then let through",
      (helper, (held or {}).get('decision'), again), (None, 'block', None))
check('  the reason speaks of the action, names no project, and says it holds once',
      ('made no tool call' in held['reason'], 'held once' in held['reason'], 'SMAD' in held['reason']), (True, True, False))

print(f'\n{len(FAILS)} failure(s)' + (': ' + '; '.join(FAILS) if FAILS else ''))
sys.exit(1 if FAILS else 0)
