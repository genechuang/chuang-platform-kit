"""A triage pass on a box: one headless Claude run that investigates and triages a project's untriaged issues, asked for
over the host's bus and run by a listener on the box, one pass at a time, bounded and logged.

  request          one `triage.wake_requested` event published (the kit's pubsub), never raising: a refused publish
                   is the line, since the issue still waits on GitHub and the next pass finds it
  prompt_text      the pass's prompt: the host's prompt file verbatim, then the host's note on what asked for it
  prepare_worktree the host's main branch checked out detached in a worktree of its own, reset and cleaned (the one
                   copy, for any read-only worktree a box keeps: a failed fetch may keep the sha it has)
  running          the pass under way by its lock file, a stale lock ignored
  start_pass       one pass started detached (the host's own `-m <module> pass <why> <kind>`), or folded into the
                   pass already running, whose prompt finds every waiting issue itself
  run_pass         one pass: the worktree refreshed, the CLI run there with the prompt on stdin, capped, its output
                   logged; on the subscriptions' accounts in turn (chuang_platform_kit.headless_claude's verdict and
                   reset time): a limited account rests and the next runs the same pass, a refused token rests a day
  result_record    the run's `result` record from its streamed output
  locked_pass      the detached process's body: the lock written, the pass run, the lock removed, the line kept

The host passes its worktree, prompt file, logs, lock, model and accounts; nothing here names a project, and nothing
has a default that reaches a real service but the git and CLI calls a caller makes on purpose. Standard library, the
kit's pubsub when a request is published and its headless_claude when a pass runs.
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

EVENT = 'triage.wake_requested'   # {why, kind: issue|warnings|sweep, number?, title?, kinds?}
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000) if os.name == 'nt' else 0
REFUSED_REST_S = 86400   # a refused account rests a day
# An account token the service refused: the CLI's and the API's words for an expired or revoked sign-in.
AUTH_REFUSED = re.compile(r'\b401\b|oauth token has expired|authentication_error|invalid (?:x-api-key|api key|bearer token)'
                          r'|please run /login|not logged in|invalid_grant', re.I)
REFUSED_NOTE = "; the {accounts} account's token was refused and rests a day"


def request(why: str, topic: str, kind: str = 'issue', number: int = None, title: str = '', kinds: list = None,
            source: str = '', publish=None, project: str = '', event: str = EVENT) -> str:
    """Publish one `event` on `topic` in `project` for `why` (an issue number, a digest's kinds, a sweep): the line the
    caller logs. `kind` is issue, warnings or sweep. Never raises: a refused publish is the line. `publish` is the test
    seam for chuang_platform_kit.pubsub.publish."""
    from chuang_platform_kit import pubsub
    data = {'why': str(why)[:200], 'kind': kind}
    if number is not None:
        data['number'] = int(number)
    if title:
        data['title'] = str(title)[:200]
    if kinds:
        data['kinds'] = [str(k)[:200] for k in kinds][:20]
    message = pubsub.envelope(event, data, source=source)
    try:
        mid = (publish or pubsub.publish)(topic, message, project=project, attributes={'type': event})
        return f'triage wake: requested for {why} ({kind}; message {mid or "?"}); the desktop runs the pass'
    except Exception as e:   # noqa: BLE001 - the pass is best effort; the issue is still on GitHub
        return f'triage wake: not requested for {why} ({type(e).__name__}: {str(e)[:160]}); the next pass finds it'


def prompt_text(path: str, note: str = '') -> str:
    """The pass's prompt: the file at `path` verbatim, then a blank line and the host's `note` (what asked for this
    pass, where it runs) when it gave one."""
    with open(path, encoding='utf-8') as f:
        text = f.read().rstrip('\n')
    return f'{text}\n\n{note}' if note else text


def prepare_worktree(root: str, worktree: str, run=subprocess.run, keep_on_failed_fetch: bool = False,
                     detail: bool = False, timeout: float = None):
    """origin/main's head checked out detached in `worktree` (added on first use from the clone at `root`, `--force`
    taking over a registration whose folder was deleted; a folder in the way still refuses), reset whatever it holds
    (`checkout --force`, its edits to tracked files discarded: the worktree is read-only by design) and its untracked
    files cleaned: the sha, or '' with the failure in the caller's hands (nothing runs on a stale or broken tree).
    `keep_on_failed_fetch` leaves an existing worktree on the sha it has when the fetch fails (reset and cleaned there
    the same way) and returns that sha, where the default is '', or, when origin/main as this box last saw it is newer
    and descends from it (a push of main from the box moves the ref with no fetch), that commit instead; `detail`
    returns (sha, note) instead, the note why it is '' or what was kept, '' when the tree is at origin/main; `timeout`
    bounds each git call (None waits). A git that cannot run is a failure, never a raise."""
    bound = {'timeout': timeout} if timeout else {}

    def git(args, cwd):
        try:
            return run(['git'] + args, cwd=cwd, capture_output=True, text=True, creationflags=NO_WINDOW, **bound)
        except (OSError, subprocess.SubprocessError) as e:
            return subprocess.CompletedProcess(args, 1, '', f'{type(e).__name__}: {e}')

    def why(got):   # git's answer on one line, bounded
        return ' '.join((got.stderr or got.stdout or 'no reason given').split())[:200]

    def head():
        got = git(['rev-parse', 'HEAD'], worktree)
        return (got.stdout or '').strip() if got.returncode == 0 else ''

    def answer(sha, note=''):
        return (sha, note) if detail else sha

    def reset(to):   # whatever the tree holds, `to` checked out and the untracked files cleaned; the failure or None
        got = git(['checkout', '-q', '--force', '--detach', to], worktree)
        if got.returncode != 0:
            return got
        git(['clean', '-qfd'], worktree)
        return None
    exists = os.path.isdir(os.path.join(worktree, '.git')) or os.path.isfile(os.path.join(worktree, '.git'))
    fetched = git(['fetch', '-q', 'origin', 'main'], root)
    if fetched.returncode != 0:
        kept = head() if keep_on_failed_fetch and exists else ''
        newer = ''
        if kept:   # origin/main as this box last saw it: a push of main from here moves it with no fetch
            got = git(['rev-parse', 'origin/main'], worktree)
            local = (got.stdout or '').strip() if got.returncode == 0 else ''
            if local and local != kept and git(['merge-base', '--is-ancestor', kept, local], worktree).returncode == 0:
                newer = local
        refused = reset(newer or kept) if kept else None
        if newer and refused is None:
            return answer(newer, f'the fetch of origin/main failed ({why(fetched)}); on {newer[:8]}, origin/main as this '
                                 f'box last saw it (a push of main from here moves it), newer than the {kept[:8]} it had')
        if kept and refused is None:
            return answer(kept, f'the fetch of origin/main failed ({why(fetched)}); kept on {kept[:8]}, the sha it already has')
        if refused is not None:
            return answer('', f'the fetch of origin/main failed ({why(fetched)}) and the worktree could not be reset to '
                              f'{(newer or kept)[:8]} ({why(refused)})')
        return answer('', f'the fetch of origin/main failed ({why(fetched)})')
    if not exists:
        added = git(['worktree', 'add', '--detach', '--force', worktree, 'origin/main'], root)
        if added.returncode != 0:
            return answer('', f'the worktree could not be added ({why(added)})')
    else:
        refused = reset('origin/main')
        if refused is not None:
            return answer('', f'the worktree could not be checked out to origin/main ({why(refused)})')
    sha = head()
    return answer(sha, '' if sha else 'the worktree has no commit checked out')


def stamp(now=None) -> str:
    """An instant as a log file's name part, `20261030T220000Z`."""
    return (now or datetime.now(timezone.utc)).strftime('%Y%m%dT%H%M%SZ')   # utc-ok: a log file's name


