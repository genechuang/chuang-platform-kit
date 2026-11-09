#!/usr/bin/env python3
"""PreToolUse on Bash and PowerShell: a GitHub comment a session writes is signed (1.23.0, SMADPickleBot #1279; Gene,
11/9/26 9:55 AM PT: "things wrote by sessions ... get confused as comments by me, because all claude sessions are
writing tickets using my GH account"). Every session writes with its owner's token, so an unsigned comment reads as the
owner's own words, on the page and to any code that takes the owner's comment as theirs.

A write is a command that starts (at the line's start or after ; & | ( or $( ) with gh and names one of:

    gh issue comment / gh pr comment
    gh issue close / gh pr close with --comment or -c
    gh pr review with --body, -b, --body-file or -F
    gh api to an issue's or pull request's comments, with a body field, --input, or -X/--method POST or PATCH

A heredoc's lines are no commands (a commit message may name these words), and `--help` is no write. The write is
signed when its signature, `— **<name>** · Claude session` (or `· pipeline`), stands anywhere in the command's own text
(inline, a heredoc, a PowerShell here-string, a printf) or ends a body file the command names (--body-file, -F,
--input, body=@FILE, $(cat FILE), < FILE; an MSYS `/d/...` or a Windows path). The gate does not parse the shell:
recognizing the verb and finding the signature's text is all it does, so a correctly signed comment is never refused
for how it was quoted, and readers (chuang_sdlc.signature.signed_by) hold the last-line rule. Refused (exit 2, the reason
on stderr), a subagent's call too, with the line to add: the session's name from Claude Code's session registry
(~/.claude/sessions/*.json, the row whose sessionId is the event's), else CHUANG_SIGNED_AS. The event is read as UTF-8
bytes (Windows decodes a piped stdin as cp1252). Anything unexpected exits 0: a broken hook must never trap a session.
"""
import glob
import json
import os
import re
import sys

SIGNED = re.compile(r'^— \*\*(?P<who>[^*\n]{1,40})\*\* · (?P<kind>Claude session|pipeline)[ \t]*\r?$', re.M)
SIGNATURE_TEXT = re.compile(r'— \*\*[^*\n]{1,40}\*\* · (?:Claude session|pipeline)')
START = r'(?:^|[;&|(]|\$\()[ \t]*(?:[A-Za-z_]\w*=\S*[ \t]+)*[&]?[ \t]*["\']?(?:[^\s"\';&|()]*[/\\])?gh(?:\.exe)?["\']?'
GLOBALS = r'(?:[ \t]+-{1,2}[A-Za-z][\w-]*(?:[ =][^\s;&|-][^\s;&|]*)?){0,4}'
WRITES = [
    ('gh issue/pr comment', re.compile(START + GLOBALS + r'[ \t]+(?:issue|pr)[ \t]+comment\b(?P<rest>[^\n;&|]*)', re.M)),
    ('gh issue/pr close --comment', re.compile(START + GLOBALS + r'[ \t]+(?:issue|pr)[ \t]+close\b(?P<rest>[^\n;&|]*)', re.M)),
    ('gh pr review', re.compile(START + GLOBALS + r'[ \t]+pr[ \t]+review\b(?P<rest>[^\n;&|]*)', re.M)),
    ('gh api comments', re.compile(START + GLOBALS + r'[ \t]+api\b(?P<rest>[^\n;&|]*)', re.M)),
]
API_PATH = re.compile(r'repos/[^/\s]+/[^/\s]+/(?:issues|pulls)/(?:\d+|\{[^}\s]+\}|:\w+|comments)/?(?:comments|\d+)?')
HEREDOC_START = re.compile(r'<<-?[ \t]*(["\']?)([A-Za-z_][\w.-]*)\1')
FILE_REFS = [re.compile(p) for p in (
    r'(?:--body-file|--input|(?<![\w-])-F)[ =]+("[^"\n]+"|\'[^\'\n]+\'|[^\s;&|)]+)',
    r'body=@("[^"\n]+"|\'[^\'\n]+\'|[^\s;&|)]+)',
    r'\$\((?:cat[ \t]+|<[ \t]*)("[^"\n]+"|\'[^\'\n]+\'|[^\s;&|)]+)[ \t]*\)',
    r'(?<![<\d])<[ \t]*("[^"\n]+"|\'[^\'\n]+\'|[^\s;&|)<]+)',
)]


def _commands(command: str) -> str:
    """The command's text with every heredoc's lines blanked (their words are a body or a message, never a command)."""
    out, end = [], None
    for line in command.split('\n'):
        if end is not None:
            out.append('')
            if line.strip() == end:
                end = None
            continue
        out.append(line)
        m = HEREDOC_START.search(line)
        if m and '<<<' not in line[max(0, m.start() - 1):m.start() + 3]:
            end = m.group(2)
    return '\n'.join(out)


