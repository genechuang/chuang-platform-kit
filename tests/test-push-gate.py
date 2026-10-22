#!/usr/bin/env python3
"""chuang_platform_kit.push_gate: a pre-push gate over a repo's .push-gate.toml, proven on real git repos in a
temporary folder (a bare remote, the pushing clone and a second clone that moves main).

Pinned (the positive case first): a push touching a check's paths runs it and goes; a check whose mark answers is
not run; a failing check refuses with the override named; a skip code passes; a push outside `gate` goes unchecked
and is told which marks are missing; a head behind the remote's main is refused before any check; a main that moved
during the checks is rebased onto, the untouched check's mark carried and the touched one run again, and the push
refused with "push again, the gate is satisfied"; a conflict, a dirty checkout and a push of another ref are refused
with nothing rewritten; the override skips it all; the config refuses an unknown key; and a real `git push` through
the hook loses the race once and clears on the second push.

Run:  python -B tests/test-push-gate.py
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import push_gate as G  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


ENV = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.test', GIT_COMMITTER_NAME='t',
           GIT_COMMITTER_EMAIL='t@example.test', GIT_CONFIG_NOSYSTEM='1')
ENV.pop('PUSH_GATE_UNCHECKED', None)
# The gate's own git calls (its rebase commits) read the process environment: a CI runner has no git identity.
os.environ.update({k: v for k, v in ENV.items() if k.startswith('GIT_')})


def git(cwd, *args, ok=True):
    r = subprocess.run(['git', '-c', 'core.autocrlf=false', '-c', 'init.defaultBranch=main', *args], cwd=cwd,
                       capture_output=True, text=True, env=ENV)
    if ok and r.returncode:
        raise RuntimeError(f'git {" ".join(args)}: {r.stderr}')
    return r.stdout.strip()


def write(repo, path, text):
    full = os.path.join(repo, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)


def commit(repo, path, text, msg):
    write(repo, path, text)
    git(repo, 'add', path)
    git(repo, 'commit', '-q', '-m', msg)
    return git(repo, 'rev-parse', 'HEAD')


class World:
    """A bare remote, the pushing clone `work` and a second clone `other`, main at one commit."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix='push-gate-test-')
        self.remote = os.path.join(self.dir, 'remote.git')
        git(self.dir, 'init', '-q', '--bare', '-b', 'main', self.remote)
        self.work = os.path.join(self.dir, 'work')
        git(self.dir, 'clone', '-q', self.remote, self.work)
        commit(self.work, 'src/a.py', 'a = 1\n', 'first')
        commit(self.work, 'docs/readme.md', 'hello\n', 'docs')
        git(self.work, 'push', '-q', 'origin', 'HEAD:main')
        self.other = os.path.join(self.dir, 'other')
        git(self.dir, 'clone', '-q', self.remote, self.other)

    def base(self):
        return git(self.work, 'rev-parse', 'origin/main')

    def move_main(self, path, text, msg):
        git(self.other, 'pull', '-q', '--rebase', 'origin', 'main')
        sha = commit(self.other, path, text, msg)
        git(self.other, 'push', '-q', 'origin', 'HEAD:main')
        return sha

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def cfg(**top):
    checks = top.pop('checks', None) or [G.Check(name='tests', run=['{python}', 'tests', '{sha}'], paths=['src/**'],
                                                  mark_url='https://marks.test/{tree}', carry=['carry', '{old}', '{new}'],
                                                  missing='has no test mark')]
    return G.Config(checks=checks, **top)


def gate(world, config, codes=None, marks=(), on_run=None, steps=None, mark_secs=0.0):
    """A Gate over `world.work` whose commands answer from `codes` (by argv[1], default 0) and record themselves.
    Its clock is fake: a command moves it on by `steps` (by argv[1]), a mark URL's ask by `mark_secs`, git never."""
    said, ran, now = [], [], [0.0]

    def execute(argv):
        ran.append(argv)
        if on_run:
            on_run(argv)
        key = argv[1] if len(argv) > 1 else argv[0]
        now[0] += (steps or {}).get(key, 0.0)
        return (codes or {}).get(key, 0)

    def head(url):
        now[0] += mark_secs
        return 200 if any(url.endswith(m) for m in marks) else 404
    g = G.Gate(world.work, config, say=said.append, env={}, execute=execute, head=head, clock=lambda: now[0])
    return g, said, ran


