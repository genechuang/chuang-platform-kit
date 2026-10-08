"""The scanner behind "in code, every calendar day is a day in the kit's zone" (Gene, 2026-10-07 PT).

Servers run UTC clocks, so `date.today()`, `datetime.today()`, a zoneless `datetime.now()`, `utcnow()`, a UTC midnight
and a `.date()` taken from a UTC instant are all UTC days in disguise; a fixed `timedelta(hours=7)` is a daylight-saving
bug waiting for November. Those are FORBIDDEN. Using UTC for an instant is fine, but the line must say so, with
`# utc-ok: <why>` (an instant against an instant, an outside API's UTC filter) or `# local-ok: <why>` (the desktop's own
clock): a UTC use with no such comment NEEDS_REASON. The 2026-10-07 incident: an API read `since` as midnight UTC, 5 PM
the day before in Pasadena, so a 7 PM game sat on the next day and an automatic cancel could not find it.

Strings and comments are not code: the file is tokenized, so a docstring that mentions `datetime.now()` (this one) is
never a finding, and the marker is read from the line's own comment token. Both host projects run the same scan from a
one-line test, and `python -m chuang_platform_kit.timecheck src tests` runs it from a shell or CI (exit 1 on findings).
"""
import io
import re
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path

MARKER = re.compile(r'(utc-ok|local-ok):')

FORBIDDEN = (
    ('date.today(): a UTC day on a server', re.compile(r'\bdate\.today\(')),
    ('datetime.today(): a UTC day on a server', re.compile(r'\bdatetime\.today\(')),
    ('datetime.now() with no zone', re.compile(r'\bdatetime\.now\(\s*\)')),
    ('utcnow()', re.compile(r'\butcnow\(')),
    ('a UTC midnight', re.compile(r'combine\([^\n]*tzinfo\s*=\s*(timezone\.utc|UTC)')),
    ('a day taken from a UTC instant', re.compile(r'now\(\s*(timezone\.utc|UTC)\s*\)\.date\(')),
    ('a fixed Pacific offset (DST breaks it)', re.compile(r'timedelta\(\s*hours\s*=\s*-?[78]\s*\)')),
)
NEEDS_REASON = re.compile(
    r'\bnow\(\s*(timezone\.utc|UTC)\s*\)|\btz\s*=\s*(timezone\.utc|UTC)\b|\btzinfo\s*=\s*(timezone\.utc|UTC)\b'
    r'|\bastimezone\(\s*(timezone\.utc|UTC)\s*\)|\btimezone\.utc\b')
EXCLUDE = ('.venv', 'node_modules', '.git', '__pycache__', 'site-packages', 'dist', 'build')


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    text: str

    def __str__(self):
        return f'{self.path}:{self.line}: {self.rule}: {self.text.strip()}'


def scan(paths, exclude=EXCLUDE) -> list:
    """Every finding under `paths` (files or directories, .py only), sorted. A line whose own comment carries
    `utc-ok:` or `local-ok:` passes whatever it does."""
    out = []
    for root in paths:
        root = Path(root)
        files = [root] if root.is_file() else sorted(p for p in root.rglob('*.py') if not _excluded(p, exclude))
        for f in files:
            out += scan_file(f)
    return sorted(out, key=lambda x: (x.path, x.line))


def scan_file(path) -> list:
    path = Path(path)
    try:
        source = path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return []
    return [Finding(str(path), n, rule, text) for n, rule, text in check_source(source)]


def check_source(source: str):
    """(line, rule, text) for each finding in one module's source. Code is what is left after strings and comments."""
    code, marked = _code_lines(source)
    lines = source.splitlines()
    for n in sorted(code):
        text = code[n]
        if not text.strip() or n in marked:
            continue
        shown = lines[n - 1] if n - 1 < len(lines) else text
        for rule, rx in FORBIDDEN:
            if rx.search(text):
                yield n, rule, shown
                break
        else:
            if NEEDS_REASON.search(text) and not re.match(r'\s*(from|import)\b', text):
                yield n, 'UTC with no utc-ok: / local-ok: reason', shown


def _code_lines(source: str):
    """Per line: the source with every string and comment token blanked out (columns kept, so the patterns match the
    code as written), and the set of lines whose comment is a marker."""
    lines = [list(line) for line in source.splitlines()]
    marked = set()
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError):
        for n, chars in enumerate(lines, 1):   # unparseable: the raw line with its comment split off
            body, _, comment = ''.join(chars).partition('#')
            lines[n - 1] = list(body)
            if MARKER.search(comment):
                marked.add(n)
        return {n: ''.join(chars) for n, chars in enumerate(lines, 1)}, marked
    for tok in tokens:
        if tok.type == tokenize.COMMENT and MARKER.search(tok.string):
            marked.add(tok.start[0])
        if tok.type in _NOT_CODE:
            _blank(lines, tok.start, tok.end)
    return {n: ''.join(chars) for n, chars in enumerate(lines, 1)}, marked


def _blank(lines, start, end):
    """Replace the span (1-based row, 0-based col) with spaces; a multi-line token blanks the lines between."""
    (r0, c0), (r1, c1) = start, end
    for r in range(r0, r1 + 1):
        if r - 1 >= len(lines):
            break
        row = lines[r - 1]
        a = c0 if r == r0 else 0
        b = c1 if r == r1 else len(row)
        for i in range(a, min(b, len(row))):
            row[i] = ' '


_NOT_CODE = {tokenize.STRING, tokenize.COMMENT} | {getattr(tokenize, name) for name in
                                                   ('FSTRING_START', 'FSTRING_MIDDLE', 'FSTRING_END') if hasattr(tokenize, name)}
"""Token kinds that are not code: strings, comments, and (3.12+) the pieces of an f-string's literal text."""


def _excluded(p: Path, exclude) -> bool:
    return any(part in exclude for part in p.parts)


def main(argv=None) -> int:
    args = [a for a in (argv if argv is not None else sys.argv[1:]) if not a.startswith('-')] or ['.']
    findings = scan(args)
    for f in findings:
        print(f)
    print(f'{len(findings)} finding(s)')
    return 1 if findings else 0


if __name__ == '__main__':
    sys.exit(main())
