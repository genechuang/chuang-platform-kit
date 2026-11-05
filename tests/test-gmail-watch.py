#!/usr/bin/env python3
"""chuang_platform_kit.gmail_watch: Gmail's push as a webhook, and its labels and filters as code (SMAD PickleBot #969).

Pinned (the positive case first): watch() asks Gmail for a label's changes on a topic and answers its cursor and
expiry; changes() reads the messages added since a cursor (every page) and, on Gmail's 404 for a stale cursor, catches
up by search with the profile's history id as the new cursor (no search: the 404 is raised); renew() restarts a watch
only when under 36 hours are left, keeping the stored cursor; push() decodes a Pub/Sub delivery and is_gmail_push()
tells Gmail's from a host's own; plan()/apply() make a missing label and filter, keep an exact one, replace a changed
one, and leave filters of undeclared labels alone; a dry run changes nothing. Offline: Gmail is a fake opener.

Run:  python -B tests/test-gmail-watch.py
"""
import base64
import io
import json
import os
import sys
import urllib.error
import urllib.parse

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import gmail_watch as W  # noqa: E402

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


class Box:
    """A gmail.Mailbox's REST shape: a token, an opener, a timeout."""

    def __init__(self, routes):
        self.routes, self.calls, self.timeout = routes, [], 5

    def access_token(self):
        return 'tok'

    def opener(self, req, timeout=None):
        url = urllib.parse.urlparse(req.full_url)
        path = url.path.split('/users/me/')[1]
        body = json.loads(req.data) if req.data else None
        self.calls.append((req.get_method(), path, dict(urllib.parse.parse_qsl(url.query)), body))
        got = self.routes(req.get_method(), path, dict(urllib.parse.parse_qsl(url.query)), body)
        if isinstance(got, int):
            raise urllib.error.HTTPError(req.full_url, got, 'no', {}, None)
        return Resp(json.dumps(got).encode())


def meta(mid, frm, subject):
    return {'id': mid, 'threadId': 't' + mid, 'snippet': subject, 'internalDate': '1793869000000',
            'payload': {'headers': [{'name': 'From', 'value': f'Play <{frm}>'}, {'name': 'Subject', 'value': subject}]}}


def test_watch_asks_for_a_labels_changes_on_the_topic():
    box = Box(lambda m, p, q, b: {'historyId': '48801697', 'expiration': '1794474000000'})
    got = W.watch(box, 'projects/p/topics/t', ['Label_21'])
    check('watch: cursor and expiry', got, {'history_id': '48801697', 'expiration': 1794474000000})
    check('  one POST to watch with the label, INCLUDE', box.calls[0],
          ('POST', 'watch', {}, {'topicName': 'projects/p/topics/t', 'labelIds': ['Label_21'], 'labelFilterBehavior': 'INCLUDE'}))


def test_changes_reads_every_page_since_the_cursor():
    pages = {'': {'history': [{'messagesAdded': [{'message': {'id': 'm1'}}]}], 'nextPageToken': 'p2', 'historyId': '50'},
             'p2': {'history': [{'messagesAdded': [{'message': {'id': 'm2'}}, {'message': {'id': 'm1'}}]}], 'historyId': '51'}}

    def routes(m, p, q, b):
        if p == 'history':
            return pages[q.get('pageToken', '')]
        return meta(p.split('/')[1], 'noreply-play-console@google.com', f'about {p}')
    box = Box(routes)
    msgs, cursor = W.changes(box, '40', 'Label_21')
    check('changes: both pages, each message once', ([m['id'] for m in msgs], cursor), (['m1', 'm2'], '51'))
    check('  the sender read from the header', msgs[0]['from'], 'noreply-play-console@google.com')
    check('  history asked from the cursor in the label', (box.calls[0][2]['startHistoryId'], box.calls[0][2]['labelId']), ('40', 'Label_21'))


def test_a_message_gone_before_the_read_is_skipped_and_the_cursor_still_advances():
    def routes(m, p, q, b):
        if p == 'history':
            return {'history': [{'messagesAdded': [{'message': {'id': 'ghost'}}, {'message': {'id': 'm1'}}]}], 'historyId': '60'}
        if p == 'messages/ghost':
            return 404
        return meta('m1', 'noreply-play-console@google.com', 'kept')
    msgs, cursor = W.changes(Box(routes), '40', 'Label_21')
    check('gone (404): skipped, the rest returned, cursor advanced', ([m['id'] for m in msgs], cursor), (['m1'], '60'))

    def down(m, p, q, b):
        if p == 'history':
            return {'history': [{'messagesAdded': [{'message': {'id': 'm1'}}]}], 'historyId': '60'}
        return 503
    try:
        W.changes(Box(down), '40', 'Label_21')
        check('  any other failure raised', 'no raise', 503)
    except W.WatchFailed as e:
        check('  any other failure raised', e.status, 503)


