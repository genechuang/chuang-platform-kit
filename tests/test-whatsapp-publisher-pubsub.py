#!/usr/bin/env python3
"""whatsapp_publisher + whatsapp_message: what every outgoing message puts on Pub/Sub.

CLAUDE.md: "Every outgoing WhatsApp message goes through whatsapp_publisher
with a deterministic correlation_id per target and part" -- the sender dedups
on it, so a Pub/Sub retry or a re-run cannot double-post. The publisher was
41% covered and the envelope 58% (2026-10-02 PT). With a fake pubsub_v1 (no
Pub/Sub client is ever built), this pins:
  - the topic (whatsapp-messages in smad-pickleball), the JSON envelope, and
    the attributes the sender filters on: message_type, source ('unknown' when
    none), dry_run as 'true'/'false', and the correlation_id passed through
    untouched -- a fresh UUID only when the caller gave none;
  - validate() refuses a message before any client is built: no recipient, a
    poll with fewer than 2 options or no question, an image with no URL, empty text;
  - the envelope round-trips through JSON (the sender's from_json).
The publish timestamp is pinned to 2026-10-02. (The joke a message may carry
is the host's: SMADPickleBot's tests/test-jokes-creds.py and friends pin it.)

Run:  python -B tests/test-whatsapp-publisher-pubsub.py
"""
import json
import logging
import os
import sys
import types
import uuid
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import whatsapp_publisher as WP, whatsapp_message as WM  # noqa: E402

WP.configure(project_id='smad-pickleball')   # the host names its project once; the topic path below carries it

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class Cap(logging.Handler):
    def __init__(self):
        super().__init__()
        self.recs = []

    def emit(self, r):
        self.recs.append(r)

    def levels(self):
        return [r.levelname for r in self.recs]


class FixedDT(datetime):
    @classmethod
    def utcnow(cls):  # utc-ok: the fake the publisher's instant stamp reads
        return datetime(2026, 10, 2, 16, 0, 0)


published, built = [], []


class FakePublisher:
    def __init__(self):
        built.append(self)

    def topic_path(self, project, topic):
        return f"projects/{project}/topics/{topic}"

    def publish(self, topic, data, **attrs):
        published.append((topic, json.loads(data.decode('utf-8')), attrs))
        return types.SimpleNamespace(result=lambda: f"msg-{len(published)}")


