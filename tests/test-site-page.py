#!/usr/bin/env python3
"""chuang_platform_kit.site_page: a builder's fragment as a whole document on a host's site (SMADPickleBot #1852).

Pinned (the positive case first): standalone() puts an explicit <head> with the charset, viewport, icons and Open Graph
tags, the fragment's own <title>, <link> and <style> moved into it, the host's nav first in the body (markup or a
function of the path); a preview title overrides og:title alone; a page with a doctype is returned as it is; head()
escapes what it writes and leaves out the image tags with no image; page_title() falls back to the default.

Run:  python -B tests/test-site-page.py
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import site_page as S  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


SITE = S.Site('Example Site', 'https://example.org/', og_image='https://example.org/og.jpg', og_image_alt='The logo',
              description='A default description of the site that is long enough for every preview scraper.')
FRAG = ('<title>Police &amp; Fire</title>\n<link rel="stylesheet" href="x.css">\n<style>body{margin:0}</style>\n'
        '<main><h1>Hello</h1></main>\n')
page = S.standalone(FRAG, SITE, '/police', 'Every incident mapped week by week.', nav=lambda p: f'<nav data-at="{p}"></nav>\n')
head_part, _, body_part = page.partition('</head>')
check("the positive case: an explicit head with the preview tags and the fragment's own head parts; the nav first in the body",
      (page.startswith('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">'),
       '<meta property="og:title" content="Police &amp; Fire">' in head_part,
       '<meta property="og:url" content="https://example.org/police">' in head_part,
       '<meta property="og:description" content="Every incident mapped week by week.">' in head_part,
       '<meta property="og:image" content="https://example.org/og.jpg">' in head_part,
       '<meta property="og:image:alt" content="The logo">' in head_part,
       '<link rel="icon" href="/favicon.png" type="image/png">' in head_part,
       '<title>Police &amp; Fire</title>' in head_part, '<style>body{margin:0}</style>' in head_part,
       body_part.startswith('\n<body>\n<nav data-at="/police"></nav>\n<main><h1>Hello</h1></main>'),
       page.endswith('</body>\n</html>\n')),
      (True,) * 11)
titled = S.standalone(FRAG, SITE, 'police', title='San Marino Police Activity: Weekly Reports Mapped', nav='<nav></nav>')
check("a preview title overrides og:title alone (the tab keeps <title>); a path without a slash; nav as markup; the default description",
      ('content="San Marino Police Activity: Weekly Reports Mapped"' in titled, '<title>Police &amp; Fire</title>' in titled,
       'content="https://example.org/police"' in titled, '<body>\n<nav></nav>' in titled,
       f'name="description" content="{SITE.description}"' in titled),
      (True, True, True, True, True))
whole = '<!DOCTYPE html><html><body>x</body></html>'
bare = S.Site('Bare', 'https://bare.example', icon='', touch_icon='')
plain = S.head(bare, 'A "quoted" <title>', '', 'd')
check("a page with a doctype is returned as it is; no image, no icons: no image tags and a summary card; values escaped; '/' for no path",
      (S.standalone(whole, SITE), 'og:image' in plain, 'rel="icon"' in plain, 'content="summary"' in plain,
       'content="A &quot;quoted&quot; &lt;title&gt;"' in plain, 'content="https://bare.example/"' in plain),
      (whole, False, False, True, True, True))
check("page_title: the title unescaped, else the default; a fragment with no title takes the site's name in standalone",
      (S.page_title(FRAG), S.page_title('<p>x</p>', 'Fallback'), S.page_title('', ''),
       '<meta property="og:title" content="Example Site">' in S.standalone('<p>x</p>', SITE)),
      ('Police & Fire', 'Fallback', '', True))

print()
print(f"{len(fails)} failure(s)" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
