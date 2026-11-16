#!/usr/bin/env python3
"""chuang_platform_kit.web_push: Web Push on the standard alone (RFC 8291 encryption, RFC 8292 VAPID, RFC 8030).

Pinned (the positive case first, offline, the push service a stand-in opener):

- the known answer: RFC 8291's own published example (section 5 and Appendix A: its keys, salt and plaintext) encrypts
  to the published body, byte for byte; the values below are the RFC's public example, not anyone's keys
- a message with a random salt and sender key decrypts with the receiver's private key to the payload
- the VAPID header: `vapid t=<JWT>, k=<public key>`, an ES256 signature the public key verifies, aud the endpoint's
  origin, exp 12 hours ahead, the subject as given; a subject that is not mailto: or https: is refused
- check_subscription: a browser's own JSON, padded or not, comes back normalized; a wrong host, http, a port, a
  password in the URL, a short auth, an off-curve point are each refused in plain words
- endpoint_ok: the four push services and nothing else (never a bare suffix, never any googleapis.com host)
- send: one POST with TTL, Urgency, Content-Encoding and the Authorization header; 201 taken, 404 and 410 gone, 429
  with its Retry-After, a 403 never gone, a redirect and a dead connection status 0; nothing is posted to an endpoint
  the allow-list refuses; a payload past 3993 octets is refused before any request
- new_private_key makes a P-256 key public_key reads; a key of another curve or junk is a ValueError without the text

Run:  python -B tests/test-web-push.py
"""
import base64
import http.client
import io
import json
import os
import sys
import urllib.error

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402
from cryptography.hazmat.primitives.kdf.hkdf import HKDF  # noqa: E402

from chuang_platform_kit import web_push as W  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


def refused(fn, *a, **k):
    """The ValueError's words, or 'accepted'."""
    try:
        fn(*a, **k)
        return 'accepted'
    except ValueError as e:
        return str(e)


# RFC 8291 section 5 and Appendix A, as printed (two independent readings of the RFC text agreed on every value).
RFC = {
    'plaintext': b'When I grow up, I want to be a watermelon',
    'as_scalar': 'yfWPiYE-n46HLnH0KqZOF1' + 'fJJU3MYrct3AELtAQ-oRw',   # the RFC's example sender, in two halves
    'as_public': 'BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8',
    'ua_scalar': 'q1dXpw3UpT5VOmu_cf_v6i' + 'h07Aems3njxI-JWgLcM94',   # the RFC's example receiver, in two halves
    'ua_public': 'BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4',
    'auth': 'BTBZMqHH6r4Tts7J_aSIgg',
    'salt': 'DGv6ra1nlYgDCS1FRnbzlw',
    'header': 'DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8',
    'body': ('DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_'
             'yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN'),
}

body = W.encrypt(RFC['plaintext'], RFC['ua_public'], RFC['auth'], salt=W.unb64url(RFC['salt']), sender_key=RFC['as_scalar'])
check("the positive case: RFC 8291's example encrypts to the published body, byte for byte",
      (body == W.unb64url(RFC['body']), len(body), body[:86] == W.unb64url(RFC['header']), body[16:21].hex()),
      (True, 144, True, '0000100041'))


def decrypt(message: bytes, ua_scalar: str, auth: str) -> bytes:
    """The receiver's side of RFC 8291, written here from the RFC apart from the module under test."""
    salt, idlen = message[:16], message[20]
    sender, record = message[21:21 + idlen], message[21 + idlen:]
    mine = ec.derive_private_key(int.from_bytes(W.unb64url(ua_scalar), 'big'), ec.SECP256R1())
    my_point = mine.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = mine.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender))
    ikm = HKDF(hashes.SHA256(), 32, W.unb64url(auth), b'WebPush: info\x00' + my_point + sender).derive(shared)
    key = HKDF(hashes.SHA256(), 16, salt, b'Content-Encoding: aes128gcm\x00').derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b'Content-Encoding: nonce\x00').derive(ikm)
    plain = AESGCM(key).decrypt(nonce, record, None)
    return plain.rstrip(b'\x00')


check("  the RFC's body decrypts with the receiver's key to the plaintext and the last-record delimiter",
      decrypt(W.unb64url(RFC['body']), RFC['ua_scalar'], RFC['auth']), RFC['plaintext'] + b'\x02')
one, two = (W.encrypt(b'{"title": "hi"}', RFC['ua_public'], RFC['auth']) for _ in range(2))
check('a message with its own random salt and sender key decrypts to the payload, and two are never the same bytes',
      (decrypt(one, RFC['ua_scalar'], RFC['auth']), one != two, one[:16] != two[:16], one[21:86] != two[21:86]),
      (b'{"title": "hi"}\x02', True, True, True))
