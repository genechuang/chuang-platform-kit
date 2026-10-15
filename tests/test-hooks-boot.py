#!/usr/bin/env python3
"""The plugin's bootstrap loader plugins/gene-hooks/hooks/boot.sh: every hook runs the newest installed version's script.

Run:  python tests/test-hooks-boot.py      (prints PASS/FAIL per case and `N failure(s)`)
"""
import json
import os
import shutil
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(HERE, '..', 'plugins', 'gene-hooks')
BOOT = os.path.join(PLUGIN, 'hooks', 'boot.sh')
failures = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ('' if ok else f'\n        got  {got!r}\n        want {want!r}'))
    if not ok:
        failures.append(label)


def cache(*versions):
    """A fake plugin cache: one directory per version, each with hooks/boot.sh and a hooks/say.sh that prints its
    version, the plugin root it was given and its stdin."""
    root = tempfile.mkdtemp()
    for v in versions:
        hooks = os.path.join(root, v, 'hooks')
        os.makedirs(hooks)
        shutil.copy(BOOT, hooks)
        with open(os.path.join(hooks, 'say.sh'), 'w', newline='\n') as f:
            f.write(f'#!/usr/bin/env bash\necho "{v} ${{CLAUDE_PLUGIN_ROOT##*/}} $1 $(cat)"\n')
    return root


def run(root, loaded, script='say.sh', arg='x', stdin='in'):
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=os.path.join(root, loaded).replace('\\', '/'))
    done = subprocess.run(['bash', os.path.join(root, loaded, 'hooks', 'boot.sh').replace('\\', '/'), script, arg],
                          input=stdin, capture_output=True, text=True, env=env)
    return done.returncode, done.stdout.strip()


def test_runs_the_newest():
    root = cache('0.5.2', '0.5.3', '0.6.0')
    check("the positive case: a session loaded on 0.5.2 runs 0.6.0's script, with stdin, args and the plugin root",
          run(root, '0.5.2'), (0, '0.6.0 0.6.0 x in'))


def test_versions_compare_as_numbers():
    root = cache('0.5.9', '0.5.10')
    check("  0.5.10 is newer than 0.5.9 (numeric, not text)", run(root, '0.5.9'), (0, '0.5.10 0.5.10 x in'))


def test_never_runs_older():
    root = cache('0.4.0', '0.6.0')
    check("  a session on the newest stays on it; an older copy is never chosen", run(root, '0.6.0'), (0, '0.6.0 0.6.0 x in'))


def test_newest_without_the_script():
    root = cache('0.5.3', '0.6.0')
    os.remove(os.path.join(root, '0.6.0', 'hooks', 'say.sh'))
    check("  a newer version that lacks the script is skipped, not a failure", run(root, '0.5.3'), (0, '0.5.3 0.5.3 x in'))


def test_missing_everywhere_and_odd_dirs():
    root = cache('0.5.3')
    os.makedirs(os.path.join(root, 'tmp-abc', 'hooks'))
    with open(os.path.join(root, 'tmp-abc', 'hooks', 'say.sh'), 'w', newline='\n') as f:
        f.write('echo WRONG\n')
    check("  a non-version directory beside it is ignored", run(root, '0.5.3'), (0, '0.5.3 0.5.3 x in'))
    check("  a script missing everywhere exits 0, silent", run(root, '0.5.3', script='nope.sh'), (0, ''))


def test_no_script_finds_itself_by_dollar_zero():
    src = {n: open(os.path.join(PLUGIN, 'hooks', n), encoding='utf-8').read()
           for n in os.listdir(os.path.join(PLUGIN, 'hooks')) if n.endswith('.sh') and n != 'boot.sh'}
    check("  no hook script locates itself with $0 (boot.sh sources it, so $0 is the loader's)",
          [n for n, s in src.items() if 'dirname "$0"' in s or "dirname '$0'" in s], [])


def test_every_hook_goes_through_it():
    with open(os.path.join(PLUGIN, 'hooks', 'hooks.json'), encoding='utf-8') as f:
        hooks = json.load(f)['hooks']
    commands = [h['command'] for groups in hooks.values() for g in groups for h in g['hooks']]
    check("  every hook in hooks.json runs through boot.sh, and every script it names exists",
          (all('/hooks/boot.sh" ' in c for c in commands),
           all(os.path.isfile(os.path.join(PLUGIN, 'hooks', c.split()[-1])) for c in commands)), (True, True))


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            fn()
    print(f'{len(failures)} failure(s)')
