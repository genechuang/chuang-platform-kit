"""Headless Claude on the subscriptions, failing over between accounts (SMAD PickleBot #294, then #660).

A production runtime that runs the Claude Code CLI in print mode with an account's long-lived token (`claude
setup-token`) uses this module, so every project has the same accounts, the same token names, the same limit words
and the same failover, and a switch of accounts is one place: each account's token stored once, the preferred order
in one variable, and a limited account resting while the other answers the same call.

Accounts: `CLAUDE_ACCOUNTS` names them in preference order (`gene,smad`); each account's token is
`CLAUDE_CODE_OAUTH_TOKEN_<NAME>` (the name upper-cased) in the environment, else Secret Manager when a project is
named (`token_for`). A call tries the accounts in order, skips one with no token or one resting, and on an answer
that says the account is limited (a usage, rate, spend or weekly limit, credits, quota) rests it until the reset time
the answer names (else RESTING_DEFAULT_S) and moves to the next at once; any other failure moves on too. The resting
marks live behind a two-method store (`get(name) -> dict`, `put(name, dict)`): a project's Firestore, or `FileStore`
on a box. Two shapes of call:

- `ask(prompt, model=, system=)`: one prompt in, the model's text out; print mode, JSON out, no tools, no settings, no
  MCP servers, no session kept, one turn (a service's reply, a story, a judgment).
- `run(args)`: the CLI with the caller's own arguments (tools, a permission mode, stream-json, a prompt on stdin),
  its output streamed through, the limit judged at its end, so a scheduled run with tools fails over too.

    python -m chuang_platform_kit.headless_claude accounts
    python -m chuang_platform_kit.headless_claude ask --model claude-haiku-5-5 [--system "..."] < prompt.txt
    python -m chuang_platform_kit.headless_claude run [--resting FILE] -- -p --model ... --allowedTools ... < prompt

Standard library only (Secret Manager through this kit's `secrets` when asked). The token goes into the child's
environment alone, never a log; the API key is dropped from that environment (the subscription's token, never the key
a caller falls back to).
"""
import collections
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

TOKEN_PREFIX = 'CLAUDE_CODE_OAUTH_TOKEN_'
ACCOUNTS_VAR = 'CLAUDE_ACCOUNTS'
RESTING_DEFAULT_S = 3600
TIMEOUT_S = 120
# What a limited account's answer says (the CLI's own words and the API's): it rests; anything else is a plain failure.
LIMITED = re.compile(r'usage limit|rate limit|spend limit|weekly limit|limit resets|credit balance|out of credits|quota|'
                     r'limit reached', re.I)   # "You've hit your org's monthly spend limit ... weekly limit resets 1pm (UTC)"
TAIL_LINES = 40   # of a streamed run's output kept for the verdict
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)   # Windows: a child console never pops up
LOGIN = 'login'   # the pseudo-account for the box's own CLI sign-in (run(login_fallback=True))


def cli_path(env=None) -> str:
    """The CLI to start: CLAUDE_CLI, else `claude` on the PATH (`claude.cmd` on Windows), else the bare name."""
    env = os.environ if env is None else env
    named = (env.get('CLAUDE_CLI') or '').strip()
    return named or shutil.which('claude') or shutil.which('claude.cmd') or 'claude'


def accounts(env=None, names=None) -> list:
    """The account names in preference order: `names` (a list or a comma-separated string), else CLAUDE_ACCOUNTS."""
    env = os.environ if env is None else env
    raw = names if names is not None else (env.get(ACCOUNTS_VAR) or '')
    if isinstance(raw, str):
        raw = raw.split(',')
    return [n.strip().lower() for n in raw if n and n.strip()]


def token_var(name: str) -> str:
    """The environment variable (and Secret Manager secret) holding an account's token."""
    return TOKEN_PREFIX + re.sub(r'[^A-Za-z0-9]+', '_', name.strip()).upper()


def token_for(name: str, env=None, project: str = '', access=None) -> str:
    """An account's token: the environment, else Secret Manager when `project` is named (a refusal is no token)."""
    env = os.environ if env is None else env
    var = token_var(name)
    tok = (env.get(var) or '').strip()
    if tok or not project:
        return tok
    try:
        if access is None:
            from . import secrets
            access = secrets.access
        return (access(project, var) or b'').decode('utf-8', 'replace').strip()
    except Exception:   # noqa: BLE001 - a secret that cannot be read is an account with no token
        return ''