check('a payload at the limit is taken, one octet past it refused before anything is encrypted',
      (len(W.encrypt(b'x' * W.MAX_PAYLOAD, RFC['ua_public'], RFC['auth'])), refused(W.encrypt, b'x' * (W.MAX_PAYLOAD + 1), RFC['ua_public'], RFC['auth'])),
      (4096, 'the payload is 3994 octets; a Web Push message carries at most 3993'))

# The server's key and the VAPID header.
PEM = W.new_private_key()
PUBLIC = W.public_key(PEM)
check('new_private_key makes a P-256 key public_key reads as a 65-octet uncompressed point',
      (PEM.startswith('-----BEGIN PRIVATE KEY-----'), len(W.unb64url(PUBLIC)), W.unb64url(PUBLIC)[0], W.public_key(PEM + '\n') == PUBLIC),
      (True, 65, 4, True))
ENDPOINT = 'https://fcm.googleapis.com/fcm/send/abc123:def'
header = W.vapid_header(ENDPOINT, PEM, 'https://example.org', now=1_800_000_000)
scheme, rest = header.split(' ', 1)
t, k = (p.split('=', 1)[1] for p in rest.split(', '))
h64, c64, s64 = t.split('.')
sig = W.unb64url(s64)
verifier = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), W.unb64url(k))
try:
    verifier.verify(encode_dss_signature(int.from_bytes(sig[:32], 'big'), int.from_bytes(sig[32:], 'big')),
                    f'{h64}.{c64}'.encode(), ec.ECDSA(hashes.SHA256()))
    verified = True
except Exception:  # noqa: BLE001 - any failure is the test's answer
    verified = False
check('the VAPID header: vapid t=<JWT>, k=<the public key>, an ES256 signature of 64 octets the public key verifies',
      (scheme, k == PUBLIC, json.loads(W.unb64url(h64)), len(sig), verified),
      ('vapid', True, {'typ': 'JWT', 'alg': 'ES256'}, 64, True))
check("  its claims: aud the endpoint's origin, exp 12 hours ahead, the subject as given",
      json.loads(W.unb64url(c64)), {'aud': 'https://fcm.googleapis.com', 'exp': 1_800_000_000 + 12 * 3600, 'sub': 'https://example.org'})
check('  a subject that is not mailto: or https:, a spaced one, and a token over a day are refused',
      (refused(W.vapid_header, ENDPOINT, PEM, 'example.org'), refused(W.vapid_header, ENDPOINT, PEM, 'mailto: a@example.org'),
       refused(W.vapid_header, ENDPOINT, PEM, 'https://example.org', seconds=24 * 3600 + 1)),
      ('the VAPID subject must be a mailto: or https: URI', 'the VAPID subject must be a mailto: or https: URI', 'a VAPID token lives at most a day'))
other = ec.generate_private_key(ec.SECP384R1()).private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                              serialization.NoEncryption()).decode()
check('a key of another curve, or text that is no key, is a ValueError that never echoes the text',
      (refused(W.public_key, other), refused(W.public_key, 'not a key at all')),
      ('the VAPID key is not a P-256 key', 'the VAPID key is not a PEM private key'))

# The endpoint allow-list.
GOOD = ['https://fcm.googleapis.com/fcm/send/x', 'https://updates.push.services.mozilla.com/wpush/v2/x',
        'https://web.push.apple.com/Qx', 'https://wns2-by3p.notify.windows.com/w/?token=x', 'https://FCM.googleapis.com:443/fcm/send/x']
BAD = ['http://fcm.googleapis.com/fcm/send/x', 'https://storage.googleapis.com/x', 'https://metadata.google.internal/x',
       'https://169.254.169.254/latest', 'https://push.apple.com/x', 'https://evilpush.apple.com.example.org/x',
       'https://fcm.googleapis.com:8443/x', 'https://fcm.googleapis.com.example.org/x', 'https://localhost/x',
       'https://' + 'user' + ':' + 'pw' + '@' + 'fcm.googleapis.com/x', 'ftp://fcm.googleapis.com/x', '', None, 'https://' + 'a' * 2000]
check('endpoint_ok: the four push services, https on the default port, and nothing else',
      ([W.endpoint_ok(u) for u in GOOD], [u for u in BAD if W.endpoint_ok(u)]), ([True] * 5, []))
check("  host_of is the one part of an endpoint that is logged", (W.host_of(GOOD[0]), W.host_of('nonsense'), W.host_of(None)),
      ('fcm.googleapis.com', '', ''))

