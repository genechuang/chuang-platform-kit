"""Web Push to a browser, on the standard alone: RFC 8291 message encryption (aes128gcm), RFC 8292 VAPID and
RFC 8030's request, with the `cryptography` library and nothing else (`pip install chuang-platform-kit[webpush]`).
No vendor SDK on the page or the server: the browser hands the page a subscription (an endpoint URL at its own push
service and two keys), the host stores it, and `send` posts one encrypted message to that endpoint.

    sub = web_push.check_subscription(body)            # ValueError says what is wrong; {'endpoint', 'p256dh', 'auth'}
    got = web_push.send(sub, b'{"title": "..."}', private_pem, 'https://example.org')
    if got['gone']: ...                                # 404 or 410: the browser dropped it, delete the row

What a host must know:

- **The endpoint is the member's to supply and the server's to post to**, so it is checked against the push services
  browsers use (`endpoint_ok`, `ALLOWED_HOSTS` and `ALLOWED_SUFFIXES`): plain printable ASCII, https, a host name in
  the strict letters-digits-hyphens grammar that is on the list, no credentials, the default port. The check reads the
  address exactly as the request library will (no tab, newline, backslash or percent sign can make the two differ),
  `check_subscription` stores it in one normal form, the request follows no redirect and uses no proxy from the
  environment, and the answer's body is never read. An endpoint is a capability URL (RFC 8030 section 8.3): log
  `host_of(endpoint)`, never the endpoint.
- **One key pair signs for the server** (VAPID): `new_private_key()` makes it once, the host keeps the PEM as a secret
  and serves `public_key(pem)` to the page, which subscribes with it. Changing the key ends every subscription.
- **A message is at most `MAX_PAYLOAD` octets** (3993: a push service need not take more than 4096 of body).
- `send` never raises for an answer or a network failure: it returns the status. A 2xx is `ok`; 404 and 410 are
  `gone`; 429 and 5xx carry `retry_after`; 400, 401 and 403 are the server's own (a VAPID problem), never a reason to
  delete a subscription; 0 is no answer, with the failure's kind in `error`.
- `TIMEOUT` bounds the connection and each read, not the whole request: a host that sends to many subscriptions
  bounds the fan-out itself.
"""
import base64
import http.client
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 10
MAX_PAYLOAD = 3993          # RFC 8291 section 4: 4096 less the header (86), the delimiter (1) and the tag (16)
RECORD_SIZE = 4096
VAPID_SECONDS = 12 * 3600   # RFC 8292: exp at most 24 hours ahead; 12 leaves room for a slow clock
MAX_TTL = 28 * 86400        # four weeks, the shortest ceiling a push service documents
URGENCIES = ('very-low', 'low', 'normal', 'high')

# The push services browsers hand out endpoints at (11/15/26): Chrome and the Chromium browsers (FCM), Firefox
# (autopush), Safari ("allow access for https://*.push.apple.com", Apple's own words) and Edge on Windows (WNS,
# "*.notify.windows.com"). Exact hosts where the vendor has one, a suffix only where the vendor documents a range;
# never a suffix of googleapis.com, which is every Google API.
ALLOWED_HOSTS = ('fcm.googleapis.com', 'updates.push.services.mozilla.com')
ALLOWED_SUFFIXES = ('.push.apple.com', '.notify.windows.com')

# Printable ASCII with no space and no backslash: what urlsplit and the request library both read the same way
# (urlsplit drops tab, CR and LF before it parses; the request library does not).
_PLAIN = re.compile(r'[\x21-\x5b\x5d-\x7e]+')
# A DNS name in the letters-digits-hyphens grammar, at least two labels, lower case: no percent sign, underscore,
# empty label or address literal, so nothing the request library would decode into another host.
_HOST = re.compile(r'(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+')


def _crypto():
    """The `cryptography` names this module uses, imported on first use so the kit imports without the extra."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    except ImportError as e:   # pragma: no cover - the extra is installed wherever this runs
        raise ImportError('web_push needs the cryptography library: pip install chuang-platform-kit[webpush]') from e
    return ec, hashes, serialization, HKDF, AESGCM, decode_dss_signature


def b64url(data: bytes) -> str:
    """Bytes as unpadded URL-safe base64, the form every Web Push value travels in."""
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def unb64url(text: str) -> bytes:
    """Unpadded or padded URL-safe base64 (a browser may send either) as bytes; ValueError when it is neither."""
    if not isinstance(text, str) or not text or any(c.isspace() for c in text):
        raise ValueError('not base64url text')
    try:
        return base64.b64decode(text.replace('-', '+').replace('_', '/') + '=' * (-len(text) % 4), validate=True)
    except (ValueError, TypeError) as e:
        raise ValueError('not base64url text') from e


def host_of(endpoint: str) -> str:
    """The endpoint's host, lower case, '' when it has none: the only part of an endpoint that is safe to log."""
    try:
        return (urllib.parse.urlsplit(endpoint).hostname or '').lower()
    except (ValueError, AttributeError, TypeError):
        return ''


