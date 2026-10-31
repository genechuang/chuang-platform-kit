#!/usr/bin/env python3
"""chuang_platform_kit.anthropic_api: Anthropic's Messages API, one client for every project (SMAD PickleBot #573).

Pinned (the positive case first): messages() sends the key, the version, the host's beta header, the payload with the
system prompt and the extras, joins the text blocks (a thinking block skipped), names the model that answered and runs
the host's meter on the body; ask() is one user turn; a non-200 is Refused with the status, Anthropic's own message and
whether the account cannot pay, a plain-text body as it is; a transport that raises is Refused with no status; no key
is Refused before any call; a meter that raises never fails the call; urllib_post answers an HTTP error as an Answer.
Offline: the transport is a fake.

Run:  python -B tests/test-anthropic-api.py
"""
import io
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import anthropic_api as A  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class Resp:
    def __init__(self, status, body=None, text=''):
        self.status_code, self._body, self.text = status, body, text

    def json(self):
        return self._body


def poster(reply, calls):
    def post(url, headers=None, json=None, timeout=None):
        calls.append({'url': url, 'headers': headers, 'json': json, 'timeout': timeout})
        if isinstance(reply, Exception):
            raise reply
        return reply
    return post


calls, metered = [], []
body = {'model': 'claude-haiku-5-5-20260601', 'stop_reason': 'end_turn', 'usage': {'input_tokens': 10, 'output_tokens': 4},
        'content': [{'type': 'thinking', 'thinking': 'hm'}, {'type': 'text', 'text': 'first'}, {'type': 'text', 'text': 'second'}]}
got = A.messages('claude-haiku-5-5', [{'role': 'user', 'content': 'FACTS'}], system='be brief', max_tokens=4000, api_key='k',
                 timeout=25, headers={'anthropic-beta': 'server-side-fallback-2026-07-01'},
                 extra={'output_config': {'effort': 'low'}, 'fallbacks': 'default'},
                 usage=lambda b, m: metered.append((b['usage'], m)), post=poster(Resp(200, body), calls))
c = calls[0]
check("the positive case: the key, the version and the beta header; the payload with system and extras; the text blocks joined; the model that answered; the meter run",
      (got['text'], got['stop_reason'], got['model'], c['url'], c['headers']['x-api-key'], c['headers']['anthropic-version'],
       c['headers']['anthropic-beta'], c['headers']['content-type'], c['json']['model'], c['json']['max_tokens'], c['json']['system'],
       c['json']['output_config'], c['json']['fallbacks'], c['json']['messages'], c['timeout'], metered, got['body'] is body),
      ('first\nsecond', 'end_turn', 'claude-haiku-5-5-20260601', A.API, 'k', '2023-06-01', 'server-side-fallback-2026-07-01',
       'application/json', 'claude-haiku-5-5', 4000, 'be brief', {'effort': 'low'}, 'default', [{'role': 'user', 'content': 'FACTS'}],
       25, [({'input_tokens': 10, 'output_tokens': 4}, 'claude-haiku-5-5')], True))

calls2 = []
asked = A.ask('hello', 'm', api_key='k', post=poster(Resp(200, {'content': [{'type': 'text', 'text': 'hi'}]}), calls2))
check("ask is one user turn; a body with no model or stop reason answers the asked model and None; no system key when none",
      (asked['text'], asked['model'], asked['stop_reason'], calls2[0]['json']['messages'], 'system' in calls2[0]['json'], calls2[0]['json']['max_tokens']),
      ('hi', 'm', None, [{'role': 'user', 'content': 'hello'}], False, 1024))


def refusal(reply, **kw):
    try:
        A.ask('x', 'm', api_key='k', post=poster(reply, []), **kw)
        return None
    except A.Refused as e:
        return (str(e), e.status, e.detail, e.out_of_credits)


broke = Resp(402, {'error': {'type': 'invalid_request_error', 'message': 'Your credit balance is too low to access the Anthropic API.'}},
             text='{"error": {"message": "Your credit balance is too low to access the Anthropic API."}}')
check("a refusal carries the status, Anthropic's own message and whether the account cannot pay; a plain body as it is; a rate limit is not out of credits",
      (refusal(broke), refusal(Resp(529, None, text='overloaded')), refusal(Resp(429, {'error': {'message': 'rate limited'}}, text='x'))[3],
       refusal(ValueError('boom'))),
      (('Anthropic answered HTTP 402: Your credit balance is too low to access the Anthropic API.', 402,
        'Your credit balance is too low to access the Anthropic API.', True),
       ('Anthropic answered HTTP 529: overloaded', 529, 'overloaded', False), False,
       ('Anthropic did not answer (ValueError: boom)', 0, 'ValueError: boom', False)))

none = []
try:
    A.ask('x', 'm', env={}, post=poster(Resp(200, {}), none))
    no_key = ''
except A.Refused as e:
    no_key = str(e)
env_calls = []
A.ask('x', 'm', env={'ANTHROPIC_API_KEY': 'from-env'}, post=poster(Resp(200, {'content': []}), env_calls),
      usage=lambda b, m: (_ for _ in ()).throw(RuntimeError('meter down')))
check("no key is Refused before any call; the environment's key is used; a meter that raises never fails the call",
      (no_key, len(none), env_calls[0]['headers']['x-api-key']), ('no Anthropic API key: pass api_key or set ANTHROPIC_API_KEY', 0, 'from-env'))


class Raw(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


seen = []


def urlopen(req, timeout=0):
    seen.append((req.get_method(), req.full_url, req.headers.get('X-api-key'), json.loads(req.data), timeout))
    if 'bad' in json.loads(req.data)['model']:
        raise urllib.error.HTTPError(req.full_url, 500, 'no', {}, io.BytesIO(b'{"error": {"message": "server"}}'))
    return Raw(b'{"content": [{"type": "text", "text": "ok"}]}')


kept = urllib.request.urlopen
try:
    urllib.request.urlopen = urlopen
    fine = A.urllib_post(A.API, headers={'x-api-key': 'k'}, json={'model': 'm'}, timeout=5)
    bad = A.urllib_post(A.API, headers={'x-api-key': 'k'}, json={'model': 'bad'}, timeout=5)
    through = A.ask('x', 'm', api_key='k')
finally:
    urllib.request.urlopen = kept
check("urllib_post: a POST with the headers and the JSON body; an HTTP error is an Answer with its status and body; messages() rides it by default",
      ((fine.status_code, fine.json()), (bad.status_code, bad.text), seen[0][:3], seen[0][4], through['text']),
      ((200, {'content': [{'type': 'text', 'text': 'ok'}]}), (500, '{"error": {"message": "server"}}'), ('POST', A.API, 'k'), 5, 'ok'))

check("out_of_credits: the billing words at 400/402/403 only",
      (A.out_of_credits(400, 'usage limit reached'), A.out_of_credits(403, 'Your credit balance is too low'), A.out_of_credits(429, 'usage limit'),
       A.out_of_credits(402, 'nope'), A.out_of_credits(500, 'billing')), (True, True, False, False, False))

print()
print(f"{len(fails)} failure(s)" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
