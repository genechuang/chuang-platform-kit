"""Who wrote a comment, when everyone writes with one person's token: a signature line at a comment's end, and the
reads that tell such a comment from that person's own words (0.15.0, SMADPickleBot #1279; Gene, 11/9/26 9:55 AM PT:
"things wrote by sessions ... get confused as comments by me, because all claude sessions are writing tickets using my
GH account").

  footer     `— **Snow White** · Claude session` or `— **Picklebot** · pipeline`, the one line
  sign       a body with its footer added once, by the signer at hand; a body already signed, or no signer, as given
  signed_by  {who, kind} of a body's footer; {} for a person's own words
  signer     who signs here: configure()'s, else CHUANG_SIGNED_AS (`Name|kind`), else the Claude Code session this
             process runs under (its registry row, ~/.claude/sessions/$CLAUDE_PID.json), else configure(default=)'s

The line is a footer, never a header: readers find their own comments and the alert closers' by a body's first words,
and a `Scrum:` or `Triage:` line is read wherever it stands, so a footer changes neither. gene-hooks' comment gate holds
the same regex (the kit's tests/test-hooks-comment-sign.py keeps them equal). Standard library only.
"""
import json
import os
import re

KINDS = ('Claude session', 'pipeline')
SIGNED = re.compile(r'^— \*\*(?P<who>[^*\n]{1,40})\*\* · (?P<kind>Claude session|pipeline)[ \t]*\r?$', re.M)
ENV = 'CHUANG_SIGNED_AS'

_SET = {'who': None, 'kind': None, 'default': None}


def configure(who: str = None, kind: str = None, default=None) -> None:
    """Who this process signs as: `who` and `kind` win over everything; `default`, a (who, kind) pair, is used only
    when neither the environment nor a Claude Code session names one (a pipeline job's own name)."""
    if who is not None:
        _SET['who'], _SET['kind'] = who, kind or 'pipeline'
    if default is not None:
        _SET['default'] = tuple(default)


def _fits(who) -> bool:
    return bool(who) and bool(re.fullmatch(r'[^*\n]{1,40}', str(who)))


def footer(who: str, kind: str = 'Claude session') -> str:
    """The signature line for `who`, `kind` one of KINDS."""
    if kind not in KINDS:
        raise ValueError(f'kind must be one of {KINDS}, not {kind!r}')
    if not _fits(who):
        raise ValueError(f'a signer is 1 to 40 characters with no * or newline, not {who!r}')
    return f'— **{who}** · {kind}'


def signed_by(body: str) -> dict:
    """{who, kind} of the signature on the body's last non-empty line; {} when that line is none (a person's own words,
    even one that quotes a signature above their reply)."""
    lines = [ln for ln in (body or '').splitlines() if ln.strip()]
    m = SIGNED.fullmatch(lines[-1].rstrip()) if lines else None
    return {'who': m.group('who'), 'kind': m.group('kind')} if m else {}


def _registry_name(env, home) -> str:
    pid = (env.get('CLAUDE_PID') or '').strip()
    if not pid.isdigit():
        return ''
    try:
        with open(os.path.join(home or os.path.expanduser('~'), '.claude', 'sessions', f'{pid}.json'), encoding='utf-8') as f:
            row = json.load(f)
    except (OSError, ValueError):
        return ''
    name = (row.get('name') or '').strip() if isinstance(row, dict) else ''
    return name if _fits(name) else ''


def signer(env=None, home: str = None):
    """(who, kind) for this process, or None: configure()'s, else CHUANG_SIGNED_AS (`Name` or `Name|kind`), else the
    Claude Code session it runs under (CLAUDE_PID's registry row; a script a session runs inherits it), else
    configure(default=)'s."""
    env = os.environ if env is None else env
    if _SET['who']:
        return _SET['who'], _SET['kind']
    said = (env.get(ENV) or '').strip()
    if said:
        who, _, kind = said.partition('|')
        kind = kind.strip() or 'Claude session'
        if _fits(who.strip()) and kind in KINDS:
            return who.strip(), kind
    name = _registry_name(env, home)
    if name:
        return name, 'Claude session'
    return _SET['default']


def sign(body: str, who: str = None, kind: str = None, env=None, home: str = None) -> str:
    """The body with its signature line added at the end, once: `who`/`kind` when given, else signer()'s. A body that
    already ends signed, or no signer at all, is answered as given."""
    body = body or ''
    if signed_by(body):
        return body
    pair = (who, kind or 'pipeline') if who else signer(env, home)
    if not pair:
        return body
    return f'{body.rstrip()}\n\n{footer(*pair)}'


def is_persons(body: str) -> bool:
    """Whether a comment can be its author's own words: True unless it carries a signature (a session's or the
    pipeline's, written with the person's token)."""
    return not signed_by(body)
