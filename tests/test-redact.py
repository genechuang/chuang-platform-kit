#!/usr/bin/env python3
"""No token or key reaches a log line, an email or a GitHub issue.

On 2026-09-25 PT a GREEN-API connect timeout wrote the API token (it is in the
URL path) into Cloud Logging, Error Reporting, two alert emails and a diagnose
issue. The fix is one scrub, webhook/shared/redact.py, at every exit. Pinned:
an ordinary line passes unchanged (the positive case); the GREEN-API URL, the
token-shaped strings and the literal values of the process's secrets are
masked, including the secret fields of a JSON credential and values from a
.env file; the functions' JSON log formatter scrubs message AND traceback; the
plain-logging filter the CLI installs does the same; both email senders scrub
subject and body; the alert-issue action and the diagnose workflow pipe their
text through it; and the sender retries a CONNECT timeout only, logging one
ERROR per lost message and no re-raise.

Run:  python -B tests/test-redact.py
"""
import base64
import io
import json
import logging
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import redact as R  # noqa: E402
from chuang_platform_kit import cloud_logging  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


# Visibly synthetic, low entropy: a 'fake' built from the leaked token kept
# its real first 25 characters and GitGuardian flagged it (2026-09-26 PT).
FAKE_TOKEN = 'f00d' * 12
URL = f"https://api.green-api.com/waInstance7105482623/sendMessage/{FAKE_TOKEN}"
ERR = (f"HTTPSConnectionPool(host='api.green-api.com', port=443): Max retries exceeded with url: "
       f"/waInstance7105482623/sendMessage/{FAKE_TOKEN} (Caused by ConnectTimeoutError(...))")
ENV = {'GREENAPI_API_TOKEN': FAKE_TOKEN}

print("\n-- redact()")
check("the positive case: an ordinary line passes unchanged",
      R.redact("Message sent to 13106001023@c.us (11-7)", env=ENV), "Message sent to 13106001023@c.us (11-7)")
check("the GREEN-API URL keeps its method, loses the id and the token",
      R.redact(URL, env={}), "https://api.green-api.com/waInstance<id>/sendMessage/***")
check("the 2026-09-25 exception text is clean", FAKE_TOKEN in R.redact(ERR, env={}), False)
check("token-shaped strings: GitHub classic and fine-grained, Anthropic, Bearer",
      [R.redact(t, env={}) for t in ('x ghp_' + 'a' * 36, 'github_pat_' + 'b' * 40, 'key sk-ant-api03-' + 'c' * 30,
                                      'Authorization: Bearer ' + 'd' * 30)],
      ['x ***', '***', 'key ***', 'Authorization: Bearer ***'])
check("a secret's literal value is masked wherever it appears (the key nobody thought of)",
      R.redact(f"token={FAKE_TOKEN}; again {FAKE_TOKEN}", env=ENV), "token=***; again ***")
check("a short value is never matched (it would shred ordinary text)",
      R.redact("true and 2026", env={'ATHENAEUM_PASSWORD': 'true'}), "true and 2026")
gmail = json.dumps({'token': 'ya29.' + 'e' * 40, 'refresh_token': '1//' + 'f' * 40, 'client_id': 'abc.apps', 'scopes': ['x']})
check("a JSON credential: its secret fields are masked, its addresses are not",
      R.redact(f"refresh 1//{'f' * 40} access ya29.{'e' * 40} client abc.apps", env={'GMAIL_OAUTH_TOKEN_JSON': gmail}),
      "refresh *** access *** client abc.apps")
with tempfile.TemporaryDirectory() as d:
    old = os.getcwd()
    os.chdir(d)
    try:
        with open('.env', 'w') as f:
            f.write(f"SMAD_SHEET_NAME=2026 Pickleball\nGREENAPI_API_TOKEN={FAKE_TOKEN}\n")
        check("a workflow's .env is read too (its step log is what report-failure quotes)",
              R.redact(f"got {FAKE_TOKEN}"), "got ***")
    finally:
        os.chdir(old)
check("None stays None", R.redact(None), None)

print("\n-- the functions' JSON log formatter")
os.environ['GREENAPI_API_TOKEN'] = FAKE_TOKEN
buf = io.StringIO()
cloud_logging.setup_logging(stream=buf)
log = logging.getLogger('test-redact')
log.info("Processing message abc from picklebot")
try:
    raise ConnectionError(ERR)