def test_a_stale_cursor_catches_up_by_search():
    def routes(m, p, q, b):
        if p == 'history':
            return 404
        if p == 'messages':
            return {'messages': [{'id': 'm9'}]}
        if p == 'profile':
            return {'historyId': '99'}
        return meta('m9', 'noreply-play-console@google.com', 'Your app is approved')
    msgs, cursor = W.changes(Box(routes), '1', 'Label_21', resync_query='label:x newer_than:8d')
    check('stale cursor: caught up by search, profile cursor', ([m['id'] for m in msgs], cursor), (['m9'], '99'))
    try:
        W.changes(Box(routes), '1', 'Label_21')
        check('  no search: the 404 raised', 'no raise', 404)
    except W.WatchFailed as e:
        check('  no search: the 404 raised', e.status, 404)


def test_renew_only_when_due_and_keeps_the_cursor():
    box = Box(lambda m, p, q, b: {'historyId': '70', 'expiration': '2000000000000'})
    now = 1_000_000_000_000
    fresh = {'history_id': '50', 'expiration': now + 3 * 86400_000}
    check('three days left: nothing', (W.renew(box, fresh, 'projects/p/topics/t', ['L'], now), box.calls), ({}, []))
    late = {'history_id': '50', 'expiration': now + 3600_000}
    check('an hour left: renewed, the stored cursor kept', W.renew(box, late, 'projects/p/topics/t', ['L'], now),
          {'history_id': '50', 'expiration': 2000000000000})
    check('  no record: started with Gmail\'s cursor', W.renew(box, {}, 'projects/p/topics/t', ['L'], now)['history_id'], '70')


def test_push_decodes_gmails_and_a_hosts_own():
    gmail = {'message': {'data': base64.b64encode(b'{"emailAddress": "g@x", "historyId": 5}').decode()}}
    own = {'message': {'data': base64.b64encode(b'{"track": "alpha"}').decode(), 'attributes': {'type': 'play.sent'}}}
    check('Gmail\'s push', (W.is_gmail_push(W.push(gmail)), W.push(gmail)['historyId']), (True, 5))
    check('a host\'s own message', (W.is_gmail_push(W.push(own)), W.push(own)['_attributes']), (False, {'type': 'play.sent'}))
    check('no message', W.push({}), {})


DECLARED = [{'label': 'Google Play Console', 'from': 'noreply-play-console@google.com'}]


def test_plan_keeps_an_exact_filter_and_replaces_a_changed_one():
    have = {'Google Play Console': 'L21', 'Venmo': 'L9'}
    exact = {'id': 'f1', 'criteria': {'from': 'noreply-play-console@google.com'}, 'action': {'addLabelIds': ['L21']}}
    old = {'id': 'f2', 'criteria': {'from': 'old@google.com'}, 'action': {'addLabelIds': ['L21']}}
    other = {'id': 'f3', 'criteria': {'from': 'venmo@venmo.com'}, 'action': {'addLabelIds': ['L9']}}
    check('exact kept, nothing else to do', W.plan(DECLARED, have, [exact, other]), {'labels': [], 'create': [], 'delete': [], 'kept': 1})
    check('the old version deleted and made again; an undeclared label\'s filter left',
          W.plan(DECLARED, have, [old, other]), {'labels': [], 'create': DECLARED, 'delete': ['f2'], 'kept': 0})
    check('archive and markRead change the action', W.plan([{**DECLARED[0], 'archive': True}], have, [exact])['delete'], ['f1'])


def test_apply_makes_the_label_then_the_filter_and_a_dry_run_changes_nothing():
    made = []

    def routes(m, p, q, b):
        made.append((m, p, b))
        if p == 'labels' and m == 'GET':
            return {'labels': []}
        if p == 'settings/filters' and m == 'GET':
            return {'filter': []}
        if p == 'labels':
            return {'id': 'L30'}
        return {'id': 'f9'}
    got = W.apply(Box(routes), DECLARED)
    check('apply: done, the label made', (got['done'], got['labels']), (True, ['Google Play Console']))
    check('  the filter made on the new label', made[-1], ('POST', 'settings/filters', {
        'criteria': {'from': 'noreply-play-console@google.com'}, 'action': {'addLabelIds': ['L30']}}))
    made.clear()
    dry = W.apply(Box(routes), DECLARED, dry_run=True)
    check('dry run: reads only', (dry['done'], [m for m, _p, _b in made]), (False, ['GET', 'GET']))


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            fn()
    print(f'\n{len(fails)} failure(s)')
    sys.exit(1 if fails else 0)
