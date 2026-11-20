#!/usr/bin/env python3
"""hooks/ticket-start.py: Gene's "do 466" moves the ticket to In shipyard at once (10/27/26). Standard library only;
the positive case first. Prints PASS/FAIL lines and a final failure count."""
import importlib.util
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, '..', 'plugins', 'gene-hooks', 'hooks')
spec = importlib.util.spec_from_file_location('ticket_start', os.path.join(HOOKS, 'ticket-start.py'))
T = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T)
FAILS = []


def check(label, got, want):
    print(('PASS  ' if got == want else 'FAIL  ') + label + ('' if got == want else f'\n        got  {got!r}\n        want {want!r}'))
    if got != want:
        FAILS.append(label)


with tempfile.TemporaryDirectory() as d:
    os.makedirs(os.path.join(d, 'scripts'))
    open(os.path.join(d, 'scripts', 'tickets.py'), 'w').close()
    sub = os.path.join(d, 'app', 'src')
    os.makedirs(sub)
    started = []
    out = io.StringIO()
    rc = T.main(io.StringIO(json.dumps({'prompt': 'do 466', 'cwd': sub})), popen=lambda args, **k: started.append((args[-2:], k['cwd'])), out=out)
    check('the positive case: "do 466" from a subfolder starts tickets.py start 466 in the project and says so',
          (rc, started, 'moving to In shipyard' in out.getvalue()), (0, [(['start', '466'], d)], True))
    check('several numbers, # and commas and "and", each once, in order',
          (T.numbers('Do 440 443 432'), T.numbers('fix #440 and #443, #440'), T.numbers('work on ticket 461'), T.numbers('Start on #12')),
          (['440', '443', '432'], ['440', '443'], ['461'], ['12']))
    check('no verb, a question, a version or a time is nothing',
          (T.numbers('why is 466 in Backlog'), T.numbers('shipped 4aabc4bc at 1:19'), T.numbers('ship'), T.numbers('')),
          ([], [], [], []))
    check('"build" alone is a store build, not a ticket; "build #466" and "build ticket 466" are; a peer\'s message is nothing (10/28/26: #19)',
          (T.numbers('I start store build 19 when you say live'), T.numbers('build 466'), T.numbers('build #466'),
           T.numbers('build ticket 466'), T.numbers('Another Claude session sent a message:\n<cross-session-message from="x">do 466')),
          ([], [], ['466'], ['466'], []))
    check('#650 (10/30/26): a subagent\'s hand-back names nothing; a number wearing a unit is a measurement ("cold start 48 s"), a bare "start 48" or "start #48 s" still a ticket',
          (T.numbers('[Subagent hand-back] The text below is the final report of a subagent\n  cold start 48 s / 8 s / 22 s; do 466'),
           T.numbers('<agent-message from="a3cf">\nstart 48'),
           T.numbers('idle RAM 4.2 GB; cold start 48 s / 8 s; take 250 MB; do 95% of it; fix 12 minutes later'),
           T.numbers('start 48'), T.numbers('start #48 s later')),
          ([], [], [], ['48'], ['48']))
    started.clear()
    rc2 = T.main(io.StringIO(json.dumps({'prompt': 'do 466', 'cwd': tempfile.gettempdir()})), popen=lambda a, **k: started.append(a), out=io.StringIO())
    rc3 = T.main(io.StringIO('not json'), popen=lambda a, **k: started.append(a), out=io.StringIO())

    def refuse(*a, **k):
        raise OSError('no python')
    out4 = io.StringIO()
    rc4 = T.main(io.StringIO(json.dumps({'prompt': 'fix 450', 'cwd': d})), popen=refuse, out=out4)
    check('outside a project, bad input or a start that fails: exit 0, the prompt goes through, a failure said',
          (rc2, rc3, rc4, started, 'could not start' in out4.getvalue()), (0, 0, 0, [], True))
    # 1.38.0 (SMADPickleBot #1810): the ticket tool is the one the project names, wherever it keeps it
    proj = os.path.join(d, 'other')
    os.makedirs(os.path.join(proj, '.claude'))
    os.makedirs(os.path.join(proj, 'tools'))
    os.makedirs(os.path.join(proj, 'scripts'))
    open(os.path.join(proj, 'tools', 'board.py'), 'w').close()
    open(os.path.join(proj, 'scripts', 'tickets.py'), 'w').close()
    with open(os.path.join(proj, '.claude', 'gene-hooks.toml'), 'w', encoding='utf-8') as f:
        f.write('[tickets]\nscript = "tools/board.py"\n')
    ran, out5 = [], io.StringIO()
    T.main(io.StringIO(json.dumps({'prompt': 'do 466', 'cwd': os.path.join(proj, 'tools')})),
           popen=lambda args, **k: ran.append((os.path.normpath(args[1]), args[2:], k['cwd'])), out=io.StringIO())
    T.main(io.StringIO(json.dumps({'prompt': 'fix 450', 'cwd': proj})), popen=refuse, out=out5)
    check("a project's .claude/gene-hooks.toml names its ticket tool: that script is started, from the project's root, "
          'and a failed start names it; with no config the scripts/tickets.py convention stands (the cases above)',
          (ran, 'run python tools/board.py start 450' in out5.getvalue()),
          ([(os.path.normpath(os.path.join(proj, 'tools', 'board.py')), ['start', '466'], proj)], True))

print(f'{len(FAILS)} failure(s)' + (':\n' + '\n'.join(f'  - {f}' for f in FAILS) if FAILS else ''))
sys.exit(1 if FAILS else 0)
