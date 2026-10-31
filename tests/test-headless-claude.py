#!/usr/bin/env python3
"""chuang_platform_kit.headless_claude: the Claude Code CLI in print mode on an account's token, failing over between
accounts (SMAD PickleBot #294, #660).

Pinned (the positive case first): ask() sends the prompt to the first account named in CLAUDE_ACCOUNTS that holds a
token and is not resting, as `claude -p` with JSON out, no tools, no settings, no MCP servers, no session kept, the
subscription's token in the child's environment and never the API key, and its text comes back; a limited answer rests
the account until the reset time it names (an epoch, or clock words in the zone the words name, else the given zone,
else an hour) and the same prompt goes to the next account at once; a plain failure moves on without resting; no
account answering is ok False with what each said; a token comes from the environment, else Secret Manager when a
project is named, a refusal being no token; FileStore keeps the marks on a box; run() streams the CLI's own
arguments through, rests a limited run and starts the next account, returns a plain failure as it is; the module's
`main` parses the three subcommands.

Run:  python -B tests/test-headless-claude.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import headless_claude as H  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


ENV = {'CLAUDE_ACCOUNTS': 'gene,smad', 'CLAUDE_CODE_OAUTH_TOKEN_GENE': 'tok-gene', 'CLAUDE_CODE_OAUTH_TOKEN_SMAD': 'tok-smad',
       'CLAUDE_CLI': 'claude'}


class Done:
    def __init__(self, code=0, out='', err=''):
        self.returncode, self.stdout, self.stderr = code, out, err


def cli(answers):
    """A stand-in for subprocess.run: each call's (token, argv, prompt, API key present) recorded, answered by `answers`
    in turn ({'result', 'is_error', 'api_error_status'} as the CLI's JSON, or a Done)."""
    calls = []

    def run(argv, input='', env=None, **k):
        calls.append((env.get('CLAUDE_CODE_OAUTH_TOKEN'), argv, input, 'ANTHROPIC_API_KEY' in env))
        a = answers.pop(0)
        if isinstance(a, Done):
            return a
        return Done(0 if not a.get('is_error') else 1, json.dumps(dict({'type': 'result', 'usage': {'output_tokens': 4}}, **a)))
    return run, calls


class Store:
    def __init__(self, marks=None):
        self.marks = dict(marks or {})

    def get(self, name):
        return self.marks.get(name, {})

    def put(self, name, data):
        self.marks[name] = data


# -- ask ------------------------------------------------------------------------------------------------------------
run, calls = cli([{'result': ' pong '}])
got = H.ask('ping', model='claude-haiku-5-5', system='one word', env=dict(ENV, ANTHROPIC_API_KEY='sk'), run=run,
            resting=H.Resting(Store()), now=lambda: 1000.0)
argv = calls[0][1]
check("the positive case: the first account answers, on its token, never the API key",
      (got['ok'], got['text'], got['account'], got['tried'], calls[0][0], calls[0][2], calls[0][3]),
      (True, 'pong', 'gene', [('gene', 'answered')], 'tok-gene', 'ping', False))
check("  the command: print mode, JSON out, no tools, no settings, no MCP, no session, the model and system prompt",
      (argv[:4], argv[argv.index('--tools') + 1], '--no-session-persistence' in argv, argv[argv.index('--setting-sources') + 1],
       '--strict-mcp-config' in argv, argv[argv.index('--model') + 1], argv[argv.index('--system-prompt') + 1]),
      (['claude', '-p', '--output-format', 'json'], '', True, '', True, 'claude-haiku-5-5', 'one word'))

store = Store()
run, calls = cli([{'result': 'Claude AI usage limit reached|2000000000', 'is_error': True}, {'result': 'from smad'}])
got = H.ask('ping', env=ENV, run=run, resting=H.Resting(store), now=lambda: 1000.0)
run2, calls2 = cli([{'result': 'again smad'}])
again = H.ask('ping', env=ENV, run=run2, resting=H.Resting(store), now=lambda: 1500.0)
store3 = Store()
run3, _ = cli([{'result': "You've hit your org's monthly spend limit", 'is_error': True}, {'result': 'ok'}])
H.ask('ping', env=ENV, run=run3, resting=H.Resting(store3), now=lambda: 1000.0)
check("a limited account rests until the time it names and the other answers; resting, it is skipped; no time is an hour",
      (got['account'], got['text'], [t for t, *_ in calls], store.marks['gene']['resting_until'], again['account'],
       [t for t, *_ in calls2], again['tried'][0], store3.marks['gene']['resting_until']),
      ('smad', 'from smad', ['tok-gene', 'tok-smad'], 2000000000.0, 'smad', ['tok-smad'], ('gene', 'resting'), 4600.0))

from datetime import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402
pt = ZoneInfo('America/Los_Angeles')
at = lambda y, mo, d, h, mi, tz=pt: datetime(y, mo, d, h, mi, tzinfo=tz).timestamp()   # noqa: E731
now = at(2026, 10, 25, 5, 43)
said = "You've hit your monthly spend limit · your weekly limit resets 6am (America/Los_Angeles)"
utc_said = "You've hit your org's monthly spend limit · your weekly limit resets 1pm (UTC)"
check("the reset in words: the zone the words name, the given zone when they name none, UTC when neither, tomorrow once passed",
      (H.reset_at(said, now) == at(2026, 10, 25, 6, 0), H.reset_at(said, at(2026, 10, 25, 7, 0)) == at(2026, 10, 26, 6, 0),
       H.reset_at('limit resets 6:30pm', now, zone='America/Los_Angeles') == at(2026, 10, 25, 18, 30),
       H.reset_at(utc_said, now) == at(2026, 10, 25, 13, 0, ZoneInfo('UTC')),
       H.reset_at('limit resets 6pm', 0.0) == 18 * 3600, H.reset_at('usage limit reached', now) == now + 3600, bool(H.LIMITED.search(utc_said))),
      (True, True, True, True, True, True, True))

store = Store()


def boom(argv, **k):
    raise subprocess.TimeoutExpired(argv, 5)


run, _ = cli([Done(1, '', 'segfault')])
got = H.ask('ping', env={'CLAUDE_ACCOUNTS': 'gene,smad', 'CLAUDE_CODE_OAUTH_TOKEN_SMAD': 'tok-smad'}, run=run, resting=H.Resting(store), now=lambda: 1.0)
timed = H.ask('ping', env={'CLAUDE_ACCOUNTS': 'gene', 'CLAUDE_CODE_OAUTH_TOKEN_GENE': 'g'}, run=boom, resting=H.Resting(store), now=lambda: 1.0)
run4, _ = cli([Done(1, '', 'crash')])
order = H.ask('ping', env={'CLAUDE_ACCOUNTS': 'gene,smad', 'CLAUDE_CODE_OAUTH_TOKEN_GENE': 'g'}, run=run4, resting=H.Resting(Store()), now=lambda: 1.0)
check("a plain failure moves on without resting; no token skips; none answering is ok False with each outcome, in account order",
      (got['ok'], got['tried'][0], got['tried'][1][1].startswith('failed: segfault'), store.marks, timed['tried'][0][1].startswith('failed: timed out'),
       [n for n, _ in order['tried']], order['tried'][0][1].startswith('failed: crash'), order['tried'][1]),
      (False, ('gene', 'no token'), True, {}, True, ['gene', 'smad'], True, ('smad', 'no token')))


class Broken:
    def get(self, name):
        raise OSError('store down')

    def put(self, name, data):
        raise OSError('store down')


run, calls = cli([{'result': 'usage limit reached', 'is_error': True}, {'result': 'ok'}])
got = H.ask('ping', env=ENV, run=run, resting=H.Resting(Broken()), now=lambda: 1.0)
check("an unreadable store keeps every account in; a mark that cannot be written is dropped quietly", (got['ok'], got['account'], len(calls)), (True, 'smad', 2))

# -- tokens and accounts -----------------------------------------------------------------------------------------------
asked = []


def access(project, name):
    asked.append((project, name))
    if name.endswith('_SMAD'):
        return b'from-secret-manager\n'
    raise RuntimeError('HTTP 403')


check("accounts and tokens: the order from CLAUDE_ACCOUNTS or the argument, the variable from the name, the environment first, Secret Manager when a project is named, a refusal no token",
      (H.accounts({'CLAUDE_ACCOUNTS': ' Gene, smad '}), H.accounts({}, 'a,b'), H.accounts({}, ['x']), H.token_var('smad'), H.token_var('my-org'),
       H.token_for('gene', {'CLAUDE_CODE_OAUTH_TOKEN_GENE': 'e'}, project='p', access=access),
       H.token_for('smad', {}, project='p', access=access), H.token_for('gene', {}, project='p', access=access), H.token_for('gene', {}), asked),
      (['gene', 'smad'], ['a', 'b'], ['x'], 'CLAUDE_CODE_OAUTH_TOKEN_SMAD', 'CLAUDE_CODE_OAUTH_TOKEN_MY_ORG', 'e', 'from-secret-manager', '', '',
       [('p', 'CLAUDE_CODE_OAUTH_TOKEN_SMAD'), ('p', 'CLAUDE_CODE_OAUTH_TOKEN_GENE')]))

with tempfile.TemporaryDirectory() as d:
    fs = H.FileStore(os.path.join(d, 'sub', 'resting.json'))
    before = fs.get('gene')
    fs.put('gene', {'resting_until': 5.0, 'why': 'w', 'at': 1.0})
    fs.put('smad', {'resting_until': 7.0})
    with open(fs.path, encoding='utf-8') as f:
        on_disk = json.load(f)
    check("FileStore: a missing file is no marks; puts keep every account's mark in one JSON file",
          (before, fs.get('gene')['resting_until'], sorted(on_disk)), ({}, 5.0, ['gene', 'smad']))

# -- run: the CLI with the caller's arguments, streamed ---------------------------------------------------------------


class Feed:
    """A fake pipe to the child's stdin: what was written to it, and whether it was closed."""

    def __init__(self):
        self.data, self.closed = [], False

    def write(self, s):
        self.data.append(s)

    def close(self):
        self.closed = True


class Proc:
    """A stand-in for subprocess.Popen: `outs` is the stdout text, `code` the exit."""
    made = []

    def __init__(self, argv, stdin=None, stdout=None, stderr=None, text=True, encoding='', errors='', env=None, cwd=None, **k):
        self.argv, self.env, self.cwd, self.flags = argv, env, cwd, k.get('creationflags')
        self.stdin_given, self.stdin = stdin, (Feed() if stdin == H.subprocess.PIPE else None)
        out, err, code = Proc.script.pop(0)
        self.stdout, self.stderr, self.returncode = io.StringIO(out), io.StringIO(err), code
        Proc.made.append(self)

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        pass


Proc.script = [('{"type":"system"}\n{"type":"result","is_error":true,"result":"You have hit your weekly limit · resets 6am (America/Los_Angeles)"}\n', '', 1),
               ('line 1\nline 2\n{"type":"result","is_error":false,"result":"done"}\n', 'warn\n', 0)]
sink, errsink, store = io.StringIO(), io.StringIO(), Store()
got = H.run(['-p', '--model', 'm', '--allowedTools', 'Read'], env=ENV, resting=H.Resting(store), now=lambda: at(2026, 10, 25, 5, 0),
            popen=Proc, out=sink, err=errsink)
check("the positive case for run: the first account's run is limited and rests, the same arguments run on the next, its output streamed through",
      (got['ok'], got['returncode'], got['account'], got['tried'][0][1].startswith('limited:'), got['tried'][1], Proc.made[0].argv,
       Proc.made[1].env['CLAUDE_CODE_OAUTH_TOKEN'], 'ANTHROPIC_API_KEY' in Proc.made[1].env, sink.getvalue().splitlines()[-3:],
       errsink.getvalue(), store.marks['gene']['resting_until'] == at(2026, 10, 25, 6, 0)),
      (True, 0, 'smad', True, ('smad', 'ran'), ['claude', '-p', '--model', 'm', '--allowedTools', 'Read'], 'tok-smad', False,
       ['line 1', 'line 2', '{"type":"result","is_error":false,"result":"done"}'], 'warn\n', True))

Proc.made, Proc.script = [], [('partial\n', 'boom\n', 2)]
got = H.run(['-p'], env=ENV, resting=H.Resting(Store()), now=lambda: 1.0, popen=Proc, out=io.StringIO(), err=io.StringIO())
Proc.script = []
none = H.run(['-p'], env={'CLAUDE_ACCOUNTS': 'gene'}, popen=Proc, out=io.StringIO(), err=io.StringIO())
check("a plain failure is the caller's, returned as it is on the first account; every account missing is 75 with no account",
      (got['ok'], got['returncode'], got['account'], got['tried'][0][1].startswith('exit 2:'), len(Proc.made), none['returncode'], none['account'], none['tried']),
      (False, 2, 'gene', True, 1, 75, '', [('gene', 'no token')]))

Proc.made, Proc.script = [], [('{"type":"result","is_error":true,"result":"usage limit reached"}\n', '', 1), ('{"type":"result","result":"sent"}\n', '', 0)]
fb = H.run(['-p'], env={'CLAUDE_ACCOUNTS': 'gene', 'CLAUDE_CODE_OAUTH_TOKEN_GENE': 'g'}, resting=H.Resting(Store()), now=lambda: 1.0,
           popen=Proc, out=io.StringIO(), err=io.StringIO(), login_fallback=True)
check("login_fallback: every account limited or missing runs once more on the box's own sign-in, no token in its environment, no console window",
      (fb['ok'], fb['account'], fb['tried'][0][1].startswith('limited:'), fb['tried'][1], 'CLAUDE_CODE_OAUTH_TOKEN' in Proc.made[1].env,
       Proc.made[0].env['CLAUDE_CODE_OAUTH_TOKEN'], Proc.made[1].flags == H.NO_WINDOW),
      (True, 'login', True, ('login', 'ran'), False, 'g', True))

check("judge: JSON out's result line, a non-zero exit, is_error or an empty answer, a 429, plain text on exit 0",
      (H.judge(0, '{"type":"result","result":" hi "}', '')['text'], H.judge(1, '{"type":"result","result":"x","is_error":true}', '')['ok'],
       H.judge(0, '{"type":"result","result":""}', 'empty')['ok'], H.judge(1, '{"type":"result","is_error":true,"result":"no","api_error_status":429}', '')['limited'],
       H.judge(0, 'plain words', '')['text'], H.judge(1, '', 'rate limit')['limited'], H.judge(1, '', '')['detail']),
      ('hi', False, False, True, 'plain words', True, 'exit 1'))

check("child_env: every account token dropped, this one as CLAUDE_CODE_OAUTH_TOKEN, no API key, no auto-update, no telemetry",
      (lambda e: (e['CLAUDE_CODE_OAUTH_TOKEN'], 'CLAUDE_CODE_OAUTH_TOKEN_GENE' in e, 'ANTHROPIC_API_KEY' in e, e['DISABLE_AUTOUPDATER'], e['PATH']))(
          H.child_env('t', {'CLAUDE_CODE_OAUTH_TOKEN_GENE': 'g', 'ANTHROPIC_API_KEY': 'k', 'PATH': '/bin'})),
      ('t', False, False, '1', '/bin'))

Proc.made, Proc.script = [], [('{"type":"result","is_error":true,"result":"monthly spend limit · weekly limit resets 5am (UTC)"}\n', '', 1),
                              ('{"type":"result","result":"ran"}\n', '', 0)]
fed = H.run(['-p'], env=ENV, resting=H.Resting(Store()), now=lambda: at(2026, 10, 31, 9, 0), popen=Proc,
            stdin=io.StringIO('the prompt\nline 2\n'), out=io.StringIO(), err=io.StringIO())
check("a prompt on stdin reaches every attempt whole: the stream is read once, each attempt fed its own pipe (Dragnet, 10/31/26 9:37 AM PT)",
      (fed['account'], [m.stdin_given == H.subprocess.PIPE for m in Proc.made], [''.join(m.stdin.data) for m in Proc.made],
       [m.stdin.closed for m in Proc.made]),
      ('smad', [True, True], ['the prompt\nline 2\n'] * 2, [True, True]))

# -- main -----------------------------------------------------------------------------------------------------------
os.environ.update({'CLAUDE_ACCOUNTS': 'gene,smad', 'CLAUDE_CODE_OAUTH_TOKEN_SMAD': 'x'})
os.environ.pop('CLAUDE_CODE_OAUTH_TOKEN_GENE', None)
buf, kept = io.StringIO(), sys.stdout
sys.stdout = buf
try:
    rc = H.main(['accounts'])
finally:
    sys.stdout = kept
check("main accounts: each account and whether it holds a token, never the token", (rc, buf.getvalue().splitlines()), (0, ['gene: no token', 'smad: token']))

os.environ.pop('CLAUDE_CODE_OAUTH_TOKEN_SMAD', None)
Proc.made, Proc.script = [], [('{"type":"result","result":"ran on the login"}\n', '', 0)]
kept_popen, H.subprocess.Popen = H.subprocess.Popen, Proc
kept_in, sys.stdin = sys.stdin, io.StringIO('from stdin')
try:
    with_login = H.main(['run', '--', '-p', '--model', 'm'])
    without = H.main(['run', '--no-login-fallback', '--', '-p'])
finally:
    H.subprocess.Popen, sys.stdin = kept_popen, kept_in
check("main run: with no token anywhere the box's own sign-in runs by default (Dragnet, 10/31/26 9:10 AM PT: exit 75 before), on the piped prompt; --no-login-fallback keeps the 75",
      (with_login, Proc.made[0].argv[1:], 'CLAUDE_CODE_OAUTH_TOKEN' in Proc.made[0].env, ''.join(Proc.made[0].stdin.data), without, len(Proc.made)),
      (0, ['-p', '--model', 'm'], False, 'from stdin', 75, 1))

print()
print(f"{len(fails)} failure(s)" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
