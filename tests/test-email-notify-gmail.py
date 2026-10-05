#!/usr/bin/env python3
"""email_notify: the Gmail send, the token chain, the contact lookup, the HTML.

tests/test-email-notify.py pins the missing-GMAIL_USERNAME alarm; everything
past that gate was untested (24% covered, 2026-10-02 PT). Email rides on every
WhatsApp DM reminder, so this pins, against fake Google client modules (no
Gmail or People API is ever reached):
  - the message actually sent: From/To/Subject, the HTML body inside the
    footer template, and the token scrub (shared/redact.py) on subject and body;
  - the gates in order: no address, EMAIL_DISABLED, dry run, no Gmail service;
    a failed send is False, never a raise;
  - the subject: "SMAD Pickleball - <label>", plus today's PT date for a
    recurring reminder so Gmail does not thread every nag into one;
  - the OAuth chain both builders share: token file, then GMAIL_OAUTH_TOKEN_JSON,
    an expired token refreshed (and saved back when it came from the file),
    a token still invalid after refresh -> None;
  - lookup_contact_by_phone(): every page searched, numbers compared on ten
    digits whatever their format, any failure -> ('', '');
  - whatsapp_to_html(): *bold* _italic_ ~strike~ ```pre```, URLs with
    underscores kept whole and clickable, HTML escaped.
The clock is pinned to 2026-10-02 PT. The suite runs from a temp directory so
redact() never reads the repo's .env.

Run:  python -B tests/test-email-notify-gmail.py
"""
import base64
import contextlib
import email
import json
import logging
import os
import sys
import tempfile
import types
from datetime import datetime

from zoneinfo import ZoneInfo

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import email_notify as EN  # noqa: E402

PST = ZoneInfo('America/Los_Angeles')
EN.configure(subject_prefix='SMAD Pickleball')   # the host names itself; the subjects below carry it

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class Cap(logging.Handler):
    def __init__(self):
        super().__init__()
        self.recs = []

    def emit(self, r):
        self.recs.append(r)

    def levels(self):
        return [r.levelname for r in self.recs]


class FixedDT(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 2, 8, 0, tzinfo=PST) if tz else datetime(2026, 10, 2, 8, 0)


# --- fake Google client modules ------------------------------------------------------
log = []


class FakeCred:
    def __init__(self, origin, expired=False, refresh_token='r', valid=True, refresh_ok=True):
        self.origin, self.expired, self.refresh_token = origin, expired, refresh_token
        self.valid, self.refresh_ok = valid, refresh_ok

    def refresh(self, request):
        log.append(('refresh', self.origin))
        if not self.refresh_ok:
            raise RuntimeError('invalid_grant')
        self.expired, self.valid = False, True

    def to_json(self):
        return '{"token": "refreshed-fake"}'


cred_plan = {'file': None, 'info': None}


class FakeCredentials:
    @staticmethod
    def from_authorized_user_file(path, scopes):
        log.append(('file', tuple(scopes) == tuple(EN.SCOPES)))
        c = cred_plan['file']
        if isinstance(c, Exception):
            raise c
        return c

    @staticmethod
    def from_authorized_user_info(info, scopes):
        log.append(('info', info))
        return cred_plan['info']


class Svc:
    """A built Gmail/People resource that records sends and serves contact pages."""
    def __init__(self, api, pages=None, send_exc=None, list_exc=None):
        self.api, self.pages, self.sent, self.listed = api, list(pages or []), [], []
        self.send_exc, self.list_exc = send_exc, list_exc

    def users(self):
        s = self

        class U:
            def messages(self):
                class M:
                    def send(self, **kw):
                        s.sent.append(kw)

                        class R:
                            def execute(self_):
                                if s.send_exc:
                                    raise s.send_exc
                                return {'id': 'm1'}
                        return R()
                return M()
        return U()

    def people(self):
        s = self

        class P:
            def connections(self):
                class C:
                    def list(self, **kw):
                        s.listed.append(kw)

                        class R:
                            def execute(self_):
                                if s.list_exc:
                                    raise s.list_exc
                                return s.pages.pop(0)
                        return R()
                return C()
        return P()


build_plan = {'fail': False, 'svc': None}