# check_subscription.
padded = base64.urlsafe_b64encode(W.unb64url(RFC['ua_public'])).decode()
sub = W.check_subscription({'endpoint': ENDPOINT, 'expirationTime': None, 'keys': {'p256dh': padded, 'auth': RFC['auth'] + '=='}})
check("check_subscription: a browser's own JSON, padded or not, comes back normalized",
      sub, {'endpoint': ENDPOINT, 'p256dh': RFC['ua_public'], 'auth': RFC['auth']})
off_curve = W.b64url(b'\x04' + b'\x01' * 64)
check('  each bad part is refused in plain words',
      (refused(W.check_subscription, {'endpoint': 'https://example.org/x', 'keys': {'p256dh': RFC['ua_public'], 'auth': RFC['auth']}}),
       refused(W.check_subscription, {'endpoint': ENDPOINT, 'keys': {'p256dh': RFC['ua_public'], 'auth': 'AAAA'}}),
       refused(W.check_subscription, {'endpoint': ENDPOINT, 'keys': {'p256dh': RFC['auth'], 'auth': RFC['auth']}}),
       refused(W.check_subscription, {'endpoint': ENDPOINT, 'keys': {'p256dh': off_curve, 'auth': RFC['auth']}}),
       refused(W.check_subscription, {'endpoint': ENDPOINT, 'keys': {'p256dh': 'not base64!', 'auth': RFC['auth']}}),
       refused(W.check_subscription, {'endpoint': ENDPOINT}), refused(W.check_subscription, 'text')),
      ("'endpoint' must be an https address at a browser's push service", "'keys.auth' must be 16 octets",
       "'keys.p256dh' must be a 65-octet uncompressed P-256 point", "'keys.p256dh' is not a point on P-256",
       "'keys.p256dh' and 'keys.auth' must be base64url text",
       'send {"endpoint": "https://...", "keys": {"p256dh": "...", "auth": "..."}}',
       'send {"endpoint": "https://...", "keys": {"p256dh": "...", "auth": "..."}}'))


# send, against a stand-in push service.
class Answer(io.BytesIO):
    def __init__(self, status, headers=None):
        super().__init__(b'')
        self.status, self.headers = status, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Service:
    """A push service: each call's request kept, the queued answer returned or raised."""

    def __init__(self, *answers):
        self.answers, self.requests = list(answers), []

    def __call__(self, req, timeout):
        self.requests.append((req, timeout))
        got = self.answers.pop(0)
        if isinstance(got, Exception):
            raise got
        return got


def http_error(code, headers=None):
    return urllib.error.HTTPError(ENDPOINT, code, 'x', headers or {}, io.BytesIO(b'{"reason": "x"}'))


svc = Service(Answer(201))
got = W.send(sub, b'{"title": "Game on"}', PEM, 'https://example.org', ttl=3600, urgency='high', topic='recruit7', opener=svc, now=1_800_000_000)
req, timeout = svc.requests[0]
sent = {k.lower(): v for k, v in req.header_items()}
check('send: one POST to the endpoint, taken with a 201',
      (got, req.get_method(), req.full_url, timeout, len(svc.requests)),
      ({'status': 201, 'ok': True, 'gone': False, 'retry_after': '', 'host': 'fcm.googleapis.com', 'error': ''}, 'POST', ENDPOINT, W.TIMEOUT, 1))
check('  its headers: TTL, Urgency, Topic, the aes128gcm coding and the VAPID Authorization',
      (sent['ttl'], sent['urgency'], sent['topic'], sent['content-encoding'], sent['content-type'],
       sent['authorization'].startswith('vapid t='), sent['authorization'].endswith(', k=' + PUBLIC)),
      ('3600', 'high', 'recruit7', 'aes128gcm', 'application/octet-stream', True, True))
check('  its body decrypts with the subscription\'s private key to the payload',
      decrypt(req.data, RFC['ua_scalar'], RFC['auth']), b'{"title": "Game on"}\x02')
answers = [http_error(404), http_error(410), http_error(429, {'Retry-After': '120'}), http_error(403), http_error(400), http_error(503),
           http_error(301, {'Location': 'https://example.org/'}), urllib.error.URLError('timed out'), ConnectionResetError('reset'),
           http.client.BadStatusLine('garbage'), http.client.LineTooLong('header line'), Answer(202), http_error(503, {'Retry-After': '30'})]