def command(model: str = '', system: str = '', cli: str = '') -> list:
    """`ask`'s CLI arguments: print mode, JSON out, the model, the caller's system prompt, and nothing of its own: no
    tools, no settings files, no MCP servers, no session kept."""
    cmd = [cli or cli_path(), '-p', '--output-format', 'json', '--tools', '', '--no-session-persistence',
           '--setting-sources', '', '--strict-mcp-config']
    if model:
        cmd += ['--model', model]
    if system:
        cmd += ['--system-prompt', system]
    return cmd


def reset_at(text: str, now: float, zone: str = '') -> float:
    """When a limited account comes back: the epoch the CLI's 'usage limit reached|<epoch>' names, or the next clock
    time its words name ('your weekly limit resets 6am (America/Los_Angeles)'), read in the zone the words name, else
    `zone`, else UTC; else RESTING_DEFAULT_S from now."""
    text = text or ''
    m = re.search(r'\|(\d{10})\b', text)
    at = float(m.group(1)) if m else 0.0
    if at <= now:
        w = re.search(r'resets\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)\b', text, re.I)
        if w:
            from datetime import datetime, timedelta, timezone
            from zoneinfo import ZoneInfo
            named = re.search(r'\(([A-Za-z_]+/[A-Za-z_]+|UTC)\)', text)
            tzname = (named.group(1) if named else '') or zone
            try:
                tz = ZoneInfo(tzname) if tzname and tzname != 'UTC' else timezone.utc   # utc-ok: the CLI's words named UTC, or named no zone and the caller gave none
            except Exception:   # noqa: BLE001 - an unknown zone reads as UTC
                tz = timezone.utc   # utc-ok: a zone name the box does not know; the CLI's own clock is UTC
            hour = int(w.group(1)) % 12 + (12 if w.group(3).lower() == 'pm' else 0)
            here = datetime.fromtimestamp(now, tz)
            when = here.replace(hour=hour, minute=int(w.group(2) or 0), second=0, microsecond=0)
            if when.timestamp() <= now:
                when += timedelta(days=1)
            at = when.timestamp()
    return at if at > now else now + RESTING_DEFAULT_S


def child_env(token: str, env=None) -> dict:
    """The child's environment: the caller's, every account token dropped, this one as CLAUDE_CODE_OAUTH_TOKEN (an
    empty token sets none: the box's own CLI sign-in answers), no API key, no auto-update, no telemetry."""
    base = os.environ if env is None else env
    out = {k: v for k, v in base.items() if not k.startswith('CLAUDE_CODE_OAUTH_TOKEN')}
    out.update({'DISABLE_AUTOUPDATER': '1', 'CLAUDE_CODE_DISABLE_TELEMETRY': '1'})
    if token:
        out['CLAUDE_CODE_OAUTH_TOKEN'] = token
    out.pop('ANTHROPIC_API_KEY', None)
    out.pop('ANTHROPIC_AUTH_TOKEN', None)
    return out


def judge(returncode: int, stdout: str, stderr: str) -> dict:
    """What a finished CLI call said: {'ok', 'text', 'limited', 'detail', 'usage'}. JSON out's last line is the result;
    a non-zero exit, `is_error`, or an empty answer is a failure, limited when the words or a 429 say so."""
    out = (stdout or '').strip()
    body = {}
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith('{'):
            try:
                cand = json.loads(line)
            except ValueError:
                continue
            if isinstance(cand, dict) and (cand.get('type') == 'result' or 'result' in cand or 'is_error' in cand):
                body = cand
                break
    said = str(body.get('result') or '') if body else ''
    if returncode == 0 and body and not body.get('is_error') and said.strip():
        return {'ok': True, 'text': said.strip(), 'limited': False, 'detail': '', 'usage': body.get('usage') or {}}
    if returncode == 0 and not body:
        return {'ok': True, 'text': out, 'limited': False, 'detail': '', 'usage': {}}
    detail = (said or (stderr or '') or out)[-600:].strip() or f'exit {returncode}'
    status = body.get('api_error_status') if body else None
    return {'ok': False, 'text': '', 'limited': bool(LIMITED.search(detail) or status == 429), 'detail': detail[-300:], 'usage': {}}


