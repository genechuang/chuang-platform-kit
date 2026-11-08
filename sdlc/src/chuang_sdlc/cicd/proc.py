"""One command runner for every pipeline script, the plumbing the scripts used to copy (SMADPickleBot, 10/22/26: six
private `_run`s and four `gh` wrappers across seven files, each with its own working folder, encoding and Windows shell
rule; moved here from its pipeline/shared/pipeline_lib.py, #235, 11/8/26, whose module keeps the host's half and
re-exports these names).

    from chuang_sdlc.cicd import proc
    proc.run(['gcloud', ...])            the CompletedProcess, text decoded as UTF-8
    proc.gh(['run', 'list', ...])        `gh <args>` through the kit's throttled github.cli where the kit is present,
                                         the CompletedProcess, never raises
    proc.out(['gh', 'api', ...])         its stripped stdout, '' on any failure (a missing CLI included)
    proc.gh_json('run', 'view', ...)     `gh <args>` parsed as JSON, None on any failure
    proc.json_out([...])                 any command's stdout parsed as JSON, None on any failure
    proc.run_capped(args, limit)         a capture with a time cap that kills the whole process tree
    proc.http_get(url, token)            (status, JSON) for a GET with a bearer, never raises
    proc.primary_root(start)             the primary checkout a worktree belongs to

One rule each, where the copies disagreed:
  - text is always UTF-8 with replacement: three copies decoded with Windows' code page, which garbles or fails on a
    character like an arrow (10/22/26: the same arrow crashed a suite in a Windows console);
  - the Windows shell only for gh and gcloud when they are .cmd shims there (`needs_shell`: gcloud is, gh.exe is not),
    its line quoted for cmd (`cmd_line`: an & in a URL stays in the argument); every other command runs directly;
  - no console window ever opens (claude-shared "No window pops up");
  - the working folder is the caller's own unless it names one (a host's wrapper may default it to its repo root).
Standard library only, so every pipeline (a CI runner, a cloud build step, the desktop) can import it before it has
the kit; the kit's throttled GitHub client is used when it is importable.
"""
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request

def primary_root(start: str, run=None) -> str:
    """The primary checkout every worktree of `start` belongs to: the parent of
    the common git dir, `git rev-parse --git-common-dir`, read from the `gitdir:` pointer in `start`'s `.git` file when git
    cannot say, and `start` itself when neither tells. The pointer matters once a worktree's admin dir is gone (#1078:
    Black Widow's fix worktree was pruned after 17636c7a's push while its waiter still ran there, and the dashboard
    publisher's `git fetch` said "not a git repository"); the files are still there, git's record of them is not. `run`
    takes the git argument list and answers like subprocess.run (the suites' seam)."""
    run = run or (lambda args: subprocess.run(args, capture_output=True, text=True))
    try:
        got = run(['git', '-C', start, 'rev-parse', '--path-format=absolute', '--git-common-dir'])
        common = (got.stdout or '').strip() if got.returncode == 0 else ''
    except OSError:   # no git on the path
        common = ''
    if not common:
        try:
            with open(os.path.join(start, '.git'), encoding='utf-8') as f:
                pointer = f.read().strip()
        except OSError:   # the primary itself (.git is a folder) or no checkout at all
            pointer = ''
        if pointer.startswith('gitdir:'):
            gitdir = pointer.split(':', 1)[1].strip().replace('\\', '/')
            head, sep, _ = gitdir.partition('/.git/')
            common = f'{head}/.git' if sep else ''
    if not common:
        return start
    return os.path.dirname(os.path.normpath(os.path.join(start, common)))


SHIMS = ('gh', 'gcloud')   # .cmd files on Windows, so only the shell finds them
GITHUB_API = 'https://api.github.com'
# The kit's throttled GitHub client (#364, #679), optional: a box without the kit (the Mac mini's Simulator job installs
# nothing) keeps the plain paths below, and scripts/check-workflow-installs.py reads the guard as optional.
try:
    from chuang_platform_kit import github as _github
except ImportError:   # pragma: no cover - the kit is on the desktop and the runners
    _github = None


