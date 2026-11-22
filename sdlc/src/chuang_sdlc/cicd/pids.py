"""A process another one did not start, named by its pid and the start a lock recorded for it (SMADPickleBot #1125,
moved from its pipeline/desktop/warm-dock.py by #2159): whether it still runs, whether that pid still names the same
process, a wait on it until it ends (never a sleep-and-check loop) with its exit code where the system gives one, and
a stop of it with every process it started.

    from chuang_sdlc.cicd import pids
    pids.lock_alive({'pid': 4120, 'pid_started': 1763775000.2})   the process a lock names still runs
    pids.proc_started(4120)                                       its start, epoch seconds, None when gone
    pids.wait_pid(4120, 30, started=1763775000.2)                 (ended, code) after at most 30 s
    pids.stop(4120)                                               it and its tree

A pid the system gave another program after a reboot or a kill is never taken for the process a lock recorded: its
start differs from the recorded one by more than PID_SLACK_S. Windows reads the start and waits through kernel32
(GetProcessTimes, WaitForSingleObject, GetExitCodeProcess); Linux through /proc and a pidfd. Standard library only.
"""
import os
import select

from . import proc

__all__ = ['PID_SLACK_S', 'alive', 'stop', 'proc_started', 'other_process', 'wait_pid', 'lock_alive']

# How far a process's own start may sit from the start a lock recorded for it and still be that process: the lock
# writes it a few milliseconds after the process began, and a pid reused after a reboot or a kill began later.
PID_SLACK_S = 5
_SYNCHRONIZE, _QUERY_LIMITED = 0x00100000, 0x1000   # OpenProcess rights; PROCESS_QUERY_LIMITED_INFORMATION
_FILETIME_EPOCH = 11644473600   # seconds from 1601-01-01 (a FILETIME's zero) to 1970-01-01


def alive(pid: int, run=None) -> bool:
    """Whether a process with this pid runs (Windows: tasklist through `run`, proc.run by default; else signal 0)."""
    if os.name == 'nt':
        got = (run or proc.run)(['tasklist', '/FI', f'PID eq {pid}', '/NH'])
        return str(pid) in (got.stdout or '')
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def stop(pid: int, run=None) -> None:
    """The process and every process it started (Windows: taskkill /T /F through `run`; else its process group)."""
    if os.name == 'nt':
        (run or proc.run)(['taskkill', '/PID', str(pid), '/T', '/F'])
    else:
        try:
            os.killpg(os.getpgid(pid), 15)
        except OSError:
            pass


def _kernel32():
    """kernel32 with the argument types of the process calls below (Windows only)."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    k32.GetExitCodeProcess.restype = wintypes.BOOL
    k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    k32.GetProcessTimes.restype = wintypes.BOOL
    k32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    return k32


def _win_started(k32, handle):
    """An open process handle's creation time in epoch seconds (GetProcessTimes), or None."""
    import ctypes
    from ctypes import wintypes
    times = [wintypes.FILETIME() for _ in range(4)]
    if not k32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
        return None
    return ((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime) / 1e7 - _FILETIME_EPOCH


def _linux_started(pid: int):
    """Process `pid`'s start in epoch seconds from /proc (its stat's starttime after the boot's btime), or None."""
    try:
        with open(f'/proc/{int(pid)}/stat', encoding='utf-8') as f:
            ticks = int(f.read().rsplit(')', 1)[1].split()[19])   # field 22, starttime; the name may hold spaces
        with open('/proc/stat', encoding='utf-8') as f:
            btime = next(int(line.split()[1]) for line in f if line.startswith('btime '))
        return btime + ticks / os.sysconf('SC_CLK_TCK')
    except (OSError, ValueError, IndexError, StopIteration):
        return None


def proc_started(pid: int):
    """When process `pid` started, in epoch seconds; None when it is gone or this system cannot say."""
    if os.name == 'nt':
        k32 = _kernel32()
        handle = k32.OpenProcess(_QUERY_LIMITED, 0, int(pid))
        if not handle:
            return None
        try:
            return _win_started(k32, handle)
        finally:
            k32.CloseHandle(handle)
    return _linux_started(pid) if os.path.isdir('/proc') else None


def other_process(got, recorded, slack: float = PID_SLACK_S) -> bool:
    """Whether a process that started at `got` is not the one a lock recorded as starting at `recorded`: both known and
    further apart than `slack` seconds."""
    return got is not None and recorded is not None and abs(float(got) - float(recorded)) > slack


def wait_pid(pid: int, seconds: float, started=None) -> tuple:
    """Wait on a process this one did not start until it ends or `seconds` pass. (ended, code): (True, its exit code)
    once it ended, the code read from the very handle the wait held, so no writer of the lock can race it (Windows);
    (True, None) when it ended with no code to read (Linux: a pidfd gives none for a process that is not our child),
    when it was already gone, or when `started` (the start the lock recorded) says the pid now names another process,
    which is never waited on; (False, None) when the time ran out first; (None, None) when this system gives no way to
    wait on it (the caller then stops it)."""
    seconds = max(0.0, float(seconds))
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        k32 = _kernel32()
        handle = k32.OpenProcess(_SYNCHRONIZE | _QUERY_LIMITED, 0, int(pid))
        if not handle:   # ERROR_INVALID_PARAMETER: no such process
            return (True, None) if ctypes.get_last_error() == 87 else (None, None)
        try:
            if other_process(_win_started(k32, handle), started):
                return True, None
            got = k32.WaitForSingleObject(handle, min(int(seconds * 1000), 0xFFFFFFFE))
            if got == 0x102:   # WAIT_TIMEOUT
                return False, None
            if got != 0:   # not WAIT_OBJECT_0
                return None, None
            code = wintypes.DWORD()
            ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
            return True, (int(code.value) if ok and code.value != 259 else None)   # 259: STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    if not hasattr(os, 'pidfd_open'):
        return None, None
    try:
        fd = os.pidfd_open(int(pid))
    except ProcessLookupError:
        return True, None
    except OSError:
        return None, None
    try:   # the pidfd holds the pid, so the start read now is that process's
        if other_process(_linux_started(pid), started):
            return True, None
        return (True, None) if select.select([fd], [], [], seconds)[0] else (False, None)
    finally:
        os.close(fd)


def lock_alive(lock: dict, alive=alive, started_at=None) -> bool:
    """Whether the process a lock names ({pid, pid_started}) still runs: its pid alive and, when the lock recorded its
    start, the process under that pid the one that started then, so a stale lock's pid that the system has since given
    another program is never taken for it, waited on or stopped. A lock with no recorded start is read by the pid."""
    pid = lock.get('pid')
    if not pid or not alive(int(pid)):
        return False
    if lock.get('pid_started') is None:
        return True
    return not other_process((started_at or proc_started)(int(pid)), lock['pid_started'])