def fake_build(api, version, credentials=None):
    log.append(('build', api, version, credentials.origin))
    if build_plan['fail']:
        raise RuntimeError('discovery failed')
    return build_plan['svc'] or Svc(api)


@contextlib.contextmanager
def fake_google(missing=False):
    mods = {
        'google.auth.transport.requests': types.SimpleNamespace(Request=lambda: 'req'),
        'google.oauth2.credentials': types.SimpleNamespace(Credentials=FakeCredentials),
        'googleapiclient.discovery': None if missing else types.SimpleNamespace(build=fake_build),
    }
    saved = {k: sys.modules.get(k) for k in mods}
    sys.modules.update(mods)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


cap = Cap()
EN.logger.addHandler(cap)
EN.logger.setLevel(logging.DEBUG)

tmpdir = tempfile.mkdtemp()
TOKEN = os.path.join(tmpdir, 'gmail-token.json')
NO_TOKEN = os.path.join(tmpdir, 'absent.json')
FAKE_GH = 'ghp_' + 'Q' * 36          # token-shaped, invented here
_keep = (EN.datetime, EN.TOKEN_FILE, EN.GMAIL_USERNAME, EN.EMAIL_DISABLED, EN._get_gmail_service,
         os.environ.get('GMAIL_OAUTH_TOKEN_JSON'), os.getcwd())
