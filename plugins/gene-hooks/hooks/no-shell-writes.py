#!/usr/bin/env python3
"""No file content through the shell: a PreToolUse hook on Bash and PowerShell.

Gene, 10/10/26 8:11 AM PT, after a session wrote a scratch YAML with `python -c ... open(...,'w')`
on the day the shared rule "Never write file content through the shell" already existed (and a heredoc
edit of a workflow the night before): "Yes do write hook and make sure all sessions use it". A rule a
session must remember slips when it is in a hurry; this does not.

Refused (exit 2, the reason on stderr, which Claude reads):
  - text a session authors sent into a file: `cat > f`, `echo ... > f`, `printf ... >> f`, a heredoc
    into a file or `tee`;
  - in-place edits: `sed -i`, `perl -i`;
  - inline programs that write files: `python -c` / `python - <<` / `node -e` with open(..., 'w'|'a'|'x'),
    write_text, write_bytes, json.dump/yaml.safe_dump into open(), fs.writeFileSync/appendFileSync;
  - PowerShell's Set-Content, Add-Content, Out-File, [IO.File]::Write*, New-Item -Value.
Allowed: a program writing its own output (a build's dist/, tar, gcloud storage cp, git), a program's
stdout captured to a file (`python report.py > out.txt`), `> /dev/null`, `2>&1`, `$null`, and a heredoc
fed to a command that writes no file (`git commit -F - <<'EOF'`).

Reads the hook's JSON on stdin ({"tool_name", "tool_input": {"command"}}); anything unreadable passes.
"""
import json
import re
import sys

SINKS = r'(?:/dev/null|\$null|nul|&\d|/dev/std(?:out|err))'
# A redirection into a file: > or >> not preceded by a digit (2>) and not to a sink.
REDIRECT = re.compile(r'(?<![\d&>=-])>>?\s*(?!' + SINKS + r'(?:\s|$|[;|&)]))[^\s;|&)]')
AUTHORS = re.compile(r'(?:^|[;&|(]\s*|\bthen\s+|\bdo\s+)(?:cat|echo|printf)\b[^;|&]*?(?<![\d&>=-])>>?\s*(?!' + SINKS + r'\b)\S')
HEREDOC = re.compile(r'<<-?\s*[\'"]?(\w+)')
TEE_FILE = re.compile(r'\btee\b(?:\s+-a)?\s+(?!' + SINKS + r')[^\s|;&]')
IN_PLACE = re.compile(r'\b(?:sed|perl)\s+(?:-[a-zA-Z]*\s+)*-[a-zA-Z]*i(?:\b|[.=\'"])')
INLINE = re.compile(r'\b(?:python3?|py)\s+(?:-[a-zA-Z]+\s+)*(?:-c\b|-\s*<<)|\bnode\s+(?:-e|--eval)\b')
WRITES = re.compile(r"open\([^)]*,\s*['\"][wax]|write_text\(|write_bytes\(|writeFileSync|appendFileSync|createWriteStream"
                    r"|(?:json|yaml)\.(?:safe_)?dump\([^)]*open\(")
POWERSHELL = re.compile(r'\b(?:Set-Content|Add-Content|Out-File)\b|\[(?:System\.)?IO\.File\]::(?:Write|Append)|\bNew-Item\b[^;|]*-Value\b', re.I)
COMMIT_MESSAGE = re.compile(r'\bgit\b[^;|&]*\bcommit\b[^;|&]*-F\s*-')


def strip_quotes(cmd: str) -> str:
    """The command with quoted strings emptied, so a '>' inside an argument is not a redirection."""
    return re.sub(r"'[^']*'|\"(?:\\.|[^\"\\])*\"", '""', cmd)


def heredoc_bodies(cmd: str) -> str:
    """The command with every heredoc body removed (a commit message may say 'x > y')."""
    lines, out, end = cmd.split('\n'), [], None
    for line in lines:
        if end is not None:
            if line.strip() == end:
                end = None
            continue
        out.append(line)
        m = HEREDOC.search(line)
        if m:
            end = m.group(1)
    return '\n'.join(out)


def refusal(tool: str, cmd: str):
    """Why `cmd` writes authored text into a file, or None."""
    if tool == 'PowerShell':
        if POWERSHELL.search(cmd):
            return 'PowerShell Set-Content / Add-Content / Out-File / [IO.File]::Write / New-Item -Value writes a file'
        return None
    if INLINE.search(cmd) and WRITES.search(cmd):
        return 'an inline python/node program writes a file'
    if IN_PLACE.search(strip_quotes(heredoc_bodies(cmd))):
        return 'sed -i / perl -i edits a file in place'
    head = strip_quotes(heredoc_bodies(cmd))
    has_heredoc = bool(HEREDOC.search(cmd))
    if AUTHORS.search(head):
        return 'cat / echo / printf sends text into a file'
    if has_heredoc and not COMMIT_MESSAGE.search(head) and (REDIRECT.search(head) or TEE_FILE.search(head)):
        return 'a heredoc is written into a file'
    if TEE_FILE.search(head) and re.search(r'\b(?:echo|printf|cat)\b[^|]*\|\s*tee\b', head):
        return 'echo / printf / cat piped into tee writes a file'
    return None


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    tool = event.get('tool_name', '')
    cmd = (event.get('tool_input') or {}).get('command', '')
    if tool not in ('Bash', 'PowerShell') or not cmd:
        return 0
    why = refusal(tool, cmd)
    if not why:
        return 0
    print(f'Refused: {why}. File content goes through the Write tool (a new file) or the Edit tool (a change), '
          'never the shell (claude-shared/CLAUDE.md, "Never write file content through the shell"; '
          'Gene, 10/10/26: enforced by claude-shared/hooks/no-shell-writes.py). A program writing its own '
          'output (a build, tar, gcloud, git) is fine; text you author is not.', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