def has(said, text):
    return any(text in s for s in said)


# 1. The positive case: a push touching the check's paths runs it, main has not moved, the push goes.
w = World()
try:
    base = w.base()
    sha = commit(w.work, 'src/a.py', 'a = 2\n', 'change a')
    g, said, ran = gate(w, cfg())
    rc = g.ref(sha, base)
    check("a push touching src/ runs the check and goes", (rc, [a[1] for a in ran], has(said, 'has not moved')),
          (0, ['tests'], True))
    check("  the check's argv has its placeholders filled", (ran[0][0] == sys.executable, ran[0][2]), (True, sha))

    # 1b. Timing: every check says its seconds and how it ended; each ref ends with the split by phase.
    g, said, ran = gate(w, cfg(), steps={'tests': 4.1}, mark_secs=0.3)
    check("a check that runs says its seconds, its mark's ask included; the ref ends with the split",
          (g.ref(sha, base), 'tests 4.4 s (ran)' in said, said[-1]),
          (0, True, 'checks 4.4 s, rebase check 0.0 s, base check 0.0 s'))
    check("  seconds only, never minutes", [s for s in said if 'min' in s or re.search(r'\d+m\b', s)], [])
    tree = git(w.work, 'rev-parse', 'HEAD^{tree}')
    g, said, ran = gate(w, cfg(), marks=[tree], mark_secs=0.3)
    check("  a check skipped on its mark says the mark's seconds",
          (g.ref(sha, base), 'tests 0.3 s (mark answered)' in said, said[-1]),
          (0, True, 'checks 0.3 s, rebase check 0.0 s, base check 0.0 s'))
    g, said, ran = gate(w, cfg(), codes={'tests': 1}, steps={'tests': 2.0})
    check("  a failed check says its seconds and exit, and the split still ends the ref",
          (g.ref(sha, base), 'tests 2.0 s (failed, exit 1)' in said, said[-1]),
          (1, True, 'checks 2.0 s, rebase check 0.0 s, base check 0.0 s'))
    g, said, ran = gate(w, cfg(checks=[G.Check(name='app', run=['x', 'app'], skip_codes=[2])]), codes={'app': 2},
                        steps={'app': 0.5})
    check("  a skip code says its seconds", (g.ref(sha, base), 'app 0.5 s (skipped, exit 2)' in said), (0, True))
    check("  the real clock is time.monotonic", G.Gate(w.work, cfg()).clock, G.time.monotonic)

    # 2. Its mark answers: not run.
    tree = git(w.work, 'rev-parse', 'HEAD^{tree}')
    g, said, ran = gate(w, cfg(), marks=[tree])
    check("a check whose mark answers is not run", (g.ref(sha, base), ran, has(said, 'already done for')), (0, [], True))

    # 3. A failing check refuses, naming the override; a skip code passes.
    g, said, ran = gate(w, cfg(), codes={'tests': 1})
    check("a failing check refuses the push, the override named", (g.ref(sha, base), has(said, 'tests refused the push'),
                                                                     has(said, 'PUSH_GATE_UNCHECKED=1 to push anyway')), (1, True, True))
    skip = cfg(checks=[G.Check(name='app', run=['x', 'app'], skip_codes=[2])])
    g, said, ran = gate(w, skip, codes={'app': 2})
    check("  a skip code is not a failure", (g.ref(sha, base), has(said, 'not applicable here')), (0, True))

    # 4. A push touching nothing `gate` guards goes unchecked, told about a missing mark.
    git(w.work, 'reset', '-q', '--hard', base)
    doc = commit(w.work, 'docs/readme.md', 'hi\n', 'docs only')
    g, said, ran = gate(w, cfg(gate=['src/**']))
    check("a push outside `gate` goes unchecked and is told the mark is missing",
          (g.ref(doc, base), ran, has(said, 'goes unchecked'), has(said, f'{doc[:8]} has no test mark')), (0, [], True, True))
    g, said, ran = gate(w, cfg())
    check("  inside the gate, a check whose paths the push misses is skipped", (g.ref(doc, base), ran, has(said, 'touches none of its paths')),
          (0, [], True))

    # 5. A head behind the remote's main is refused before any check.
    git(w.work, 'reset', '-q', '--hard', base)
    sha = commit(w.work, 'src/a.py', 'a = 3\n', 'a three')
    w.move_main('docs/other.md', 'x\n', 'someone else')
    g, said, ran = gate(w, cfg())
    check("a head that does not descend from the fetched main is refused first, no check run",
          (g.ref(sha, base), ran, has(said, 'main moved (') and has(said, 'git fetch origin && git rebase origin/main')), (1, [], True))
