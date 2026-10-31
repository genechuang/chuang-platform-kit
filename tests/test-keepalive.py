#!/usr/bin/env python3
"""chuang_platform_kit.keepalive: kept-alive HTTPS (and HTTP) calls on one pooled requests.Session.

Pinned against a real local HTTP/1.1 server (127.0.0.1, so no outside address), the positive case first: two calls
reuse one connection; a session unused over IDLE_SECONDS is closed and replaced; a GET whose connection the server
dropped without answering is sent again on a new one, and a POST never is; urllib3's Retry carries the module's counts
and retries connect errors for any method; every call has a timeout.

Run:  python -B tests/test-keepalive.py
"""
import http.server
import os
import socket
import sys
import threading

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import keepalive as K  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class Server:
    """A local HTTP/1.1 server that keeps connections open, counts the connections it accepted and the requests it
    answered, and drops (closes without answering) the next `drop` requests."""

    def __init__(self):
        self.conns, self.requests, self.drop = set(), [], 0
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *a):
                pass

            def _answer(self):
                outer.conns.add(self.client_address)
                n = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(n) if n else b''
                if outer.drop:
                    outer.drop -= 1
                    outer.requests.append((self.command, 'dropped'))
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                outer.requests.append((self.command, body.decode()))
                out = b'{"ok": true}'
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            do_GET = do_POST = _answer

        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.httpd.server_address[1]}'
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


srv, clock = Server(), Clock()
K.reset()

# 1. The positive case: two calls, one connection.
a = K.request('GET', f'{srv.url}/a', now=clock)
clock.t += 5
b = K.request('POST', f'{srv.url}/b', json={'x': 1}, now=clock)
check("the positive case: two calls answered on one kept connection",
      (a.status_code, a.json(), b.status_code, len(srv.conns), srv.requests), (200, {'ok': True}, 200, 1, [('GET', ''), ('POST', '{"x": 1}')]))

# 2. Idle over IDLE_SECONDS: the session is replaced, so the next call makes a new connection.
first = K.session(now=clock)
clock.t += K.IDLE_SECONDS + 1
K.request('GET', f'{srv.url}/c', now=clock)
check("  a session unused over IDLE_SECONDS is replaced and its connections closed, so the next call connects anew",
      (K.session(now=clock) is first, len(srv.conns)), (False, 2))
clock.t += K.IDLE_SECONDS - 1
same = K.session(now=clock)
clock.t += 1
check("  used within IDLE_SECONDS it is kept", K.session(now=clock) is same, True)

# 3. A GET whose answer never came is sent once more; a POST is not.
srv.requests.clear()
srv.drop = 1
g = K.request('GET', f'{srv.url}/d', now=clock)
check("  a GET whose connection was dropped unanswered is sent again and answered",
      (g.status_code, srv.requests), (200, [('GET', 'dropped'), ('GET', '')]))
srv.requests.clear()
srv.drop = 1
try:
    K.request('POST', f'{srv.url}/e', json={'y': 2}, now=clock)
    posted = 'answered'
except Exception as e:   # noqa: BLE001
    posted = type(e).__name__
check("  a POST whose answer never came is never sent twice: requests' ConnectionError",
      (posted, srv.requests), ('ConnectionError', [('POST', 'dropped')]))

# 4. The retry policy as urllib3 holds it.
r = K.session(now=clock).get_adapter('https://x').max_retries
check("  urllib3 Retry: connect for any method, read for idempotent ones only, never on a status",
      (r.connect, r.read, r.status, 'POST' in (r.allowed_methods or ()), 'GET' in r.allowed_methods),
      (K.CONNECT_RETRIES, K.READ_RETRIES, 0, False, True))
check("  one pool of POOL_SIZE connections a host; no proxy from the environment",
      (K.session(now=clock).get_adapter('https://x')._pool_maxsize, K.session(now=clock).trust_env), (K.POOL_SIZE, False))

# 5. Every call has a timeout.
seen = []
keep = K.session


class Fake:
    def request(self, method, url, **kw):
        seen.append(kw.get('timeout'))
        return 'resp'


K.session = lambda now=None: Fake()
try:
    K.request('GET', 'https://x/y')
    K.request('GET', 'https://x/y', timeout=3)
finally:
    K.session = keep
check("  every call carries a timeout: TIMEOUT_SECONDS unless given", seen, [K.TIMEOUT_SECONDS, 3])

# 6. urlopen (0.16.0, #562): urllib.request.urlopen's shape over the pool, for every module that takes an opener.
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402

sent = []


class Resp:
    def __init__(self, status, content=b'', reason='OK'):
        self.status_code, self.content, self.reason, self.headers = status, content, reason, {'X-H': '1'}


answers = iter([Resp(200, b'{"name": "m1"}'), Resp(404, b'{"error": "gone"}', 'Not Found'), ConnectionError('reset')])
keep_request = K.request


def fake_request(method, url, headers=None, data=None, json=None, timeout=None, now=None):
    sent.append((method, url, headers, data, timeout))
    nxt = next(answers)
    if isinstance(nxt, Exception):
        raise nxt
    return nxt


K.request = fake_request
try:
    req = urllib.request.Request('https://pubsub.example/v1/t:publish', data=b'{"messages": []}', method='POST',
                                 headers={'Content-Type': 'application/json', 'Authorization': 'Bearer t'})
    with K.urlopen(req, timeout=7) as r:
        ok = (r.status, r.read(), r.headers['X-H'], r.getcode())
    try:
        K.urlopen(urllib.request.Request('https://github.example/repos/x'), timeout=5)
        missing = None
    except urllib.error.HTTPError as e:
        missing = (e.code, e.read())
    try:
        K.urlopen('https://firestore.example/doc')
        unreachable = None
    except urllib.error.URLError as e:
        unreachable = type(e.reason).__name__
finally:
    K.request = keep_request
plain = K.urlopen(f'{srv.url}/plain', timeout=5)   # plain http: urllib's own urlopen, not the pool
check("urlopen: any method's headers and body on the pool, the answer read whole; a 4xx is HTTPError with its body; no answer is URLError; plain http is urllib's own",
      (ok, sent[0][0], sent[0][3], sent[0][4], sent[0][2].get('Authorization'), missing, unreachable,
       type(plain).__name__ != 'Answer', plain.status),
      ((200, b'{"name": "m1"}', '1', 200), 'POST', b'{"messages": []}', 7, 'Bearer t', (404, b'{"error": "gone"}'),
       'ConnectionError', True, 200))

K.reset()
srv.close()
print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
