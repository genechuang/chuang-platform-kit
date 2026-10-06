#!/usr/bin/env python3
"""email_notify.send_message and redact.register_secret_values: the two additions of 0.2.0.

ChuangFinance keeps its own Gmail OAuth tokens (the finance watch: readonly+send+drive; the collector: send-only)
and records every relay with the Gmail message id as proof. The older senders build their own service from a fixed
scope list and answer True/False, so a host like that could not use them. Pinned, against a fake Gmail service (no
Google library is imported, nothing is sent):
  - the id Gmail answers with comes back, and the message that went out has To / Subject / plain body;
  - an HTML alternative is added only when asked for, and the sender header only when given;
  - subject and body are scrubbed: a registered secret value, a JSON credential's secret fields and a token-shaped
    string never reach the wire;
  - an API error is RAISED (the host stores it), never swallowed into False;
  - a newline in the subject is refused rather than letting a header be injected;
  - register_secret_values() masks a value no variable name would flag, takes effect for a redact() call made
    BEFORE it was registered (the cache is keyed on it), splits a JSON credential into its secret fields, and
    ignores a value too short to mask without shredding ordinary text.

Run:  python -B tests/test-send-message.py
"""
import base64
import email
import email.policy
import json
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import email_notify as EN  # noqa: E402
from chuang_platform_kit import redact as R  # noqa: E402

os.chdir(tempfile.mkdtemp())   # redact() reads a .env from the working directory; never the repo's

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class FakeGmail:
    """users().messages().send(userId=, body=).execute() -> {'id': ...}; keeps what was sent."""

    def __init__(self, error=None):
        self.sent, self.error = [], error

    def users(self):
        return self

    def messages(self):
        return self

    def send(self, userId, body):
        outer = self

        class Call:
            def execute(self):
                if outer.error:
                    raise outer.error
                outer.sent.append(body)
                return {'id': 'msg-' + str(len(outer.sent)), 'threadId': 't'}
        return Call()

    def last(self):
        return email.message_from_bytes(base64.urlsafe_b64decode(self.sent[-1]["raw"]), policy=email.policy.default)


print('send_message: the id comes back and the message is what was asked for')
g = FakeGmail()
check('returns the Gmail message id', EN.send_message(g, 'genechuang@gmail.com', '[CF Watch] quiet', 'No news today.'), 'msg-1')
m = g.last()
check('To', m['To'], 'genechuang@gmail.com')
check('Subject', m['Subject'], '[CF Watch] quiet')
check('plain body', m.get_content().strip(), 'No news today.')
check('no From unless given', m['From'], None)
check('single part: no HTML alternative unless asked', m.is_multipart(), False)

EN.send_message(g, 'a@example.com', 's', 'plain', html='<p>rich</p>', sender='me@example.com')
m = g.last()
check('From when given', m['From'], 'me@example.com')
check('plain + html alternative', [p.get_content_type() for p in m.walk() if not p.is_multipart()], ['text/plain', 'text/html'])

print('send_message: the scrub')
R.register_secret_values('hook-https://hooks.slack.test/T000/B000/abcdefghijklmnop')
cred = json.dumps({'token': 'ya29.access-token-value-1234', 'refresh_token': '1//refresh-token-value-5678',
                   'client_id': 'public-client-id.apps.example.test', 'client_secret': 'GOCSPX-client-secret-9999'})
R.register_secret_values(cred)
EN.send_message(g, 'a@example.com', 'failed: hook-https://hooks.slack.test/T000/B000/abcdefghijklmnop',
                'refresh 1//refresh-token-value-5678 and Bearer abcdefghijklmnopqrstuvwxyz0123 and ghp_' + 'a' * 30
                + ' but client public-client-id.apps.example.test stays')
m = g.last()
body = m.get_content()
check('registered value masked in the subject', 'hooks.slack.test' in m['Subject'], False)
check("credential's refresh token masked in the body", '1//refresh-token-value-5678' in body, False)
check('Bearer header masked', 'abcdefghijklmnopqrstuvwxyz0123' in body, False)
check('GitHub-shaped token masked', 'ghp_' + 'a' * 30 in body, False)
check('a non-secret field of the credential is left alone', 'public-client-id.apps.example.test' in body, True)

print('send_message: failures are the host\'s to see')
boom = RuntimeError('503 backendError')
try:
    EN.send_message(FakeGmail(error=boom), 'a@example.com', 's', 't')
    raised = None
except RuntimeError as e:
    raised = e
check('an API error is raised, not turned into False', raised is boom, True)
try:
    EN.send_message(FakeGmail(), 'a@example.com', 'two\nlines', 't')
    refused = False
except ValueError:
    refused = True
check('a newline in the subject is refused', refused, True)

print('register_secret_values')
text = 'the key is s3cr3t-value-0000 ok'
check('unregistered: passes unchanged (the positive case)', R.redact(text), text)
R.register_secret_values('s3cr3t-value-0000')
check('registered AFTER a first redact(): masked on the next call', R.redact(text), 'the key is *** ok')
R.register_secret_values('short', '', None, 5)
check('too short to mask without shredding ordinary text: ignored', R.redact('a short word'), 'a short word')
check('a JSON credential is split: its access token is masked', R.redact('t=ya29.access-token-value-1234'), 't=***')
check('ordinary line unchanged', R.redact('digest 7 AM PT, 0 urgent'), 'digest 7 AM PT, 0 urgent')

print(f'\n{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