def _parts(endpoint, hosts, suffixes):
    """(host, path, query) of an endpoint this server may post to, None for any other: the one parse `endpoint_ok`,
    `normal` and `vapid_header` share."""
    if not isinstance(endpoint, str) or len(endpoint) > 2000 or not _PLAIN.fullmatch(endpoint):
        return None
    try:
        parts = urllib.parse.urlsplit(endpoint)
    except ValueError:
        return None
    host = (parts.hostname or '').lower()
    # The written authority is the host itself, or the host and the default port: no user, no password, no other
    # port, no ':' with nothing after it, no ':0443'.
    if parts.scheme != 'https' or not _HOST.fullmatch(host) or parts.netloc.lower() not in (host, host + ':443'):
        return None
    if host not in hosts and not any(host.endswith(s) and len(host) > len(s) for s in suffixes):
        return None
    return host, parts.path, parts.query


def endpoint_ok(endpoint, hosts=ALLOWED_HOSTS, suffixes=ALLOWED_SUFFIXES) -> bool:
    """Whether `endpoint` is one this server may post to: at most 2000 printable ASCII characters with no space or
    backslash, https, no user or password, the default port, and a host name in the strict DNS grammar that is one
    of `hosts` exactly or ends in one of `suffixes`. The guard against a subscription that names an address inside
    the server's own network; it reads the address as the request library will."""
    return _parts(endpoint, hosts, suffixes) is not None


def normal(endpoint, hosts=ALLOWED_HOSTS, suffixes=ALLOWED_SUFFIXES) -> str:
    """`endpoint` in its one stored form, `https://<lower-case host><path>?<query>` (no port, no fragment), so two
    spellings of one endpoint are one subscription; ValueError for an endpoint `endpoint_ok` refuses."""
    got = _parts(endpoint, hosts, suffixes)
    if got is None:
        raise ValueError("the endpoint is not an https address at a browser's push service")
    host, path, query = got
    return f'https://{host}{path}' + (f'?{query}' if query else '')


def _keys(p256dh, auth) -> tuple:
    """(the receiver's 65-octet point, the 16-octet secret) of a subscription's two keys, each checked; ValueError in
    plain words for text that is not base64url, a point of the wrong shape or off the curve, or a short secret."""
    try:
        point, secret = unb64url(p256dh), unb64url(auth)
    except ValueError as e:
        raise ValueError("'keys.p256dh' and 'keys.auth' must be base64url text") from e
    if len(secret) != 16:
        raise ValueError("'keys.auth' must be 16 octets")
    if len(point) != 65 or point[0] != 4:
        raise ValueError("'keys.p256dh' must be a 65-octet uncompressed P-256 point")
    ec = _crypto()[0]
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
    except ValueError as e:
        raise ValueError("'keys.p256dh' is not a point on P-256") from e
    return point, secret


def check_subscription(body, hosts=ALLOWED_HOSTS, suffixes=ALLOWED_SUFFIXES) -> dict:
    """A browser's `PushSubscription.toJSON()` ({endpoint, keys: {p256dh, auth}}) as {'endpoint', 'p256dh', 'auth'}:
    the endpoint in its `normal` form, the keys re-encoded unpadded. ValueError, in words a person can read, for an
    endpoint `endpoint_ok` refuses, a `p256dh` that is not a 65-octet uncompressed point on P-256, or an `auth` that
    is not 16 octets."""
    if not isinstance(body, dict) or not isinstance(body.get('keys'), dict):
        raise ValueError('send {"endpoint": "https://...", "keys": {"p256dh": "...", "auth": "..."}}')
    try:
        endpoint = normal(body.get('endpoint'), hosts, suffixes)
    except ValueError as e:
        raise ValueError("'endpoint' must be an https address at a browser's push service") from e
    point, secret = _keys(body['keys'].get('p256dh'), body['keys'].get('auth'))
    return {'endpoint': endpoint, 'p256dh': b64url(point), 'auth': b64url(secret)}


def new_private_key() -> str:
    """A new P-256 private key as PKCS8 PEM text: the server's VAPID key, made once and kept as a secret."""
    ec, _, serialization = _crypto()[:3]
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode('ascii')