def http_get(url, token=None, opener=None, timeout=15):
    """(status, parsed JSON body or None) for a GET; a token is sent as a bearer. GitHub's media type is asked for, which
    the bucket ignores. A refusal is its code and None; a network failure is 0 and None; never raises. A GitHub URL goes
    through chuang_platform_kit.github's throttled client (#364, #679), a quota it refuses to spend reading as 429."""
    if _github is not None and url.startswith(GITHUB_API):
        try:
            status, body = _github.request(token or '', 'GET', url, opener=opener, timeout=timeout)
            return status, body if isinstance(body, (dict, list)) else None
        except Exception as e:   # noqa: BLE001 - GitHubError carries the status (0 for no answer); anything else is no answer
            return getattr(e, 'status', 0) or 0, None
    opener = opener or urllib.request.urlopen
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'chuang-sdlc-cicd'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    try:
        with opener(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
            body = r.read()
            try:
                return getattr(r, 'status', 200), json.loads(body or b'null')
            except ValueError:
                return getattr(r, 'status', 200), None
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:   # noqa: BLE001 - a network failure reads as no answer
        return 0, None


NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000) if os.name == 'nt' else 0


def run(args, cwd=None, **kw) -> subprocess.CompletedProcess:
    """Run `args` (a list) and capture it: UTF-8 text, the shell only for a Windows .cmd shim, no window. `cwd` is the caller's
    own folder unless given (a host's wrapper may default it to its repo root); any subprocess.run keyword passes
    through (timeout, env, input, ...)."""
    kw.setdefault('capture_output', True)
    kw.setdefault('text', True)
    kw.setdefault('encoding', 'utf-8')
    kw.setdefault('errors', 'replace')
    if os.name == 'nt':
        kw.setdefault('shell', bool(args) and needs_shell(args[0]))
        kw.setdefault('creationflags', NO_WINDOW)
        if kw['shell'] and isinstance(args, (list, tuple)) and any(CMD_META & set(str(a)) for a in args):
            args = cmd_line(args)
    return subprocess.run(args, cwd=cwd, **kw)


def run_capped(args: list, limit: int, popen=subprocess.Popen, kill=None, **kw) -> subprocess.CompletedProcess:
    """subprocess.run's capture with a time cap that cannot hang: on the cap the whole process tree is killed
    (taskkill /T on Windows, the process group elsewhere) before the pipes are read again, then TimeoutExpired.
    subprocess.run kills only the child it started, and on Windows with shell=True that is cmd.exe: claude's Node
    process kept the pipes open, run's second read waited for ever, and the daemon's desktop-tasks thread took no
    delivery from 4:41 to 5:56 PM PT on 10/27/26 (a wake that ran long while its recipient was busy). runner-listener's
    wake and ship.py's dock-sync suites (#1094) run through it; text, encoding and window flags are the caller's."""
    kw.setdefault('stdout', subprocess.PIPE)
    kw.setdefault('stderr', subprocess.PIPE)
    if os.name != 'nt':
        kw.setdefault('start_new_session', True)
    p = popen(args, **kw)
    try:
        out, err = p.communicate(timeout=limit)
    except subprocess.TimeoutExpired:
        (kill or kill_tree)(p)
        try:
            p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        raise
    return subprocess.CompletedProcess(args, p.returncode, out, err)


def is_link(path: str) -> bool:
    """A junction or a symbolic link: never followed, never deleted through (android_build's rule, 10/19/26). A
    Windows Python without os.path.isjunction (before 3.12) reads the reparse-point attribute of the path itself."""
    if os.path.islink(path):
        return True
    if hasattr(os.path, 'isjunction'):
        return bool(os.path.isjunction(path))
    try:
        attrs = getattr(os.lstat(path), 'st_file_attributes', 0)
    except OSError:
        return False
    return bool(attrs & 0x400)   # FILE_ATTRIBUTE_REPARSE_POINT


def kill_tree(p) -> None:
    """The process and every child of it, killed; a failure is ignored (the cap's own TimeoutExpired is the report)."""
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(p.pid)], capture_output=True, timeout=15,
                           creationflags=NO_WINDOW)
        else:
            os.killpg(p.pid, 9)
    except (OSError, subprocess.SubprocessError):
        p.kill()


_FOUND = {}


