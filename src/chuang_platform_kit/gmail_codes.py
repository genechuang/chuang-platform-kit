"""A one-time code from Gmail, for a sign-in a job drives.

A portal emails a code, the job reads it from the inbox and types it into the page. Four copies of this loop lived in one
host (one per portal, each with its own query and regex); this is the one. The caller supplies the Gmail `service` (any
object with Gmail's `users().messages().list/get` shape), the search `query` for the portal's code mail, the epoch second
the sign-in started (`since_epoch`: a code mailed before that is a previous attempt's), and the `pattern` whose first
group - or whole match - is the code. The function polls `attempts` times, `pause` seconds apart, newest mail first, and
answers the code or None. Nothing here knows which portal; `query` and `pattern` carry that.

    code = wait_for_code(service, 'from:ibp3@example.com subject:"One-time Verification Code"', started, SIX_DIGITS)

The mail body is read the way a person would: the text/plain part when there is one, else the HTML stripped of tags
(entities decoded), else Gmail's snippet. Only the standard library is used, so the kit's `gmail` extra is not required to
import this module; the service object comes from the host.
"""
import base64
import re
import time
from html.parser import HTMLParser

SIX_DIGITS = r'(?<!\d)(\d{6})(?!\d)'
"""The commonest code shape: six digits standing alone (not part of a longer number)."""

GRACE_SECONDS = 60
"""A mail stamped up to this long before `since_epoch` still counts: the portal's clock and Gmail's differ a little."""


def wait_for_code(service, query: str, since_epoch: int, pattern, attempts: int = 24, pause: float = 5.0,
                  sleep=time.sleep) -> str | None:
    """Poll Gmail for a mail matching `query` received after `since_epoch` (less a minute of grace) and return the code
    `pattern` finds in its body: the first group when the pattern has one, else the whole match. None after `attempts`
    tries `pause` seconds apart. `sleep` is injectable so a test never waits."""
    rx = re.compile(pattern) if isinstance(pattern, str) else pattern
    floor = int(since_epoch) - GRACE_SECONDS
    q = f'{query} after:{floor}'
    for attempt in range(max(1, int(attempts))):
        listed = service.users().messages().list(userId='me', q=q, maxResults=5).execute() or {}
        for m in listed.get('messages') or []:
            msg = service.users().messages().get(userId='me', id=m['id'], format='full').execute() or {}
            if int(msg.get('internalDate') or 0) // 1000 < floor:
                continue
            found = rx.search(body_text(msg))
            if found:
                return found.group(1) if found.groups() else found.group(0)
        if attempt < attempts - 1:
            sleep(pause)
    return None


def body_text(msg: dict) -> str:
    """The readable text of a Gmail message in `format='full'`: text/plain parts first, else HTML stripped, else the snippet."""
    plain, html = [], []
    _collect(msg.get('payload') or {}, plain, html)
    if plain:
        return '\n'.join(plain)
    if html:
        return '\n'.join(_strip_html(h) for h in html)
    return msg.get('snippet') or ''


def _collect(part: dict, plain: list, html: list) -> None:
    mime = (part.get('mimeType') or '').lower()
    data = (part.get('body') or {}).get('data')
    if data and mime.startswith('text/'):
        text = _decode(data)
        (plain if mime == 'text/plain' else html).append(text)
    for child in part.get('parts') or []:
        _collect(child, plain, html)


def _decode(data: str) -> str:
    padded = data + '=' * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode('utf-8', errors='replace')


class _Text(HTMLParser):
    """Collects the text of an HTML document, skipping script and style; entities are decoded by the parser."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self._skip += 1
        elif tag in ('br', 'p', 'div', 'tr', 'li', 'h1', 'h2', 'h3', 'td'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _strip_html(html: str) -> str:
    p = _Text()
    p.feed(html)
    p.close()
    return re.sub(r'[ \t]+', ' ', ''.join(p.parts))
