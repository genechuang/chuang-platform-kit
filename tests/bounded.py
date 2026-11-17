"""The suites' one way to start a child process: a stdin of its own and a timeout that ends.

`run()` is subprocess.run's capture with two differences (SMADPickleBot #1686, 11/17/26):

- **The child never reads the suite's own stdin.** With no `input` and no `stdin` it gets the null device. A hook script
  opens with `input=$(cat)`; started with the suite's stdin, its cat waits for whatever started the suite to close it.
  Run from a terminal or with `< /dev/null` that is at once; run as a background task whose stdin stays open it is
  never (the kit's 0.35.0 release stopped there for 27 minutes).
- **The timeout ends the call.** subprocess.run kills only the child it started and then reads the pipes again with no
  limit; a grandchild that holds a pipe (bash's cat, forked) keeps that read waiting for ever, so the suite's
  `timeout=30` never returned. Here the whole tree is killed first (chuang_sdlc.cicd.proc.kill_tree, the one tree
  kill), the pipes are given ten more seconds, and TimeoutExpired is raised.

tests/test-tests-bounded.py refuses a suite that starts a process any other way.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'sdlc', 'src'))
from chuang_sdlc.cicd.proc import kill_tree  # noqa: E402

DRAIN_SECONDS = 10


def run(args, input=None, timeout=None, capture_output=False, check=False, **kw):
    """subprocess.run(args, ...) as a CompletedProcess, the child on its own stdin and the timeout a real one."""
    if capture_output:
        kw.setdefault('stdout', subprocess.PIPE)
        kw.setdefault('stderr', subprocess.PIPE)
    if input is not None:
        kw['stdin'] = subprocess.PIPE
    else:
        kw.setdefault('stdin', subprocess.DEVNULL)
    if os.name != 'nt':
        kw.setdefault('start_new_session', True)   # its own process group, so kill_tree's killpg takes the tree
    p = subprocess.Popen(args, **kw)
    try:
        out, err = p.communicate(input, timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(p)
        try:
            p.communicate(timeout=DRAIN_SECONDS)
        except (subprocess.TimeoutExpired, ValueError, OSError):
            pass
        raise
    done = subprocess.CompletedProcess(args, p.returncode, out, err)
    if check:
        done.check_returncode()
    return done
