"""Pub/Sub without the client library: one REST publish, one token, one envelope, one decoder.

Every producer of a host publishes through `publish()`, so no host needs `google-cloud-pubsub` and its gRPC stack
for a publish. It is one POST to the topic's `:publish`, the regional endpoint when the message has an ordering
key (messages that share one must go to one region) and the global one otherwise. The token comes from
`access_token()`, in this order:

1. On Google Cloud (Cloud Run, Cloud Functions, Cloud Run Jobs, Compute Engine), the metadata server: the
   service account's own token.
2. Anywhere `google-auth` is installed, Application Default Credentials (a CI runner's workload identity, or
   `gcloud auth application-default login`).
3. `gcloud auth print-access-token`: a developer's desktop.

The token is kept until a minute before it expires. A failure raises `PublishFailed` naming its kind, never the
token. A 5xx or a dropped connection is tried again once.

`envelope()` is the shape of an event a host publishes on its event topic (`type`, `v`, `id`, `at`, `source`,
`subject`, `actor`, `correlation_id`, `data`). `decode()` reads a delivery, whether a push subscription's JSON
body or an Eventarc CloudEvent, into (payload, attributes, message id). Standard library only; `google-auth` is
imported only for step 2, when present.
"""
import base64
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

from . import keepalive

METADATA_TOKEN_URL = 'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token'
SCOPE = 'https://www.googleapis.com/auth/pubsub'
_ON_GOOGLE_CLOUD = ('K_SERVICE', 'CLOUD_RUN_JOB', 'FUNCTION_TARGET', 'GCE_METADATA_HOST')
_token = {'value': '', 'expires': 0.0}


class PublishFailed(Exception):
    """The message was not published: no token, the network, or Pub/Sub refused it. The message names the kind
    of failure, never a token."""


def on_google_cloud() -> bool:
    """Whether this process runs where the metadata server answers (its platform's variables say so)."""
    return any(os.environ.get(k) for k in _ON_GOOGLE_CLOUD)


def _metadata_token(fetch):
    req = urllib.request.Request(METADATA_TOKEN_URL, headers={'Metadata-Flavor': 'Google'})
    with fetch(req, timeout=3) as resp:
        got = json.loads(resp.read().decode('utf-8'))
    return got['access_token'], float(got.get('expires_in') or 300)


def _adc_token():
    import google.auth   # noqa: PLC0415 - optional: only where google-auth is installed
    import google.auth.transport.requests   # noqa: PLC0415
    creds, _ = google.auth.default(scopes=[SCOPE])
    creds.refresh(google.auth.transport.requests.Request())
    left = 300.0
    if getattr(creds, 'expiry', None):
        left = (creds.expiry.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).total_seconds()  # utc-ok: google-auth's expiry is naive UTC; a duration
    return creds.token, left


def _gcloud_token(run):
    exe = shutil.which('gcloud') or 'gcloud'
    got = run([exe, 'auth', 'print-access-token'], capture_output=True, text=True, timeout=60,
              shell=os.name == 'nt')
    if got.returncode != 0 or not got.stdout.strip():
        raise PublishFailed('gcloud gave no access token')
    return got.stdout.strip(), 300.0


def access_token(fetch=None, run=None, now=time.monotonic) -> str:
    """An OAuth access token for Pub/Sub, kept until a minute before it expires: the metadata server on Google
    Cloud, else Application Default Credentials, else gcloud's (see the module's docstring). `fetch` (urlopen's
    shape) and `run` (subprocess.run's) are the test seams. PublishFailed when no source answers."""
    if _token['value'] and now() < _token['expires']:
        return _token['value']
    fetch, run = fetch or urllib.request.urlopen, run or subprocess.run
    sources = ([lambda: _metadata_token(fetch)] if on_google_cloud() else []) + [_adc_token, lambda: _gcloud_token(run)]
    kinds = []
    for source in sources:
        try:
            value, left = source()
        except Exception as e:   # noqa: BLE001 - try the next source; say only the kind
            kinds.append(type(e).__name__)
            continue
        if value:
            _token.update(value=value, expires=now() + max(left - 60, 30))
            return value
    raise PublishFailed(f"no access token ({', '.join(kinds) or 'no source'})")


