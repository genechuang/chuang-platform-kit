#!/usr/bin/env python3
"""plugins/gene-hooks/hooks/session-mail.py (SMADPickleBot #391, 10/26/26; in the plugin since 1.38.0, #1810): the hook
finds the session's name in Claude Code's registry, hands over its unread letters as additionalContext on
UserPromptSubmit, PostToolUse and SessionStart, marks them read once, and says nothing for another event, an unnamed
session, or an empty inbox. Standard library only.

    python tests/test-hooks-session-mail.py
"""
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, '..', 'plugins', 'gene-hooks', 'hooks')
spec = importlib.util.spec_from_file_location('session_mail_hook', os.path.join(HOOKS, 'session-mail.py'))
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)

failures = []


def check(label, got, want):
    ok = got == want
    print(('  PASS  ' if ok else '  FAIL  ') + label)
    if not ok:
        print(f'        got  {got!r}\n        want {want!r}')
        failures.append(label)


def main():
    with tempfile.TemporaryDirectory() as d:
        sessions, mail = os.path.join(d, 'sessions'), os.path.join(d, 'mail')
        os.makedirs(sessions)
        with open(os.path.join(sessions, '1.json'), 'w', encoding='utf-8') as f:
            json.dump({'sessionId': 'sid-1', 'name': 'Snow White', 'pid': 1}, f)
        with open(os.path.join(sessions, '2.json'), 'w', encoding='utf-8') as f:
            json.dump({'sessionId': 'sid-2', 'pid': 2}, f)
        inbox = os.path.join(mail, 'snow-white')
        os.makedirs(inbox)
        with open(os.path.join(inbox, 'inbox.jsonl'), 'w', encoding='utf-8') as f:
            f.write(json.dumps({'id': 'a1', 'to': 'Snow White', 'from': 'Black Widow', 'subject': 'landing', 'text': 'landed #383', 'sent_at': 1_000_000.0}) + '\n')
            f.write(json.dumps({'id': 'a2', 'to': 'Snow White', 'from': 'Mr Sandman', 'subject': '', 'text': 'triaged', 'sent_at': 1_000_100.0}) + '\n')
        event = json.dumps({'hook_event_name': 'UserPromptSubmit', 'session_id': 'sid-1'})
        H.ARM_SCRIPT = os.path.join(d, 'no-such-script.py')   # the reminder's own checks are below
        out = H.run(event, mail, sessions, now=lambda: 5.0)
        got = json.loads(out)
        ctx = got['hookSpecificOutput']['additionalContext']
        check('the positive case: two unread letters handed over on UserPromptSubmit, oldest first, sender and subject named',
              (got['hookSpecificOutput']['hookEventName'], 'Session mail from Black Widow' in ctx, ': landing' in ctx,
               'landed #383' in ctx, 'Session mail from Mr Sandman' in ctx, ctx.index('landed') < ctx.index('triaged'),
               'id a1' in ctx, 'never the user' in ctx),
              ('UserPromptSubmit', True, True, True, True, True, True, True))
        marks = [json.loads(ln)['id'] for ln in open(os.path.join(inbox, 'read.jsonl'), encoding='utf-8')]
        check('  marked read once, with the clock', marks, ['a1', 'a2'])
        # 1.38.0: a letter's stamp is Pacific by the zone, never the box's local time called PT. 1,794,528,000 is
        # 11/13/26 00:00 UTC (standard time, UTC-8); 1,783,987,200 is 7/14/26 00:00 UTC (daylight time, UTC-7).
        check('  a stamp is Pacific whatever the box: standard time, daylight time, and inside the letter',
              (H.stamp(1_794_528_000), H.stamp(1_783_987_200), '(1/12/70 5:46 AM PT, id a1)' in ctx),
              ('11/12/26 4:00 PM PT', '7/13/26 5:00 PM PT', True))
        real = sys.modules.pop('zoneinfo', None)
        sys.modules['zoneinfo'] = None   # a box whose Python has no zone data: the rule worked out by hand
        try:
            check('  and the same with no zone database on the box, on both sides of each changeover',
                  (H.stamp(1_794_528_000), H.stamp(1_783_987_200), H.stamp(1_772_964_000 - 60), H.stamp(1_772_964_000),
                   H.stamp(1_793_523_600 - 60), H.stamp(1_793_523_600)),
                  ('11/12/26 4:00 PM PT', '7/13/26 5:00 PM PT', '3/08/26 1:59 AM PT', '3/08/26 3:00 AM PT',
                   '11/01/26 1:59 AM PT', '11/01/26 1:00 AM PT'))
        finally:
            del sys.modules['zoneinfo']
            if real is not None:
                sys.modules['zoneinfo'] = real
        proj = os.path.join(d, 'proj')
        os.makedirs(os.path.join(proj, '.claude'))
        os.makedirs(os.path.join(proj, 'tools', 'deep'))
        open(os.path.join(proj, 'tools', 'mail.py'), 'w').close()
        with open(os.path.join(proj, '.claude', 'gene-hooks.toml'), 'w', encoding='utf-8') as f:
            f.write('# the hooks\' facts\nsessions = true\n[mail]\nscript = "tools/mail.py"   # the pull\n[tickets]\nscript = "tools/none.py"\n')
        check('  the arm script is the one the project names in .claude/gene-hooks.toml, found from a subfolder; a '
              'project with no file, or naming a script that is not there, has none',
              (H.arm_script(os.path.join(proj, 'tools', 'deep')) == os.path.normpath(os.path.join(proj, 'tools', 'mail.py')),
               H.arm_script(d), H.arm_script('')), (True, '', ''))
        sys.path.insert(0, HOOKS)
        import project_config as PC
        check('  the small reader and tomllib read the file the same; a script that is missing is no script',
              (PC.parse.__call__('sessions = true\n[mail]\nscript = "a/b.py"  # c\n'), PC.script(proj, 'tickets'), PC.read(d)),
              ({'sessions': True, 'mail': {'script': 'a/b.py'}}, ('', ''), ('', {})))
        check('  the next prompt says nothing', H.run(event, mail, sessions), '')
        with open(os.path.join(inbox, 'inbox.jsonl'), 'a', encoding='utf-8') as f:
            f.write(json.dumps({'id': 'a3', 'to': 'Snow White', 'from': 'session-mail', 'text': 'receipt', 'sent_at': 1_000_200.0}) + '\n')
        # #728: the same event from a subagent inside the session (agent_id and agent_type beside the session's own
        # session_id, as a real delegate's event read on 11/1/26) delivers nothing and marks nothing read
        with open(os.path.join(inbox, 'read.jsonl'), 'w', encoding='utf-8') as f:
            f.write('')
        check('  a subagent gets no letter', H.run(json.dumps({'hook_event_name': 'PostToolUse', 'session_id': 'sid-1',
                                                                 'agent_id': 'a1b2c3', 'agent_type': 'general-purpose'}), mail, sessions), '')
        check('  and it stays unread for the session', 'a3' in [r['id'] for r in H.unread('Snow White', mail)], True)
        for ev in ('PostToolUse', 'SessionStart'):
            got = json.loads(H.run(json.dumps({'hook_event_name': ev, 'session_id': 'sid-1'}), mail, sessions))
            check(f'  a new letter on {ev}', (got['hookSpecificOutput']['hookEventName'], 'receipt' in got['hookSpecificOutput']['additionalContext']), (ev, True))
            with open(os.path.join(inbox, 'read.jsonl'), 'w', encoding='utf-8') as f:
                f.write('')
        check('  another event says nothing', H.run(json.dumps({'hook_event_name': 'PreToolUse', 'session_id': 'sid-1'}), mail, sessions), '')
        check('  an unnamed session says nothing', H.run(json.dumps({'hook_event_name': 'UserPromptSubmit', 'session_id': 'sid-2'}), mail, sessions), '')
        check('  an unknown session says nothing', H.run(json.dumps({'hook_event_name': 'UserPromptSubmit', 'session_id': 'nope'}), mail, sessions), '')
        check('  bad JSON says nothing', H.run('{', mail, sessions), '')
        check('  the name slug', (H.slug('Snow White'), H.slug("Tim Apple's Mac"), H.slug('')), ('snow-white', 'tim-apple-s-mac', ''))
        check('  unread of a session with no inbox', H.unread('Nobody', mail), [])
        # #641: the arm reminder
        script = os.path.join(d, 'session-mail.py')
        with open(script, 'w', encoding='utf-8') as f:
            f.write('# the arm script\n')
        first = H.arm_reminder('Snow White', mail, now=lambda: 10_000.0, alive=lambda p: False, script=script, has_token=True)
        check('  the positive case: an unarmed session with an inbox here is asked to arm its own pull, with the command',
              (first.startswith('Session mail (#641): your own pull is not armed'), f'python "{script}" wait --for "Snow White"' in first, 'run_in_background' in first),
              (True, True, True))
        check('  not again within the half hour', H.arm_reminder('Snow White', mail, now=lambda: 10_500.0, alive=lambda p: False, script=script, has_token=True), '')
        check('  again after it', H.arm_reminder('Snow White', mail, now=lambda: 20_000.0, alive=lambda p: False, script=script, has_token=True) != '', True)
        with open(os.path.join(inbox, H.ARMED), 'w', encoding='utf-8') as f:
            json.dump({'pid': 4242, 'started': 1.0}, f)
        check('  armed: a marker whose process lives; a dead one is not armed',
              (H.armed('Snow White', mail, alive=lambda p: p == 4242), H.armed('Snow White', mail, alive=lambda p: False), H.armed('Nobody', mail)),
              (True, False, False))
        check('  nothing while armed', H.arm_reminder('Snow White', mail, now=lambda: 9000.0, alive=lambda p: True, script=script, has_token=True), '')
        os.remove(os.path.join(inbox, H.ARMED))
        check('  nothing without the script, a token, or an inbox folder here',
              (H.arm_reminder('Snow White', mail, now=lambda: 9000.0, alive=lambda p: False, script=os.path.join(d, 'gone.py'), has_token=True),
               H.arm_reminder('Snow White', mail, now=lambda: 9000.0, alive=lambda p: False, script=script, has_token=False),
               H.arm_reminder('Nobody', mail, now=lambda: 9000.0, alive=lambda p: False, script=script, has_token=True)),
              ('', '', ''))
        H.ARM_SCRIPT = script
        H.mark_read('Snow White', ['a1', 'a2', 'a3'], mail)   # the cases above left them unread
        with open(os.path.join(inbox, H.REMINDED), 'w', encoding='utf-8') as f:
            json.dump({'at': 0.0}, f)
        got = json.loads(H.run(json.dumps({'hook_event_name': 'SessionStart', 'session_id': 'sid-1'}), mail, sessions, now=lambda: 50_000.0,
                               arm=lambda who, m, now: H.arm_reminder(who, m, now=now, alive=lambda p: False, has_token=True)))
        check('  the reminder rides SessionStart with no mail', ('wait --for "Snow White"' in got['hookSpecificOutput']['additionalContext'], 'Session mail from' in got['hookSpecificOutput']['additionalContext']), (True, False))
        check('  never on PostToolUse', H.run(json.dumps({'hook_event_name': 'PostToolUse', 'session_id': 'sid-1'}), mail, sessions, now=lambda: 90_000.0,
                                             arm=lambda who, m, now: 'should not be asked'), '')
        check('  pid_alive knows this process and not a negative one', (H.pid_alive(os.getpid()), H.pid_alive(-7)), (True, False))
    print(f'\n{len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
