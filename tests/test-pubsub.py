#!/usr/bin/env python3
"""pubsub: the REST publish every host's producers share (0.6.0), its token chain, the envelope and the decoder.

Offline: urlopen and subprocess.run are fakes, and google-auth is made to fail, so nothing reaches Google. Pinned:
  - a publish is one POST of base64 JSON with string attributes; an ordering key sends it to the regional
    endpoint, and no key to the global one;
  - a 5xx and a dropped connection are tried once more, a 401 drops the kept token first, a 4xx is final, and
    the failure never carries the token;
  - the token: the metadata server on Google Cloud only, then ADC, then gcloud; it is kept until a minute
    before it expires;
  - the envelope's fields, and decode() of a push body, a CloudEvent and a bare message.

Run:  python -B tests/test-pubsub.py
"""
import base64
import io
import json
import os
import sys
import types
import urllib.error
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import pubsub as P  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def answers(*replies):
    """A fake urlopen answering each request in turn: a dict is a JSON body, an exception is raised."""
    seen = []

    def fetch(req, timeout):
        seen.append(req)
        r = replies[min(len(seen), len(replies)) - 1]
        if isinstance(r, Exception):
            raise r
        return Resp(json.dumps(r).encode())
    return fetch, seen


def http(code):
    return urllib.error.HTTPError('u', code, 'x', {}, None)


def no_adc():
    raise RuntimeError('no ADC here')


P._adc_token = no_adc
for k in P._ON_GOOGLE_CLOUD:
    os.environ.pop(k, None)

print("\n-- publish()")
P.forget_token()
fetch, seen = answers({'messageIds': ['m1']})
mid = P.publish('votes', {'b': 1, 'a': 2}, project='proj', attributes={'kind': 'vote', 'n': 3},
                ordering_key='game-1', fetch=fetch, token='tok')
body = json.loads(seen[0].data)['messages'][0]
check("the positive case: the id Pub/Sub assigned, one POST to the regional endpoint with the ordering key",
      (mid, seen[0].full_url, seen[0].get_method(), body['orderingKey']),
      ('m1', 'https://us-west1-pubsub.googleapis.com/v1/projects/proj/topics/votes:publish', 'POST', 'game-1'))
check("  the data is sorted JSON in base64, the attributes strings, the bearer the token",
      (base64.b64decode(body['data']).decode(), body['attributes'], seen[0].get_header('Authorization')),
      ('{"a": 2, "b": 1}', {'kind': 'vote', 'n': '3'}, 'Bearer tok'))
fetch, seen = answers({'messageIds': ['m2']})
P.publish('whatsapp-messages', b'raw', project='proj', fetch=fetch, token='tok')
body = json.loads(seen[0].data)['messages'][0]
check("no ordering key: the global endpoint, the bytes as given, no orderingKey",
      (seen[0].full_url, base64.b64decode(body['data']), 'orderingKey' in body),
      ('https://pubsub.googleapis.com/v1/projects/proj/topics/whatsapp-messages:publish', b'raw', False))
fetch, seen = answers(http(503), {'messageIds': ['m3']})
check("a 503 is tried once more", (P.publish('t', {}, project='p', fetch=fetch, token='tok'), len(seen)), ('m3', 2))
fetch, seen = answers(urllib.error.URLError('reset'), {'messageIds': ['m4']})
check("a dropped connection is tried once more", (P.publish('t', {}, project='p', fetch=fetch, token='tok'), len(seen)),
      ('m4', 2))


def failure(fn):
    try:
        fn()
    except P.PublishFailed as e:
        return str(e)
    return None


fetch, seen = answers(http(403))
check("a 403 is final, named, and never names the token",
      (failure(lambda: P.publish('t', {}, project='p', fetch=fetch, token='secret-tok')), len(seen)), ('HTTP 403', 1))
fetch, seen = answers(http(500), http(502))
check("two 5xx: PublishFailed with the last", failure(lambda: P.publish('t', {}, project='p', fetch=fetch, token='x')),
      'HTTP 502')