finally:
    w.close()

# 6. Main moves during the checks: rebased, the untouched check carried, the touched one run again.
w = World()
try:
    base = w.base()
    sha = commit(w.work, 'src/a.py', 'a = 4\n', 'a four')
    two = [G.Check(name='tests', run=['x', 'tests'], paths=['src/**'], carry=['x', 'carry', '{old}', '{new}']),
           G.Check(name='docs', run=['x', 'docs'], paths=['docs/**'], when=['**'])]
    moved = []
    g, said, ran = gate(w, cfg(checks=two), on_run=lambda a: moved or moved.append(w.move_main('docs/new.md', 'n\n', 'race')),
                        steps={'tests': 3.0, 'docs': 1.5, 'carry': 0.2})
    rc = g.ref(sha, base)
    head = git(w.work, 'rev-parse', 'HEAD')
    check("main moved during the checks: rebased, refused with 'push again, the gate is satisfied'",
          (rc, has(said, f'rebased onto {moved[0][:8]}'), has(said, 'push again, the gate is satisfied'),
           git(w.work, 'merge-base', '--is-ancestor', moved[0], head, ok=False) == '' and head != sha), (1, True, True, True))
    check("  the check the rebase did not touch is carried, the touched one runs again",
          ([a[1] for a in ran], ran[2][2:], has(said, 'docs: the rebase changed 1 of its paths (docs/new.md)')),
          (['tests', 'docs', 'carry', 'docs'], [sha, head], True))
    check("  the rebase's carry and rerun say their seconds, and count as the rebase check",
          ('tests 0.2 s (mark carried)' in said, 'docs 1.5 s (ran again)' in said, said[-1]),
          (True, True, 'checks 4.5 s, rebase check 1.7 s, base check 0.0 s'))
    g, said, ran = gate(w, cfg(checks=two))
    check("  the second push of the rebased head clears", (g.ref(head, moved[0]), has(said, 'has not moved')), (0, True))

    # 7. A carry that refuses runs the check again.
    base = git(w.work, 'rev-parse', 'HEAD')
    sha = commit(w.work, 'src/a.py', 'a = 5\n', 'a five')
    moved = []
    g, said, ran = gate(w, cfg(checks=two[:1]), codes={'carry': 1},
                        on_run=lambda a: moved or moved.append(w.move_main('docs/n2.md', 'n\n', 'race 2')))
    check("  a carry that refuses runs the check again",
          (g.ref(sha, base), [a[1] for a in ran], has(said, 'its carry refused')), (1, ['tests', 'carry', 'tests'], True))
finally:
    w.close()

