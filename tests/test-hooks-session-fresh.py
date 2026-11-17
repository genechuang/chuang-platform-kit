#!/usr/bin/env python3
"""plugins/gene-hooks/hooks/session-fresh.lib.sh and stale-skills.py (1.28.0; SMADPickleBot #1665): what a session
cannot see for itself, handed to it on its next prompt. A compacted session's re-stated skills against the newest
installed copy (11/16/26: a session followed its 0.8.18 handover skill while 0.8.31 was installed), and an account
switch since its last prompt (Gene, 11/16/26 10:13 PM PT: "Why do I even need to prompt the hibernating sw to read
bootstrap.md?"). pacific-clock.sh and plugin-version.sh run through bash as the host runs them, against a plugin cache,
a transcript and an app data folder made in a temp folder. Standard library only; the positive case first.

    python tests/test-hooks-session-fresh.py
"""
import json
import os
import shutil
import bounded
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, '..', 'plugins', 'gene-hooks', 'hooks')
CLOCK = os.path.join(HOOKS, 'pacific-clock.sh').replace('\\', '/')
VERSION = os.path.join(HOOKS, 'plugin-version.sh').replace('\\', '/')
STALE = os.path.join(HOOKS, 'stale-skills.py')
BASH = shutil.which('bash') or 'bash'
FAILS = []


def check(label, got, want):
    print(('PASS  ' if got == want else 'FAIL  ') + label + ('' if got == want else f'\n        got  {got!r}\n        want {want!r}'))
    if got != want:
        FAILS.append(label)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def transcript(path, skills):
    """A transcript whose newest invoked_skills attachment re-states `skills` ([(path label, base dir)])."""
    lines = [json.dumps({'type': 'user', 'message': {'content': 'hi'}}),
             json.dumps({'type': 'attachment', 'attachment': {'type': 'invoked_skills', 'skills': [
                 {'name': 'old', 'path': 'plugin:p:old', 'content': 'Base directory for this skill: /nowhere/1.0/skills/old\n'}]}}),
             json.dumps({'type': 'system', 'subtype': 'compact_boundary'}),
             json.dumps({'type': 'attachment', 'attachment': {'type': 'invoked_skills', 'skills': [
                 {'name': os.path.basename(base), 'path': label, 'content': f'Base directory for this skill: {base}\n\n# text\n'}
                 for label, base in skills]}})]
    write(path, '\n'.join(lines) + '\n')


def clock(d, sid, tp, app=None, note=None, extra=None):
    env = dict(os.environ, HOME=d.replace('\\', '/'), CLAUDE_SESSION_FRESH_STATE=os.path.join(d, 'state').replace('\\', '/'),
               CLAUDE_MODEL_CHECK_OFF='1', CLAUDE_SESSION_FRESH_APP=(app or os.path.join(d, 'noapp')).replace('\\', '/'))
    env.pop('LOCALAPPDATA', None)
    if note:
        env['ACCOUNT_SWITCH_NOTE'] = note.replace('\\', '/')
    env.update(extra or {})
    r = bounded.run([BASH, CLOCK], input=json.dumps({'hook_event_name': 'UserPromptSubmit', 'prompt': 'hello',
                                                        'session_id': sid, 'transcript_path': tp}),
                       capture_output=True, text=True, encoding='utf-8', env=env, timeout=30)
    out = r.stdout.strip()
    return json.loads(out)['hookSpecificOutput']['additionalContext'] if out else ''


def start(d, sid, source):
    env = dict(os.environ, HOME=d.replace('\\', '/'), CLAUDE_SESSION_FRESH_STATE=os.path.join(d, 'state').replace('\\', '/'),
               CLAUDE_PLUGIN_ROOT=os.path.join(d, 'noplugin').replace('\\', '/'))
    bounded.run([BASH, VERSION], input=json.dumps({'hook_event_name': 'SessionStart', 'source': source, 'session_id': sid}),
                   capture_output=True, text=True, encoding='utf-8', env=env, timeout=30)
    return os.path.exists(os.path.join(d, 'state', f'stale-{sid}.mark'))