def run_once(prompt: str, model: str, system: str, token: str, run=subprocess.run, timeout: float = TIMEOUT_S,
             cli: str = '', env=None) -> dict:
    """One `ask` call on one account: {'ok', 'text', 'limited', 'detail', 'usage', 'model'}."""
    try:
        done = run(command(model, system, cli or cli_path(env)), input=prompt, capture_output=True, text=True,
                   encoding='utf-8', errors='replace', timeout=timeout, env=child_env(token, env), creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        return {'ok': False, 'text': '', 'limited': False, 'detail': f'timed out after {timeout:.0f} s', 'usage': {}, 'model': model}
    except OSError as e:
        return {'ok': False, 'text': '', 'limited': False, 'detail': f'the CLI did not start: {e}', 'usage': {}, 'model': model}
    got = judge(done.returncode, done.stdout, done.stderr)
    got['model'] = model
    return got


class Resting:
    """Which accounts rest, and until when, behind `store` (get(name) -> dict, put(name, dict)); a store that fails is
    no one resting (the CLI then says so itself)."""

    def __init__(self, store, now=time.time):
        self.store, self.now = store, now

    def until(self, name: str) -> float:
        try:
            return float((self.store.get(name) or {}).get('resting_until') or 0)
        except Exception:   # noqa: BLE001 - an unreadable mark never keeps an account out
            return 0.0

    def rest(self, name: str, until: float, why: str) -> None:
        try:
            self.store.put(name, {'resting_until': until, 'why': why[:200], 'at': self.now()})
        except Exception:   # noqa: BLE001 - the mark is advice; the next call learns the same from the CLI
            pass


class FileStore:
    """A resting store for a box: one JSON file, {name: mark}."""

    def __init__(self, path: str):
        self.path = path

    def _read(self) -> dict:
        try:
            with open(self.path, encoding='utf-8') as f:
                return json.load(f) or {}
        except (OSError, ValueError):
            return {}

    def get(self, name: str) -> dict:
        return self._read().get(name) or {}

    def put(self, name: str, data: dict) -> None:
        marks = self._read()
        marks[name] = data
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(marks, f)


def _candidates(env, names, project, access, resting, now, tried):
    """The (name, token) pairs to try, in order, the skipped ones recorded in `tried`."""
    out = []
    for name in accounts(env, names):
        token = token_for(name, env, project, access)
        if not token:
            tried.append((name, 'no token'))
            continue
        if resting and resting.until(name) > now():
            tried.append((name, 'resting'))
            continue
        out.append((name, token))
    return out


def ask(prompt: str, model: str = '', system: str = '', env=None, resting: Resting = None, run=subprocess.run,
        now=time.time, timeout: float = TIMEOUT_S, names=None, project: str = '', access=None, cli: str = '',
        zone: str = '') -> dict:
    """The prompt on the first account that answers: {'ok', 'text', 'account', 'tried': [(name, outcome)], 'usage'}.
    An account with no token is skipped; a resting one is skipped until its time; a limited answer rests it and moves
    on; any other failure moves on too (the next account may be fine). ok False when none answered."""
    env = os.environ if env is None else env
    tried = []
    for name, token in _candidates(env, names, project, access, resting, now, tried):
        got = run_once(prompt, model, system, token, run=run, timeout=timeout, cli=cli, env=env)
        if got['ok']:
            tried.append((name, 'answered'))
            return {'ok': True, 'text': got['text'], 'account': name, 'tried': tried, 'usage': got.get('usage') or {}}
        if got['limited']:
            if resting:
                resting.rest(name, reset_at(got['detail'], now(), zone), got['detail'])
            tried.append((name, f"limited: {got['detail'][:120]}"))
        else:
            tried.append((name, f"failed: {got['detail'][:120]}"))
    return {'ok': False, 'text': '', 'account': '', 'tried': tried, 'usage': {}}


def stream(args: list, token: str, env=None, stdin=None, out=None, err=None, timeout: float = 0, popen=subprocess.Popen,
           cwd: str = None, cli: str = '') -> dict:
    """The CLI with the caller's arguments on one account, stdout and stderr streamed to `out` and `err` (sys.stdout
    and sys.stderr by default) as they come, the last TAIL_LINES of each kept for the verdict; `timeout` seconds
    (0: none) kills it. {'ok', 'returncode', 'limited', 'detail'}."""
    out, err = out if out is not None else sys.stdout, err if err is not None else sys.stderr
    tails = {'out': collections.deque(maxlen=TAIL_LINES), 'err': collections.deque(maxlen=TAIL_LINES)}
    try:
        p = popen([cli or cli_path(env), *args], stdin=stdin if stdin is not None else subprocess.DEVNULL,
                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                  env=child_env(token, env), cwd=cwd, creationflags=NO_WINDOW)
    except OSError as e:
        return {'ok': False, 'returncode': 127, 'limited': False, 'detail': f'the CLI did not start: {e}'}

    def pump(src, sink, tail):
        for line in iter(src.readline, ''):
            tail.append(line)
            try:
                sink.write(line)
                sink.flush()
            except (OSError, ValueError):
                pass
    threads = [threading.Thread(target=pump, args=(p.stdout, out, tails['out']), daemon=True),
               threading.Thread(target=pump, args=(p.stderr, err, tails['err']), daemon=True)]
    for t in threads:
        t.start()
    try:
        p.wait(timeout=timeout or None)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
        for t in threads:
            t.join(5)
        return {'ok': False, 'returncode': p.returncode, 'limited': False, 'detail': f'killed after {timeout:.0f} s'}
    for t in threads:
        t.join(5)
    got = judge(p.returncode, ''.join(tails['out']), ''.join(tails['err']))
    return {'ok': got['ok'], 'returncode': p.returncode, 'limited': got['limited'], 'detail': got['detail']}


def run(args: list, env=None, resting: Resting = None, now=time.time, timeout: float = 0, names=None, project: str = '',
        access=None, popen=subprocess.Popen, stdin=None, out=None, err=None, cwd: str = None, cli: str = '',
        zone: str = '', login_fallback: bool = False) -> dict:
    """The CLI with the caller's arguments on the first account whose run is not refused for a limit: {'ok',
    'returncode', 'account', 'tried'}. A limited run rests the account and the same run starts on the next; a plain
    failure (any other exit) is the caller's, returned as it is, since a run with tools may have done work. With
    `login_fallback`, every account missing or resting runs once more on the box's own CLI sign-in (the account named
    LOGIN), the way a desktop ran before it had tokens; without it that case is exit 75 and no account."""
    env = os.environ if env is None else env
    tried = []
    candidates = _candidates(env, names, project, access, resting, now, tried)
    if login_fallback:
        candidates.append((LOGIN, ''))
    for name, token in candidates:
        got = stream(args, token, env=env, stdin=stdin, out=out, err=err, timeout=timeout, popen=popen, cwd=cwd, cli=cli)
        if got['limited'] and name != LOGIN:
            if resting:
                resting.rest(name, reset_at(got['detail'], now(), zone), got['detail'])
            tried.append((name, f"limited: {got['detail'][:120]}"))
            continue
        tried.append((name, 'ran' if got['ok'] else f"exit {got['returncode']}: {got['detail'][:120]}"))
        return {'ok': got['ok'], 'returncode': got['returncode'], 'account': name, 'tried': tried}
    return {'ok': False, 'returncode': 75, 'account': '', 'tried': tried}   # EX_TEMPFAIL: every account resting or missing


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog='python -m chuang_platform_kit.headless_claude',
                                 description='Headless Claude on the subscriptions, failing over between accounts.')
    ap.add_argument('--accounts', default=None, help=f'the names in order (default: ${ACCOUNTS_VAR})')
    ap.add_argument('--project', default='', help='the Google Cloud project whose Secret Manager holds a token not in the environment')
    ap.add_argument('--resting', default='', help='a JSON file keeping the resting marks on this box')
    ap.add_argument('--zone', default='', help='the zone a reset time in words is read in when the words name none')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('accounts', help='each account and whether it holds a token (never the token)')
    a = sub.add_parser('ask', help='one prompt on stdin, the text on stdout')
    a.add_argument('--model', default='')
    a.add_argument('--system', default='')
    a.add_argument('--timeout', type=float, default=TIMEOUT_S)
    r = sub.add_parser('run', help='the CLI with your own arguments after --, stdin and stdout passed through')
    r.add_argument('--timeout', type=float, default=0, help='seconds before the run is killed (0: none)')
    r.add_argument('args', nargs=argparse.REMAINDER)
    ns = ap.parse_args(argv)
    resting = Resting(FileStore(ns.resting)) if ns.resting else None
    if ns.cmd == 'accounts':
        for name in accounts(names=ns.accounts):
            print(f'{name}: {"token" if token_for(name, project=ns.project) else "no token"}'
                  + (f', resting until {int(resting.until(name))}' if resting and resting.until(name) > time.time() else ''))
        return 0
    if ns.cmd == 'ask':
        got = ask(sys.stdin.read(), model=ns.model, system=ns.system, resting=resting, timeout=ns.timeout,
                  names=ns.accounts, project=ns.project, zone=ns.zone)
        if got['ok']:
            print(got['text'])
            return 0
        print('headless_claude: no account answered: ' + '; '.join(f'{n}: {o}' for n, o in got['tried']), file=sys.stderr)
        return 75
    args = ns.args[1:] if ns.args[:1] == ['--'] else ns.args
    got = run(args, resting=resting, timeout=ns.timeout, names=ns.accounts, project=ns.project, zone=ns.zone, stdin=sys.stdin)
    if not got['account']:
        print('headless_claude: no account ran: ' + '; '.join(f'{n}: {o}' for n, o in got['tried']), file=sys.stderr)
    return got['returncode']


if __name__ == '__main__':
    sys.exit(main())
