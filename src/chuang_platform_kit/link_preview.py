"""A web page's preview for a link (its title, description, image and site name), fetched safely, standard library only.

A server that reads pages its users link to must read only what a user could open anyway and nothing inside its own
network. So every fetch here is https alone, on the standard port, with no credentials in the address; every address
the host resolves to is public (no loopback, private, link-local or metadata address), checked again at each of at
most `max_redirects` redirects, which are followed by hand and never by the opener; `timeout` seconds and `max_bytes`
bytes at most; an HTML answer alone. A page that cannot be read is a preview of its address alone, never an error.

    lp.check_url(url)                         # the address, or Refused saying why in a user's words
    final, page = lp.fetch(url, agent=AGENT)  # the final address and its HTML text, or '' when it is not readable
    lp.parse(page, final)                     # {url, title, description, image, site}: og:*, twitter:*, <title>
    lp.preview(url, agent=AGENT)              # all of it, a YouTube link through YouTube's oEmbed first

A YouTube watch page puts its Open Graph tags after more than half a megabyte of inline script, so a YouTube link is
asked of YouTube's oEmbed first (one small JSON with the video's title, channel and thumbnail), through the same
checks; an oEmbed that cannot be read falls back to the page. `opener` (a urllib OpenerDirector that follows no
redirect) and `resolve` (socket.getaddrinfo's shape) are the test seams. The host names itself in `agent`.
"""
import html
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.request
from urllib.parse import quote, urljoin, urlsplit

FETCH_S = 5
MAX_BYTES = 512 * 1024
MAX_REDIRECTS = 3
URL_MAX = 2000
TEXT_MAX = {'title': 200, 'description': 300, 'site': 80}
AGENT = 'chuang-platform-kit-link-preview/1.0'
YOUTUBE_HOSTS = ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be')
OEMBED = 'https://www.youtube.com/oembed?url={url}&format=json'
REDIRECTS = (301, 302, 303, 307, 308)


class Refused(ValueError):
    """An address that will not be fetched: why, in a user's words."""


