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


# -- the REST transport (0.19.0): a host without the Google client library sends with the standard library alone
import io  # noqa: E402
import json  # noqa: E402
import urllib.error  # noqa: E402


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


rest_calls, life = [], [3599]


def rest_opener(req, timeout=None):
    rest_calls.append((req.full_url, req.get_method(), req.data, dict(req.header_items()), timeout))
    if 'oauth2' in req.full_url:
        return Resp(json.dumps({'access_token': f"at-{sum(1 for c in rest_calls if 'oauth2' in c[0])}", 'expires_in': life[0]}).encode())
    return Resp(json.dumps({'id': 'gm-1'}).encode())


rest = G.Mailbox(token_json={'refresh_token': 'rt', 'client_id': 'ci', 'client_secret': 'cs'}, opener=rest_opener, timeout=9)
sent_id = rest.send('a@x.com', 'Hi', 'plain', '<b>h</b>', headers={'From': 'Bot <bot@x.com>'})
refresh, post = rest_calls[0], rest_calls[1]
mail3 = email.message_from_bytes(base64.urlsafe_b64decode(json.loads(post[2])['raw']))
check("REST (the positive case): the refresh at Google's token endpoint, then users.messages.send with the bearer; From as given, text and HTML",
      (sent_id, rest.uses_rest(), refresh[0], refresh[1], b'refresh_token=rt' in refresh[2] and b'client_id=ci' in refresh[2],
       post[0], post[1], post[3].get('Authorization'), post[4], mail3['From'], mail3['To'],
       [p.get_content_type() for p in mail3.walk() if not p.is_multipart()]),
      ('gm-1', True, google_auth.TOKEN_URI, 'POST', True, G.SEND_URL, 'POST', 'Bearer at-1', 9, 'Bot <bot@x.com>', 'a@x.com',
       ['text/plain', 'text/html']))
rest.send('b@x.com', 'Again', 'p')
kept_refreshes = sum(1 for c in rest_calls if 'oauth2' in c[0])
life[0] = 30
short = G.Mailbox(token_json={'refresh_token': 'rt'}, rest=True, opener=rest_opener)
first, second = short.access_token(), short.access_token()
check("the access token is kept between sends until a minute before it expires; one Google says lives under a minute is asked for again",
      (kept_refreshes, first != second), (1, True))


def refuse(code):
    def opener(req, timeout=None):
        if 'oauth2' in req.full_url and code == 'refresh':
            raise urllib.error.HTTPError(req.full_url, 400, 'no', {}, io.BytesIO(b'{}'))
        if 'oauth2' in req.full_url:
            return Resp(json.dumps({'access_token': 'at'}).encode())
        if code == 'down':
            raise TimeoutError('slow')
        raise urllib.error.HTTPError(req.full_url, code, 'no', {}, io.BytesIO(b'{}'))
    return opener


def said(box, call='send'):
    try:
        box.send('a@x.com', 's', 't') if call == 'send' else box.search('x')
        return 'ok'
    except (G.NoMailbox, G.SendFailed) as e:
        return f'{type(e).__name__}: {e}'


tok = {'refresh_token': 'rt'}
check("refusals name Google's code or the error's kind, never a token; no token is NoMailbox; a REST mailbox does not read",
      (said(G.Mailbox(token_json=tok, opener=refuse(401))), said(G.Mailbox(token_json=tok, opener=refuse('refresh'))),
       said(G.Mailbox(token_json=tok, opener=refuse('down'))), said(G.Mailbox(token_env='NOTHING_HERE', rest=True)),
       said(G.Mailbox(token_json=tok, rest=True), call='search'), G.Mailbox(service=fake).uses_rest(),
       G.Mailbox(rest=False, token_json=tok).uses_rest(), G.mailbox('smad', {'smad': 'X'}, rest=True).rest),
      ('SendFailed: Google answered 401', 'SendFailed: Google answered 400', 'SendFailed: Google could not be reached (TimeoutError)',
       'NoMailbox: no usable Gmail token for this mailbox',
       'NoMailbox: reading a mailbox needs google-api-python-client (chuang-platform-kit[gmail]); the REST transport sends only',
       False, False, True))


