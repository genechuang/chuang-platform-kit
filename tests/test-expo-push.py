#!/usr/bin/env python3
"""chuang_platform_kit.expo_push: push notifications to phones through Expo, on the standard library alone.

Pinned (the positive case first, offline, Expo a stand-in transport):

- send: two recipients in one batch both taken, each ticket kept with its recipient, its token and its id
- a batch of BATCH and one more: two posts, the second of one message
- one message refused DeviceNotRegistered: its token in `gone`, its error scrubbed of the token; another code is an
  error, not gone; a batch Expo does not answer, or answers with too few tickets, is an error for each of its
  recipients while the next batch still goes; a programming error in `build` is raised, never swallowed
- the module's own `post` is the default and a replaced one is used, as a host's test replaces it
- message: the plain shape; urgent adds the time-sensitive level and high priority; a channel and a badge only
  when given
- token_ok and scrub: both token spellings, a wrong shape and an overlong one refused; every token in a text masked
- receipts: ok and error by id, an id with no receipt left out, batches of RECEIPT_BATCH, a refusal or a missing
  data a ValueError with the token scrubbed
- phone_gone and is_alarm: DeviceNotRegistered and a DeveloperError for one phone's token are gone and no alarm;
  InvalidCredentials and a DeveloperError of another reason are alarms; MessageTooBig is neither
- dedupe: the same push to the same recipients in another order is one key; another recipient another key; the two
  slots are this minute and the next; MemoryDedupe takes all or none

Run:  python -B tests/test-expo-push.py
"""
import os
import sys
import urllib.error
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import expo_push as E  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


T1, T2, T3 = 'ExponentPushToken[aaa]', 'ExponentPushToken[bbb]', 'ExpoPushToken[ccc]'


