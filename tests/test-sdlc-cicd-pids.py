#!/usr/bin/env python3
"""chuang_sdlc.cicd.pids: a process named by its pid and the start a lock recorded (SMADPickleBot #2159, from
warm-dock.py's #1125 check).

Pinned (the positive case first): this very process is alive, its start read near now, and a lock naming it with that
start is alive; a lock whose recorded start is far off (a reused pid) or whose pid is gone is not, and one with no
recorded start is read by the pid; wait_pid sees a child end (with its code where the system gives one) and times out
on one that sleeps, and never waits on a pid whose start differs from the recorded one; other_process's slack; alive
and stop on Windows go through the runner they are given.

Run:  python -B tests/test-sdlc-cicd-pids.py
"""
import os
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))

from chuang_sdlc.cicd import pids as P  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


me = os.getpid()
began = P.proc_started(me)
check("the positive case: this process is alive, its start read within a day of now, and a lock naming it is alive",
      (P.alive(me), began is not None and abs(time.time() - began) < 86400,
       P.lock_alive({'pid': me, 'pid_started': began})),
      (True, True, True))
check("a lock whose recorded start is far from the pid's (a reused pid), or whose pid is gone, is not alive; one with "
      "no recorded start is read by the pid alone",
      (P.lock_alive({'pid': me, 'pid_started': began - 3600}), P.lock_alive({'pid': 0}),
       P.lock_alive({'pid': me, 'pid_started': None}), P.lock_alive({'pid': 99, 'pid_started': 1.0}, alive=lambda p: False)),
      (False, False, True, False))
check("other_process: both known and further apart than the slack; an unknown side is never another process",
      (P.other_process(100.0, 104.0), P.other_process(100.0, 106.0), P.other_process(None, 1.0),
       P.other_process(1.0, None), P.other_process(100.0, 101.0, slack=0.5)),
      (False, True, False, False, True))

flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
quick = subprocess.Popen([sys.executable, '-c', 'import sys; sys.exit(3)'], creationflags=flags, stdin=subprocess.DEVNULL)
ended, code = P.wait_pid(quick.pid, 20)
quick.wait()
check("wait_pid: a child that exits is seen to end, with its exit code on Windows (a pidfd gives none)",
      (ended, code), (True, 3 if os.name == 'nt' else None))
slow = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], creationflags=flags,
                        stdin=subprocess.DEVNULL)
try:
    slow_start = P.proc_started(slow.pid)
    timed = P.wait_pid(slow.pid, 0.3, started=slow_start)
    reused = P.wait_pid(slow.pid, 20, started=(slow_start or 0) + 3600)
    live = P.lock_alive({'pid': slow.pid, 'pid_started': slow_start})
finally:
    slow.kill()
    slow.wait()
check("wait_pid: times out on a child that sleeps; a recorded start that differs is never waited on; it is alive",
      (timed, reused if slow_start is not None else (True, None), live), ((False, None), (True, None), True))

calls = []


class Ran:
    stdout = 'python.exe   4120 Console   1   9,000 K'


if os.name == 'nt':
    check("alive and stop on Windows run tasklist and taskkill through the runner given",
          (P.alive(4120, run=lambda a: (calls.append(a[0]), Ran())[1]), P.alive(77, run=lambda a: Ran()),
           P.stop(4120, run=lambda a: calls.append(a)), calls),
          (True, False, None, ['tasklist', ['taskkill', '/PID', '4120', '/T', '/F']]))

print()
print(f"{len(fails)} failure(s)" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