def running(lock: str, now: float = None, stale: int = 660) -> dict:
    """The pass under way by its lock file ({why, kind, pid, started}), or {} when none or when the lock is older than
    `stale` seconds (a pass killed without its cleanup: the next one takes over)."""
    try:
        with open(lock, encoding='utf-8') as f:
            doc = json.load(f)
        if (now or time.time()) - float(doc.get('started') or 0) < stale:
            return doc
    except (OSError, ValueError):
        pass
    return {}


def start_pass(why: str, kind: str, module: str, root: str, lock: str, logs: str, popen=None, now: float = None,
               stale: int = 660) -> str:
    """Start one pass detached (`python -m <module> pass <why> <kind>` from `root`, the hidden console every box process
    shares); the line for the listener's log. A pass already running (`running`) covers this request: its prompt lists
    every waiting issue itself, so nothing is lost by folding."""
    busy = running(lock, now, stale)
    if busy:
        return (f"triage pass for {why}: folded into the pass running for {busy.get('why')} "
                f"(since {int((now or time.time()) - float(busy.get('started') or 0))} s); it finds this too")
    cmd = [sys.executable, '-m', module, 'pass', str(why), kind]
    flags = {'creationflags': NO_WINDOW | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)} if os.name == 'nt' else {'start_new_session': True}
    try:
        p = (popen or subprocess.Popen)(cmd, cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        close_fds=True, **flags)
    except OSError as e:
        return f'triage pass for {why}: not started ({e})'
    return f'triage pass for {why} ({kind}): started, pid {getattr(p, "pid", "?")}; its log lands in {logs}'