def needs_shell(cmd: str) -> bool:
    """Whether `cmd` must go through cmd.exe: one of SHIMS that is not an .exe on this box (gcloud.cmd; gh is gh.exe on
    the desktop, and runs directly). One not found on PATH counts as a shim, as before. Looked up once per process."""
    if cmd not in SHIMS:
        return False
    if cmd not in _FOUND:
        _FOUND[cmd] = shutil.which(cmd) or ''
    return not _FOUND[cmd].lower().endswith('.exe')


CMD_META = set('&|<>^')


def cmd_line(args) -> str:
    """`args` as one cmd.exe command line: each quoted as Windows quotes it (subprocess.list2cmdline), and one holding a
    cmd metacharacter (& | < > ^) double-quoted too, so cmd passes it whole. Python's own join leaves `&` bare, and cmd
    split `gh api .../runs?head_sha=X&per_page=50` into two commands: gh printed the runs, `per_page` failed, the exit
    was 1, and `out` read it as nothing (10/22/26, 33f8fd39's release profile with no runs and no stages). Only a line
    with such a character is built here; any other keeps its list."""
    parts = []
    for a in args:
        q = subprocess.list2cmdline([str(a)])
        parts.append(f'"{q}"' if CMD_META & set(q) and not q.startswith('"') else q)
    return ' '.join(parts)


def gh(args, run=None, check=False, **kw) -> subprocess.CompletedProcess:
    """`gh <args>` (the arguments after `gh`) through chuang_platform_kit.github.cli (#364, #679), the one way a pipeline
    script runs gh: the per-process cap and the spent-quota refusal the kit's HTTP calls share. `run` is this module's
    run unless a caller (or a suite's stand-in) gives its own, and it gets the caller's keywords alone, never the kit's
    defaults (a stand-in may take none; this module's run sets UTF-8 and no timeout itself). Never raises: a quota the
    kit refuses to spend is returncode 429 with its words on stderr, a gh that cannot start 127, so a caller's
    returncode check covers both; `check` raises CalledProcessError for any of them. Without the kit (a box that has
    none, an old one without cli) the same run, unthrottled."""
    runner = run if run is not None else globals()['run']   # this module's run as it is now (a suite may stand in for it)
    argv = ['gh', *[str(a) for a in args]]
    cli = getattr(_github, 'cli', None)
    try:
        if cli is None:
            done = runner(argv, **kw)
        else:
            done = cli(argv[1:], run=lambda a, **_kit: runner(a, **kw))
    except (OSError, subprocess.SubprocessError) as e:
        done = subprocess.CompletedProcess(argv, 127, '', str(e))
    except RuntimeError as e:
        if not isinstance(e, getattr(_github, 'GitHubError', ())):
            raise
        done = subprocess.CompletedProcess(argv, 429, '', str(e))   # the kit's RateLimited: nothing was sent
    if check and getattr(done, 'returncode', 0):
        raise subprocess.CalledProcessError(done.returncode, argv, getattr(done, 'stdout', None), getattr(done, 'stderr', None))
    return done


def out(args, **kw) -> str:
    """The command's stripped stdout when it exits 0, else ''; a command that cannot start (a missing CLI, a timeout)
    is '' too, never an exception. A `gh` command runs through gh() above, the kit's throttled cli, a refusal reading
    as ''."""
    try:
        if isinstance(args, (list, tuple)) and args and args[0] == 'gh':
            kw.setdefault('timeout', 60)   # out's gh calls are reads; one never waits longer (as before #364's B2)
            done = gh(list(args[1:]), **kw)
        else:
            done = run(args, **kw)
    except (OSError, subprocess.SubprocessError):
        return ''
    return (done.stdout or '').strip() if done.returncode == 0 else ''


def parse_json(done):
    """A finished command's stdout parsed as JSON, or None when it failed, printed nothing or printed something else.
    For a caller that runs the command itself (a test may stand in for its runner)."""
    text = (getattr(done, 'stdout', '') or '').strip() if getattr(done, 'returncode', 1) == 0 else ''
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


def json_out(args, **kw):
    """The command's stdout parsed as JSON, or None when it failed, printed nothing or printed something else."""
    text = out(args, **kw)
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


def gh_json(*args, **kw):
    """`gh <args>` parsed as JSON (gh api ..., gh run view --json ...), None on any failure."""
    return json_out(['gh', *args], **kw)