def writes(command: str):
    """The comment writes the command makes: [(what, rest of its line)]. A line continued with `\\` (bash) or a backtick
    (PowerShell) is one line."""
    text = _commands(re.sub(r'(?:\\|`)\r?\n', ' ', command))
    found = []
    for what, rx in WRITES:
        for m in rx.finditer(text):
            rest = m.group('rest') or ''
            if re.search(r'(?:^|\s)(?:--help|-h)\b', rest):
                continue
            if 'close' in what and not re.search(r'(?:^|\s)(?:--comment|-c)\b', rest):
                continue
            if what == 'gh pr review' and not re.search(r'(?:^|\s)(?:--body|-b|--body-file|-F)\b', rest):
                continue
            if what == 'gh api comments':
                if not API_PATH.search(rest) or '/comments' not in rest:
                    continue
                method = re.search(r'(?:-X|--method)[ =]+["\']?(\w+)', rest)
                body = re.search(r'(?:^|\s)(?:-f|-F|--field|--raw-field)[ =]+["\']?body=|--(?:raw-)?field=body=|--input\b', rest)
                if (method.group(1).upper() if method else ('POST' if body else 'GET')) not in ('POST', 'PATCH'):
                    continue
            found.append((what, rest))
    return found


def _path(raw: str, cwd: str) -> str:
    """A named file as this Python opens it: quotes off, an MSYS `/d/x` as `d:/x` on Windows, relative under cwd."""
    p = raw.strip().strip('"\'')
    m = re.match(r'^/([a-zA-Z])(/.*)?$', p)
    if os.name == 'nt' and m:
        p = f'{m.group(1)}:{m.group(2) or "/"}'
    return p if os.path.isabs(p) else os.path.join(cwd or '.', p)


def _signed_file(raw: str, cwd: str) -> bool:
    try:
        with open(_path(raw, cwd), encoding='utf-8') as f:
            text = f.read()
    except (OSError, ValueError):
        return False
    try:   # an --input JSON's body
        text = json.loads(text).get('body') or text
    except (ValueError, AttributeError):
        pass
    lines = [ln for ln in str(text).splitlines() if ln.strip()]
    return bool(lines) and bool(SIGNED.fullmatch(lines[-1].rstrip()))


def signed(command: str, cwd: str = '') -> bool:
    """Whether the command carries a signature: its text anywhere, or a body file it names ending signed."""
    if SIGNATURE_TEXT.search(command):
        return True
    return any(_signed_file(m.group(1), cwd) for rx in FILE_REFS for m in rx.finditer(command))


def session_name(session_id: str, env=None, home: str = None) -> str:
    """The session's name in Claude Code's registry (the row whose sessionId is this one), else CHUANG_SIGNED_AS's;
    '' when neither names one a signature can carry (1 to 40 characters, no *)."""
    env = os.environ if env is None else env
    name = ''
    for path in glob.glob(os.path.join(home or os.path.expanduser('~'), '.claude', 'sessions', '*.json')):
        try:
            with open(path, encoding='utf-8') as f:
                row = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(row, dict) and session_id and row.get('sessionId') == session_id and row.get('name'):
            name = str(row['name']).strip()
            break
    name = name or (env.get('CHUANG_SIGNED_AS') or '').partition('|')[0].strip()
    return name if re.fullmatch(r'[^*\n]{1,40}', name or '') else ''


def refusal(event: dict, env=None, home: str = None) -> str:
    """The reason to refuse this call; '' to let it run."""
    if event.get('tool_name') not in ('Bash', 'PowerShell'):
        return ''
    command = str((event.get('tool_input') or {}).get('command') or '')
    if 'gh' not in command.lower():
        return ''
    found = writes(command)
    if not found or signed(command, str(event.get('cwd') or '')):
        return ''
    name = session_name(str(event.get('session_id') or ''), env, home) or '<your session name>'
    return (f'Sign the comment: this {found[0][0]} writes with the owner\'s GitHub token, so unsigned it reads as the '
            f'owner\'s own words, on the page and to the code that takes their comment as theirs. End its body with this '
            f'line, its last line, after a blank line:\n\n— **{name}** · Claude session\n\n(gene-hooks comment-sign.py; '
            f'Gene, 11/9/26: sessions\' comments were read as his.)')


def main() -> int:
    try:
        event = json.loads(sys.stdin.buffer.read().decode('utf-8', errors='replace'))
        reason = refusal(event) if isinstance(event, dict) else ''
    except Exception:   # noqa: BLE001 - a hook that cannot check gets out of the way
        return 0
    if reason:
        try:
            sys.stderr.buffer.write((reason + '\n').encode('utf-8'))
            sys.stderr.flush()
        except Exception:   # noqa: BLE001 - the refusal stands even when its words cannot be written
            pass
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