svc = Service(*answers)
outs = [W.send(sub, b'{}', PEM, 'https://example.org', opener=svc) for _ in answers]
check('404 and 410 are gone; 429 carries its Retry-After; 403, 400 and 503 are never gone; a redirect, a dead connection and a broken answer are status 0 with their kind',
      [(o['status'], o['ok'], o['gone'], o['retry_after'], o['error']) for o in outs],
      [(404, False, True, '', ''), (410, False, True, '', ''), (429, False, False, '120', ''), (403, False, False, '', ''),
       (400, False, False, '', ''), (503, False, False, '', ''), (0, False, False, '', 'redirect'), (0, False, False, '', 'URLError'),
       (0, False, False, '', 'ConnectionResetError'), (0, False, False, '', 'BadStatusLine'), (0, False, False, '', 'LineTooLong'),
       (202, True, False, '', ''), (503, False, False, '30', '')])
check('  no failure names the address', [o for o in outs if 'fcm/send' in json.dumps(o)], [])
svc = Service()
check('nothing is posted to an endpoint the allow-list refuses, for a bad ttl, urgency or topic, or past the size limit',
      (refused(W.send, dict(sub, endpoint='https://169.254.169.254/x'), b'{}', PEM, 'https://example.org', opener=svc),
       refused(W.send, sub, b'{}', PEM, 'https://example.org', ttl=0, opener=svc),
       refused(W.send, sub, b'{}', PEM, 'https://example.org', urgency='urgent', opener=svc),
       refused(W.send, sub, b'{}', PEM, 'https://example.org', topic='a b', opener=svc),
       refused(W.send, sub, b'x' * 3994, PEM, 'https://example.org', opener=svc), svc.requests),
      ("the subscription's endpoint is not at a browser's push service", 'ttl must be 1 second to 28 days',
       'urgency must be one of very-low, low, normal, high', 'topic must be at most 32 letters and digits',
       'the payload is 3994 octets; a Web Push message carries at most 3993', []))
handler = W._NoRedirect()
check('the default opener follows no redirect', handler.redirect_request(None, None, 302, 'Found', {}, 'https://example.org/'), None)

# The review's findings (three lenses, each finding checked by a skeptic), each pinned.
# 1. The check reads the address as the request library will: urlsplit drops tab, CR and LF and never decodes a
#    percent sign, the request library keeps the one and decodes the other, so each of these named one host to the
#    check and another to the request.
TRICKS = ['https://fcm.goog\tleapis.com/x', 'https://a\n.push.apple.com/x', 'https://fcm.googleapis.com/x\r\nX-A: b',
          'https://127.0.0.1%3a80%2f.push.apple.com/x', 'https://127.0.0.1%00.push.apple.com/x', 'https://127.0.0.1%20.push.apple.com/x',
          'https://169.254.169.254%23.push.apple.com/x', 'https://evil.example\\.push.apple.com/x', 'https://a b.push.apple.com/x',
          'https://..push.apple.com/x', 'https://x_y.push.apple.com/x', 'https://fcm.googleapis.com:0443/x', 'https://fcm.googleapis.com:/x',
          'https://@fcm.googleapis.com/x', 'https://fcm.googleapis.com./x', 'https://fcm.googleapis.com/x y', 'https://fcm.googleapis.com/café',
          'https://[::1]/x', 'https://.push.apple.com/x', 'https://-a.push.apple.com/x', 'HTTPS://' + 'a' * 64 + '.push.apple.com/x']
check('endpoint_ok refuses every address the request library would read differently, and an address literal or a malformed name',
      [u for u in TRICKS if W.endpoint_ok(u)], [])
check('  check_subscription stores one normal form: the lower-case host, no port, no fragment, the path and query kept',
      (W.check_subscription({'endpoint': 'https://FCM.googleapis.com:443/fcm/send/aB_c-1?x=1#frag', 'keys': {'p256dh': RFC['ua_public'], 'auth': RFC['auth']}})['endpoint'],
       W.normal('https://web.push.apple.com/QAbc'), refused(W.normal, 'https://example.org/x')),
      ('https://fcm.googleapis.com/fcm/send/aB_c-1?x=1', 'https://web.push.apple.com/QAbc', "the endpoint is not an https address at a browser's push service"))
# 2. The VAPID audience is the origin, whatever spelling the endpoint had.
aud = lambda endpoint: json.loads(W.unb64url(W.vapid_header(endpoint, PEM, 'mailto:push@example.org', now=0).split('t=')[1].split(',')[0].split('.')[1]))['aud']
check('the VAPID aud is https and the lower-case host, never a port; a mailto: contact is taken',
      (aud('https://FCM.googleapis.com:443/fcm/send/x'), aud(ENDPOINT)), ('https://fcm.googleapis.com', 'https://fcm.googleapis.com'))
