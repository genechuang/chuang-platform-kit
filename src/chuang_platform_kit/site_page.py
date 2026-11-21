"""A dashboard builder's page as a whole document for a site, its link preview included (SMADPickleBot #1852).

A builder writes the fragment a claude.ai artifact takes: <title>, <link>, <style> and the body's markup, which the
artifact host wraps in the document skeleton at publish time. A site serving the page itself wraps nothing, so
`standalone` adds the doctype, charset and viewport, the tab icon, and the Open Graph tags WhatsApp, Facebook and
LinkedIn read for a link preview, with an EXPLICIT <head> and <body>: a browser infers the head from a bare fragment,
but LinkedIn's scraper does not, and with no <head> element it read none of the og: tags (SMADPickleBot, 10/3/26).
LinkedIn wants a preview title of 15 to 250 characters and a description of 100 or more. A page that already has a
doctype is left alone.

The site is the host's: `Site(name, url, og_image, ...)` names it once, and the host's own nav bar is passed in
(`nav`, its markup or a function of the page's path), so the kit names no project. SMADPickleBot's
`scripts/site_page.py` is its site and its PAGES over this; SMPDReport builds /police with the same call. Standard
library only.
"""
import html as _html
import re
from dataclasses import dataclass

__all__ = ['Site', 'page_title', 'head', 'standalone']


@dataclass(frozen=True)
class Site:
    """The site a page is published on: its `name` (og:site_name, and the title of a page with none), its `url`
    (the origin, no trailing slash), the preview image's address and alt text (1200 by 630), the tab icons' paths and
    the description a page given none gets."""
    name: str
    url: str
    og_image: str = ''
    og_image_alt: str = ''
    description: str = ''
    icon: str = '/favicon.png'
    touch_icon: str = '/apple-touch-icon.png'


def page_title(html: str, default: str = '') -> str:
    """The fragment's <title>, unescaped, or `default`."""
    m = re.search(r'<title>(.*?)</title>', html or '', re.S | re.I)
    return _html.unescape(m.group(1).strip()) if m else default


def head(site: Site, title: str, path: str = '', description: str = '') -> str:
    """The head tags every page of `site` carries (inside <head>, which the caller opens): charset, viewport,
    description, the tab icons and the Open Graph and Twitter preview tags; `path` is the page's path on the site."""
    a = lambda s: _html.escape(s, quote=True)   # noqa: E731
    desc = description or site.description
    url = site.url.rstrip('/') + (path if path.startswith('/') else f'/{path}' if path else '/')
    lines = ['<meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             f'<meta name="description" content="{a(desc)}">']
    if site.icon:
        lines.append(f'<link rel="icon" href="{a(site.icon)}" type="image/png">')
    if site.touch_icon:
        lines.append(f'<link rel="apple-touch-icon" href="{a(site.touch_icon)}">')
    lines += ['<meta property="og:type" content="website">',
              f'<meta property="og:site_name" content="{a(site.name)}">',
              f'<meta property="og:title" content="{a(title)}">',
              f'<meta property="og:description" content="{a(desc)}">',
              f'<meta property="og:url" content="{a(url)}">']
    if site.og_image:
        lines += [f'<meta property="og:image" content="{a(site.og_image)}">',
                  '<meta property="og:image:width" content="1200">',
                  '<meta property="og:image:height" content="630">']
        if site.og_image_alt:
            lines.append(f'<meta property="og:image:alt" content="{a(site.og_image_alt)}">')
        lines.append('<meta name="twitter:card" content="summary_large_image">')
    else:
        lines.append('<meta name="twitter:card" content="summary">')
    return '\n'.join(lines + [''])


# The fragment's leading head elements: <title>, <link>, <meta>, <style> and whitespace, up to the first element that
# belongs in the body.
_HEAD_PART = re.compile(r'\A(?:\s+|<title>.*?</title>|<link\b[^>]*>|<meta\b[^>]*>|<style\b[^>]*>.*?</style>)*',
                        re.S | re.I)


def standalone(html: str, site: Site, path: str = '', description: str = '', title: str = '', nav='') -> str:
    """The page as a whole document on `site`, with an explicit <head> and <body> and `nav` first in the body (the
    host's nav bar: its markup, or a function of `path` that returns it; '' for none). `title` overrides the preview
    title only; the tab keeps the page's own <title>. A page that already starts with a doctype is returned as it is."""
    if (html or '').lstrip()[:15].lower().startswith('<!doctype'):
        return html
    body = (html or '').lstrip()
    lead = _HEAD_PART.match(body).group(0)
    bar = nav(path) if callable(nav) else (nav or '')
    return ('<!doctype html>\n<html lang="en">\n<head>\n'
            + head(site, title or page_title(html, site.name), path, description)
            + lead.strip() + '\n</head>\n<body>\n' + bar + body[len(lead):].lstrip()
            + '\n</body>\n</html>\n')
