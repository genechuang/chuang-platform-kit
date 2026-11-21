#!/usr/bin/env python3
"""chuang_platform_kit.link_preview: a page's preview, fetched safely (https only, public addresses, checked redirects).

Pinned (the positive case first, offline: a stand-in opener and resolver):

- a public https page: og:title, og:description, og:image (made absolute, https alone) and og:site_name, the host's
  user agent sent; twitter:* and <title> stand in when og:* is missing; empty fields left out
- check_url: http, credentials, another port, a malformed port, an unknown host, an overlong address and any
  address that resolves to loopback, private, link-local or metadata space are each Refused in plain words
- a redirect is followed by hand and checked: one to a private address gives the address alone, more than
  max_redirects give ''; a non-HTML or non-200 answer is the address alone; a charset Python lacks reads as UTF-8
- the body read stops at max_bytes
- a YouTube link is asked of the oEmbed first; an oEmbed that fails falls back to the page

Run:  python -B tests/test-link-preview.py
"""
import io
import os
import socket
import sys
import urllib.error
from email.message import Message

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import link_preview as L  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


ADDRESSES = {'example.org': '93.184.215.14', 'www.youtube.com': '142.250.72.14',
             'inside.example.org': '10.0.0.5', 'meta.example.org': '169.254.169.254',
             'loop.example.org': '127.0.0.1', 'v6.example.org': 'fd00::1'}


def resolve(host, port, proto=None):
    if host not in ADDRESSES:
        raise socket.gaierror('no such host')
    a = ADDRESSES[host]
    return [(socket.AF_INET6 if ':' in a else socket.AF_INET, socket.SOCK_STREAM, 6, '', (a, port))]


class Resp:
    def __init__(self, status, body=b'', kind='text/html; charset=utf-8', location=None):
        self.status, self.body = status, body
        self.headers = Message()
        if kind:
            self.headers['Content-Type'] = kind
        if location:
            self.headers['Location'] = location
        self.asked = None

    def read(self, n=-1):
        self.asked = n
        return self.body if n < 0 else self.body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Opener:
    def __init__(self, pages):
        self.pages, self.requests = pages, []

    def open(self, req, timeout=None):
        self.requests.append(req)
        r = self.pages.get(req.full_url)
        if r is None:
            raise urllib.error.URLError('no route')
        if r.status >= 400 or r.status in L.REDIRECTS:
            raise urllib.error.HTTPError(req.full_url, r.status, 'x', r.headers, io.BytesIO(b''))
        return r


PAGE = (b'<html><head><title>Plain title</title><meta property="og:title" content="The &amp; Title">'
        b'<meta property="og:description" content="  A   page  ">'
        b'<meta property="og:image" content="/img/card.png"><meta property="og:site_name" content="Example"></head></html>')

op = Opener({'https://example.org/a': Resp(200, PAGE)})
got = L.preview('https://example.org/a', op, resolve, agent='host-agent/1')
check('a public page: the og fields cleaned, the image made absolute, the host agent sent',
      (got, op.requests[0].get_header('User-agent')),
      ({'url': 'https://example.org/a', 'title': 'The & Title', 'description': 'A page',
        'image': 'https://example.org/img/card.png', 'site': 'Example'}, 'host-agent/1'))

check('twitter and <title> stand in; an http image is left out; empty fields go',
      L.parse('<title> T </title><meta name="twitter:description" content="D">'
              '<meta property="og:image" content="http://x/y.png">', 'https://example.org/b'),
      {'url': 'https://example.org/b', 'title': 'T', 'description': 'D', 'site': 'example.org'})


def why(url):
    try:
        L.check_url(url, resolve)
        return 'accepted'
    except L.Refused as e:
        return str(e)


check('check_url refuses each unsafe address in plain words',
      [why(u) for u in ('http://example.org/', 'https://u:p@example.org/', 'https://example.org:8443/',
                        'https://example.org:99999999/', 'https://nowhere.example/', 'https://example.org/' + 'a' * 2000,
                        'https://inside.example.org/', 'https://meta.example.org/', 'https://loop.example.org/',
                        'https://v6.example.org/', 'https://example.org:443/ok')],
      ['only https links get a preview', 'only https links get a preview',
       'only https links on the standard port get a preview', 'only https links on the standard port get a preview',
       'that site could not be found', 'a link is one web address, at most 2,000 characters',
       'that address is not a public web page', 'that address is not a public web page',
       'that address is not a public web page', 'that address is not a public web page', 'accepted'])

op = Opener({'https://example.org/r': Resp(302, location='https://inside.example.org/secret')})
check('a redirect to a private address gives the address alone, and the private one is never asked',
      (L.preview('https://example.org/r', op, resolve), [r.full_url for r in op.requests]),
      ({'url': 'https://example.org/r', 'site': 'example.org'}, ['https://example.org/r']))

op = Opener({'https://example.org/r': Resp(301, location='/next'), 'https://example.org/next': Resp(200, PAGE)})
check('a redirect to a public address is followed by hand and checked', L.fetch('https://example.org/r', op, resolve)[0],
      'https://example.org/next')

loop = {f'https://example.org/{i}': Resp(302, location=f'/{i + 1}') for i in range(10)}
check('more redirects than max_redirects give no page',
      L.fetch('https://example.org/0', Opener(loop), resolve, max_redirects=2), ('https://example.org/3', ''))

op = Opener({'https://example.org/j': Resp(200, b'{}', kind='application/json'),
             'https://example.org/e': Resp(500), 'https://example.org/c': Resp(200, PAGE, kind='text/html; charset=nonesuch')})
check('a non-HTML or non-200 answer is the address alone; an unknown charset reads as UTF-8',
      (L.preview('https://example.org/j', op, resolve), L.preview('https://example.org/e', op, resolve),
       L.preview('https://example.org/c', op, resolve)['title']),
      ({'url': 'https://example.org/j', 'site': 'example.org'}, {'url': 'https://example.org/e', 'site': 'example.org'},
       'The & Title'))

big = Resp(200, b'<title>x</title>' + b'y' * 100)
L.fetch('https://example.org/big', Opener({'https://example.org/big': big}), resolve, max_bytes=50)
check('the body read stops at max_bytes', big.asked, 50)

video = 'https://www.youtube.com/watch?v=abc'
ask = L.OEMBED.format(url=L.quote(video, safe=''))
op = Opener({ask: Resp(200, b'{"title": "A Video", "author_name": "A Channel", "thumbnail_url": "https://i.ytimg.com/t.jpg"}',
                       kind='application/json')})
check('a YouTube link is read from the oEmbed first',
      L.preview(video, op, resolve), {'url': video, 'title': 'A Video', 'description': 'A Channel',
                                      'image': 'https://i.ytimg.com/t.jpg', 'site': 'YouTube'})
op = Opener({ask: Resp(404), video: Resp(200, PAGE)})
check('an oEmbed that fails falls back to the page', L.preview(video, op, resolve)['title'], 'The & Title')

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
