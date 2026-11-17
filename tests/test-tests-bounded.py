#!/usr/bin/env python3
"""tests/bounded.py, the suites' one way to start a child process (SMADPickleBot #1686, 11/17/26).

The kit's 0.35.0 release stopped for 27 minutes on a hook suite: it started plugin-version.sh, whose first line reads
stdin, with the suite's own stdin, which a background task never closes; and the suite's timeout=30 never returned,
since subprocess.run kills only the child it started and then reads the pipes with no limit while bash's forked cat
still holds them.

Pinned: a child with no input reads an empty stdin, never the suite's; `input` reaches it; a timeout ends the call
even when a grandchild holds the pipes, and that grandchild is gone; `check` raises on a non-zero exit; and no suite
starts a process any other way (subprocess.run and its cousins are refused, a Popen must name its stdin).
"""
import ast
import glob
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bounded  # noqa: E402

FAILURES = []
RUNNERS = ('run', 'check_output', 'check_call', 'call', 'getoutput', 'getstatusoutput')


def eq(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ('' if ok else f"\n        got:  {got!r}\n        want: {want!r}"))
    if not ok:
        FAILURES.append(label)


def unbounded(source):
    """The line numbers in `source` (Python text) where a process is started outside bounded.run."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = getattr(node.func.value, 'id', '')
        if owner == 'subprocess' and node.func.attr in RUNNERS:
            found.append(node.lineno)
        elif owner == 'subprocess' and node.func.attr == 'Popen' and 'stdin' not in {k.arg for k in node.keywords}:
            found.append(node.lineno)
        elif owner == 'os' and node.func.attr in ('system', 'popen'):
            found.append(node.lineno)
    return sorted(found)


def test_the_check_finds_a_bare_subprocess_run():
    """the positive case: subprocess.run, a Popen with no stdin and os.system are each found; bounded.run and a Popen
    that names its stdin are not"""
    eq('found', unbounded('import subprocess, os, bounded\n'
                          'subprocess.run(["a"])\n'
                          'subprocess.Popen(["b"])\n'
                          'os.system("c")\n'
                          'bounded.run(["d"])\n'
                          'subprocess.Popen(["e"], stdin=subprocess.PIPE)\n'
                          'subprocess.check_output(["f"], stdin=subprocess.DEVNULL)\n'), [2, 3, 4, 7])


def test_a_child_reads_an_empty_stdin_of_its_own():
    """with no input the child's stdin is the null device: it reads nothing and ends, whatever the suite's stdin is"""
    read, write = os.pipe()   # an open stdin nobody writes to, as a background task's is
    keep = os.dup(0)
    os.dup2(read, 0)
    try:
        began = time.monotonic()
        done = bounded.run([sys.executable, '-c', 'import sys; print(len(sys.stdin.read()))'], capture_output=True,
                           text=True, timeout=20)
        took = time.monotonic() - began
    finally:
        os.dup2(keep, 0)
        for fd in (keep, read, write):
            os.close(fd)
    eq('  it read nothing and ended', (done.returncode, done.stdout.strip(), took < 15), (0, '0', True))


def test_input_reaches_the_child():
    """`input` is the child's stdin, and its output and exit code come back as subprocess.run's do"""
    done = bounded.run([sys.executable, '-c', 'import sys; sys.stdout.write(sys.stdin.read().upper()); sys.exit(3)'],
                       input='abc', capture_output=True, text=True, timeout=20)
    eq('  echoed', (done.returncode, done.stdout, done.stderr), (3, 'ABC', ''))


def test_a_timeout_ends_the_call_and_the_tree():
    """a child whose own child holds the pipes: the call raises TimeoutExpired soon after its timeout, and the
    grandchild is killed with it"""
    with tempfile.TemporaryDirectory() as d:
        mark = os.path.join(d, 'still-alive').replace('\\', '/')
        grand = f"import time; time.sleep(8); open({mark!r}, 'w').close()"
        child = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {grand!r}]); time.sleep(60)"
        began = time.monotonic()
        try:
            bounded.run([sys.executable, '-c', child], capture_output=True, text=True, timeout=2)
            raised = False
        except subprocess.TimeoutExpired:
            raised = True
        took = time.monotonic() - began
        eq('  raised within the drain', (raised, took < 2 + bounded.DRAIN_SECONDS + 5), (True, True))
        time.sleep(max(0, 10 - took))   # past the moment the grandchild would have written its mark
        eq('  the grandchild never finished', os.path.exists(mark), False)


def test_check_raises_on_a_failed_child():
    """check=True raises CalledProcessError for a non-zero exit, and nothing for zero"""
    try:
        bounded.run([sys.executable, '-c', 'import sys; sys.exit(4)'], check=True, timeout=20)
        code = None
    except subprocess.CalledProcessError as e:
        code = e.returncode
    eq('  raised with the code', code, 4)
    eq('  zero passes', bounded.run([sys.executable, '-c', 'pass'], check=True, timeout=20).returncode, 0)


def test_no_suite_starts_a_process_any_other_way():
    """every file under tests/ and sdlc/tests/ but bounded.py itself starts its processes through bounded.run"""
    root = os.path.abspath(os.path.join(HERE, '..'))
    files = sorted(glob.glob(os.path.join(root, 'tests', '*.py')) + glob.glob(os.path.join(root, 'sdlc', 'tests', '*.py')))
    bad = []
    for path in files:
        if os.path.basename(path) == 'bounded.py':
            continue
        with open(path, encoding='utf-8') as f:
            bad += [f'{os.path.relpath(path, root).replace(os.sep, "/")}:{n}' for n in unbounded(f.read())]
    eq('  suites read', len(files) > 50, True)
    eq('  none starts a process outside bounded.run (use bounded.run, or give a Popen its stdin)', bad, [])


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            print(fn.__doc__.strip().splitlines()[0] if fn.__doc__ else name)
            fn()
    print(f'{len(FAILURES)} failure(s)')
    sys.exit(1 if FAILURES else 0)
