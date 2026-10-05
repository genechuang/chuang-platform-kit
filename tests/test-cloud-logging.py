#!/usr/bin/env python3
"""Regression tests: the functions log one JSON line per record with a severity,
and an ERROR carries the Error Reporting type.

WHY. 2026-09-11 9:28 PM PT: picklebot's `logger.error("Failed to get full player
data: EOF ...")` reached Cloud Logging as plain text with no severity, so the
hourly Error Reporting sweep saw nothing, opened no issue, and the alert
investigator routines had nothing to investigate. The first case is the
positive one: an INFO line is a JSON object with severity INFO.

Run:  python -B tests/test-cloud-logging.py
"""
import io
import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))

from chuang_platform_kit import cloud_logging   # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


buf = io.StringIO()
# functions-framework installs a root handler before main.py runs; model it.
stale = logging.StreamHandler(io.StringIO())
logging.getLogger().addHandler(stale)
cloud_logging.setup_logging(service='smad-picklebot', stream=buf)
log = logging.getLogger('shared.player_data')


def lines():
    out = [json.loads(l) for l in buf.getvalue().splitlines() if l.strip()]
    buf.seek(0); buf.truncate(0)
    return out


print("\n-- the positive case: an INFO line is JSON with severity INFO")
log.info("Processing command: /pb match")
got = lines()
check("one line", len(got), 1)
check("severity INFO", got[0]['severity'], 'INFO')
check("message intact", got[0]['message'], 'Processing command: /pb match')
check("logger named", got[0]['logger'], 'shared.player_data')
check("not an error event", '@type' in got[0], False)
check("the stale root handler was replaced (force)", stale in logging.getLogger().handlers, False)

print("\n-- an ERROR carries the Error Reporting type and the service")
log.error("Failed to get full player data: EOF occurred in violation of protocol (_ssl.c:2437)")
got = lines()
check("severity ERROR", got[0]['severity'], 'ERROR')
check("@type is ReportedErrorEvent", got[0]['@type'], cloud_logging.ERROR_EVENT_TYPE)
check("serviceContext.service", got[0]['serviceContext'], {'service': 'smad-picklebot'})

print("\n-- exc_info puts the traceback IN the message, still one line")
try:
    raise OSError("EOF occurred in violation of protocol (_ssl.c:2437)")
except OSError:
    log.error("Failed to get full player data", exc_info=True)
raw = buf.getvalue()
check("a single physical line", raw.count('\n'), 1)
got = lines()
check("message starts with the text", got[0]['message'].startswith('Failed to get full player data'), True)
check("and carries the traceback", 'Traceback (most recent call last)' in got[0]['message'] and 'OSError: EOF' in got[0]['message'], True)

print("\n-- WARNING has a severity but is not an error event; unicode survives")
log.warning("Duplicate-match check skipped: 🏓")
got = lines()
check("severity WARNING", got[0]['severity'], 'WARNING')
check("no @type", '@type' in got[0], False)
check("emoji not escaped", '🏓' in got[0]['message'], True)

print("\n-- service defaults to K_SERVICE")
os.environ['K_SERVICE'] = 'smad-whatsapp-sender'
buf2 = io.StringIO()
cloud_logging.setup_logging(stream=buf2)
logging.getLogger('x').error("boom")
check("K_SERVICE picked up", json.loads(buf2.getvalue())['serviceContext']['service'], 'smad-whatsapp-sender')
os.environ.pop('K_SERVICE')

print()
if fails:
    print(f"{len(fails)} failure(s):")
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)
print("0 failure(s)")