fetch, seen = answers({'messageIds': []})
check("no message id is a failure", failure(lambda: P.publish('t', {}, project='p', fetch=fetch, token='x')),
      'no message id')
check("no topic or project: refused before any request", failure(lambda: P.publish('', {}, project='p', token='x')),
      'no topic or project')

print("\n-- access_token()")
P.forget_token()
calls = []


def gcloud(args, **kw):
    calls.append(args[1:])
    return types.SimpleNamespace(returncode=0, stdout='gc-token\n')


clock = [100.0]
check("the positive case off Google Cloud: ADC fails, gcloud answers",
      (P.access_token(run=gcloud, now=lambda: clock[0]), calls), ('gc-token', [['auth', 'print-access-token']]))
check("  kept: a second call asks nobody", (P.access_token(run=gcloud, now=lambda: clock[0]), len(calls)), ('gc-token', 1))
clock[0] += 300
P.access_token(run=gcloud, now=lambda: clock[0])
check("  asked again once it is within a minute of expiring", len(calls), 2)
P.forget_token()
os.environ['K_SERVICE'] = 'svc'
fetch, seen = answers({'access_token': 'md-token', 'expires_in': 3599})
check("on Google Cloud: the metadata server first, with its flavor header",
      (P.access_token(fetch=fetch, run=gcloud), seen[0].full_url, seen[0].get_header('Metadata-flavor')),
      ('md-token', P.METADATA_TOKEN_URL, 'Google'))
del os.environ['K_SERVICE']
P.forget_token()
check("no source answers: PublishFailed naming the kinds",
      failure(lambda: P.access_token(run=lambda a, **k: types.SimpleNamespace(returncode=1, stdout=''))),
      'no access token (RuntimeError, PublishFailed)')
P.forget_token()
fetch, seen = answers(http(401), {'messageIds': ['m5']})
tokens = iter(['old', 'new'])
P._adc_token = lambda: (next(tokens), 3600.0)
check("a 401 drops the kept token and asks again",
      (P.publish('t', {}, project='p', fetch=fetch), [r.get_header('Authorization') for r in seen]),
      ('m5', ['Bearer old', 'Bearer new']))
P._adc_token = no_adc
P.forget_token()

print("\n-- envelope() and decode()")
e = P.envelope('match.recorded', {'score': [11, 0]}, source='api', subject={'game_id': 4}, actor={'slug': 'x'},
               correlation_id='req-1', at=datetime(2026, 10, 16, 18, 0, tzinfo=timezone.utc), id='e1', tenant='t1')  # utc-ok: an instant
check("the positive case: every field, `at` in UTC with a Z, extra keys beside them",
      e, {'id': 'e1', 'type': 'match.recorded', 'v': 1, 'at': '2026-10-16T18:00:00Z', 'source': 'api',
          'subject': {'game_id': 4}, 'actor': {'slug': 'x'}, 'correlation_id': 'req-1', 'data': {'score': [11, 0]},
          'tenant': 't1'})
check("  a fresh id per event", P.envelope('x', {}, source='s')['id'] != P.envelope('x', {}, source='s')['id'], True)
msg = {'data': base64.b64encode(json.dumps(e).encode()).decode(), 'attributes': {'type': 'match.recorded'},
       'messageId': '77'}
check("decode: a push subscription's body", P.decode({'message': msg, 'subscription': 's'}),
      (e, {'type': 'match.recorded'}, '77'))
check("decode: an Eventarc CloudEvent", P.decode(types.SimpleNamespace(data={'message': msg})),
      (e, {'type': 'match.recorded'}, '77'))
check("decode: the body as JSON text", P.decode(json.dumps({'message': msg}))[2], '77')
check("decode: text that is not JSON comes back as text",
      P.decode({'message': {'data': base64.b64encode(b'hello').decode()}})[0], 'hello')
try:
    P.decode({'nothing': 1})
    refused = None
except ValueError as err:
    refused = str(err)
check("decode: no message is a ValueError", refused, 'no Pub/Sub message in the delivery')

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