fake_pubsub = types.ModuleType('google.cloud.pubsub_v1')
fake_pubsub.PublisherClient = FakePublisher
import google.cloud as _gc  # noqa: E402
_saved_mod, _saved_attr = sys.modules.get('google.cloud.pubsub_v1'), getattr(_gc, 'pubsub_v1', None)
sys.modules['google.cloud.pubsub_v1'] = fake_pubsub
_gc.pubsub_v1 = fake_pubsub
_keep_dt = WP.datetime
WP.datetime = FixedDT
TOPIC = 'projects/smad-pickleball/topics/whatsapp-messages'
try:
    print("\n-- send_dm(): the envelope and attributes on the topic")
    mid = WP.send_dm('16265551111@c.us', 'Your balance is $12', source='payment-reminder',
                     include_signature=True, include_joke=True, correlation_id='pay-rem-2026-10-02-16265551111')
    topic, body, attrs = published[0]
    check("the positive case: published to whatsapp-messages, the Pub/Sub id returned", (mid, topic), ('msg-1', TOPIC))
    check("  the attributes the sender filters and dedups on, correlation_id untouched",
          attrs, {'message_type': 'text_dm', 'source': 'payment-reminder', 'dry_run': 'false',
                  'correlation_id': 'pay-rem-2026-10-02-16265551111'})
    check("  the JSON envelope carries the text, flags and a pinned UTC timestamp",
          (body['recipient_id'], body['content'], body['include_signature'], body['include_joke'],
           body['message_type'], body['timestamp'], body['correlation_id']),
          ('16265551111@c.us', 'Your balance is $12', True, True, 'text_dm', '2026-10-02T16:00:00',
           'pay-rem-2026-10-02-16265551111'))
    check("  the envelope round-trips into the sender's dataclass",
          WM.WhatsAppMessage.from_json(json.dumps(body)).message_type, WM.MessageType.TEXT_DM)

    WP.send_group_message('120363000000@g.us', 'Courts booked', dry_run=True, correlation_id='book-1')
    _, body, attrs = published[-1]
    check("a group message: type text_group, dry_run 'true', no source -> 'unknown'",
          (attrs['message_type'], attrs['dry_run'], attrs['source'], body['recipient_id']),
          ('text_group', 'true', 'unknown', '120363000000@g.us'))
    WP.send_group_message('120363000000@g.us', 'no key given')
    cid = published[-1][2]['correlation_id']
    check("no correlation_id -> a fresh UUID4 in attribute and body alike",
          (str(uuid.UUID(cid)) == cid, uuid.UUID(cid).version, published[-1][1]['correlation_id'] == cid), (True, 4, True))
    WP.send_poll('120363000000@g.us', 'Which nights?', ['Tue', 'Thu'], multiple_answers=False, source='poll')
    _, body, attrs = published[-1]
    check("a poll: question, options and single-answer flag in the envelope",
          (attrs['message_type'], body['content'], body['poll_options'], body['poll_multiple_answers']),
          ('poll', 'Which nights?', ['Tue', 'Thu'], False))
    WP.send_image('120363000000@g.us', 'https://example.com/h.png', caption='Heatmap', source='survey',
                  correlation_id='heatmap-2026-10-02')
    _, body, attrs = published[-1]
    check("an image: URL and caption, its correlation_id kept (a re-run must not re-send)",
          (attrs['message_type'], body['image_url'], body['image_caption'], body['content'], attrs['correlation_id']),
          ('image', 'https://example.com/h.png', 'Heatmap', 'Heatmap', 'heatmap-2026-10-02'))
    msg = WM.create_dm('1@c.us', 'raw', correlation_id='raw-1')
    msg.timestamp = '2026-01-01T00:00:00'
    WP.publish_raw(msg)
    check("publish_raw(): a caller's own timestamp is kept", published[-1][1]['timestamp'], '2026-01-01T00:00:00')
    check("one client per publish", len(built), len(published))

    print("\n-- validate(): a bad message never reaches Pub/Sub")
    n = len(published)

    def refused(fn):
        try:
            fn()
            return None
        except ValueError as e:
            return str(e)
    check("the positive case: no recipient", refused(lambda: WP.send_dm('', 'hi')), 'recipient_id is required')
    check("a poll with one option", refused(lambda: WP.send_poll('g@g.us', 'Q?', ['Tue'])),
          'poll_options requires at least 2 options')
    check("a poll with no question", refused(lambda: WP.send_poll('g@g.us', '', ['Tue', 'Thu'])),
          'content (poll question) is required for POLL type')
    check("an image with no URL", refused(lambda: WP.send_image('g@g.us', '')), 'image_url is required for IMAGE type')
    check("empty text", refused(lambda: WP.send_group_message('g@g.us', '')), 'content is required for text messages')
    check("  none of them published", len(published), n)
finally:
    WP.datetime = _keep_dt
    if _saved_mod is None:
        sys.modules.pop('google.cloud.pubsub_v1', None)
    else:
        sys.modules['google.cloud.pubsub_v1'] = _saved_mod
    if _saved_attr is None:
        if hasattr(_gc, 'pubsub_v1'):
            delattr(_gc, 'pubsub_v1')
    else:
        _gc.pubsub_v1 = _saved_attr

print("\n-- the envelope: to_dict / from_dict")
d = WM.create_poll('g@g.us', 'Q?', ['a', 'b'], source='s').to_dict()
check("the positive case: the enum serialises as its value", d['message_type'], 'poll')
check("from_dict() accepts the enum's value back", WM.WhatsAppMessage.from_dict(dict(d)).poll_options, ['a', 'b'])
d2 = dict(d, message_type=WM.MessageType.IMAGE)
check("from_dict() accepts an enum member as is", WM.WhatsAppMessage.from_dict(d2).message_type, WM.MessageType.IMAGE)
try:
    WM.WhatsAppMessage.from_dict(dict(d, message_type='sms'))
    bad = None
except ValueError:
    bad = 'ValueError'
check("an unknown message_type is refused", bad, 'ValueError')

print(f"\n{len(fails)} failure(s)")
if fails:
    sys.exit(1)