# 8. A conflicting rebase is aborted, nothing rewritten; a dirty checkout and another ref are refused.
w = World()
try:
    base = w.base()
    sha = commit(w.work, 'src/a.py', 'a = 6\n', 'a six')
    moved = []
    g, said, ran = gate(w, cfg(), on_run=lambda a: moved or moved.append(w.move_main('src/a.py', 'a = 7\n', 'conflict')))
    check("a conflicting rebase is aborted with nothing rewritten",
          (g.ref(sha, base), git(w.work, 'rev-parse', 'HEAD'), has(said, 'conflicts; nothing was rewritten')), (1, sha, True))
    git(w.work, 'reset', '-q', '--hard', moved[0])
    base = moved[0]
    sha = commit(w.work, 'src/a.py', 'a = 8\n', 'a eight')
    moved = []

    def dirty(a):
        if not moved:
            moved.append(w.move_main('docs/d.md', 'd\n', 'race 3'))
            write(w.work, 'src/a.py', 'uncommitted\n')
    g, said, ran = gate(w, cfg(), on_run=dirty)
    check("  a dirty checkout is not rebased", (g.ref(sha, base), git(w.work, 'rev-parse', 'HEAD'), has(said, 'uncommitted changes, so nothing was rebased')),
          (1, sha, True))
    git(w.work, 'checkout', '-q', '--', 'src/a.py')
    git(w.work, 'reset', '-q', '--hard', moved[0])
    first = commit(w.work, 'src/a.py', 'a = 9\n', 'nine')
    commit(w.work, 'src/b.py', 'b\n', 'b')
    g, said, ran = gate(w, cfg())
    check("  a push of a commit that is not HEAD is refused when a check would run",
          (g.ref(first, moved[0]), ran, has(said, "is not this checkout's HEAD")), (1, [], True))
finally:
    w.close()

# 9. run(): only the guarded branch, never a delete; the override skips everything.
w = World()
try:
    base = w.base()
    sha = commit(w.work, 'src/a.py', 'a = 10\n', 'ten')
    said, ran = [], []
    seams = dict(say=said.append, execute=lambda a: ran.append(a) or 0, head=lambda u: 404)
    rc = G.run([f'refs/heads/x {sha} refs/heads/feature {base}', f'(delete) {G.ZERO} refs/heads/main {base}'],
               w.work, cfg(), env={}, **seams)
    check("run(): another branch and a delete are not gated", (rc, ran), (0, []))
    rc = G.run([f'refs/heads/main {sha} refs/heads/main {base}'], w.work, cfg(), env={'PUSH_GATE_UNCHECKED': '1'}, **seams)
    check("  the override lets the push go unchecked", (rc, ran, has(said, 'PUSH_GATE_UNCHECKED=1: the push goes unchecked')), (0, [], True))
finally:
    w.close()

# 10. The config and the globs.
d = tempfile.mkdtemp(prefix='push-gate-cfg-')
try:
    p = os.path.join(d, '.push-gate.toml')
    with open(p, 'w', encoding='utf-8') as f:
        f.write('override = "MY_UNCHECKED"\ngate = ["app/**"]\n[[check]]\nname = "t"\nrun = ["{python}", "t.py"]\n'
                'paths = ["**", "!app/**"]\nmark_url = "https://m/{tree}"\nskip_codes = [2]\n')
    c = G.load(p)
    check("load(): the top keys and a check", (c.override, c.gate, c.branch, c.checks[0].name, c.checks[0].skip_codes), ('MY_UNCHECKED', ['app/**'], 'main', 't', [2]))
    with open(p, 'w', encoding='utf-8') as f:
        f.write('[[check]]\nname = "t"\nrun = ["x"]\nmakr = ["typo"]\n')
    try:
        G.load(p)
        got = 'loaded'
    except ValueError as e:
        got = 'makr' in str(e)
    check("  an unknown key is refused, never a check quietly off", got, True)
finally:
    shutil.rmtree(d, ignore_errors=True)
check("matches(): ** any depth, * within one folder, ! excludes, a trailing / is the folder",
      (G.matches(['**', '!app/**'], 'src/x/y.py'), G.matches(['**', '!app/**'], 'app/a.ts'), G.matches(['src/*.py'], 'src/x/y.py'),
       G.matches(['src/**/*.py'], 'src/y.py'), G.matches(['app/'], 'app/x/y'), G.matches(['req.txt'], 'a/req.txt')),
      (True, False, False, True, True, False))

