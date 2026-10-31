"""Gmail, one client per mailbox for every project (SMAD PickleBot #661; Gene, 10/31/26 8:42 AM PT: "should we not rely
on connectors but rather 3rd party api keys/tokens in one place in Chuang-kit?", and 8:52 AM PT: "The Gmail 3rd party
api kit needs both Gmail accounts because some use cases use one and some use the other").

A claude.ai Gmail connector belongs to one Claude account and dies with every account handover, and a headless run on
a setup token carries none (Dragnet, 10/31/26 9:51 AM PT). A mailbox's stored OAuth token, read through the kit,
survives both. `Mailbox(**sources)` is one mailbox on the kit's `google_auth` token readers (`token_json`,
`token_file`, `token_env`, `secret=(project, name)`; the refreshed token written back): `search` threads, read a
`thread`'s messages (sender, recipients, subject, date, ids), `has_message_from` an address, `reply_all` to a message
with an explicit `to`, `send`. `mailbox(name, mailboxes, project)` opens one by a host's own map of mailbox to secret
name, so each project names its mailboxes once.

Two transports (0.19.0). The Google client library (the `gmail` extra: google-api-python-client, google-auth) reads
and sends. A host without it, a slim image such as SMAD PickleBot's API and its watchdog job, which each carried the
OAuth refresh and the users.messages.send call themselves until #661, sends over REST with the standard library alone:
`Mailbox(rest=True, ...)`, or an `opener` given (urllib's shape, a suite's fake), or the library not installed. A REST
mailbox sends only; `search` and `thread` on one are NoMailbox. The token's own granted scopes are used (0.15.1) and
must cover the calls: reading needs gmail.readonly (or gmail.modify), replying and sending gmail.send. No message
body, address or token is logged; a refused send is SendFailed with Google's code or the error's kind.
"""
import base64
import json
import logging
import time
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import getaddresses

READONLY = 'https://www.googleapis.com/auth/gmail.readonly'
SEND = 'https://www.googleapis.com/auth/gmail.send'
MODIFY = 'https://www.googleapis.com/auth/gmail.modify'
READ_SEND = (READONLY, SEND)
HEADERS = ('From', 'To', 'Cc', 'Subject', 'Date', 'Message-ID', 'References', 'In-Reply-To')
SEND_URL = 'https://gmail.googleapis.com/gmail/v1/users/me/messages/send'
TOKEN_SOURCES = ('token_json', 'token_file', 'token_env', 'secret', 'session')

log = logging.getLogger(__name__)


class NoMailbox(RuntimeError):
    """No usable token for the mailbox (none stored, unreadable, or the client library missing where a read needs it)."""


class SendFailed(RuntimeError):
    """The message was not sent over REST: Google's code ("Google answered 401") or the error's kind ("Google could not
    be reached (TimeoutError)"), never a token or an address."""


def _headers(msg: dict) -> dict:
    return {h.get('name', ''): h.get('value', '') for h in ((msg.get('payload') or {}).get('headers') or [])}


def _addresses(value: str) -> list:
    return [a.lower() for _n, a in getaddresses([value or '']) if a]


def message(msg: dict) -> dict:
    """One Gmail message as a plain dict: id, thread_id, from (the address), from_raw, to, cc (addresses), subject,
    date, message_id, references, snippet, internal_date (ms)."""
    h = _headers(msg)
    frm = _addresses(h.get('From', ''))
    return {'id': msg.get('id'), 'thread_id': msg.get('threadId'), 'from': frm[0] if frm else '', 'from_raw': h.get('From', ''),
            'to': _addresses(h.get('To', '')), 'cc': _addresses(h.get('Cc', '')), 'subject': h.get('Subject', ''),
            'date': h.get('Date', ''), 'message_id': h.get('Message-ID', '') or h.get('Message-Id', ''),
            'references': h.get('References', ''), 'snippet': msg.get('snippet', ''),
            'internal_date': int(msg.get('internalDate') or 0)}