os.chdir(tmpdir)                       # redact() reads ./.env; there is none here
EN.datetime = FixedDT
EN.GMAIL_USERNAME = 'bot@example.com'
EN.EMAIL_DISABLED = False
os.environ.pop('GMAIL_OAUTH_TOKEN_JSON', None)
try:
    print("\n-- send_notification_email(): the message Gmail is asked to send")
    gmail = Svc('gmail')
    EN._get_gmail_service = lambda: gmail
    ok = EN.send_notification_email('ann@example.com', f'Vote Reminder {FAKE_GH}', f'<p>Hi Ann</p><p>{FAKE_GH}</p>')
    sent = gmail.sent[0]
    msg = email.message_from_bytes(base64.urlsafe_b64decode(sent['body']['raw']))
    html = msg.get_payload()[0].get_payload(decode=True).decode()
    check("the positive case: True, one send as 'me'", (ok, len(gmail.sent), sent['userId']), (True, 1, 'me'))
    check("  From the bot account, To the player", (msg['From'], msg['To']), ('bot@example.com', 'ann@example.com'))
    check("  the subject arrives with the token masked", msg['Subject'], 'Vote Reminder ***')
    check("  the body is HTML inside the footer template, token masked",
          ('<p>Hi Ann</p>' in html, 'automated notification from SMAD Pickleball' in html, FAKE_GH in html, msg.get_payload()[0].get_content_type()),
          (True, True, False, 'text/html'))
    check("  logged at INFO", cap.levels()[-1], 'INFO')

    gmail.sent.clear()
    check("no address -> False, nothing sent", (EN.send_notification_email('', 's', 'b'), gmail.sent), (False, []))
    EN.EMAIL_DISABLED = True
    check("EMAIL_DISABLED -> False, nothing sent", (EN.send_notification_email('a@b.c', 's', 'b'), gmail.sent), (False, []))
    EN.EMAIL_DISABLED = False
    check("dry run -> True, nothing sent", (EN.send_notification_email('a@b.c', 's', 'b', dry_run=True), gmail.sent), (True, []))
    EN._get_gmail_service = lambda: None
    cap.recs.clear()
    check("no Gmail service -> False at WARNING", (EN.send_notification_email('a@b.c', 's', 'b'), cap.levels()), (False, ['WARNING']))
    gmail = Svc('gmail', send_exc=RuntimeError('quota'))
    EN._get_gmail_service = lambda: gmail
    cap.recs.clear()
    check("the send raises -> False at ERROR, no raise", (EN.send_notification_email('a@b.c', 's', 'b'), cap.levels()), (False, ['ERROR']))

    print("\n-- maybe_send_email(): subject label, today's PT date, WhatsApp -> HTML")
    gmail = Svc('gmail')
    EN._get_gmail_service = lambda: gmail
    ok = EN.maybe_send_email({'name': 'Ann Lee', 'email': 'ann@example.com'}, '*Vote* now', subject='Vote Reminder', append_date=True)
    msg = email.message_from_bytes(base64.urlsafe_b64decode(gmail.sent[0]['body']['raw']))
    check("the positive case: sent, subject dated MM/DD/YY in PT",
          (ok, msg['Subject']), (True, 'SMAD Pickleball - Vote Reminder - 10/02/26'))
    check("  the WhatsApp bold became <strong>",
          '<strong>Vote</strong> now' in msg.get_payload()[0].get_payload(decode=True).decode(), True)
    gmail.sent.clear()
    EN.maybe_send_email({'email': 'ann@example.com'}, 'x', subject='Last Call')
    msg = email.message_from_bytes(base64.urlsafe_b64decode(gmail.sent[0]['body']['raw']))
    check("no append_date -> the undated subject", msg['Subject'], 'SMAD Pickleball - Last Call')
    gmail.sent.clear()
    check("a player with no email -> False, nothing sent", (EN.maybe_send_email({'name': 'Bo'}, 'x'), gmail.sent), (False, []))
    cap.recs.clear()
    EN.maybe_send_extra_notifications({'name': 'Ann', 'email': 'a@b.c'}, 'x', subject='S', dry_run=True)
    check("maybe_send_extra_notifications() -> the same path; dry run says 'Would also send'",
          (gmail.sent, any('Would also send email to Ann' in r.getMessage() for r in cap.recs)), ([], True))
    check("subject_date(): today in PT", EN.subject_date(), '10/02/26')
    EN._get_gmail_service = _keep[4]

    print("\n-- _get_gmail_service() / _get_people_service(): the OAuth token chain")
    EN.TOKEN_FILE = TOKEN
    with open(TOKEN, 'w') as f:
        f.write('{"token": "old-fake"}')
    with fake_google():
        cred_plan['file'] = FakeCred('file', expired=True)
        log.clear()
        build_plan['svc'] = None
        svc = EN._get_gmail_service()
        check("the positive case: the token file, refreshed, then gmail v1 built",
              (svc.api, log), ('gmail', [('file', True), ('refresh', 'file'), ('build', 'gmail', 'v1', 'file')]))
        with open(TOKEN) as f:
            check("  the refreshed token is saved back to the file", f.read(), '{"token": "refreshed-fake"}')

        EN.TOKEN_FILE = NO_TOKEN
        os.environ['GMAIL_OAUTH_TOKEN_JSON'] = json.dumps({'token': 'env-fake'})
        cred_plan['info'] = FakeCred('env', expired=True)
        log.clear()
        svc = EN._get_people_service()
        check("no file -> GMAIL_OAUTH_TOKEN_JSON, refreshed, people v1 built (nothing saved)",
              (svc.api, log, os.path.exists(NO_TOKEN)),
              ('people', [('info', {'token': 'env-fake'}), ('refresh', 'env'), ('build', 'people', 'v1', 'env')], False))

        os.environ['GMAIL_OAUTH_TOKEN_JSON'] = '{not json'
        check("a broken env token and no file -> None from both",
              (EN._get_gmail_service(), EN._get_people_service()), (None, None))
        os.environ.pop('GMAIL_OAUTH_TOKEN_JSON', None)
        check("no token anywhere -> None from both", (EN._get_gmail_service(), EN._get_people_service()), (None, None))

        EN.TOKEN_FILE = TOKEN
        cred_plan['file'] = FakeCred('file', expired=True, refresh_ok=False, valid=False)
        cap.recs.clear()
        check("refresh fails and the token stays invalid -> None, two WARNINGs (refresh, invalid)",
              (EN._get_gmail_service(), cap.levels()), (None, ['WARNING', 'WARNING']))
        cred_plan['file'] = FakeCred('file', expired=True, refresh_ok=False, valid=False)
        cap.recs.clear()
        check("  the same for the People API", (EN._get_people_service(), cap.levels()), (None, ['WARNING', 'WARNING']))

        cred_plan['file'] = RuntimeError('corrupt token file')
        os.environ['GMAIL_OAUTH_TOKEN_JSON'] = json.dumps({'token': 'env-fake'})
        cred_plan['info'] = FakeCred('env')
        log.clear()
        EN._get_gmail_service()
        check("an unreadable token file falls through to the env token", log[-1], ('build', 'gmail', 'v1', 'env'))
        log.clear()
        EN._get_people_service()
        check("  the People API falls through the same way", log[-1], ('build', 'people', 'v1', 'env'))
        os.environ.pop('GMAIL_OAUTH_TOKEN_JSON', None)

        cred_plan['file'] = FakeCred('file')
        build_plan['fail'] = True
        check("discovery build fails -> None from both", (EN._get_gmail_service(), EN._get_people_service()), (None, None))
        build_plan['fail'] = False
    with fake_google(missing=True):
        check("Google libraries missing -> None from both, no raise",
              (EN._get_gmail_service(), EN._get_people_service()), (None, None))

    print("\n-- lookup_contact_by_phone(): every page, ten-digit comparison")
    people = Svc('people', pages=[
        {'connections': [{'names': [{'displayName': 'Bo'}], 'phoneNumbers': [{'value': '626-555-0000'}]}],
         'nextPageToken': 'P2'},
        {'connections': [{'names': [{'displayName': 'Ann Lee'}], 'phoneNumbers': [{'value': '+1 (703) 555-0100'}],
                          'emailAddresses': [{'value': 'ann@example.com'}, {'value': 'ann2@example.com'}]}]}])
    _keep_people = EN._get_people_service
    EN._get_people_service = lambda: people
    try:
        got = EN.lookup_contact_by_phone('17035550100')
        check("the positive case: found on page 2 despite '+1 (703)' formatting -> (name, first email)",
              got, ('Ann Lee', 'ann@example.com'))
        check("  page 2 asked for with the page token, names/phones/emails only",
              (people.listed[1].get('pageToken'), people.listed[0]['personFields'], people.listed[0]['pageSize']),
              ('P2', 'names,phoneNumbers,emailAddresses', 1000))
        people = Svc('people', pages=[{'connections': [{'phoneNumbers': [{'value': '7035550100'}]}]}])
        check("a match with no name or email -> ('', '')", EN.lookup_contact_by_phone('7035550100'), ('', ''))
        people = Svc('people', pages=[{'connections': [{'names': [{'displayName': 'Bo'}],
                                                        'phoneNumbers': [{'value': '626-555-0000'}]}]}])
        check("no match on the last page -> ('', '')", EN.lookup_contact_by_phone('17035550100'), ('', ''))
        people = Svc('people', list_exc=RuntimeError('403'))
        check("the API raises -> ('', '')", EN.lookup_contact_by_phone('17035550100'), ('', ''))
        people = None
        check("no People service -> ('', '')", EN.lookup_contact_by_phone('17035550100'), ('', ''))
    finally:
        EN._get_people_service = _keep_people

    print("\n-- whatsapp_to_html(): WhatsApp formatting as email HTML")
    check("the positive case: bold, italic, strike, and HTML escaped",
          EN.whatsapp_to_html('*Hi* _there_ ~old~ <b>&'),
          '<strong>Hi</strong> <em>there</em> <del>old</del> &lt;b&gt;&amp;')
    check("a URL with underscores stays whole and becomes a link",
          EN.whatsapp_to_html('see https://x.com/reel/DT_2k_W/ now'),
          'see <a href="https://x.com/reel/DT_2k_W/">https://x.com/reel/DT_2k_W/</a> now')
    check("three or more newlines collapse to one blank line; newlines become <br>",
          EN.whatsapp_to_html('a\n\n\n\nb'), 'a<br>\n<br>\nb')
    check("a ``` block becomes <pre> and keeps its newlines",
          EN.whatsapp_to_html('t\n```x\ny```'),
          't<br>\n<pre style="font-family:monospace;white-space:pre">x\ny</pre>')
finally:
    os.chdir(_keep[6])
    (EN.datetime, EN.TOKEN_FILE, EN.GMAIL_USERNAME, EN.EMAIL_DISABLED, EN._get_gmail_service) = _keep[:5]
    if _keep[5] is None:
        os.environ.pop('GMAIL_OAUTH_TOKEN_JSON', None)
    else:
        os.environ['GMAIL_OAUTH_TOKEN_JSON'] = _keep[5]
    EN.logger.removeHandler(cap)

print(f"\n{len(fails)} failure(s)")
if fails:
    sys.exit(1)
