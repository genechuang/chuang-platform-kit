#!/usr/bin/env python3
"""chuang_platform_kit.gmail: one Gmail client per mailbox for every project (SMAD PickleBot #661).

Pinned (the positive case first): search() lists the threads Gmail finds; thread() reads the messages oldest first with
their sender, recipients, subject and ids from the headers alone; has_message_from() is the never-send-twice check;
reply_all() sends in the thread to exactly the `to` given, with "Re:" once, In-Reply-To and References, the plain text
and the HTML alternative; send() without a thread; mailbox() opens a mailbox by the host's map, the environment's
variable first and Secret Manager's secret second; no usable token is NoMailbox. Offline: the Gmail service is a fake.

Run:  python -B tests/test-gmail.py
"""
import base64
import email
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import gmail as G  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


def hdrs(**kv):
    return {'headers': [{'name': k.replace('_', '-'), 'value': v} for k, v in kv.items()]}


THREAD = {'id': 't1', 'messages': [
    {'id': 'm2', 'threadId': 't1', 'internalDate': '2000', 'snippet': 'thanks',
     'payload': hdrs(From='Gene Chuang <GeneChuang@gmail.com>', To='Records <records@city.gov>', Subject='Re: Weekly Activity Report',
                     Message_ID='<m2@mail.gmail.com>')},
    {'id': 'm1', 'threadId': 't1', 'internalDate': '1000', 'snippet': 'attached',
     'payload': hdrs(From='Records <records@city.gov>', To='a@x.com, B <b@x.com>', Cc='c@x.com', Subject='Weekly Activity Report',
                     Date='Mon, 26 Oct 2026 16:00:00 -0700', Message_ID='<m1@city.gov>', References='<m0@city.gov>')}]}


class Call:
    def __init__(self, svc, kind, kw):
        self.svc, self.kind, self.kw = svc, kind, kw

    def execute(self):
        self.svc.calls.append((self.kind, self.kw))
        if self.kind == 'threads.list':
            return {'threads': [{'id': 't1', 'snippet': 'attached'}, {'id': 't0'}]}
        if self.kind == 'threads.get':
            return THREAD
        if self.kind == 'messages.send':
            return {'id': 'sent-1'}
        return {}


class Fake:
    def __init__(self):
        self.calls = []

    def users(self):
        svc = self

        class Users:
            def threads(self):
                class T:
                    def list(self, **kw):
                        return Call(svc, 'threads.list', kw)

                    def get(self, **kw):
                        return Call(svc, 'threads.get', kw)
                return T()

            def messages(self):
                class M:
                    def send(self, **kw):
                        return Call(svc, 'messages.send', kw)
                return M()
        return Users()


fake = Fake()
box = G.Mailbox(service=fake)
found = box.search('from:city.gov subject:"Weekly Activity Report" newer_than:10d', limit=5)
th = box.thread('t1')
first = th['messages'][0]
check("the positive case: search lists the threads; thread reads the messages oldest first from the headers alone",
      (found, [m['id'] for m in th['messages']], first['from'], first['to'], first['cc'], first['subject'], first['message_id'],
       fake.calls[0][1], fake.calls[1][1]['format'], 'Message-ID' in fake.calls[1][1]['metadataHeaders']),
      ([{'id': 't1', 'snippet': 'attached'}, {'id': 't0', 'snippet': ''}], ['m1', 'm2'], 'records@city.gov', ['a@x.com', 'b@x.com'],
       ['c@x.com'], 'Weekly Activity Report', '<m1@city.gov>',
       {'userId': 'me', 'q': 'from:city.gov subject:"Weekly Activity Report" newer_than:10d', 'maxResults': 5}, 'metadata', True))
check("has_message_from: the never-send-twice check, case-blind; no address is False",
      (G.Mailbox.has_message_from(th, 'genechuang@gmail.com'), G.Mailbox.has_message_from(th, 'jin@x.com'), G.Mailbox.has_message_from(th, '')),
      (True, False, False))