def run_pass(why: str, kind: str, root: str, worktree: str, cmd: list, prompt, logs: str, run=None, now=None,
             limit: int = 600, prepare=None, attempts=None, attempts_of=None, resting=None, resting_of=None,
             zone_of=None, auth_refused=AUTH_REFUSED, refused_rest_s: int = REFUSED_REST_S,
             refused_note: str = REFUSED_NOTE, signed_as: str = '') -> str:
    """One pass: the worktree refreshed (`prepare(root, worktree, run)`, else prepare_worktree), the CLI `cmd` run there
    with the prompt (`prompt()`, read once the worktree is ready) on stdin, capped at `limit` seconds, its output in
    `logs/<stamp>-<why>.log`; the one-line result for the listener's log, or why it did not run. `run` is
    subprocess.run's shape (a test's fake). The accounts are `attempts` ([(account, token)]), else `attempts_of(resting)`;
    an account whose answer names a limit rests (`resting.rest`, until headless_claude.reset_at in `zone_of()`) and the
    next runs the same pass; one whose token the service refused (`auth_refused`) rests `refused_rest_s` and is said in
    `refused_note` ({accounts}); the box's own sign-in (headless_claude.LOGIN) never rests. `resting` is the marks'
    object, else `resting_of()`. `signed_as` (`Name|kind`, 0.15.0) is the pass's own CHUANG_SIGNED_AS, so every comment
    it writes is signed by the triager it is (chuang_sdlc.signature), never by the host that started it."""
    head = (prepare or prepare_worktree)(root, worktree, run or subprocess.run)
    if not head:
        return f'triage pass for {why}: the worktree at {worktree} could not be brought to origin/main; not run'
    os.makedirs(logs, exist_ok=True)
    safe = re.sub(r'[^A-Za-z0-9#._-]+', '_', str(why))[:40]
    log = os.path.join(logs, f'{stamp(now)}-{safe}.log')
    from chuang_platform_kit import headless_claude as hc
    resting = resting or resting_of()
    text_in = prompt()
    t0 = time.monotonic()
    r = None
    refused = []
    try:
        for account, token in (attempts if attempts is not None else attempts_of(resting)):
            r = (run or subprocess.run)(cmd, cwd=worktree, input=text_in, capture_output=True, text=True, encoding='utf-8',
                                        timeout=limit, shell=(os.name == 'nt'), creationflags=NO_WINDOW,
                                        env=dict(hc.child_env(token), **({'CHUANG_SIGNED_AS': signed_as} if signed_as else {})))
            verdict = hc.judge(r.returncode, r.stdout, r.stderr)
            if verdict['limited'] and account != hc.LOGIN:
                resting.rest(account, hc.reset_at(verdict['detail'], time.time(), zone_of() if zone_of else ''), verdict['detail'])
                continue
            if not verdict['ok'] and account != hc.LOGIN and auth_refused.search(f"{verdict['detail']}\n{r.stderr or ''}"):
                # A token the service refused (expired, revoked) rests and the pass goes on to the next account.
                resting.rest(account, time.time() + refused_rest_s, f"token refused: {verdict['detail'][-160:]}")
                refused.append(account)
                continue
            break
    except subprocess.TimeoutExpired:
        return f'triage pass for {why}: killed at {limit} s; the next pass finds what it left'
    except OSError as e:
        return f'triage pass for {why}: claude did not start ({e})'
    note = refused_note.format(accounts=', '.join(refused)) if refused else ''
    if r is None:
        return f'triage pass for {why}: no account to run on{note}'
    seconds = int(time.monotonic() - t0)
    with open(log, 'w', encoding='utf-8') as f:
        f.write((r.stdout or '') + ('\n--- stderr\n' + r.stderr if r.stderr else ''))
    if r.returncode != 0:
        return f'triage pass for {why}: claude exited {r.returncode} in {seconds} s (log {log}){note}'
    answer = result_record(r.stdout or '')
    said = str(answer.get('result') or '').strip()
    if answer.get('is_error'):
        return f'triage pass for {why}: the run answered an error in {seconds} s: {said[:160]} (log {log}){note}'
    turns = answer.get('num_turns')
    return (f'triage pass for {why}: done in {seconds} s on {head[:7]}' + (f', {turns} turns' if turns is not None else '')
            + f' (log {log})' + (f': {said[:120]}' if said else '') + note)


def result_record(stdout: str) -> dict:
    """The run's `result` record from its streamed output (the last line whose type is `result`), or the one JSON
    document when the output was not streamed; {} when neither parses."""
    last = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith('{'):
            continue
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        if isinstance(doc, dict) and (doc.get('type') == 'result' or 'result' in doc and 'type' not in doc):
            last = doc
    return last


def locked_pass(why: str, kind: str, lock: str, run_pass_, now: float = None) -> str:
    """The detached process's body: the lock written ({why, kind, pid, started}), `run_pass_(why, kind)` run, the lock
    removed however it ends; the pass's line, also appended to passes.log beside the lock for the record."""
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    with open(lock, 'w', encoding='utf-8') as f:
        json.dump({'why': str(why), 'kind': kind, 'pid': os.getpid(), 'started': now or time.time()}, f)
    try:
        line = run_pass_(why, kind)
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass
    try:
        with open(os.path.join(os.path.dirname(lock), 'passes.log'), 'a', encoding='utf-8') as f:
            f.write(f'{stamp()} {line}\n')
    except OSError:
        pass
    return line