def _load(private_pem: str):
    """The P-256 private key in `private_pem`; ValueError when it is not one (never echoing the text)."""
    ec, _, serialization = _crypto()[:3]
    try:
        key = serialization.load_pem_private_key(private_pem.strip().encode('ascii'), password=None)
    except (ValueError, TypeError, AttributeError, UnicodeError):
        raise ValueError('the VAPID key is not a PEM private key') from None   # never the library's echo of the text
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError('the VAPID key is not a P-256 key')
    return key


def _point(key) -> bytes:
    """A private key's public half as the 65-octet uncompressed point."""
    serialization = _crypto()[2]
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def public_key(private_pem: str) -> str:
    """The VAPID public key for `private_pem`, base64url of the uncompressed point: what the page passes to
    `pushManager.subscribe({applicationServerKey})` and what the `k` of the Authorization header carries."""
    return b64url(_point(_load(private_pem)))


def encrypt(payload: bytes, p256dh: str, auth: str, salt: bytes = None, sender_key: str = None) -> bytes:
    """`payload` encrypted for one subscription as RFC 8291 says (one aes128gcm record, RFC 8188): the body to post.
    A new sender key pair and a random salt per message; `salt` (16 octets) and `sender_key` (a base64url P-256
    scalar) are the seams a known-answer test fixes, never for a host to pass. ValueError for a payload that is not
    bytes or is past `MAX_PAYLOAD`, and for keys `check_subscription` would refuse (a message encrypted to a
    malformed key is one no browser can read)."""
    if not isinstance(payload, (bytes, bytearray)):
        raise ValueError('the payload must be bytes')
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f'the payload is {len(payload)} octets; a Web Push message carries at most {MAX_PAYLOAD}')
    receiver, secret = _keys(p256dh, auth)
    ec, hashes, _, HKDF, AESGCM, _ = _crypto()
    theirs = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), receiver)
    ours = (ec.derive_private_key(int.from_bytes(unb64url(sender_key), 'big'), ec.SECP256R1()) if sender_key
            else ec.generate_private_key(ec.SECP256R1()))
    sender = _point(ours)
    salt = os.urandom(16) if salt is None else salt
    if len(salt) != 16:
        raise ValueError('the salt must be 16 octets')
    shared = ours.exchange(ec.ECDH(), theirs)
    ikm = HKDF(hashes.SHA256(), 32, secret, b'WebPush: info\x00' + receiver + sender).derive(shared)
    key = HKDF(hashes.SHA256(), 16, salt, b'Content-Encoding: aes128gcm\x00').derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b'Content-Encoding: nonce\x00').derive(ikm)
    record = AESGCM(key).encrypt(nonce, bytes(payload) + b'\x02', None)   # 0x02: the last record's delimiter
    return salt + RECORD_SIZE.to_bytes(4, 'big') + bytes([len(sender)]) + sender + record


def _subject_ok(subject) -> bool:
    """A VAPID contact a push service takes: `mailto:` and an address, or `https://` and a host, with no space."""
    if not isinstance(subject, str) or not _PLAIN.fullmatch(subject):
        return False
    if subject.startswith('mailto:'):
        return bool(re.fullmatch(r'[^@<>]+@[^@<>]+\.[^@<>]+', subject[7:]))
    return subject.startswith('https://') and bool(_HOST.fullmatch(host_of(subject)))


def vapid_header(endpoint: str, private_pem: str, subject: str, now=None, seconds: int = VAPID_SECONDS) -> str:
    """The Authorization header RFC 8292 asks for, `vapid t=<JWT>, k=<public key>`: an ES256 JWT whose `aud` is the
    endpoint's origin (`https://` and the lower-case host, never a port), `exp` `seconds` from `now` (at most a day)
    and `sub` the server's contact, a `mailto:` address or an `https:` URL (Apple refuses anything else). `now` is
    the clock seam, seconds since the epoch. ValueError for an endpoint that is not https with a host, a subject of
    another shape, `seconds` out of range or a key that is not a P-256 PEM."""
    if not _subject_ok(subject):
        raise ValueError('the VAPID subject must be a mailto: or https: URI')
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 0 < seconds <= 24 * 3600:
        raise ValueError('a VAPID token lives at most a day')
    host = host_of(endpoint)
    if not isinstance(endpoint, str) or not endpoint.lower().startswith('https://') or not _HOST.fullmatch(host):
        raise ValueError('the endpoint must be an https address')
    ec, hashes, _, _, _, decode_dss_signature = _crypto()
    key = _load(private_pem)
    claims = {'aud': f'https://{host}', 'exp': int(time.time() if now is None else now) + seconds, 'sub': subject}
    signing = (b64url(json.dumps({'typ': 'JWT', 'alg': 'ES256'}, separators=(',', ':')).encode()) + '.'
               + b64url(json.dumps(claims, separators=(',', ':')).encode()))
    r, s = decode_dss_signature(key.sign(signing.encode('ascii'), ec.ECDSA(hashes.SHA256())))
    token = signing + '.' + b64url(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))   # always 64 octets, zeros kept
    return f'vapid t={token}, k={b64url(_point(key))}'


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A push service answers the endpoint itself: a redirect is refused, never followed to another address."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open(req, timeout):
    """urllib with no redirect followed and no proxy taken from the environment."""
    return urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({})).open(req, timeout=timeout)