# 11. A real `git push` through a hook that calls the module: the race lost once, the second push clears.
w = World()
try:
    hooks = os.path.join(w.work, '.githooks')
    write(w.work, '.githooks/pre-push', f'#!/bin/sh\nexec "{sys.executable.replace(os.sep, "/")}" -m chuang_platform_kit.push_gate "$@"\n')
    os.chmod(os.path.join(hooks, 'pre-push'), 0o755)
    marks = os.path.join(w.dir, 'marks').replace(os.sep, '/')
    os.makedirs(marks)
    script = os.path.join(w.dir, 'tests.py').replace(os.sep, '/')
    with open(script, 'w', encoding='utf-8') as f:
        f.write('import os, subprocess, sys\n'
                f'open(os.path.join({marks!r}, sys.argv[1]), "w").close()\n'
                f'flag = os.path.join({marks!r}, "raced")\n'
                'if not os.path.exists(flag):\n'
                '    open(flag, "w").close()\n'
                f'    o = {w.other!r}\n'
                '    subprocess.run(["git", "-C", o, "pull", "-q", "--rebase", "origin", "main"], check=True)\n'
                '    open(os.path.join(o, "docs", "race.md"), "w").write("r\\n")\n'
                '    subprocess.run(["git", "-C", o, "add", "docs/race.md"], check=True)\n'
                '    subprocess.run(["git", "-C", o, "commit", "-q", "-m", "race"], check=True)\n'
                '    subprocess.run(["git", "-C", o, "push", "-q", "origin", "HEAD:main"], check=True)\n')
    marked = f'import os, sys; sys.exit(0 if os.path.exists(os.path.join({marks!r}, sys.argv[1])) else 1)'
    write(w.work, '.push-gate.toml',
          'override = "DEMO_UNCHECKED"\n[[check]]\nname = "tests"\n'
          f'run = ["{{python}}", "{script}", "{{tree}}"]\npaths = ["src/**"]\n'
          f'mark = ["{{python}}", "-c", "{marked}", "{{tree}}"]\n'
          f'carry = ["{{python}}", "{script}", "{{tree}}"]\n')
    git(w.work, 'add', '.githooks', '.push-gate.toml')
    git(w.work, 'commit', '-q', '-m', 'the gate')
    commit(w.work, 'src/a.py', 'a = 11\n', 'eleven')
    git(w.work, 'config', 'core.hooksPath', '.githooks')
    env = dict(ENV, PYTHONPATH=os.path.join(ROOT, 'src'))
    first = subprocess.run(['git', 'push', 'origin', 'HEAD:main'], cwd=w.work, capture_output=True, text=True, env=env)
    out1 = first.stdout + first.stderr
    second = subprocess.run(['git', 'push', 'origin', 'HEAD:main'], cwd=w.work, capture_output=True, text=True, env=env)
    out2 = second.stdout + second.stderr
    check("a real push loses the race: refused, rebased, 'push again, the gate is satisfied'",
          (first.returncode != 0, 'push again, the gate is satisfied' in out1, 'tests: running' in out1), (True, True, True))
    check("  the second push clears on the mark and lands",
          (second.returncode, 'already done for' in out2, git(w.work, 'rev-parse', 'HEAD') == git(w.remote, 'rev-parse', 'main')),
          (0, True, True))
    split = r'push-gate: checks \d+\.\d s, rebase check \d+\.\d s, base check \d+\.\d s'
    check("  a real push prints each check's seconds and the split, on the real clock",
          (bool(re.search(r'push-gate: tests \d+\.\d s \(ran\)', out1)), bool(re.search(split, out1)),
           bool(re.search(r'push-gate: tests \d+\.\d s \(mark answered\)', out2)), bool(re.search(split, out2))),
          (True, True, True, True))
    if second.returncode:
        print(out1, out2)
finally:
    w.close()

print(f"\n{len(fails)} failure(s)" + (': ' + '; '.join(fails) if fails else ''))
sys.exit(1 if fails else 0)
