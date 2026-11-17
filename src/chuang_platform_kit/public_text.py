"""Text bound for a public page, a group chat or a public ticket: emails and phone numbers scrubbed (stdlib only).

One definition for every builder that publishes a host's own words: a release page, release notes, a post-mortem
page, a ticket filed from a person's message. It is not `redact`, which scrubs tokens and keys from logs and mail:
this one hides what names a person.

A phone number is any US-shaped one: separated (626-555-0104, (626) 555-0104), with a +country code, or bare digits
as WhatsApp writes them (16265550104, @16265550104, 16265550104@c.us). A bare run must look like a North American
number (area code and exchange starting 2-9), so a 10-digit Unix timestamp or a build id is left alone.
"""
import re

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(
    r"(?:\+?1[-.\s]?)?(?:\(\d{3}\)\s?|\d{3}[-.\s])\d{3}[-.\s]\d{4}\b"
    r"|\+\d{10,15}\b"
    r"|(?<![\w.])1?[2-9]\d{2}[2-9]\d{6}(?![\w.])"
)


def redact(s: str) -> str:
    """`s` with every email as "[email redacted]" and every phone number as "[phone redacted]"."""
    s = EMAIL_RE.sub("[email redacted]", s)
    return PHONE_RE.sub("[phone redacted]", s)


def findings(s: str) -> list:
    """What redact() would hide, as ('email' | 'phone', text) pairs: for a check that refuses the text instead."""
    return [('email', m.group(0)) for m in EMAIL_RE.finditer(s)] + [('phone', m.group(0)) for m in PHONE_RE.finditer(s)]
