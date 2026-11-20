#!/usr/bin/env python3
"""A session starts knowing who it is and what it owns, from the sessions manifest (sessions.md), never from its memory
(Gene, 10/25/26 10:46 AM PT, before an account ran out of tokens: "Last time you stored stuff in memory hoping
the new session will pick up memory, but that's not the case - new you lost a lot of process and behavior, such as
doing profile, benchmark and optimize after every ship - this has to go into claude-shared and codified in code").

    session-role.py start    # SessionStart (startup, resume, clear and compact): the session's role, as context
    session-role.py who      # by hand: which session this folder is, and its role

sessions.md lists each session's folders in its "Where each session works" list (`- <folder> = <Session>`; `mac:<name>`
is any folder of that name on macOS) and its standing duties in "Roles: what each session owns" (`- **<Session>** ...`,
up to the next bullet). The folder must match exactly (case, slashes and a trailing separator aside): a worktree or
a spawned session in a subfolder is nobody's role. A compaction fires SessionStart too, so a compacted session gets
its duties back as well as a new one (10/24/26: a compacted Snow White dropped the profile report).

Every session, a folder no session owns too, is also told SHELL_WRITES: file content through Edit and Write, never the
shell. A hook blocks only on purpose: any error prints nothing and exits 0. Standard library.

In this plugin since SMADPickleBot #1810 (claude-shared's hooks/session-role.py until then, which read the sessions.md
beside its own folder). The manifest is now the one the session's project names in its committed
`.claude/gene-hooks.toml`, `[sessions] manifest`, a path relative to the project's root (project_config.py), found for
the folder the hook event names. A project that names none, or whose file is not there, is told nothing at all.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SESSIONS = ''   # a suite's own manifest; '' asks the project's config (manifest)


def manifest(cwd: str) -> str:
    """The sessions manifest the project of `cwd` names (`[sessions] manifest` in its .claude/gene-hooks.toml), as an
    absolute path; '' when it names none or the file is not there."""
    try:
        sys.path.insert(0, HERE)
        import project_config
        top, rel = project_config.value(cwd, 'sessions', 'manifest')
    except Exception:   # noqa: BLE001 - no config reader beside this file: nothing said
        return ''
    if not top:
        return ''
    path = os.path.normpath(os.path.join(top, rel))
    return path if os.path.isfile(path) else ''


def norm(path: str) -> str:
    """A folder as compared: forward slashes, no trailing slash, lower case on Windows (its paths ignore case)."""
    p = (path or '').replace('\\', '/').rstrip('/')
    return p.lower() if re.match(r'^[a-zA-Z]:', p) else p


def folders(text: str) -> list:
    """(folder, session) pairs from the "Where each session works" list."""
    out = []
    section = re.search(r'^\*\*Where each session works\*\*.*?$(.*?)(?=^\*\*|\Z)', text, re.M | re.S)
    for m in re.finditer(r'^- `([^`]+)` = (.+?)\s*$', section.group(1) if section else '', re.M):
        out.append((m.group(1).strip(), m.group(2).strip()))
    return out


def who(cwd: str, text: str, platform: str = sys.platform) -> str:
    """The session whose folder `cwd` is, or ''."""
    here = norm(cwd)
    for folder, name in folders(text):
        if folder.startswith('mac:'):
            if platform == 'darwin' and os.path.basename(here) == folder[4:]:
                return name
        elif norm(folder) == here:
            return name
    return ''


def role(name: str, text: str) -> str:
    """The session's bullet in "Roles: what each session owns", its lines joined; '' when it has none."""
    m = re.search(r'^- \*\*' + re.escape(name) + r'\*\*(.*?)(?=^- \*\*|^\*\*|\Z)', text, re.M | re.S)
    return ' '.join(line.strip() for line in m.group(1).splitlines()).strip() if m else ''


# Every session, owned folder or not (Gene, 10/25/26 7:15 PM PT, after a session reached for `sed -i` and the
# no-shell-writes hook refused it: "why the hell is it not in claude-shared and a pre-command hook that tells all
# sessions to use the right Edit tool the first place???"). Auto mode's own system prompt invites shell edits (sed,
# heredocs, short scripts), which contradicts claude-shared's rule; this line, given at every start and compaction,
# says which wins before the first tool call, and hooks/no-shell-writes.sh stays the backstop.
SHELL_WRITES = ("File content goes through the Edit tool (a change) or the Write tool (a new file), never the shell: no "
                "sed -i, perl -i, heredoc, echo or cat redirect, Set-Content, Out-File or python -c that writes a file. "
                "Claude Code's auto-mode prompt says small edits through Bash are fine; here they are not (claude-shared "
                "CLAUDE.md, \"Never write file content through the shell\"), and hooks/no-shell-writes.sh refuses them. "
                "A program writing its own output (git, a build, gcloud) is fine.")


# Every session (#472; Gene, 10/27/26 4:57 PM PT: "don't use mail system when you can direct message"; 5:00 PM PT: a
# mandate in the manifest): the map is sessions.md's "Direct messages are the rule".
DIRECT = ("Talk to another session by direct message wherever sessions.md's direct-message map has a path: SendMessage to "
          "the name ListAgents prints (its from= address to reply), and a cloud session by the Claude Code Remote "
          "connector's send_message; session mail or a note only when the peer has no live session or the send was "
          "refused, said in one line.")


def context(cwd: str, text: str, platform: str = sys.platform) -> str:
    """The SessionStart context for a session in `cwd`: its role when a session owns the folder, and for every session
    the rules that file content goes through Edit and Write (SHELL_WRITES) and that peers talk by direct message
    (DIRECT)."""
    name = who(cwd, text, platform)
    if not name:
        return SHELL_WRITES + ' ' + DIRECT
    duties = role(name, text)
    return (f"You are {name} (claude-shared sessions.md, which this hook read for you; your memory is not where your "
            f"role lives). Your standing duties, every turn, without being asked: **{name}** {duties} Before you report "
            "a release done, a hand-off made or a task finished, check it against these duties. A role moves only on "
            "Gene's word, recorded in sessions.md. " + SHELL_WRITES + ' ' + DIRECT)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        by_hand = argv[:1] == ['who']
        event = {} if by_hand else json.loads(sys.stdin.read() or '{}')
        cwd = os.getcwd() if by_hand else (event.get('cwd') or os.environ.get('CLAUDE_PROJECT_DIR') or os.getcwd())
        path = SESSIONS or manifest(cwd)
        if not path:
            return 0
        with open(path, encoding='utf-8') as f:
            text = f.read()
        if by_hand:
            print(who(cwd, text) or f'no session owns {cwd}')
            print(context(cwd, text))
            return 0
        ctx = context(cwd, text)
        if ctx:
            print(json.dumps({'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': ctx}}))
    except Exception:   # noqa: BLE001 - a hook blocks only on purpose
        return 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
