#!/usr/bin/env python3
"""Regression tests for the email failure path in webhook/shared/email_notify.py.

WHY. Three workflows DMed every player and emailed nobody, with green runs, for
as long as they existed: they wrote no Gmail config, and the mailer answered a
missing GMAIL_USERNAME with a per-email WARNING and `return False` that nothing
read. Found 2026-09-04 PT. The fix is not to change the return -- callers count
a True from _send_dm() as a reminder sent, and email is a rider on the DM -- but
to make the config gap an ERROR, once per process, that names the variable.

A suite of silence checks proves nothing until one case exercises the positive,
so the first case here asserts the ERROR actually fires.

Run:  python -B tests/test-email-notify.py
"""
import logging
import os
import sys

os.environ.pop('GMAIL_USERNAME', None)
os.environ.pop('EMAIL_DISABLED', None)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))

from chuang_platform_kit import email_notify as EN

fails = []
def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class Cap(logging.Handler):
    def __init__(self): super().__init__(); self.recs = []
    def emit(self, r): self.recs.append(r)

cap = Cap()
EN.logger.addHandler(cap)
EN.logger.setLevel(logging.DEBUG)

# The positive: unset username -> False AND an ERROR naming the variable.
EN.GMAIL_USERNAME = ''
EN._reported_no_username = False
r = EN.send_notification_email('someone@example.com', 'subj', '<p>hi</p>')
errs = [x for x in cap.recs if x.levelno >= logging.ERROR]
check("unset GMAIL_USERNAME returns False", r, False)
check("  and logs at ERROR, not WARNING", len(errs) >= 1, True)
check("  and the error names the variable",
      any('GMAIL_USERNAME' in x.getMessage() for x in errs), True)
check("  and says every email in the run is affected, not just this one",
      any('EVERY email' in x.getMessage() for x in errs), True)

# Once per process: a second skip must not repeat the ERROR (the per-email
# scroll is what buried it before), but must still return False.
cap.recs.clear()
r2 = EN.send_notification_email('other@example.com', 'subj', '<p>hi</p>')
check("second skip still returns False", r2, False)
check("  but does NOT log the ERROR again (once per process)",
      len([x for x in cap.recs if x.levelno >= logging.ERROR]), 0)

# The other branches keep their semantics.
check("dry_run returns True without a username", 
      EN.send_notification_email('a@b.c', 's', 'b', dry_run=True), True)
check("no recipient returns False", EN.send_notification_email('', 's', 'b'), False)

print("\n%d failure(s)" % len(fails))
sys.exit(1 if fails else 0)