def check_url(url: str, resolve=socket.getaddrinfo, url_max: int = URL_MAX) -> str:
    """The address as given when it is https on the standard port with no credentials and its host resolves only to
    public addresses; Refused otherwise."""
    url = (url or '').strip()
    if not url or len(url) > url_max:
        raise Refused(f'a link is one web address, at most {url_max:,} characters')
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
        raise Refused('only https links get a preview')
    try:
        port = parts.port
    except ValueError:
        raise Refused('only https links on the standard port get a preview')
    if port not in (None, 443):
        raise Refused('only https links on the standard port get a preview')
    try:
        infos = resolve(parts.hostname, 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        raise Refused('that site could not be found')
    addresses = {info[4][0] for info in infos}
    if not addresses or not all(ipaddress.ip_address(a.split('%')[0]).is_global for a in addresses):
        raise Refused('that address is not a public web page')
    return url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


OPENER = urllib.request.build_opener(_NoRedirect)   # follows no redirect: each hop is checked by fetch


def get(url: str, opener=None, accept: str = 'text/html,application/xhtml+xml', agent: str = AGENT,
        timeout: float = FETCH_S, max_bytes: int = MAX_BYTES) -> tuple:
    """(status, headers, the first `max_bytes` of the body) for one request, a redirect returned and not followed. The
    address is the caller's to have checked."""
    req = urllib.request.Request(url, headers={'User-Agent': agent, 'Accept': accept})
    try:
        r = (opener or OPENER).open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        return e.code, e.headers, b''
    with r:
        return r.status, r.headers, r.read(max_bytes)


def fetch(url: str, opener=None, resolve=socket.getaddrinfo, *, agent: str = AGENT, timeout: float = FETCH_S,
          max_bytes: int = MAX_BYTES, max_redirects: int = MAX_REDIRECTS) -> tuple:
    """(the final address, its HTML text or '') after at most `max_redirects` redirects, each address checked first
    (Refused when one is not public). A non-200 or non-HTML answer is ''."""
    for _ in range(max_redirects + 1):
        check_url(url, resolve)
        status, headers, body = get(url, opener, agent=agent, timeout=timeout, max_bytes=max_bytes)
        if status in REDIRECTS and headers and headers.get('Location'):
            url = urljoin(url, headers['Location'])
            continue
        kind = (headers.get('Content-Type') if headers else '') or ''
        if status != 200 or 'html' not in kind.lower():
            return url, ''
        charset = re.search(r'charset=([\w-]+)', kind)
        try:
            return url, body.decode(charset.group(1) if charset else 'utf-8', errors='replace')
        except LookupError:   # a charset Python does not know
            return url, body.decode('utf-8', errors='replace')
    return url, ''


_META = re.compile(r'<meta\s+[^>]*>', re.I)
_ATTR = re.compile(r'([\w:-]+)\s*=\s*("([^"]*)"|\'([^\']*)\')', re.I)
_TITLE = re.compile(r'<title[^>]*>(.*?)</title>', re.I | re.S)


def _clean(text: str, cap: int) -> str:
    return re.sub(r'\s+', ' ', html.unescape(text or '')).strip()[:cap]


def parse(page: str, url: str, max_bytes: int = MAX_BYTES) -> dict:
    """{url, title, description, image, site} from a page's head: og:* first, then twitter:* and <meta name=description>,
    then <title>; an image only as an https address; empty fields left out."""
    head = page[:max_bytes]
    found = {}
    for tag in _META.findall(head):
        attrs = {m.group(1).lower(): (m.group(3) if m.group(3) is not None else m.group(4)) for m in _ATTR.finditer(tag)}
        key = (attrs.get('property') or attrs.get('name') or '').lower()
        if key and attrs.get('content') and key not in found:
            found[key] = attrs['content']
    title_tag = _TITLE.search(head)
    title = found.get('og:title') or found.get('twitter:title') or (title_tag.group(1) if title_tag else '')
    raw = (found.get('og:image') or found.get('twitter:image') or '').strip()
    image = urljoin(url, raw) if raw else ''
    out = {'url': url,
           'title': _clean(title, TEXT_MAX['title']),
           'description': _clean(found.get('og:description') or found.get('twitter:description')
                                 or found.get('description') or '', TEXT_MAX['description']),
           'image': image if image.startswith('https://') and len(image) <= URL_MAX else '',
           'site': _clean(found.get('og:site_name') or urlsplit(url).hostname or '', TEXT_MAX['site'])}
    return {k: v for k, v in out.items() if v}


def is_youtube(url: str) -> bool:
    """Whether the address is a YouTube link (YOUTUBE_HOSTS), whose watch page hides its tags past MAX_BYTES."""
    return (urlsplit(url).hostname or '').lower() in YOUTUBE_HOSTS


def oembed(url: str, opener=None, resolve=socket.getaddrinfo, *, agent: str = AGENT, timeout: float = FETCH_S,
           max_bytes: int = MAX_BYTES) -> dict:
    """A YouTube link's preview from YouTube's oEmbed: {url, title, description (the channel), image (the thumbnail,
    https alone), site YouTube}, read through the same checks and bounds as a page; {} when it cannot be read or names
    no title, and the caller reads the page instead."""
    try:
        ask = check_url(OEMBED.format(url=quote(url, safe='')), resolve)
        status, headers, body = get(ask, opener, accept='application/json', agent=agent, timeout=timeout,
                                    max_bytes=max_bytes)
        if status != 200:
            return {}
        data = json.loads(body.decode('utf-8', errors='replace'))
        title = _clean(str(data.get('title') or ''), TEXT_MAX['title'])
        if not title:
            return {}
        image = str(data.get('thumbnail_url') or '').strip()
        out = {'url': url, 'title': title, 'description': _clean(str(data.get('author_name') or ''), TEXT_MAX['description']),
               'image': image if image.startswith('https://') and len(image) <= URL_MAX else '', 'site': 'YouTube'}
        return {k: v for k, v in out.items() if v}
    except (Refused, urllib.error.URLError, OSError, ValueError, AttributeError, TypeError):
        return {}


def preview(url: str, opener=None, resolve=socket.getaddrinfo, *, agent: str = AGENT, timeout: float = FETCH_S,
            max_bytes: int = MAX_BYTES, max_redirects: int = MAX_REDIRECTS) -> dict:
    """The preview of one checked https address (Refused for any other): a YouTube link's from its oEmbed, else the
    page's; a page that cannot be read is {url, site}."""
    url = check_url(url, resolve)
    if is_youtube(url):
        got = oembed(url, opener, resolve, agent=agent, timeout=timeout, max_bytes=max_bytes)
        if got:
            return got
    try:
        final, page = fetch(url, opener, resolve, agent=agent, timeout=timeout, max_bytes=max_bytes,
                            max_redirects=max_redirects)
    except (Refused, urllib.error.URLError, OSError, ValueError):
        return {'url': url, 'site': urlsplit(url).hostname or ''}
    return parse(page, final, max_bytes) if page else {'url': final, 'site': urlsplit(final).hostname or ''}
