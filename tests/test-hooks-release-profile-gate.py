#!/usr/bin/env python3
"""release-profile-gate.py (Gene, 10/22/26 1:28 and 1:30 PM PT: a push to main owes a release profile, done by an agent
so the session returns at once). Pinned, the positive case first: a push's own line records the commit for the session
that pushed; once its runs finish the turn is held until an agent is started for it; an Agent prompt naming the
profile and the commit pays it; another session, a running deploy, another repo or a lapsed push holds nothing; it
lets go after MAX_BLOCKS; a broken event never fails.

In the plugin since SMADPickleBot #1810: the project's facts are its `[release]` table in `.claude/gene-hooks.toml`,
which this suite sets as the module's constants for the cases that came with the hook, and proves through a temp
project at the end (no table: nothing said; a table: its values used).

Run:  python tests/test-hooks-release-profile-gate.py      (PASS/FAIL per case, then `N failure(s)`)
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import contextlib

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, '..', 'plugins', 'gene-hooks', 'hooks')
spec = importlib.util.spec_from_file_location('release_profile_gate', os.path.join(HOOKS, 'release-profile-gate.py'))
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)
# One project's facts, as its `[release]` table would give them (the folders under a temp folder nothing writes to).
NOWHERE = os.path.join(tempfile.gettempdir(), 'gene-hooks-test-no-such-folder')
FACTS = {'REPO': 'genechuang/SMADPickleBot', 'CAPTAIN_FOLDER': 'smadpicklebot',
         'SHIP_DIR': os.path.join(NOWHERE, 'ship'), 'TF_LOCK': os.path.join(NOWHERE, 'tf.lock'),
         'PROFILE_COMMAND': 'python pipeline/desktop/release-profile.py compare'}
for _name, _value in FACTS.items():
    setattr(G, _name, _value)
failures = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ('' if ok else f'\n        got  {got!r}\n        want {want!r}'))
    if not ok:
        failures.append(label)


PUSH_OUT = ('remote: GitHub found 65 vulnerabilities\nTo https://github.com/genechuang/SMADPickleBot.git\n'
            '   56ecb8ff..49cf7b8b  HEAD -> main\n')


def bash(out, session='s1'):
    return {'session_id': session, 'cwd': '', 'tool_name': 'Bash', 'tool_response': {'stdout': out, 'stderr': ''}}


def transcript(*texts):
    lines = [json.dumps({'type': 'user', 'message': {'content': 'ship'}})]
    lines += [json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': t}]}}) for t in texts]
    return lambda path: lines


def main():
    owed = {}
    check('the positive case: a push to main records its new head for the session that pushed',
          (G.record(bash(PUSH_OUT), owed), owed['49cf7b8b']['session'], owed['49cf7b8b']['paid']),
          (['49cf7b8b'], 's1', False))
    done = lambda sha, cwd: True   # noqa: E731
    ans = G.decide({'session_id': 's1'}, owed, transcript('Deploy finished.'), done=done)
    check('runs finished, no agent: the turn is held, and the reason asks for a background agent naming the commit',
          (ans['decision'], 'Release profile: 49cf7b8b' in ans['reason'], 'run_in_background' in ans['reason']),
          ('block', True, True))
    check("  the reason has the numbers read with release-profile.py's compare, never by hand, and names the four parts",
          ('release-profile.py compare 49cf7b8b`' in ans['reason'], '--gate-log' in ans['reason'],
           'critical path' in ans['reason'], 'the cuts' in ans['reason']), (True, True, True, True))
    agent = {'session_id': 's1', 'tool_name': 'Agent',
             'tool_input': {'prompt': 'Release profile: 49cf7b8b against 56ecb8ff ...'}}
    check('an Agent whose prompt names "Release profile" and the commit pays it',
          (G.delegated(agent, owed), owed['49cf7b8b']['paid'], owed['49cf7b8b'].get('by')), (['49cf7b8b'], True, 'agent'))
    check('  ...and the turn then ends', G.decide({'session_id': 's1'}, owed, transcript('ok'), done=done), None)

    owed = {}
    G.record(bash(PUSH_OUT), owed)
    REPORT = ('49cf7b8b is live.\n| Stage | 56ecb8ff | 49cf7b8b | Change |\n|---|---|---|---|\n| App | 44 s | 42 s | -2 s |\n'
              '**Critical path:** the App run.\n**Cuts:** keep the bundle beside the unpack, Black Widow, 5 s, sent.')
    check('  the report itself pays it: the commit, a table, the critical path and the cuts',
          G.decide({'session_id': 's1'}, owed, transcript(REPORT), done=done), None)
    owed = {}
    G.record(bash(PUSH_OUT), owed)
    check("  naming the release profile and the commit no longer pays it (10/25/26: four ships went by so)",
          G.decide({'session_id': 's1'}, owed, transcript('**Release profile: 49cf7b8b** is ready.'), done=done)['decision'],
          'block')
    owed = {}
    G.record(bash(PUSH_OUT), owed)
    check('  while any run of the commit is going, the turn ends (the deploy watch is in the background)',
          G.decide({'session_id': 's1'}, owed, transcript('Deploy running.'), done=lambda s, c: False), None)
    check('  another session owes nothing for this push', G.decide({'session_id': 's2'}, owed, transcript('x'), done=done),
          None)
    check('  an Agent from another session does not pay it',
          G.delegated(dict(agent, session_id='s2'), owed), [])
    check('  an Agent prompt without the commit does not pay it',
          G.delegated(dict(agent, tool_input={'prompt': 'Release profile of the last release'}), owed), [])
    check('  the same push read again (a log Read later) is not owed twice', G.record(bash(PUSH_OUT), owed), [])
    read_event = {'session_id': 's1', 'tool_name': 'Read', 'tool_response': {'file': {'content': PUSH_OUT}}}
    check("  a background push's output, Read from its file, is recorded", G.record(read_event, {}), ['49cf7b8b'])
    check('  a push to another repo, or to dock, is not a release',
          (G.pushes(PUSH_OUT.replace('SMADPickleBot.git', 'claude-shared.git')),
           G.pushes(PUSH_OUT.replace('-> main', '-> dock'))), ([], []))
    owed = {}
    G.record(bash(PUSH_OUT), owed, now=1000)
    check('  a push older than LAPSE_HOURS lapses',
          (G.decide({'session_id': 's1'}, owed, transcript('x'), done=done, now=1000 + G.LAPSE_HOURS * 3600 + 1), owed),
          (None, {}))
    owed = {}
    G.record(bash(PUSH_OUT), owed)
    answers = [G.decide({'session_id': 's1'}, owed, transcript('x'), done=done) for _ in range(G.MAX_BLOCKS + 1)]
    check('  it holds at most MAX_BLOCKS times, then lets go saying the profile is still owed',
          ([a.get('decision') for a in answers], 'still owed' in answers[-1]['systemMessage']),
          (['block'] * G.MAX_BLOCKS + [None], True))
    SHIP_OUT = ('dock: moved to 8866880 with main\nship stages: tests 0 s, build 3 s\nsmoke and profile run in the '
                'background; run `python pipeline/desktop/desktop-smoke.py profile-wait ' + '8866880c' + 'a' * 32 + '` in '
                'the background\n')
    check("a ship's own lines name the release (10/25/26: the gate was blind to ship.py, which prints no git push line)",
          G.pushes(SHIP_OUT), ['8866880c' + 'a' * 32, '8866880'])
    owed = {}
    check('  ...recorded once, the short and the full sha the same release', G.record(bash(SHIP_OUT), owed),
          ['8866880c' + 'a' * 32])
    with tempfile.TemporaryDirectory() as d:
        # SMADPickleBot #1664: an old ship's lines read back from a log are owed nothing; a recent ship's are
        old, new = '3745882' + 'd' * 33, '1112223' + 'e' * 33
        for sha, started in ((old, 1000.0), (new, 1000.0 + 20 * 24 * 3600 - 60)):
            with open(os.path.join(d, f'stages-{sha}.json'), 'w', encoding='utf-8') as f:
                json.dump({'kind': 'phone', 'pushed': True, 'started': started}, f)
        at = 1000.0 + 20 * 24 * 3600
        line = lambda sha: f'dock: moved to {sha[:7]} with main\nrun `python pipeline/desktop/desktop-smoke.py profile-wait {sha}` in the background\n'  # noqa: E731
        check('  an old ship\'s lines printed from a log are owed nothing, a ship of this hour is, and a sha with no stages file is owed as before',
              (G.record(bash(line(old)), {}, now=at, ship_dir=d), G.record(bash(line(new)), {}, now=at, ship_dir=d),
               G.record(bash(line('9' * 40)), {}, now=at, ship_dir=d), G._started(old[:7], d), G._started('f' * 7, d)),
              ([], [new], ['9' * 40], 1000.0, 0.0))
    with tempfile.TemporaryDirectory() as d:
        full = '8866880c' + 'b' * 32
        with open(os.path.join(d, f'stages-{full}.json'), 'w', encoding='utf-8') as f:
            json.dump({'kind': 'phone', 'pushed': True, 'started': 2_000_000_100.0}, f)
        with open(os.path.join(d, 'stages-' + 'c' * 40 + '.json'), 'w', encoding='utf-8') as f:
            json.dump({'kind': 'phone', 'pushed': True, 'started': 2_000_000_000.0}, f)   # an older ship
        call = json.dumps({'type': 'assistant', 'timestamp': '2033-05-18T03:35:00Z', 'message': {'content': [
            {'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'cd /v/w && python pipeline/desktop/app/ship.py --phone --push'}}]}})
        lines = [json.dumps({'type': 'user', 'message': {'content': 'Ship'}}), call]
        owed = {}
        ans = G.decide({'session_id': 's1'}, owed, lambda p: lines, done=done, now=2_000_000_200.0, ship_dir=d)
        check("  a background ship the session never read is found from its own Bash call and the ship's stages file; "
              "only the newest is owed",
              (ans and ans['decision'], list(owed), owed[full]['session']), ('block', [full], 's1'))
        said = lines + [json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': REPORT.replace('49cf7b8b', full[:8])}]}}),
                        json.dumps({'type': 'user', 'message': {'content': 'thanks'}})]
        check('  a report given earlier in the transcript pays a ship found later (the hook landed after it)',
              G.decide({'session_id': 's1'}, {}, lambda p: said, done=done, now=2_000_000_200.0, ship_dir=d), None)
        check('  another session, which made no ship call, owes nothing',
              G.decide({'session_id': 's2'}, {}, lambda p: [lines[0]], done=done, now=2_000_000_200.0, ship_dir=d), None)
        owed = {}
        ans = G.decide({'session_id': 'cap', 'cwd': 'D:\\code\\SMADPickleBot'}, owed, lambda p: [lines[0]], done=done,
                       now=2_000_000_200.0, ship_dir=d)
        check('  SMADPickleBot #1184: a ship no session made (the SHIP card, a word the daemon shipped) is the captain\'s, '
              'whose folder is the repo\'s own clone; held like its own',
              (ans and ans['decision'], owed[full]['session'], owed[full]['adopted']), ('block', 'cap', True))
        check("  a worktree's session (SMADPickleBot-app) adopts nothing",
              G.decide({'session_id': 'bw', 'cwd': 'V:\\SMADPickleBot-app'}, {}, lambda p: [lines[0]], done=done,
                       now=2_000_000_200.0, ship_dir=d), None)
        pending = {'ship@2000000090': {'session': 'other', 'at': 2_000_000_090.0, 'pending': True}}
        G.decide({'session_id': 'cap', 'cwd': 'D:\\code\\SMADPickleBot'}, pending, lambda p: [lines[0]], done=done,
                 now=2_000_000_200.0, ship_dir=d)
        check("  a ship another session's own unresolved call can claim is left to it", full in pending, False)
        ship_event = {'session_id': 's1', 'cwd': '/v/w', 'tool_name': 'Bash',
                      'tool_input': {'command': 'cd /v/w && python pipeline/desktop/app/ship.py --phone --push'}}
        owed = {}
        key = G.ship_call(ship_event, owed, now=2_000_000_090.0)
        check("the ship's own Bash call is recorded at once, before any output (the transcript's tail lost it 10/25/26)",
              (key, owed[key]['pending'], owed[key]['session']), ('ship@2000000090', True, 's1'))
        check('  a pending ship holds nothing while its stages file is not written',
              G.decide({'session_id': 's1'}, dict(owed), lambda p: [], done=done, now=2_000_000_095.0,
                       ship_dir=tempfile.gettempdir() + '/no-such-ship-dir'), None)
        ans = G.decide({'session_id': 's1'}, owed, lambda p: [], done=done, now=2_000_000_200.0, ship_dir=d)
        check('  ...and with the transcript empty, the Stop hook finds its release from the stages file and holds the turn',
              (ans and ans['decision'], list(owed), owed[full]['cwd']), ('block', [full], '/v/w'))
        owed = {}
        G.ship_call(ship_event, owed, now=2_000_000_090.0)
        check('  a report already given pays the ship found',
              G.decide({'session_id': 's1'}, owed, lambda p: said, done=done, now=2_000_000_200.0, ship_dir=d), None)
        owed = {}
        G.ship_call(ship_event, owed, now=2_000_000_000.0 - G.SHIP_START - 1)
        check("  a ship that started long after the call is another's, not this call's",
              (G.decide({'session_id': 's1'}, owed, lambda p: [], done=done, now=2_000_000_200.0, ship_dir=d),
               [o.get('pending') for o in owed.values()]), (None, [True]))
        owed = {}
        G.ship_call(ship_event, owed, now=2_000_000_090.0)
        check('  another session does not resolve or owe my pending ship',
              (G.decide({'session_id': 's2'}, owed, lambda p: [], done=done, now=2_000_000_200.0, ship_dir=d), list(owed)),
              (None, ['ship@2000000090']))
        check('  a Bash call without --push, or any other tool, is no ship',
              (G.ship_call(dict(ship_event, tool_input={'command': 'python ship.py --warm'}), {}),
               G.ship_call(dict(ship_event, tool_name='Read'), {})), ('', ''))
        check('  a commit message naming `ship.py ... --push` is no ship (10/25/26: the first one recorded was that)',
              G.ship_call(dict(ship_event, tool_input={'command': "git commit -F - <<'EOF'\n- a Bash call running "
                                                                    "`ship.py ... --push` is recorded\nEOF"}), {}), '')
        check('  python -u, python3 and py all run a ship',
              [bool(G.SHIP_CALL.search(c)) for c in ('python -u pipeline/desktop/app/ship.py --phone --push',
                                                     'python3 ship.py --push', 'py V:/w/pipeline/desktop/app/ship.py --push')],
              [True, True, True])

    class R:
        def __init__(self, out, rc=0):
            self.stdout, self.returncode = out, rc

    def gh(runs, ladder='dev'):
        return lambda args, **kw: (R('a' * 40) if args[0] == 'git' else R(ladder + '\n') if 'variable' in args
                                   else R(json.dumps(runs)))
    done_runs = [{'name': 'Deploy', 'status': 'completed'}, {'name': 'Promote', 'status': 'completed'}]
    check('runs_done: promoted and every run finished is due (Gene, 10/22/26 2:18 PM PT: after prod, not dev)',
          G.runs_done('abc1234', run=gh(done_runs)), True)
    check('  dev deploy finished but not promoted yet: not due',
          G.runs_done('abc1234', run=gh([{'name': 'Deploy', 'status': 'completed'}])), False)
    check('  promoted while a run still goes: not due',
          G.runs_done('abc1234', run=gh(done_runs + [{'name': 'App', 'status': 'in_progress'}])), False)
    check('  gh unreadable: not due', G.runs_done('abc1234', run=lambda a, **k: R('', 1)), False)
    check('  LADDER full (10/22/26): the push\'s own runs done is production, no promote needed',
          G.runs_done('abc1234', run=gh([{'name': 'Deploy', 'status': 'completed'}], ladder='full')), True)
    check('  LADDER full with a run still going: not due',
          G.runs_done('abc1234', run=gh([{'name': 'Deploy', 'status': 'in_progress'}], ladder='full')), False)
    with tempfile.TemporaryDirectory() as d:
        G.OWED = os.path.join(d, 'owed.json')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sys.stdin = io.StringIO('not json')
            rc = G.main(['stop'])
        check('  a broken event exits 0 and prints nothing', (rc, out.getvalue()), (0, ''))
        sys.stdin = io.StringIO(json.dumps(bash(PUSH_OUT)))
        G.main(['post'])
        check('  post writes the owed file', list(G.load(G.OWED)), ['49cf7b8b'])
    # SMADPickleBot #848: a report that names the release by its change order alone is paid, when the ship's stages
    # file carries that number; another number, or no number on file, is not
    sha = 'b61fcc0374f39a18a1e4473580b0db4980a8ee8a'
    report = 'Live: CO #847.\n| stage | last | this |\nCritical path: the phone build.\nCuts: none.'
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, f'stages-{sha}.json'), 'w', encoding='utf-8') as f:
            json.dump({'pushed': True, 'change_order': 847}, f)
        with tempfile.TemporaryDirectory() as empty:
            check('the change order names the release: CO #847 paid; CO #846 not; no number on file not; the sha still paid',
                  (G.paid(report, sha, d), G.paid(report.replace('#847', '#846'), sha, d), G.paid(report, sha, empty),
                   G.paid(report.replace('CO #847', 'b61fcc0'), sha, empty),
                   G.delegated_paid('Release profile: CO #847', sha, d)),
                  (True, False, False, True, True))
    # SMADPickleBot #1242 (Gene, 11/9/26 1:26 AM PT: "Why the heck would you not profile the entire ship apps when I
    # issued a ship apps???"): a word's release (stages `source: word`) is the whole chain: due only once the chain ended
    # (its tf-history record, or no tf_pipe lock), paid only by a report with its `word to` row or an agent started
    # after that end and asked for the whole chain; a plain ship keeps the old rule
    sha = '514dc30b649fa35dd6f5824055dc2e76468606fb'
    ship_only = 'Live: 514dc30.\n| stage | last | this |\nCritical path: the App run.\nCuts: none.'
    whole = ship_only + '\n| word to both ends | 1234 s | 1066 s |'
    stop = {'session_id': 's', 'transcript_path': ''}
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, f'stages-{sha}.json'), 'w', encoding='utf-8') as f:
            json.dump({'pushed': True, 'source': 'word', 'started': 1.0}, f)
        lock = os.path.join(d, 'smad-tf-pipe.lock')
        open(lock, 'w').close()
        keep = G.TF_LOCK
        G.TF_LOCK = lock
        try:
            owed = lambda: {sha: {'session': 's', 'at': 0, 'blocks': 0, 'paid': False}}  # noqa: E731
            running = (G.chain(sha, d), G.chain_done(sha, d), G.paid(ship_only, sha, d), G.paid(whole, sha, d),
                       G.delegated_paid('Release profile: 514dc30, end to end', sha, d),
                       G.decide(stop, owed(), lambda p: [], done=lambda s, c: True, now=10.0, ship_dir=d))
            with open(os.path.join(d, 'tf-history.jsonl'), 'w', encoding='utf-8') as f:
                f.write(json.dumps({'sha': sha, 'target': 'apps', 'total': 1234}) + '\n')
            held = G.decide(stop, owed(), lambda p: [], done=lambda s, c: True, now=10.0, ship_dir=d) or {}
            ended = (G.chain_done(sha, d), G.delegated_paid('Release profile: 514dc30, end to end', sha, d),
                     G.delegated_paid('Release profile: 514dc30, the ship to web live', sha, d),
                     held.get('decision'), 'word to' in held.get('reason', ''))
        finally:
            G.TF_LOCK = keep
        check('the positive case: a word chain is not due while it runs, then paid only by its whole-chain report or an '
              'agent asked for the whole chain after its end',
              (running, ended), ((True, False, False, True, False, None), (True, True, False, 'block', True)))
        with open(os.path.join(d, f'stages-{sha}.json'), 'w', encoding='utf-8') as f:
            json.dump({'pushed': True, 'source': 'card'}, f)
        check('  a plain ship keeps the old rule: its ship report pays it', G.paid(ship_only, sha, d), True)
    # SMADPickleBot #1822 (11/18/26: three plain "ship" releases held for a `word to` row): a spoken plain ship also
    # writes `source: word` (ship_card.py word, #1659), so it is a chain only with tf_pipe's own evidence
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, f'stages-{sha}.json'), 'w', encoding='utf-8') as f:
            json.dump({'pushed': True, 'source': 'word', 'started': 1.0}, f)
        keep = G.TF_LOCK
        G.TF_LOCK = os.path.join(d, 'no-such.lock')
        try:
            check('  a spoken plain ship (source word, no tf_pipe record or lock) is no chain: its ship report and a plain '
                  'agent pay it',
                  (G.chain(sha, d), G.paid(ship_only, sha, d), G.delegated_paid('Release profile: 514dc30b', sha, d)),
                  (False, True, True))
        finally:
            G.TF_LOCK = keep
    # SMADPickleBot #1810: the project's facts come from its `.claude/gene-hooks.toml`; none, no gate
    held = G.decide({'session_id': 's'}, {'49cf7b8b': {'session': 's', 'at': 10.0, 'blocks': 0, 'paid': False}},
                    lambda p: [], done=lambda s, c: True, now=10.0, ship_dir=NOWHERE) or {}
    check('  the hold names the configured profile command with the commit',
          '`python pipeline/desktop/release-profile.py compare 49cf7b8b`' in held.get('reason', ''), True)
    keep_env = os.environ.pop('RELEASE_CAPTAIN', None)
    try:
        with tempfile.TemporaryDirectory() as d:
            bare, table = os.path.join(d, 'bare'), os.path.join(d, 'Proj')
            for top, text in ((bare, '[mail]\nscript = "scripts/mail.py"\n'),
                              (table, '[release]\nrepo = "someone/proj"\ncaptain_folder = "Proj"\n'
                                      'ship_logs = "proj-ship"\ntf_lock = "proj-tf.lock"\n'
                                      'profile_command = "python tools/profile.py compare"\n')):
                os.makedirs(os.path.join(top, '.claude'))
                with open(os.path.join(top, '.claude', 'gene-hooks.toml'), 'w', encoding='utf-8') as f:
                    f.write(text)
            push = 'To https://github.com/someone/proj.git\n   56ecb8ff..49cf7b8b  HEAD -> main\n'
            said = []
            for top in (bare, d, table):   # no [release] table; no config at all; a table
                for name in FACTS:
                    setattr(G, name, '')
                G.OWED = os.path.join(d, f'owed-{os.path.basename(top)}.json')
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    codes = []
                    for mode, event in (('post', dict(bash(push), cwd=top)), ('stop', {'session_id': 's1', 'cwd': top}),
                                        ('status', None)):
                        sys.stdin = io.StringIO(json.dumps(event) if event else '')
                        keep_cwd = os.getcwd()
                        os.chdir(top)
                        try:
                            codes.append(G.main([mode]))
                        finally:
                            os.chdir(keep_cwd)
                said.append((codes, out.getvalue() if top != table else bool(out.getvalue()), os.path.exists(G.OWED)))
            check('a project with no [release] table, or no config at all: every mode exits 0, says nothing, writes nothing',
                  said[:2], [([0, 0, 0], '', False)] * 2)
            check('  a project with the table: its values are the ones used, and its push is owed',
                  (said[2], G.REPO, G.CAPTAIN_FOLDER, G.SHIP_DIR == os.path.join(G.TEMP, 'proj-ship'),
                   G.TF_LOCK == os.path.join(G.TEMP, 'proj-tf.lock'), G.PROFILE_COMMAND, list(G.load(G.OWED)),
                   G.pushes(PUSH_OUT), G.is_captain({'cwd': table}), G.is_captain({'cwd': bare})),
                  (([0, 0, 0], True, True), 'someone/proj', 'proj', True, True, 'python tools/profile.py compare',
                   ['49cf7b8b'], [], True, False))
    finally:
        if keep_env is not None:
            os.environ['RELEASE_CAPTAIN'] = keep_env
        for name, value in FACTS.items():
            setattr(G, name, value)
    print(f'{len(failures)} failure(s)' + (':' if failures else ''))
    for f in failures:
        print(f'  {f}')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