def main():
    d = tempfile.mkdtemp()
    plug = os.path.join(d, 'cache', 'mkt', 'plug')
    for v in ('0.8.18', '0.8.31'):
        write(os.path.join(plug, v, 'skills', 'account-handover', 'SKILL.md'), f'# {v}\n')
    write(os.path.join(plug, '0.8.31', 'skills', 'fresh', 'SKILL.md'), '# fresh\n')
    tp = os.path.join(d, 't.jsonl')
    transcript(tp, [('plugin:gene-shared:account-handover', os.path.join(plug, '0.8.18', 'skills', 'account-handover')),
                    ('plugin:gene-shared:fresh', os.path.join(plug, '0.8.31', 'skills', 'fresh')),
                    ('bundled:x', 'bundled:x')])

    # stale-skills.py itself
    r = bounded.run([sys.executable, STALE, tp], capture_output=True, text=True, timeout=30)
    check('the positive case: stale-skills.py names the re-stated skill older than the newest installed one, and only it',
          (r.returncode, r.stdout.strip()),
          (0, 'plugin:gene-shared:account-handover is the 0.8.18 copy your context carries; 0.8.31 is installed'))
    r = bounded.run([sys.executable, STALE, os.path.join(d, 'missing.jsonl')], capture_output=True, text=True, timeout=30)
    check('  a transcript that cannot be read prints nothing and exits 0', (r.returncode, r.stdout), (0, ''))

    # through the hooks: the compaction's mark, then the prompt
    check('a SessionStart from a compaction marks the session; one from a fresh start does not',
          (start(d, 'sess1', 'compact'), start(d, 'sess2', 'startup')), (True, False))
    ctx = clock(d, 'sess1', tp)
    check('  the next prompt names the stale skill and has the session load it again with the Skill tool, once',
          ('Stale skills' in ctx, 'account-handover is the 0.8.18 copy' in ctx, 'load it again with the Skill tool' in ctx,
           os.path.exists(os.path.join(d, 'state', 'stale-sess1.mark'))), (True, True, True, False))
    check('  the prompt after that says nothing of it', 'Stale skills' in clock(d, 'sess1', tp), False)
    check('  a session with no mark runs no scan', 'Stale skills' in clock(d, 'sess2', tp), False)

    # the account switch
    app = os.path.join(d, 'app')
    write(os.path.join(app, 'config.json'), json.dumps({'lastKnownAccountUuid': 'aaaa-1111'}))
    note = os.path.join(d, 'note.txt')
    write(note, 'Read ../claude-shared/bootstrap.md and follow it.\n')
    first = clock(d, 'snow', tp, app, note)
    check('first sight of an account records it and tells no one', 'Account switch' in first, False)
    write(os.path.join(app, 'config.json'), json.dumps({'lastKnownAccountUuid': 'bbbb-2222'}))
    ctx = clock(d, 'snow', tp, app, note)
    check('the positive case for the switch: the account changed, and the woken session is handed the box\'s note once',
          ('Account switch' in ctx, 'Read ../claude-shared/bootstrap.md and follow it.' in ctx), (True, True))
    check('  its next prompt is quiet', 'Account switch' in clock(d, 'snow', tp, app, note), False)
    check('  another session woken after the same switch is handed it too, once',
          ('Account switch' in clock(d, 'widow', tp, app, note), 'Account switch' in clock(d, 'widow', tp, app, note)), (True, False))
    os.remove(note)
    write(os.path.join(app, 'config.json'), json.dumps({'lastKnownAccountUuid': 'cccc-3333'}))
    ctx = clock(d, 'snow', tp, app, note)
    check('  with no note file the generic line is handed, JSON-safe', ('Account switch' in ctx, "project's instructions" in ctx), (True, True))
    check('  no app data folder: nothing said and nothing fails', clock(d, 'x', tp, os.path.join(d, 'none')).startswith('Pacific clock'), True)

    shutil.rmtree(d, ignore_errors=True)
    print(f'\n{len(FAILS)} failure(s)')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