def forget_token() -> None:
    """Drop the kept token (a test's reset, or after Pub/Sub answered 401)."""
    _token.update(value='', expires=0.0)


def _body(data) -> bytes:
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        return data.encode('utf-8')
    return json.dumps(data, sort_keys=True).encode('utf-8')


def publish(topic: str, data, *, project: str, attributes: dict = None, ordering_key: str = '',
            region: str = 'us-west1', fetch=None, token: str = None, timeout: float = 10) -> str:
    """Publish one message to `topic` in `project` and return the id Pub/Sub assigned. `data` is a dict (sent as
    sorted JSON), a str or bytes. Attribute values are sent as strings. With an `ordering_key` the message goes
    to `region`'s endpoint. A 5xx or a dropped connection is tried once more; a 401 drops the kept token first.
    PublishFailed on failure."""
    if not topic or not project:
        raise PublishFailed('no topic or project')
    fetch = fetch or keepalive.urlopen   # kept-alive (#562): a publish never re-sent by the pool once it went out
    message = {'data': base64.b64encode(_body(data)).decode('ascii'),
               'attributes': {str(k): str(v) for k, v in (attributes or {}).items()}}
    host = 'pubsub.googleapis.com'
    if ordering_key:
        message['orderingKey'] = ordering_key
        host = f'{region}-pubsub.googleapis.com'
    url = f'https://{host}/v1/projects/{project}/topics/{topic}:publish'
    payload = json.dumps({'messages': [message]}).encode('utf-8')
    last = 'unknown'
    for attempt in range(2):
        bearer = token or access_token(fetch=fetch)
        req = urllib.request.Request(url, data=payload, method='POST',
                                     headers={'Authorization': f'Bearer {bearer}', 'Content-Type': 'application/json'})
        try:
            with fetch(req, timeout=timeout) as resp:
                ids = json.loads(resp.read().decode('utf-8')).get('messageIds') or []
        except urllib.error.HTTPError as e:
            last = f'HTTP {e.code}'
            if e.code == 401 and not token:
                forget_token()
                continue
            if e.code >= 500:
                continue
            raise PublishFailed(last)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = type(e).__name__
            continue
        except ValueError as e:
            raise PublishFailed(f'not JSON ({type(e).__name__})')
        if not ids:
            raise PublishFailed('no message id')
        return ids[0]
    raise PublishFailed(last)


def envelope(type: str, data: dict, *, source: str, subject: dict = None, actor: dict = None,
             correlation_id: str = '', version: int = 1, at: datetime = None, id: str = None, **extra) -> dict:
    """An event as a host publishes it on its event topic: what happened (`type`, e.g. `match.recorded`, and its
    schema version `v`), a unique `id` for the consumers' dedup, when (`at`, ISO UTC), who said so (`source`),
    what about (`subject`), by whom (`actor`), what caused it (`correlation_id`) and the facts (`data`). `extra`
    keys (a host's tenant, say) go at the top level beside them."""
    stamp = (at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec='seconds')  # utc-ok: an instant
    return dict(extra, id=id or str(uuid.uuid4()), type=type, v=version, at=stamp.replace('+00:00', 'Z'),
                source=source, subject=subject or {}, actor=actor or {}, correlation_id=correlation_id, data=data)


def decode(delivery) -> tuple:
    """(payload, attributes, message_id) from one delivery: a push subscription's JSON body
    ({"message": {...}}), an Eventarc CloudEvent (its `.data` holds the same), or the message dict itself.
    The payload is the decoded JSON, or the text when it is not JSON. ValueError when there is no message."""
    body = getattr(delivery, 'data', delivery)
    if isinstance(body, (bytes, str)):
        body = json.loads(body)
    message = (body or {}).get('message', body) if isinstance(body, dict) else None
    if not isinstance(message, dict) or 'data' not in message:
        raise ValueError('no Pub/Sub message in the delivery')
    raw = message.get('data') or ''
    text = base64.b64decode(raw).decode('utf-8') if isinstance(raw, str) else bytes(raw).decode('utf-8')
    try:
        payload = json.loads(text)
    except ValueError:
        payload = text
    return payload, dict(message.get('attributes') or {}), message.get('messageId') or message.get('message_id') or ''