sent = box.reply_all(first, text='Report 2026-43 is up.', html='<b>Report 2026-43</b> is up.', to=['records@city.gov', 'jin@x.com'])
body = fake.calls[-1][1]['body']
mail = email.message_from_bytes(base64.urlsafe_b64decode(body['raw']))
parts = [p.get_content_type() for p in mail.walk() if not p.is_multipart()]
check("reply_all: in the thread, to exactly the addresses given, Re: once, In-Reply-To and References, text and HTML",
      (sent, body['threadId'], mail['To'], mail['Subject'], mail['In-Reply-To'], mail['References'], parts, fake.calls[-1][1]['userId']),
      ('sent-1', 't1', 'records@city.gov, jin@x.com', 'Re: Weekly Activity Report', '<m1@city.gov>', '<m0@city.gov> <m1@city.gov>',
       ['text/plain', 'text/html'], 'me'))
box.reply_all(th['messages'][1], text='again')
again = email.message_from_bytes(base64.urlsafe_b64decode(fake.calls[-1][1]['body']['raw']))
box.send('x@y.com', 'Hello', text='hi', cc='z@y.com')
plain = fake.calls[-1][1]['body']
mail2 = email.message_from_bytes(base64.urlsafe_b64decode(plain['raw']))
check("a reply to a Re: keeps one Re: and goes to its sender with no `to`; send has no thread",
      (again['Subject'], again['To'], 'threadId' in plain, mail2['To'], mail2['Cc'], mail2.get_content_type()),
      ('Re: Weekly Activity Report', 'genechuang@gmail.com', False, 'x@y.com', 'z@y.com', 'text/plain'))

opened = G.mailbox('gene', {'gene': 'GMAIL_OAUTH_TOKEN_GENE', 'smad': 'GMAIL_OAUTH_TOKEN_SMAD'}, project='p1')
bare = G.mailbox('smad', {'smad': 'GMAIL_OAUTH_TOKEN_SMAD'})
try:
    G.mailbox('nobody', {'gene': 'X'})
    unknown = ''
except G.NoMailbox as e:
    unknown = str(e)
check("mailbox(): by the host's map, the environment's variable first and Secret Manager's secret second; an unknown name refused",
      (opened.sources, bare.sources, opened.scopes, G.mailbox('gene', {'gene': 'X'}, scopes=[G.READONLY]).scopes, unknown),
      ({'token_env': 'GMAIL_OAUTH_TOKEN_GENE', 'secret': ('p1', 'GMAIL_OAUTH_TOKEN_GENE')}, {'token_env': 'GMAIL_OAUTH_TOKEN_SMAD', 'secret': None},
       None, (G.READONLY,), "no mailbox named 'nobody' (known: gene)"))

from chuang_platform_kit import google_auth  # noqa: E402
kept = google_auth.service
asked = []
google_auth.service = lambda api, version, scopes, **k: asked.append((api, version, scopes)) or None
try:
    G.Mailbox(token_env='NOTHING_HERE').search('x')
    none = ''
except G.NoMailbox as e:
    none = str(e)
finally:
    google_auth.service = kept
check("no usable token is NoMailbox; the service is asked for the token's own scopes (None) unless narrowed",
      (none, asked), ('no usable Gmail token for this mailbox', [('gmail', 'v1', None)]))


class Creds:
    made = []

    @classmethod
    def from_authorized_user_info(cls, data, scopes):
        cls.made.append(scopes)
        return 'creds'


import logging  # noqa: E402
src, made = google_auth._from_sources(Creds, None, {'refresh_token': 'r'}, None, None, None, None, logging.getLogger('t'))
google_auth._from_sources(Creds, ['s1'], {'refresh_token': 'r'}, None, None, None, None, logging.getLogger('t'))
check("google_auth: scopes None keeps the token's own (from_authorized_user_info gets None); a list is passed as a list",
      (src, made, Creds.made), ('json', 'creds', [None, ['s1']]))

print()
print(f"{len(fails)} failure(s)" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
