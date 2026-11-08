"""Pipelines kept in step: a commit that changes one or two of a project's release pipelines says how the others have
the same change, or why they cannot, in a `Pipeline-Parity:` trailer.

A host names its pipelines as path sets, {name: [glob, ...]} (fnmatch's `*` crosses folders, so a folder's glob covers
its subfolders), and reads the commit's staged files and their diffs itself:

    pipes = touched(files, PIPELINES, diff_of)
    why = verdict(message, pipes, PIPELINES)        '' when the commit may go, else the refusal

A commit that touches all of them, or none, needs nothing; a workflow change that is only generated `paths:` lines is
no pipeline change. Standard library only.
"""
import fnmatch
import re

# One line each: [ \t] never crosses a newline, so a bare `Pipeline-Parity:` never borrows the line after it.
TRAILER = re.compile(r'^[ \t]*Pipeline-Parity:[ \t]*(.*)$', re.I | re.M)
MIN_REASON = 10   # characters of the stripped value: a reason, not "n/a"
PATHS_LINE = re.compile(r"^\s*- '[^']*'\s*$")   # one entry of a generated `paths:` list
EXAMPLE = 'Pipeline-Parity: {first} has it too; {last} n/a, <why>'


def has_trailer(message: str) -> bool:
    """Whether any `Pipeline-Parity:` line of the message carries a reason of MIN_REASON characters or more, its own
    spaces and a carriage return not counted."""
    return any(len(m.group(1).strip()) >= MIN_REASON for m in TRAILER.finditer(message or ''))


def which(path: str, pipelines: dict) -> str:
    """The pipeline a file belongs to, or '' when it is shared or no pipeline's (case counts, on every platform)."""
    path = path.replace('\\', '/')
    for name, globs in pipelines.items():
        if any(fnmatch.fnmatchcase(path, g) for g in globs):
            return name
    return ''


def only_paths(diff: str) -> bool:
    """True when every changed line of a file's diff is a generated `paths:` entry."""
    lines = [ln[1:] for ln in diff.splitlines() if ln[:1] in '+-' and not ln.startswith(('+++', '---'))]
    return bool(lines) and all(PATHS_LINE.match(ln) for ln in lines)


def touched(files, pipelines: dict, diff_of) -> set:
    """The pipelines a commit's files change; `diff_of(file)` is that file's diff (a .yml whose diff is only `paths:`
    lines changes none)."""
    out = set()
    for f in files:
        p = which(f, pipelines)
        if p and not (f.endswith('.yml') and only_paths(diff_of(f))):
            out.add(p)
    return out


def verdict(message: str, pipes: set, pipelines: dict, example: str = EXAMPLE, cite: str = '') -> str:
    """'' when the commit may go; else the refusal, with `example` (its {first} and {last} the missing pipelines) and
    the rule's `cite` on its own last line when given. A name in `pipes` that is not one of `pipelines` is ignored."""
    pipes = set(pipes or ()) & set(pipelines)
    if not pipes or len(pipes) == len(pipelines) or has_trailer(message):
        return ''
    missing = sorted(set(pipelines) - pipes)
    out = (f"pipeline-parity: this commit changes the {' and '.join(sorted(pipes))} pipeline"
           f"{'s' if len(pipes) > 1 else ''} but not {' or '.join(missing)}. Make the same change there, or add a "
           f"trailer saying how they have it or why they cannot, e.g.\n"
           f"  {example.format(first=missing[0], last=missing[-1])}")
    return out + (f'\n{cite}' if cite else '')