# -- 0.48.0 (SMADPickleBot #1852): the address, every matching message id, a message's attachments and their bytes
PDF = b'%PDF-1.7 weekly report'
FULL = {'id': 'w1', 'payload': {'mimeType': 'multipart/mixed', 'parts': [
    {'mimeType': 'multipart/alternative', 'parts': [{'mimeType': 'text/plain', 'body': {'size': 5}}]},
    {'mimeType': 'application/pdf', 'filename': 'Weekly Report 11-16.PDF', 'body': {'attachmentId': 'a1', 'size': 22}},
    {'mimeType': 'message/rfc822', 'filename': '', 'parts': [
        {'mimeType': 'image/png', 'filename': 'logo.png', 'body': {'attachmentId': 'a2', 'size': '9'}}]},
    {'mimeType': 'application/pdf', 'filename': 'inline.pdf', 'body': {'data': 'eA'}}]}}


class ReadFake:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def users(self):
        fake = self

        class Exe:
            def __init__(self, kind, kw):
                self.kind, self.kw = kind, kw

            def execute(self):
                fake.calls.append((self.kind, self.kw))
                if self.kind == 'profile':
                    return {'emailAddress': 'gene@x.com'}
                if self.kind == 'list':
                    return fake.pages[self.kw.get('pageToken')]
                if self.kind == 'get':
                    return FULL
                return {'data': base64.urlsafe_b64encode(PDF).decode().rstrip('=')}

        class Users:
            def getProfile(self, **kw):
                return Exe('profile', kw)

            def messages(self):
                class M:
                    def list(self, **kw):
                        return Exe('list', kw)

                    def get(self, **kw):
                        return Exe('get', kw)

                    def attachments(self):
                        class A:
                            def get(self, **kw):
                                return Exe('attachment', kw)
                        return A()
                return M()
        return Users()


pages = {None: {'messages': [{'id': 'w3'}, {'id': 'w2'}], 'nextPageToken': 'p2'}, 'p2': {'messages': [{'id': 'w1'}]}}
rf = ReadFake(pages)
rbox = G.Mailbox(service=rf)
ids = rbox.message_ids('from:city.gov has:attachment')
pdfs = rbox.attachments('w1', suffix='.pdf')
data = rbox.attachment('w1', 'a1')
check("reads (the positive case): every page of matching message ids, a message's PDF attachments listed, one's bytes decoded",
      (rbox.address(), ids, [c[1].get('pageToken') for c in rf.calls if c[0] == 'list'], pdfs, data,
       [c[1] for c in rf.calls if c[0] in ('get', 'attachment')]),
      ('gene@x.com', ['w3', 'w2', 'w1'], [None, 'p2'],
       [{'filename': 'Weekly Report 11-16.PDF', 'mime_type': 'application/pdf', 'attachment_id': 'a1', 'size': 22}], PDF,
       [{'userId': 'me', 'id': 'w1', 'format': 'full'}, {'userId': 'me', 'messageId': 'w1', 'id': 'a1'}]))
check("attachment_parts: the message's order, nested parts too, no suffix lists all; an inline part with no attachmentId is not one",
      ([p['filename'] for p in G.attachment_parts(FULL['payload'])], G.attachment_parts(FULL['payload'])[1]['size'],
       G.attachment_parts({}), G.attachment_parts(None)),
      (['Weekly Report 11-16.PDF', 'logo.png'], 9, [], []))
check("message_ids stops at `limit`, reading no page past it",
      (G.Mailbox(service=ReadFake(pages)).message_ids('q', limit=2), G.Mailbox(service=ReadFake(pages)).message_ids('q', limit=3)),
      (['w3', 'w2'], ['w3', 'w2', 'w1']))
try:
    G.Mailbox(token_json=tok, rest=True).attachments('w1')
    rest_read = 'read'
except G.NoMailbox as e:
    rest_read = str(e)
check("a REST mailbox does not read attachments either",
      rest_read, 'reading a mailbox needs google-api-python-client (chuang-platform-kit[gmail]); the REST transport sends only')

print()
print(f"{len(fails)} failure(s)" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