check('  an endpoint that is not https with a host, a bare mailto:, and seconds that are not a whole number are refused',
      (refused(W.vapid_header, 'http://fcm.googleapis.com/x', PEM, 'https://example.org'), refused(W.vapid_header, 'https:///x', PEM, 'https://example.org'),
       refused(W.vapid_header, ENDPOINT, PEM, 'mailto:'), refused(W.vapid_header, ENDPOINT, PEM, 'https://'),
       refused(W.vapid_header, ENDPOINT, PEM, 'https://example.org', seconds=10.5), refused(W.vapid_header, ENDPOINT, PEM, 'https://example.org', seconds=True)),
      ('the endpoint must be an https address', 'the endpoint must be an https address', 'the VAPID subject must be a mailto: or https: URI',
       'the VAPID subject must be a mailto: or https: URI', 'a VAPID token lives at most a day', 'a VAPID token lives at most a day'))
# The signature is always 64 octets: r and s each keep their leading zero octets (about 1 signature in 128 has one).
short = 0
for n in range(3000):
    raw = W.unb64url(W.vapid_header(ENDPOINT, PEM, 'https://example.org', now=n).split('t=')[1].split(',')[0].split('.')[2])
    if len(raw) != 64:
        short = -1
        break
    short += raw[0] == 0 or raw[32] == 0
check('  the signature is 64 octets even when r or s begins with a zero octet (seen in this run)', (short > 0, short != -1), (True, True))
# 3. A message is never encrypted to a key no browser could read with.
compressed = W.b64url(bytes([2 + (W.unb64url(RFC['ua_public'])[64] & 1)]) + W.unb64url(RFC['ua_public'])[1:33])
check('encrypt refuses a compressed point, a short secret, an off-curve point and text that is not base64url',
      (refused(W.encrypt, b'x', compressed, RFC['auth']), refused(W.encrypt, b'x', RFC['ua_public'], 'AAAA'),
       refused(W.encrypt, b'x', off_curve, RFC['auth']), refused(W.encrypt, b'x', None, RFC['auth']), refused(W.encrypt, 'text', RFC['ua_public'], RFC['auth'])),
      ("'keys.p256dh' must be a 65-octet uncompressed P-256 point", "'keys.auth' must be 16 octets", "'keys.p256dh' is not a point on P-256",
       "'keys.p256dh' and 'keys.auth' must be base64url text", 'the payload must be bytes'))
# 4. send says what is wrong with a stored row in words, before any request, and never raises anything else.
svc = Service()
check('send refuses, in words and with no request: a subscription that is no dict, missing or short keys, ttl True or past four weeks, a bad key or subject',
      (refused(W.send, None, b'{}', PEM, 'https://example.org', opener=svc), refused(W.send, 'text', b'{}', PEM, 'https://example.org', opener=svc),
       refused(W.send, {'endpoint': ENDPOINT}, b'{}', PEM, 'https://example.org', opener=svc),
       refused(W.send, dict(sub, auth='AAAA'), b'{}', PEM, 'https://example.org', opener=svc),
       refused(W.send, sub, b'{}', PEM, 'https://example.org', ttl=True, opener=svc),
       refused(W.send, sub, b'{}', PEM, 'https://example.org', ttl=W.MAX_TTL + 1, opener=svc),
       refused(W.send, sub, b'{}', 'not a key', 'https://example.org', opener=svc), refused(W.send, sub, b'{}', PEM, 'example.org', opener=svc),
       refused(W.send, sub, None, PEM, 'https://example.org', opener=svc), svc.requests),
      ('the subscription must be {endpoint, p256dh, auth}', 'the subscription must be {endpoint, p256dh, auth}',
       "'keys.p256dh' and 'keys.auth' must be base64url text", "'keys.auth' must be 16 octets", 'ttl must be 1 second to 28 days',
       'ttl must be 1 second to 28 days', 'the VAPID key is not a PEM private key', 'the VAPID subject must be a mailto: or https: URI',
       'the payload must be bytes', []))
svc = Service(Answer(201), Answer(201))
W.send(sub, b'{}', PEM, 'https://example.org', ttl=W.MAX_TTL, opener=svc)
narrow = refused(W.send, sub, b'{}', PEM, 'https://example.org', opener=svc, hosts=(), suffixes=('.push.apple.com',))
check("no Topic header when there is no topic; four weeks is taken; send applies the host's own allow-list",
      ('Topic' in dict(svc.requests[0][0].header_items()), dict(svc.requests[0][0].header_items())['Ttl'], narrow, len(svc.requests)),
      (False, str(W.MAX_TTL), "the subscription's endpoint is not at a browser's push service", 1))

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
