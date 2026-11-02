#!/usr/bin/env python3
"""chuang_platform_kit.slack: a post, a redraw, any Web API call and the incoming-webhook send, over a fake opener.

Pinned (the positive case first): post() sends chat.postMessage as JSON under the bot token with the channel, the text,
unfurl_links off, and blocks and thread_ts only when given, and returns Slack's answer; update() sends chat.update;
call() takes any method; form() sends form fields; a refusal (ok false), a non-2xx status, an HTTPError and no answer
are each a SlackError with the status and Slack's error or the failure's kind, never the token; a blank token is
refused before any call; webhook_post() sends the payload to the URL with no token, takes `ok`, and raises on a
refusal without the URL in the text.

Run:  python -B tests/test-slack.py
"""
import io
import json
import os
import sys
import urllib.error

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import slack as S  # noqa: E402

fails = []
TOKEN = 'xoxb-FAKE-token-for-the-suite'
HOOK = 'https://hooks.slack.com/services/T000/B000/FAKE-secret-path'


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class Resp(io.BytesIO):
    def __init__(self, body=b'{"ok": true}', status=200):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake(calls, answer=None):
    def opener(req, timeout=None):
        calls.append((req.get_method(), req.full_url, req.data, dict(req.header_items()), timeout))
        if callable(answer):
            return answer(req)
        return answer or Resp(b'{"ok": true, "ts": "1761866400.000100", "channel": "C1"}')
    return opener


def raised(fn):
    try:
        fn()
    except S.SlackError as e:
        return e
    return None


print("the positive case: post() sends chat.postMessage as JSON under the bot token and returns Slack's answer")
calls = []
got = S.post(TOKEN + '\n', 'C1', 'hello', opener=fake(calls))
method, url, data, headers, timeout = calls[0]
check("the call", (method, url, json.loads(data), headers.get('Authorization'), headers.get('Content-type'), timeout),
      ('POST', 'https://slack.com/api/chat.postMessage', {'channel': 'C1', 'text': 'hello', 'unfurl_links': False},
       f'Bearer {TOKEN}', 'application/json; charset=utf-8', 15))
check("the answer", (got.get('ok'), got.get('ts'), got.get('channel')), (True, '1761866400.000100', 'C1'))

print("blocks, a thread reply and the unfurl switches are sent only when given")
calls = []
blocks = [{'type': 'section', 'text': {'type': 'mrkdwn', 'text': '*hi*'}}]
S.post(TOKEN, 'C1', 'n', blocks=blocks, thread_ts='1.2', unfurl_links=True, unfurl_media=False, opener=fake(calls), timeout=8)
check("every field", (json.loads(calls[0][2]), calls[0][4]),
      ({'channel': 'C1', 'text': 'n', 'unfurl_links': True, 'unfurl_media': False, 'blocks': blocks, 'thread_ts': '1.2'}, 8))
calls = []
S.post(TOKEN, 'C1', 'n', blocks=[], thread_ts='', opener=fake(calls))
check("empty blocks and no thread are left out", json.loads(calls[0][2]), {'channel': 'C1', 'text': 'n', 'unfurl_links': False})

print("update() sends chat.update with the ts, the text and the blocks")
calls = []
S.update(TOKEN, 'C1', '1.2', 'again', blocks=blocks, opener=fake(calls))
check("chat.update", (calls[0][1], json.loads(calls[0][2])),
      ('https://slack.com/api/chat.update', {'channel': 'C1', 'ts': '1.2', 'text': 'again', 'blocks': blocks}))
calls = []
S.update(TOKEN, 'C1', '1.2', 'plain', opener=fake(calls))
check("no blocks key without blocks", json.loads(calls[0][2]), {'channel': 'C1', 'ts': '1.2', 'text': 'plain'})

print("call() takes any method as JSON; form() sends form fields")
calls = []
S.call('chat.unfurl', TOKEN, {'channel': 'C1', 'ts': '1.2', 'unfurls': {}}, opener=fake(calls))
check("chat.unfurl", (calls[0][1], json.loads(calls[0][2])), ('https://slack.com/api/chat.unfurl', {'channel': 'C1', 'ts': '1.2', 'unfurls': {}}))
calls = []
S.form('files.getUploadURLExternal', TOKEN, {'filename': 'a.png', 'length': 3}, opener=fake(calls))
check("form fields", (calls[0][1], calls[0][2], calls[0][3].get('Content-type')),
      ('https://slack.com/api/files.getUploadURLExternal', b'filename=a.png&length=3', 'application/x-www-form-urlencoded'))

