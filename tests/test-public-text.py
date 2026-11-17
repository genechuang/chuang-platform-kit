#!/usr/bin/env python3
"""public_text: emails and phone numbers scrubbed from text bound for a public page, a group or a public ticket.

Every number and address here is invented (the 555 exchange, example.test).

Run:  python -B tests/test-public-text.py
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit.public_text import EMAIL_RE, PHONE_RE, findings, redact  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


# The positive case: a WhatsApp @-mention and an email are both scrubbed.
check("an @-mention's number and an email are scrubbed",
      redact('Player "@19175550123" not found; ask someone+test@example.test'),
      'Player "@[phone redacted]" not found; ask [email redacted]')

for s in ('626-555-0104', '(626) 555-0104', '+1 626 555 0104', '+16265550104', '6265550104', '16265550104',
          '16265550104@c.us'):
    check(f"a phone shape: {s}", 'redacted' in redact(f'call {s} now'), True)

s = 'at 1791598776 build 20261009-1932 sha 18bd3ed5 in 2026 rated 3.51 paid $20 score 11-9'
check("a Unix timestamp, a build stamp, a sha, a year, a rating, an amount and a score stay", redact(s), s)

check("findings() names what redact() would hide, kind and text",
      findings('a@b.co and 6265550104'), [('email', 'a@b.co'), ('phone', '6265550104')])
check("clean text has no findings and comes back as it went", (findings('nothing here'), redact('nothing here')),
      ([], 'nothing here'))
check("the patterns are the module's own", (bool(EMAIL_RE.search('x@y.io')), bool(PHONE_RE.search('626-555-0104'))),
      (True, True))

print(f"\n{len(fails)} failure(s)")
if fails:
    sys.exit(1)