except ConnectionError:
    log.error("Message abc failed", exc_info=True)
lines = [json.loads(ln) for ln in buf.getvalue().splitlines()]
check("the positive case: an ordinary line is emitted unchanged", lines[0]['message'], "Processing message abc from picklebot")
check("an ERROR with a traceback carrying the token URL: the token is nowhere in the emitted JSON",
      (FAKE_TOKEN in buf.getvalue(), 'waInstance<id>/sendMessage/***' in lines[1]['message'], lines[1]['severity']),
      (False, True, 'ERROR'))

print("\n-- the plain-logging filter the CLI installs")
pbuf = io.StringIO()
h = logging.StreamHandler(pbuf)
h.setFormatter(logging.Formatter('%(levelname)s %(message)s'))
plain = logging.getLogger('test-redact-plain')
plain.propagate = False
plain.addHandler(h)
cloud_logging.install_redaction(plain)
cloud_logging.install_redaction(plain)
plain.warning("sending with %s", URL)
try:
    raise TimeoutError(ERR)
except TimeoutError:
    plain.exception("failed")
check("message and traceback both scrubbed; installing twice adds one filter",
      (FAKE_TOKEN in pbuf.getvalue(), 'waInstance<id>' in pbuf.getvalue(), len(h.filters)), (False, True, 1))

print("\n-- the email sender")
from chuang_platform_kit import email_notify as E  # noqa: E402
sent = []


class _Send:
    def __init__(self, body): self.body = body
    def execute(self): sent.append(self.body); return {}


class _Gmail:
    def users(self): return self
    def messages(self): return self
    def send(self, userId=None, body=None): return _Send(body)


E._get_gmail_service = lambda: _Gmail()
E.GMAIL_USERNAME, E.EMAIL_DISABLED = 'bot@example.com', False
E.send_notification_email('gene@example.com', f"Alert: {URL}", f"<pre>{ERR}</pre>")
raw = base64.urlsafe_b64decode(sent[-1]['raw']).decode('utf-8', 'replace')
check("the positive case: the email goes, subject and body scrubbed",
      (len(sent), FAKE_TOKEN in raw, 'waInstance<id>/sendMessage/***' in raw), (1, False, True))

print("\n-- the stdin filter (what a host's failure reporter pipes a step log through)")
import subprocess  # noqa: E402
out = subprocess.run([sys.executable, '-m', 'chuang_platform_kit.redact'], input=ERR, capture_output=True, text=True,
                     env={**os.environ, 'PYTHONPATH': os.path.join(ROOT, 'src')}).stdout
check("python -m chuang_platform_kit.redact scrubs stdin to stdout", (FAKE_TOKEN in out, 'waInstance<id>' in out), (False, True))
# (How each host wires it -- its report-failure action, its log workflow, its
# own mail sender, its WhatsApp sender's retry -- is that host's suite.)

print("\n-- a connection URL (DATABASE_URL, kit 0.5.0 db.py): the URL and its password on its own")
DSN = 'postgresql://FAKE-app:FAKE-p%40ssw0rd-long@db.example.test/ledger?sslmode=require'   # a fixture, never a database
check("DATABASE_URL is a secret name", 'DATABASE_URL' in R.secret_names({'DATABASE_URL': DSN}), True)
check("the whole URL is masked, and the password as the driver quotes it (decoded) and as the URL spells it",
      R.redact('dsn ' + DSN + ' failed for FAKE-p@ssw0rd-long / FAKE-p%40ssw0rd-long', {'DATABASE_URL': DSN}),
      'dsn *** failed for *** / ***')
check("a short password is not masked on its own (it would shred ordinary text); the URL still is",
      R.redact('x postgresql://u:short@h/d y short', {'DATABASE_URL': 'postgresql://u:short@h/d'}), 'x *** y short')
check("a URL without a password contributes only itself", R.redact('postgresql://h/database-name z', {'DATABASE_URL': 'postgresql://h/database-name'}), '*** z')

print()
if fails:
    print(f"{len(fails)} failure(s):")
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)
print("0 failure(s)")