print("a refusal, a non-2xx status, an HTTPError and no answer are each a SlackError naming the status and the error, never the token")
refused = raised(lambda: S.post(TOKEN, 'C1', 'x', opener=fake([], Resp(b'{"ok": false, "error": "not_in_channel"}'))))
check("ok false", (refused.status, refused.error, refused.unreachable, str(refused)), (200, 'not_in_channel', False, 'Slack chat.postMessage: HTTP 200 (not_in_channel)'))
status = raised(lambda: S.post(TOKEN, 'C1', 'x', opener=fake([], Resp(b'no', 500))))
check("a non-2xx status with no JSON", (status.status, status.error), (500, 'HTTP 500'))


def http_error(req, timeout=None):
    raise urllib.error.HTTPError(req.full_url, 404, 'no_service', {}, io.BytesIO(b'no_service'))


def http_error_json(req, timeout=None):
    raise urllib.error.HTTPError(req.full_url, 429, 'Too Many', {}, io.BytesIO(b'{"ok": false, "error": "ratelimited"}'))


def unreachable(req, timeout=None):
    raise urllib.error.URLError(TimeoutError('timed out'))


def dns(req, timeout=None):
    raise urllib.error.URLError('name or service not known')


e404 = raised(lambda: S.post(TOKEN, 'C1', 'x', opener=http_error))
e429 = raised(lambda: S.post(TOKEN, 'C1', 'x', opener=http_error_json))
down = raised(lambda: S.post(TOKEN, 'C1', 'x', opener=unreachable))
nodns = raised(lambda: S.post(TOKEN, 'C1', 'x', opener=dns))
check("HTTPError", ((e404.status, e404.error), (e429.status, e429.error)), ((404, 'no_service'), (429, 'ratelimited')))
check("no answer", ((down.status, down.error, down.unreachable), (nodns.status, nodns.error)), ((0, 'TimeoutError', True), (0, 'URLError')))
check("the token is in no error", any(TOKEN in str(e) or TOKEN in e.error for e in (refused, status, e404, e429, down, nodns)), False)
none = []
blank = raised(lambda: S.post('  ', 'C1', 'x', opener=fake(none)))
check("a blank token is refused before any call", (blank.status, blank.error, none), (0, 'no token', []))
check("a bad JSON answer with ok missing is 'no ok'", raised(lambda: S.call('x', TOKEN, {}, opener=fake([], Resp(b'<html>')))).error, 'no ok')

print("webhook_post() sends the payload to the URL with no token, takes `ok`, and a refusal never names the URL")
calls = []
S.webhook_post(HOOK + ' ', {'text': 'hi', 'blocks': blocks}, opener=fake(calls, Resp(b'ok')))
check("the call", (calls[0][0], calls[0][1], json.loads(calls[0][2]), 'Authorization' in calls[0][3]),
      ('POST', HOOK, {'text': 'hi', 'blocks': blocks}, False))
check("an empty answer is fine too", S.webhook_post(HOOK, {'text': 'x'}, opener=fake([], Resp(b''))), None)
bad = raised(lambda: S.webhook_post(HOOK, {}, opener=http_error))
check("refused", (bad.status, bad.error, bad.method, HOOK in str(bad), 'FAKE-secret' in str(bad)), (404, 'no_service', 'webhook', False, False))
nope = raised(lambda: S.webhook_post(HOOK, {}, opener=fake([], Resp(b'{"ok": false, "error": "invalid_blocks"}'))))
check("a JSON ok false", (nope.status, nope.error), (200, 'invalid_blocks'))
gone = raised(lambda: S.webhook_post(HOOK, {}, opener=unreachable))
check("no answer", (gone.status, gone.error), (0, 'TimeoutError'))
none = []
check("a blank URL is refused before any call", (raised(lambda: S.webhook_post('', {}, opener=fake(none))).error, none), ('no webhook url', []))

print(f"{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
