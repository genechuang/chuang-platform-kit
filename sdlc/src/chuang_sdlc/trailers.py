"""A commit message's trailers: which lines are plumbing a renderer drops, and a trailer's values.

A commit body is read by people (a release dashboard, a changelog) and by tools (a ticket check, a parity check, a
post-ship runner). The trailers the tools read are plumbing to the people: a session's name, the ticket it names, a
note to the next maintainer, an instruction to the build. One matcher says which lines those are, so every renderer
drops the same ones; a host adds its own shapes beside the standard ones.

    matcher()                              the standard shapes
    matcher(*STANDARD, shape('Deploy-Hint', r'\\w+'))   with one of the host's
    is_plumbing(line, pattern)             True for a plumbing line
    values(message, 'Post-Ship')           every value of one trailer, in order

Standard library only.
"""
import re

# The plumbing shapes by name, each one whole line. Session names have no URL and no more than 40 characters; the old
# `Claude-Session:` trailer and a bare session URL (a squash-merged body can carry one) are still dropped, anchored to
# /code/session so a line citing another claude.ai page is kept.
SHAPES = {
    'claude-session': r"^\s*Claude-Session:\s*\S*\s*$",
    'session-url': r"^\s*https?://claude\.ai/code/session[_/]\S*\s*$",
    'session': r"^\s*Session:\s*[\w .'-]{1,40}\s*$",
    'pipeline-parity': r"^\s*Pipeline-Parity:.*$",
    'post-ship': r"^\s*Post-Ship:.*$",
    'ticket': r"^\s*Ticket:\s*(#\d+|none\b).*$",
}
STANDARD = tuple(SHAPES)


def shape(key: str, value: str = r'.*') -> str:
    """One trailer's line shape, `<key>: <value>` alone on its line (the key matched as written, any case)."""
    return rf"^\s*{re.escape(key)}:\s*{value}\s*$"


def matcher(*shapes) -> re.Pattern:
    """The plumbing lines as one pattern: each of `shapes` a name in SHAPES or a regex of its own (shape() makes one);
    STANDARD when none is given. Case is ignored."""
    return re.compile('|'.join(SHAPES.get(s, s) for s in (shapes or STANDARD)), re.I)


_STANDARD = matcher()


def is_plumbing(line: str, pattern: re.Pattern = None) -> bool:
    """Whether one body line is a plumbing trailer, by `pattern` (matcher()'s), the standard shapes when none."""
    return bool((pattern or _STANDARD).match(line or ''))


def values(message: str, key: str) -> list:
    """Every value of the trailer `key` in a commit message, in order, each stripped: values(m, 'Post-Ship'). One line
    each: an empty trailer has no value and never borrows the next line."""
    return re.findall(rf"^[ \t]*{re.escape(key)}:[ \t]*(\S[^\n]*?)[ \t\r]*$", message or '', re.I | re.M)
