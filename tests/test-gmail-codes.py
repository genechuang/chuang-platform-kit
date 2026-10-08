#!/usr/bin/env python3
"""gmail_codes.wait_for_code: the one-time-code loop that four portal sign-ins each carried a copy of.

Pinned against a fake Gmail service (no Google library, nothing on the wire):
  - the code comes back from a text/plain body (the positive case), from an HTML body stripped of tags and entities,
    and from the snippet when the message has no readable part;
  - a mail stamped before the sign-in started (less a minute of grace) is a previous attempt's and is skipped;
  - the first group is the code when the pattern has one, the whole match when it does not;
  - nothing within `attempts` tries -> None, with `sleep(pause)` called between tries and not after the last;
  - the query Gmail receives carries `after:<since - 60>`.

Run:  python -B tests/test-gmail-codes.py
"""
import base64
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import gmail_codes as G  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


def b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip('=')


def msg(mid, stamp_s, plain=None, html=None, snippet=''):
    parts = []
    if plain is not None:
        parts.append({'mimeType': 'text/plain', 'body': {'data': b64(plain)}})
    if html is not None:
        parts.append({'mimeType': 'text/html', 'body': {'data': b64(html)}})
    payload = {'mimeType': 'multipart/alternative', 'parts': parts} if parts else {'mimeType': 'text/plain', 'body': {}}
    return {'id': mid, 'internalDate': str(stamp_s * 1000), 'snippet': snippet, 'payload': payload}


class Call:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeGmail:
    """messages().list() answers the ids newest first, like Gmail; get() the message. Records the queries it saw."""

    def __init__(self, messages):
        self.by_id = {m['id']: m for m in messages}
        self.queries, self.gets = [], []

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, userId, q, maxResults):
        self.queries.append(q)
        ids = sorted(self.by_id, key=lambda i: -int(self.by_id[i]['internalDate']))
        return Call(lambda: {'messages': [{'id': i} for i in ids]})

    def get(self, userId, id, format):
        self.gets.append(id)
        return Call(lambda: self.by_id[id])


T0 = 1_791_400_000          # the sign-in started (epoch seconds)
no_sleep = []

print('the code from a plain body')
g = FakeGmail([msg('a', T0 + 20, plain='Your SCE.com verification code is 482913. It expires in 10 minutes.')])
check('six digits found', G.wait_for_code(g, 'from:x subject:code', T0, G.SIX_DIGITS, sleep=no_sleep.append), '482913')
check('the query carries after:<since - 60>', g.queries[-1], f'from:x subject:code after:{T0 - 60}')
check('no sleep on a first-try hit', no_sleep, [])

print('html, snippet, and the older mail')
g = FakeGmail([msg('old', T0 - 300, plain='code 111111'),
               msg('h', T0 + 5, html='<html><style>.x{}</style><body><p>Your code is</p><b>22&#48;333</b><script>var a=1;</script></body></html>')])
check('html stripped, entity decoded, script and style skipped', G.wait_for_code(g, 'q', T0, G.SIX_DIGITS, sleep=no_sleep.append), '220333')
check('newest first: the old mail was never even fetched', g.gets, ['h'])
check('a mail stamped before the sign-in is a previous attempt, not a code',
      G.wait_for_code(FakeGmail([msg('old', T0 - 300, plain='code 111111')]), 'q', T0, G.SIX_DIGITS, attempts=1, sleep=no_sleep.append), None)
g = FakeGmail([msg('s', T0 + 1, snippet='Enter 654321 to continue')])
check('snippet when there is no readable part', G.wait_for_code(g, 'q', T0, G.SIX_DIGITS, sleep=no_sleep.append), '654321')
check('a mail within the minute of grace counts', G.wait_for_code(FakeGmail([msg('g', T0 - 30, plain='777777')]), 'q', T0, G.SIX_DIGITS, sleep=no_sleep.append), '777777')

print('patterns')
g = FakeGmail([msg('p', T0 + 1, plain='Security code: ABCD-1234')])
check('a pattern with a group answers the group', G.wait_for_code(g, 'q', T0, r'code:\s*([A-Z]{4}-\d{4})', sleep=no_sleep.append), 'ABCD-1234')
check('a pattern without a group answers the whole match', G.wait_for_code(g, 'q', T0, r'[A-Z]{4}-\d{4}', sleep=no_sleep.append), 'ABCD-1234')
check('a compiled pattern works too', G.wait_for_code(g, 'q', T0, __import__('re').compile(r'(\d{4})'), sleep=no_sleep.append), '1234')
check('a six-digit run inside a longer number is not a code', G.wait_for_code(FakeGmail([msg('n', T0 + 1, plain='order 1234567890')]), 'q', T0, G.SIX_DIGITS, attempts=1, sleep=no_sleep.append), None)

print('nothing arrives')
slept = []
g = FakeGmail([])
check('None after the attempts', G.wait_for_code(g, 'q', T0, G.SIX_DIGITS, attempts=3, pause=0.25, sleep=slept.append), None)
check('slept between tries, not after the last', slept, [0.25, 0.25])
check('listed once per attempt', len(g.queries), 3)

print(f'\n{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