class Expo:
    """A stand-in Expo: each call takes the next answer (a dict, or an exception to raise); records every batch."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.batches = []

    def __call__(self, payload):
        self.batches.append(payload)
        a = self.answers.pop(0)
        if isinstance(a, BaseException):
            raise a
        return a(payload) if callable(a) else a


def build(chunk):
    return [E.message(t, 'Title', 'Body', {'url': '/games'}) for _, t in chunk]


def ok_tickets(payload):
    return {'data': [{'status': 'ok', 'id': f'id-{m["to"][-4:-1]}'} for m in payload]}


# --- send ---------------------------------------------------------------------------------------------------------

expo = Expo(ok_tickets)
got = E.send([('ann', T1), ('bob', T2)], build, post=expo)
check('two recipients in one batch: both taken, each ticket with its recipient, token and id',
      (got, len(expo.batches), [m['to'] for m in expo.batches[0]]),
      ({'sent': 2, 'errors': [], 'gone': [], 'tickets': [('ann', T1, 'id-aaa'), ('bob', T2, 'id-bbb')]}, 1, [T1, T2]))

expo = Expo(ok_tickets, ok_tickets)
rows = [(f'p{i}', f'ExponentPushToken[t{i:03d}]') for i in range(E.BATCH + 1)]
got = E.send(rows, build, post=expo)
check('BATCH and one more: two posts, the second of one message, every one taken',
      (got['sent'], [len(b) for b in expo.batches]), (E.BATCH + 1, [E.BATCH, 1]))

expo = Expo({'data': [{'status': 'error', 'message': f'"{T1}" is not a registered push notification recipient',
                       'details': {'error': 'DeviceNotRegistered'}},
                      {'status': 'error', 'message': 'too big', 'details': {'error': 'MessageTooBig'}}]})
got = E.send([('ann', T1), ('bob', T2)], build, post=expo)
check('DeviceNotRegistered is gone with its error scrubbed; another code is an error and not gone',
      got, {'sent': 0, 'gone': [T1], 'tickets': [],
            'errors': [{'who': 'ann', 'error': 'DeviceNotRegistered: "<push token>" is not a registered push notification recipient'},
                       {'who': 'bob', 'error': 'MessageTooBig: too big'}]})

expo = Expo(urllib.error.URLError(f'refused for {T1}'), {'data': [{'status': 'ok'}]}, {'data': []})
rows = [(f'p{i}', T1) for i in range(E.BATCH)] + [('last', T2), ]
got = E.send(rows, build, post=expo)
check("a batch Expo does not answer is an error for each recipient, scrubbed; the next batch still goes",
      (got['sent'], len(got['errors']), got['errors'][0], got['tickets']),
      (1, E.BATCH, {'who': 'p0', 'error': 'Expo did not take the batch (URLError: <urlopen error refused for <push token>>)'}, []))

expo = Expo({'data': [{'status': 'ok', 'id': 'x'}]})
got = E.send([('ann', T1), ('bob', T2)], build, post=expo)
check('an answer with fewer tickets than messages is an error for each recipient',
      (got['sent'], [e['who'] for e in got['errors']], got['errors'][0]['error'].startswith("Expo did not take the batch (ValueError: Expo's answer has no ticket per message")),
      (0, ['ann', 'bob'], True))


def broken(chunk):
    raise KeyError('a bug in the host')


try:
    E.send([('ann', T1)], broken, post=Expo(ok_tickets))
    raised = None
except KeyError as e:
    raised = str(e)
check("a programming error in build is raised, never read as Expo's refusal", raised, "'a bug in the host'")

saved = E.post
try:
    E.post = Expo(ok_tickets)
    got = E.send([('ann', T1)], build)
    check("the module's post is the default, and a replaced one is used", (got['sent'], len(E.post.batches)), (1, 1))
finally:
    E.post = saved

# --- message, token_ok, scrub -------------------------------------------------------------------------------------

check('message: the plain shape',
      E.message(T1, 'T', 'B', {'k': 1}), {'to': T1, 'title': 'T', 'body': 'B', 'data': {'k': 1}, 'sound': 'default'})
check('message: urgent, a channel and a badge',
      E.message(T1, 'T', 'B', {}, urgent=True, channel='games', badge=3),
      {'to': T1, 'title': 'T', 'body': 'B', 'data': {}, 'sound': 'default', 'interruptionLevel': 'time-sensitive',
       'channelId': 'games', 'priority': 'high', 'badge': 3})
check('message: a badge of 0 is sent; no channel unless given',
      E.message(T1, 'T', 'B', {}, badge=0), {'to': T1, 'title': 'T', 'body': 'B', 'data': {}, 'sound': 'default', 'badge': 0})

check('token_ok: both spellings, never a wrong shape, an overlong token or a non-string',
      [E.token_ok(T1), E.token_ok(T3), E.token_ok('PushToken[abc]'), E.token_ok('ExponentPushToken[a b]'),
       E.token_ok('ExponentPushToken[' + 'a' * E.TOKEN_MAX + ']'), E.token_ok(None)],
      [True, True, False, False, False, False])
check('scrub masks every token in a text, and reads None as empty',
      (E.scrub(f'{T1} and {T3} and ExponentPushToken[]'), E.scrub(None)),
      ('<push token> and <push token> and <push token>', ''))

# --- receipts -----------------------------------------------------------------------------------------------------

get = Expo({'data': {'a': {'status': 'ok'},
                     'b': {'status': 'error', 'message': f'Apple refused {T2}', 'details': {'error': 'DeviceNotRegistered'}}}})
check('receipts: ok and error by id, scrubbed; an id with no receipt yet left out',
      E.receipts(['a', 'b', 'c'], get=get), {'a': ('ok', None), 'b': ('error', 'DeviceNotRegistered: Apple refused <push token>')})

get = Expo({'data': {}}, {'data': {}})
E.receipts([str(i) for i in range(E.RECEIPT_BATCH + 1)], get=get)
check('receipts: batches of RECEIPT_BATCH', [len(b) for b in get.batches], [E.RECEIPT_BATCH, 1])


def why(fn):
    try:
        fn()
        return 'accepted'
    except ValueError as e:
        return str(e)


check('receipts: a refusal and an answer with no data are ValueErrors, scrubbed',
      (why(lambda: E.receipts(['a'], get=Expo(OSError(f'down for {T1}')))),
       why(lambda: E.receipts(['a'], get=Expo({'errors': [T1]})))),
      ("Expo's receipts did not answer (OSError: down for <push token>)",
       "Expo's receipts answer has no data: {'errors': ['<push token>']}"))

# --- phone_gone, is_alarm -----------------------------------------------------------------------------------------

cases = ['DeviceNotRegistered: gone', 'DeveloperError: APNs said reason: BadDeviceToken',
         'DeveloperError: reason: Unregistered', 'InvalidCredentials: bad key',
         'DeveloperError: reason: TopicDisallowed', 'MessageTooBig: big', '', None]
check('phone_gone and is_alarm: one phone gone, the sender wrong, or neither',
      [(E.phone_gone(c), E.is_alarm(c)) for c in cases],
      [(True, False), (True, False), (True, False), (False, True), (False, True), (False, False), (False, False),
       (False, False)])

# --- dedupe -------------------------------------------------------------------------------------------------------

k = E.dedupe_key('recruit', 'T', 'B', {'u': 1}, ['bob', 'ann', 'ann'])
check('dedupe_key: recipients in another order or repeated are one push; another recipient is another',
      (k == E.dedupe_key('recruit', 'T', 'B', {'u': 1}, ['ann', 'bob']), k == E.dedupe_key('recruit', 'T', 'B', {'u': 1}, ['ann']),
       len(k), E.dedupe_key('n', 't', 'b', None, None) == E.dedupe_key('n', 't', 'b', {}, [])),
      (True, False, 32, True))
now = datetime(2026, 11, 20, 12, 0, 30, tzinfo=timezone.utc)   # utc-ok: a fixed instant for the epoch slots
slot = int(now.timestamp() // 60)
check('dedupe_slots: this minute and the next; another slot length when asked',
      (E.dedupe_slots('k', now), E.dedupe_slots('k', now, seconds=3600)),
      ([f'k-{slot}', f'k-{slot + 1}'], [f'k-{int(now.timestamp() // 3600)}', f'k-{int(now.timestamp() // 3600) + 1}']))
m = E.MemoryDedupe()
check('MemoryDedupe takes all or none',
      (m.claim(['a', 'b']), m.claim(['b', 'c']), 'c' in m.ids, m.claim(['c'])), (True, False, False, True))

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
