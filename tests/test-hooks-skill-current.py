#!/usr/bin/env python3
"""plugins/gene-hooks/hooks/skill-current.lib.sh (1.20.0; SMADPickleBot #1169, Gene 11/8/26 12:32 AM PT: a session
begun on an older gene-shared followed its stale account-handover skill): pacific-clock.sh's PostToolUse branch, run
through bash as the host runs the hook, on a Skill call's response, against a plugin cache made in a temp folder.
Standard library only; the positive case first.

    python tests/test-hooks-skill-current.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, '..', 'plugins', 'gene-hooks', 'hooks')
CLOCK = os.path.join(HOOKS, 'pacific-clock.sh').replace('\\', '/')
BASH = shutil.which('bash') or 'bash'
FAILS = []


def check(label, got, want):
    print(('PASS  ' if got == want else 'FAIL  ') + label + ('' if got == want else f'\n        got  {got!r}\n        want {want!r}'))
    if got != want:
        FAILS.append(label)


def cache(versions, skill='handover', missing=()):
    """A plugin cache <tmp>/cache/mkt/plug/<version>/skills/<skill>/SKILL.md for each version (none in `missing`)."""
    root = tempfile.mkdtemp()
    plug = os.path.join(root, 'cache', 'mkt', 'plug')
    for v in versions:
        d = os.path.join(plug, v, 'skills', skill)
        os.makedirs(d)
        if v not in missing:
            with open(os.path.join(d, 'SKILL.md'), 'w', encoding='utf-8') as f:
                f.write(f'# {skill} {v}\n')
    return plug


def run(payload):
    """pacific-clock.sh on one PostToolUse payload; its additionalContext."""
    got = subprocess.run([BASH, CLOCK], input=json.dumps(payload), capture_output=True, text=True, encoding='utf-8',
                         timeout=60)
    try:
        return json.loads(got.stdout)['hookSpecificOutput']['additionalContext']
    except (ValueError, KeyError):
        return f'(no JSON: {got.stdout!r} {got.stderr!r})'


def skill_call(base, tool='Skill'):
    return {'hook_event_name': 'PostToolUse', 'tool_name': tool, 'tool_input': {'skill': 'plug:handover'},
            'tool_response': f'Launching skill: plug:handover\nBase directory for this skill: {base}\n\n# handover\n'}


def as_bash(path):
    p = path.replace('\\', '/')
    return f'/{p[0].lower()}{p[2:]}' if len(p) > 1 and p[1] == ':' else p


def test_an_older_copy_is_pointed_at_the_newest():
    plug = cache(['0.8.18', '0.8.29', '0.8.9'])
    ctx = run(skill_call(os.path.join(plug, '0.8.18', 'skills', 'handover')))
    want = f"{as_bash(plug)}/0.8.29/skills/handover/SKILL.md"
    check('the positive case: a skill loaded from 0.8.18 is told the newest installed is 0.8.29 (by version, not by name: '
          '0.8.9 is older) and given its SKILL.md to read instead; the clock stays first',
          (ctx.startswith('Pacific clock: '), "the copy this session loaded at its start, 0.8.18" in ctx,
           'the newest installed is 0.8.29' in ctx, want in ctx), (True, True, True, True))


def test_the_newest_copy_and_other_tools_get_the_clock_alone():
    plug = cache(['0.8.18', '0.8.29'])
    newest = run(skill_call(os.path.join(plug, '0.8.29', 'skills', 'handover')))
    bash = run({'hook_event_name': 'PostToolUse', 'tool_name': 'Bash', 'tool_input': {'command': 'ls'},
                'tool_response': f'Base directory for this skill: {os.path.join(plug, "0.8.18", "skills", "handover")}'})
    check('  the newest copy loaded, and a Bash call quoting an old path, say nothing of skills',
          ('SMADPickleBot #1169' in newest, 'SMADPickleBot #1169' in bash, newest.startswith('Pacific clock: ')),
          (False, False, True))


def test_a_newer_folder_without_the_skill_or_an_odd_path_says_nothing():
    plug = cache(['0.8.18', '0.8.29'], missing=('0.8.29',))
    gone = run(skill_call(os.path.join(plug, '0.8.18', 'skills', 'handover')))
    odd = run(skill_call(os.path.join(tempfile.mkdtemp(), 'not-a-cache')))
    none = run({'hook_event_name': 'PostToolUse', 'tool_name': 'Skill', 'tool_input': {}, 'tool_response': 'nothing'})
    check('  a newer version that lacks the skill, a base directory outside a plugin cache, and a response with no base '
          'directory each leave the clock alone', ['SMADPickleBot #1169' in c for c in (gone, odd, none)],
          [False, False, False])


def test_the_session_start_check_names_a_stale_gene_shared():
    """  plugin-version.sh (1.20.0): the newest installed gene-shared behind a claude-shared checkout beside the project is
    said with the two update commands; equal versions say nothing"""
    root = tempfile.mkdtemp()
    hooks_root = os.path.join(root, 'cache', 'gene-hooks', 'gene-hooks', '1.20.0')
    os.makedirs(os.path.join(hooks_root, '.claude-plugin'))
    with open(os.path.join(hooks_root, '.claude-plugin', 'plugin.json'), 'w') as f:
        f.write('{"version": "1.20.0"}')
    for v in ('0.8.18', '0.8.28'):
        os.makedirs(os.path.join(root, 'cache', 'gene-shared', 'gene-shared', v))
    project = os.path.join(root, 'code', 'proj')
    os.makedirs(project)
    shared = os.path.join(root, 'code', 'claude-shared', 'plugins', 'gene-shared', '.claude-plugin')
    os.makedirs(shared)

    def said(latest):
        with open(os.path.join(shared, 'plugin.json'), 'w') as f:
            f.write(f'{{"version": "{latest}"}}')
        env = {**os.environ, 'CLAUDE_PLUGIN_ROOT': hooks_root.replace('\\', '/'), 'CLAUDE_PROJECT_DIR': project.replace('\\', '/')}
        return subprocess.run([BASH, os.path.join(HOOKS, 'plugin-version.sh').replace('\\', '/')], capture_output=True,
                              text=True, env=env, timeout=30).stdout
    behind, even = said('0.8.29'), said('0.8.28')
    check('  gene-shared 0.8.28 installed against claude-shared 0.8.29 is named with its update; 0.8.28 against 0.8.28 says nothing',
          ('gene-shared 0.8.28 is installed, claude-shared has 0.8.29' in behind,
           'claude plugin update gene-shared@gene-shared' in behind, json.loads(behind)['systemMessage'] != '', even),
          (True, True, True, ''))


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            fn()
    print(f'{len(FAILS)} failure(s)' + (':\n  - ' + '\n  - '.join(FAILS) if FAILS else ''))
    sys.exit(1 if FAILS else 0)