def send(subscription: dict, payload: bytes, private_pem: str, subject: str, ttl: int = 86400,
         urgency: str = 'normal', topic: str = '', opener=None, timeout: int = TIMEOUT, now=None,
         hosts=ALLOWED_HOSTS, suffixes=ALLOWED_SUFFIXES) -> dict:
    """One message to one subscription ({'endpoint', 'p256dh', 'auth'}, as `check_subscription` returns): `payload`
    encrypted, signed with the server's VAPID key and posted to the endpoint. `ttl` seconds the push service may hold
    it for a browser that is not running (1 second to `MAX_TTL`, four weeks), `urgency` one of `URGENCIES`, `topic`
    (at most 32 letters and digits) lets a newer message replace an undelivered one; `hosts` and `suffixes` are the
    allow-list, the same a host gave `check_subscription`.

    Returns {'status', 'ok', 'gone', 'retry_after', 'host', 'error'} and never raises for an answer or a network
    failure: `ok` is any 2xx (201 is the standard's); `gone` is True on 404 and 410, the only answers that mean the
    subscription is over; `retry_after` is the push service's own header, '' when it sent none; status 0 is no
    answer at all (a timeout, a refused or broken connection, a redirect), `error` then the failure's kind, never
    the address. ValueError before any request for a subscription that is not a dict, an endpoint the allow-list
    refuses, keys of the wrong shape, a bad `ttl`, `urgency` or `topic`, a payload that is not bytes or is past
    `MAX_PAYLOAD`, a subject that is not a mailto: or https: URI, or a key that is not a P-256 PEM.

    `opener(request, timeout)` is the test seam: the default follows no redirect and takes no proxy from the
    environment, and anything passed in its place must do the same."""
    if not isinstance(subscription, dict):
        raise ValueError('the subscription must be {endpoint, p256dh, auth}')
    endpoint = subscription.get('endpoint')
    if not endpoint_ok(endpoint, hosts, suffixes):
        raise ValueError("the subscription's endpoint is not at a browser's push service")
    if isinstance(ttl, bool) or not isinstance(ttl, int) or not 1 <= ttl <= MAX_TTL:
        raise ValueError('ttl must be 1 second to 28 days')
    if urgency not in URGENCIES:
        raise ValueError(f"urgency must be one of {', '.join(URGENCIES)}")
    if topic and not (isinstance(topic, str) and len(topic) <= 32 and topic.isascii() and topic.isalnum()):
        raise ValueError('topic must be at most 32 letters and digits')
    authorization = vapid_header(endpoint, private_pem, subject, now=now)   # a bad key or subject costs no encryption
    body = encrypt(payload, subscription.get('p256dh'), subscription.get('auth'))
    headers = {'Authorization': authorization, 'TTL': str(ttl), 'Urgency': urgency, 'Content-Encoding': 'aes128gcm',
               'Content-Type': 'application/octet-stream', 'User-Agent': 'chuang-platform-kit'}
    if topic:
        headers['Topic'] = topic
    status, retry, error = 0, '', ''
    try:
        req = urllib.request.Request(endpoint, data=body, method='POST', headers=headers)
        with (opener or _open)(req, timeout) as r:
            status = getattr(r, 'status', None) or r.getcode()
            retry = (getattr(r, 'headers', None) or {}).get('Retry-After') or ''
    except urllib.error.HTTPError as e:
        status, retry = e.code, (e.headers.get('Retry-After') if e.headers else '') or ''
        if 300 <= status < 400:
            status, error = 0, 'redirect'   # a redirect is no answer from the push service
        try:
            e.close()
        except Exception:  # noqa: BLE001 - closing a refused answer never matters
            pass
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as e:
        status, error = 0, type(e).__name__
    return {'status': status, 'ok': 200 <= status < 300, 'gone': status in (404, 410), 'retry_after': str(retry),
            'host': host_of(endpoint), 'error': error}
