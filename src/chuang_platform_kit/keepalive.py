"""HTTPS calls on kept-alive connections, pooled by host, for a service that calls the same few hosts again and again.

A new connection per call (urllib.request.urlopen) pays a TLS handshake each time; behind Cloudflare or Google's front
end that was 0.3 to 0.75 s of a call. This keeps one requests.Session per process, so urllib3's pool holds the
connections and reuses them, rather than a pool of our own (SMAD PickleBot #505, 10/29/26: a hand-rolled pool met
"Broken pipe" on a connection the far end had closed while it sat idle). What urllib3 does, and this module leans on:

  - before reusing a pooled connection it checks the socket and opens a new one when the far end closed it;
  - Retry counts a failure to connect apart from a failure after the request went out: a connect error is retried
    for any method (nothing was sent), a read error only for an idempotent method (GET, HEAD, PUT, DELETE, OPTIONS,
    TRACE), so a POST is never sent twice.

On top of it, the guard the hand-rolled pool learned (#536): a session left unused IDLE_SECONDS is closed and a new one
made, since Cloudflare and Google's front end close an idle connection on their own clock and a closed socket that
sent no FIN looks alive until it is written to. Every call has a timeout (TIMEOUT_SECONDS unless given).

    from chuang_platform_kit import keepalive
    resp = keepalive.request('GET', 'https://api.example/v1/x', headers={...})   # a requests.Response
    resp.raise_for_status()

requests is the kit's one dependency and is imported on the first call (lazy_import.py), never at cold start.
"""
import threading
import time

from .lazy_import import lazy

requests = lazy('requests')

IDLE_SECONDS = 60          # a session unused this long is replaced before its next call
TIMEOUT_SECONDS = 15       # every call's connect and read timeout unless the caller gives one
POOL_SIZE = 8              # kept connections per host
CONNECT_RETRIES = 2        # a connection that could not be made, tried again on a new one (any method)
READ_RETRIES = 1           # an idempotent call whose answer never came, sent once more on a new connection
BACKOFF_SECONDS = 0.2      # urllib3's backoff factor between those tries

_LOCK = threading.Lock()
_STATE = {'session': None, 'used': 0.0}


def _new_session():
    """A requests.Session whose https:// and http:// adapters pool POOL_SIZE connections a host and retry as the module
    docstring says (urllib3 Retry: connect for any method, read for idempotent ones, never on a status)."""
    from urllib3.util.retry import Retry
    retry = Retry(total=CONNECT_RETRIES + READ_RETRIES, connect=CONNECT_RETRIES, read=READ_RETRIES, status=0, other=0,
                  allowed_methods=Retry.DEFAULT_ALLOWED_METHODS, backoff_factor=BACKOFF_SECONDS,
                  raise_on_status=False, respect_retry_after_header=False)
    s = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=POOL_SIZE, pool_maxsize=POOL_SIZE, max_retries=retry)
    s.mount('https://', adapter)
    s.mount('http://', adapter)
    s.trust_env = False   # no proxy or .netrc from the environment: nothing these services run has one
    return s


def session(now=None):
    """The process's session: the one in use, or a new one when there is none or it sat unused over IDLE_SECONDS (the
    old one closed, its pooled connections with it). Marks it used now."""
    t = (now or time.monotonic)()
    with _LOCK:
        held = _STATE['session']
        if held is None or t - _STATE['used'] > IDLE_SECONDS:
            if held is not None:
                held.close()
            held = _STATE['session'] = _new_session()
        _STATE['used'] = t
        return held


def request(method: str, url: str, headers: dict = None, data=None, json=None, timeout: float = TIMEOUT_SECONDS,
            now=None):
    """`method url` on the process's kept-alive session (session()), with `timeout` for the connect and the read: the
    requests.Response, whatever its status (the caller decides what a 4xx means). A connection that could not be made
    or an idempotent call whose answer never came is retried by urllib3 as the module docstring says; what is left
    raises requests' own exception (requests.exceptions.ConnectionError, Timeout)."""
    resp = session(now).request(method, url, headers=headers, data=data, json=json, timeout=timeout)
    with _LOCK:
        _STATE['used'] = (now or time.monotonic)()   # a slow answer counts as use up to its end
    return resp


def reset() -> None:
    """Close the session and its connections (tests, and a process that wants a clean slate)."""
    with _LOCK:
        if _STATE['session'] is not None:
            _STATE['session'].close()
        _STATE['session'], _STATE['used'] = None, 0.0