class Mailbox:
    """One Gmail mailbox on a stored token (`sources` as `google_auth.credentials` takes them), or on a ready
    `service` (a suite's fake, or a host's own). `scopes` None (the default, 0.15.1) uses the token's own granted
    scopes: asking a refresh for one it was never granted is refused, so a caller names scopes only to narrow them.
    `rest`, `opener` and `timeout` (0.19.0) pick the REST transport (module docstring); the access token it refreshes
    is kept on the mailbox until a minute before Google says it expires, one refresh for a batch of emails."""

    def __init__(self, scopes=None, service=None, logger=None, rest=None, opener=None, timeout=15, **sources):
        self.scopes = tuple(scopes) if scopes else None
        self._service, self.logger, self.sources = service, logger or log, sources
        self.rest, self.opener, self.timeout = rest, opener, timeout
        self._access = ('', 0.0)   # (access token, monotonic second it is good until)

    def uses_rest(self) -> bool:
        """Whether this mailbox is on the REST transport: `rest` True, or an `opener` given, or google-api-python-client
        not installed; a ready `service`, or `rest` False, is the client library."""
        if self._service is not None:
            return False
        if self.rest is not None:
            return bool(self.rest)
        if self.opener is not None:
            return True
        try:
            import googleapiclient  # noqa: F401
        except ImportError:
            return True
        return False

    def service(self):
        if self._service is None:
            if self.uses_rest():
                raise NoMailbox('reading a mailbox needs google-api-python-client (chuang-platform-kit[gmail]); the REST transport sends only')
            from . import google_auth
            self._service = google_auth.service('gmail', 'v1', list(self.scopes) if self.scopes else None,
                                                logger=self.logger, **self.sources)
            if self._service is None:
                raise NoMailbox('no usable Gmail token for this mailbox')
        return self._service

    def access_token(self) -> str:
        """The REST transport's bearer token: the stored token (the same sources) refreshed through
        `google_auth.refresh_access_token` and kept until a minute before Google says it expires; NoMailbox without a
        token, SendFailed when Google refuses the refresh."""
        tok, until = self._access
        if tok and until > time.monotonic():
            return tok
        from . import google_auth
        info = google_auth.token_info(logger=self.logger, **{k: v for k, v in self.sources.items() if k in TOKEN_SOURCES})
        if info is None:
            raise NoMailbox('no usable Gmail token for this mailbox')
        try:
            tok, life = google_auth.refresh_access_token(info, opener=self.opener, timeout=self.timeout)
        except google_auth.RefreshFailed as e:
            raise SendFailed(str(e))
        self._access = (tok, time.monotonic() + max(0, life - 60))
        return tok

    def search(self, query: str, limit: int = 20) -> list:
        """The threads Gmail's own search finds, newest first: [{'id', 'snippet'}]."""
        got = self.service().users().threads().list(userId='me', q=query, maxResults=int(limit)).execute() or {}
        return [{'id': t.get('id'), 'snippet': t.get('snippet', '')} for t in got.get('threads') or []]

    def thread(self, thread_id: str) -> dict:
        """The thread's messages, oldest first, as `message()` dicts (headers only, no bodies)."""
        got = self.service().users().threads().get(userId='me', id=thread_id, format='metadata',
                                                    metadataHeaders=list(HEADERS)).execute() or {}
        msgs = sorted((message(m) for m in got.get('messages') or []), key=lambda m: m['internal_date'])
        return {'id': got.get('id') or thread_id, 'messages': msgs}

    @staticmethod
    def has_message_from(thread: dict, address: str) -> bool:
        """Whether any message in the thread was sent from `address` (the "never send twice" check)."""
        want = (address or '').strip().lower()
        return bool(want) and any(m['from'] == want for m in thread.get('messages') or [])

    def send(self, to, subject: str, text: str = '', html: str = '', cc=None, thread_id: str = '', headers=None) -> str:
        """Send a message (plain text, with an HTML alternative when given) and answer its id; `headers` are set as
        given (a From with a display name, In-Reply-To)."""
        em = EmailMessage()
        em['To'] = ', '.join(to if isinstance(to, (list, tuple)) else [to])
        if cc:
            em['Cc'] = ', '.join(cc if isinstance(cc, (list, tuple)) else [cc])
        em['Subject'] = subject
        for k, v in (headers or {}).items():
            em[k] = v
        em.set_content(text or '')
        if html:
            em.add_alternative(html, subtype='html')
        body = {'raw': base64.urlsafe_b64encode(em.as_bytes()).decode('ascii')}
        if thread_id:
            body['threadId'] = thread_id
        if self.uses_rest():
            return self._send_rest(body)
        sent = self.service().users().messages().send(userId='me', body=body).execute() or {}
        return sent.get('id', '')

    def _send_rest(self, body: dict) -> str:
        """users.messages.send over REST with the standard library: the message's id, or SendFailed."""
        req = urllib.request.Request(SEND_URL, data=json.dumps(body).encode(), method='POST',
                                     headers={'Authorization': f'Bearer {self.access_token()}', 'Content-Type': 'application/json'})
        try:
            with (self.opener or urllib.request.urlopen)(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raise SendFailed(f'Google answered {e.code}')
        except Exception as e:   # noqa: BLE001 - a timeout or a refused connection is one kind to the caller
            raise SendFailed(f'Google could not be reached ({type(e).__name__})')
        try:
            got = json.loads(raw.decode()) if raw else {}
        except ValueError:
            got = {}
        return got.get('id', '') if isinstance(got, dict) else ''

    def reply_all(self, msg: dict, text: str = '', html: str = '', to=None, cc=None) -> str:
        """Reply in `msg`'s thread (a `message()` dict): `to` the addresses given (else the message's sender), `cc` as
        given; the subject "Re: " once; In-Reply-To and References so every mail client threads it. Its id."""
        subject = msg.get('subject') or ''
        if not subject.lower().startswith('re:'):
            subject = f'Re: {subject}'
        refs = ' '.join(p for p in ((msg.get('references') or '').strip(), msg.get('message_id') or '') if p)
        headers = {'In-Reply-To': msg['message_id'], 'References': refs} if msg.get('message_id') else {}
        return self.send(list(to) if to else [msg.get('from')], subject, text, html, cc, msg.get('thread_id') or '', headers)


def mailbox(name: str, mailboxes: dict, project: str = '', scopes=None, **kw) -> Mailbox:
    """The mailbox `name` by the host's map {name: the secret / environment variable holding its token}: the
    environment's variable first, else Secret Manager's secret in `project`. `kw` reaches Mailbox (`rest`, `opener`)."""
    if name not in mailboxes:
        raise NoMailbox(f'no mailbox named {name!r} (known: {", ".join(sorted(mailboxes))})')
    var = mailboxes[name]
    return Mailbox(scopes, token_env=var, secret=(project, var) if project else None, **kw)
