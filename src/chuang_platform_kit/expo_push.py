"""Push notifications to phones through Expo's push service, on the standard library alone.

An Expo app registers an Expo push token (`ExponentPushToken[...]`) on every start; the host stores it and sends to
it here. Expo delivers through APNs and FCM and answers each message with a ticket; a minute later the ticket's
receipt holds Apple's or Google's own answer.

    if not expo_push.token_ok(token): ...                       # the shape an Expo app hands out
    got = expo_push.send(rows, lambda chunk: [expo_push.message(t, 'Title', 'Body', {'url': '/games'})
                                              for _, t in chunk])
    got['gone']                                                 # tokens Expo says are unregistered: delete them
    later = expo_push.receipts(ticket_ids)                      # {id: ('ok', None) | ('error', 'Code: message')}

What a host must know:

- **A token never leaves the host**: Expo's errors name the token, so every error text this module returns has been
  through `scrub`, and a host scrubs any text of its own that may hold one.
- **`send` never raises for Expo's answer or a network failure**: a batch Expo refuses, or one that does not answer,
  is an error for each of its recipients, and the other batches still go. `receipts` raises ValueError (scrubbed),
  since a receipts read is retried whole later.
- **Which errors are about one phone and which about the sender**: `phone_gone` (the token can never be delivered
  to: delete it) and `is_alarm` (the sender's credentials are wrong for every phone: page someone). Everything else
  is one phone's passing trouble.
- **The same push twice** is the host's to stop: `dedupe_key` names a push by what its recipients would see, and
  `dedupe_slots` gives the two minute-slots a create-only claim takes, so the same push again within one to two
  minutes finds a slot taken. `MemoryDedupe` is that claim in one process (a laptop, the tests).
- `post` and `get_receipts` are the transports, module attributes a test replaces, or pass your own to `send` and
  `receipts`.
"""
import hashlib
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

PUSH_URL = 'https://exp.host/--/api/v2/push/send'
RECEIPTS_URL = 'https://exp.host/--/api/v2/push/getReceipts'
BATCH = 100            # Expo takes at most 100 messages a request
RECEIPT_BATCH = 1000   # Expo reads at most 1000 receipts a request
TIMEOUT_SECONDS = 10
TOKEN_RE = re.compile(r'^Expo(nent)?PushToken\[[A-Za-z0-9_-]+\]$')
ANY_TOKEN = re.compile(r'Expo(nent)?PushToken\[[^\]]*\]')
TOKEN_MAX = 200
DEDUPE_SLOT_SECONDS = 60

# Answers that mean the sender is wrong for every phone, not one phone: an alarm, where one phone's trouble
# (DeviceNotRegistered, MessageTooBig, MessageRateExceeded) is not.
ALARM_ERRORS = ('InvalidCredentials', 'MismatchSenderId', 'DeveloperError', 'InvalidProviderToken')
# Apple's reasons, inside Expo's DeveloperError, that are about one token and never the credentials: a token the
# production app cannot be sent to (BadDeviceToken: a development build's or the Simulator's sandbox token;
# DeviceTokenNotForTopic: another app's), or one Apple has dropped (Unregistered). Such a phone is gone.
ONE_PHONE_REASONS = ('BadDeviceToken', 'DeviceTokenNotForTopic', 'Unregistered')

_FAILURES = (urllib.error.URLError, TimeoutError, OSError, ValueError)


def token_ok(token) -> bool:
    """True when `token` has the shape of an Expo push token (`ExponentPushToken[...]` or `ExpoPushToken[...]`), at
    most TOKEN_MAX characters."""
    return isinstance(token, str) and len(token) <= TOKEN_MAX and bool(TOKEN_RE.match(token))


def scrub(text) -> str:
    """`text` with every Expo push token in it masked as `<push token>`."""
    return ANY_TOKEN.sub('<push token>', str(text or ''))


def message(token: str, title: str, body: str, data: dict, *, urgent: bool = False, channel: str = None,
            badge: int = None, sound: str = 'default') -> dict:
    """One Expo message. `urgent` asks iOS for the time-sensitive level (shown through a Focus once the app has that
    entitlement; ignored before) and FCM for high priority; `channel` is the Android notification channel, sent only
    when given; `badge` the app icon's number on iOS, sent only when given."""
    out = {'to': token, 'title': title, 'body': body, 'data': data, 'sound': sound}
    if urgent:
        out['interruptionLevel'] = 'time-sensitive'
    if channel:
        out['channelId'] = channel
    if urgent:
        out['priority'] = 'high'
    if badge is not None:
        out['badge'] = badge
    return out


def _request(url: str, payload) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), method='POST',
                                 headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as r:
        return json.loads(r.read().decode('utf-8') or '{}')


def post(messages: list) -> dict:
    """POST one batch of messages to Expo; its JSON answer."""
    return _request(PUSH_URL, messages)


def get_receipts(ids: list) -> dict:
    """POST one batch of ticket ids to Expo's receipts; its JSON answer."""
    return _request(RECEIPTS_URL, {'ids': ids})


def send(rows, build, post=None) -> dict:
    """Send to every (who, token) in `rows`, in batches of BATCH; `build(chunk)` makes the chunk's messages, one per
    row in order. Returns {sent, errors: [{who, error}], gone: [token], tickets: [(who, token, id)]}: `sent` the
    messages Expo took (`tickets`, each with its id, for receipts() a minute later), `gone` the tokens Expo says are
    no longer registered (DeviceNotRegistered: the app deleted or its permission off), for the caller to delete."""
    post = post or globals()['post']
    rows = list(rows)
    out = {'sent': 0, 'errors': [], 'gone': [], 'tickets': []}
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        try:
            answer = post(build(chunk))
            tickets = answer.get('data') if isinstance(answer, dict) else None
            if not isinstance(tickets, list) or len(tickets) != len(chunk):
                raise ValueError(f"Expo's answer has no ticket per message: {str(answer)[:200]}")
        except _FAILURES as e:
            why = scrub(f"Expo did not take the batch ({type(e).__name__}: {e})")
            out['errors'].extend({'who': who, 'error': why} for who, _ in chunk)
            continue
        for (who, token), ticket in zip(chunk, tickets):
            ticket = ticket if isinstance(ticket, dict) else {}
            if ticket.get('status') == 'ok':
                out['sent'] += 1
                if ticket.get('id'):
                    out['tickets'].append((who, token, str(ticket['id'])))
                continue
            code = (ticket.get('details') or {}).get('error') or 'error'
            if code == 'DeviceNotRegistered':
                out['gone'].append(token)
            out['errors'].append({'who': who, 'error': scrub(f"{code}: {ticket.get('message') or ''}".strip(': '))})
    return out


def receipts(ids, get=None) -> dict:
    """The phone platform's answer for each ticket id, through Expo's receipts: {id: (status, error)}, status 'ok' or
    'error' and error "<code>: <message>" (scrubbed, at most 200 characters) or None. An id Expo has no receipt for
    yet is left out, to be asked again later. A batch Expo refuses raises ValueError with the reason, scrubbed."""
    get = get or globals()['get_receipts']
    ids = list(ids)
    out = {}
    for i in range(0, len(ids), RECEIPT_BATCH):
        chunk = ids[i:i + RECEIPT_BATCH]
        try:
            answer = get(chunk)
        except _FAILURES as e:
            raise ValueError(scrub(f"Expo's receipts did not answer ({type(e).__name__}: {e})"))
        found = answer.get('data') if isinstance(answer, dict) else None
        if not isinstance(found, dict):
            raise ValueError(scrub(f"Expo's receipts answer has no data: {str(answer)[:200]}"))
        for tid in chunk:
            r = found.get(tid)
            if not isinstance(r, dict):
                continue
            if r.get('status') == 'ok':
                out[tid] = ('ok', None)
            else:
                code = (r.get('details') or {}).get('error') or 'error'
                out[tid] = ('error', scrub(f"{code}: {r.get('message') or ''}".strip(': '))[:200])
    return out


def phone_gone(error: str) -> bool:
    """True when `error` ("<code>: <message>", as receipts() keeps it) says this one token can never be delivered to:
    Expo's DeviceNotRegistered, or a DeveloperError whose APNs reason is in ONE_PHONE_REASONS."""
    error = error or ''
    code = error.split(':')[0]
    if code == 'DeviceNotRegistered':
        return True
    return code == 'DeveloperError' and any(f'reason: {r}' in error for r in ONE_PHONE_REASONS)


def is_alarm(error: str) -> bool:
    """True when `error` means the sender is wrong for every phone (ALARM_ERRORS), not one phone's token."""
    return (error or '').split(':')[0] in ALARM_ERRORS and not phone_gone(error)


def dedupe_key(kind, title, body, data, recipients) -> str:
    """A push's identity: its kind, title, body, data and its recipients (sorted, once each), hashed to 32 hex
    characters. The same message to other recipients is another push."""
    raw = json.dumps([kind, title, body, data or {}, sorted(set(recipients or []))], sort_keys=True)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]


def dedupe_slots(key: str, now: datetime = None, seconds: int = DEDUPE_SLOT_SECONDS) -> list:
    """The two claim ids a push takes: this slot's and the next (`seconds` long each)."""
    slot = int((now or datetime.now(timezone.utc)).timestamp() // seconds)   # utc-ok: an instant's epoch slot, no day
    return [f'{key}-{slot}', f'{key}-{slot + 1}']


class MemoryDedupe:
    """The claims in this process. claim(ids) is all or none, as a create-only commit is."""

    def __init__(self):
        self.ids = set()

    def claim(self, ids, expires_at=None) -> bool:
        """Take every id in `ids`, or none when any is taken already; True when taken."""
        if any(i in self.ids for i in ids):
            return False
        self.ids.update(ids)
        return True
