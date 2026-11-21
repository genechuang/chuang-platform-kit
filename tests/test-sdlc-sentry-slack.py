#!/usr/bin/env python3
"""chuang_sdlc.sentry_slack: a Sentry alarm as a Slack card, its buttons, a tap's change, the echo window and the post
record.

Pinned (the positive case first, on fakes, never Slack or Sentry): an unresolved card has Resolve, Resolve in next
release, Archive, the host's people and sessions in the menu and Open in Sentry; a resolved one Unresolve alone; each
tap's change and words; a person's key and a session's hand-off; the status line; Slack's signature and its replay
window; the webhook's issue id; the echo of the card's own change within the window, archive and ignore one kind; the
assignee by first name; the claim once; the redraw only with a record.

Run:  python -B tests/test-sdlc-sentry-slack.py
"""
import hashlib
import hmac
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))

from chuang_sdlc import sentry_slack as S  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(('  PASS  ' if ok else '  FAIL  ') + label)
    if not ok:
        print(f'        got  {got!r}\n        want {want!r}')
        fails.append(label)


PEOPLE, SESSIONS = (('Pat', 'pat'),), ('Ana', 'Bo')
ISSUE = {'id': '42', 'shortId': 'APP-3', 'status': 'unresolved', 'permalink': 'https://o.sentry.io/issues/42/'}

card = S.blocks(ISSUE, 'the alarm', people=PEOPLE, sessions=SESSIONS)
els = card[2]['elements']
check('the positive case: an unresolved card\'s buttons, menu and link',
      ([e.get('action_id') for e in els], [o['value'] for o in els[3]['options']], card[0]['text']['text']),
      (['resolve', 'resolve_next', 'archive', 'assign', 'open'], ['42|pat', '42|session:Ana', '42|session:Bo'], 'the alarm'))
check('a resolved card offers Unresolve; no actions leaves the buttons out',
      ([e.get('action_id') for e in S.blocks(dict(ISSUE, status='resolved'), 't', people=PEOPLE)[2]['elements']][:1],
       len(S.blocks(ISSUE, 't', actions=False))), (['unresolve'], 2))

check('each tap\'s change and words',
      [S.change_for(a, v, PEOPLE, SESSIONS) for a, v in
       (('resolve', '42'), ('resolve_next', '42'), ('archive', '42'), ('unresolve', '42'), ('assign', '42|pat'),
        ('assign', '42|session:Bo'), ('assign', '42|session:Zed'), ('open', '42'), ('resolve', 'x'))],
      [('42', {'status': 'resolved', 'statusDetails': {}}, 'Resolved'),
       ('42', {'status': 'resolved', 'statusDetails': {'inNextRelease': True}}, 'Resolved in the next release'),
       ('42', {'status': 'ignored', 'substatus': 'archived_until_escalating', 'statusDetails': {}}, 'Archived'),
       ('42', {'status': 'unresolved', 'statusDetails': {}}, 'Unresolved'),
       ('42', {'assignedTo': 'pat'}, 'Assigned to Pat'), ('42', None, 'Handed to Bo'), None, None, None])
check('the kind each change echoes as', [S.change_kind(u) for u in ({'assignedTo': 'x'}, {'status': 'ignored'},
                                                                    {'status': 'resolved'}, None)],
      ['assigned', 'archived', 'resolved', ''])

check('the status line', (S.status_line(dict(ISSUE, assignedTo={'name': 'Pat'}), {'text': 'Assigned', 'by': 'pat', 'at': '9:01 PM PT'}),
                          S.status_line({'status': 'resolved', 'statusDetails': {'inNextRelease': True}})),
      (':red_circle: Unresolved · assigned to Pat · Assigned by pat, 9:01 PM PT',
       ':white_check_mark: Resolved in the next release'))

body, secret, now = b'payload=x', 'shh', 1_000_000
sig = 'v0=' + hmac.new(secret.encode(), b'v0:1000000:' + body, hashlib.sha256).hexdigest()
headers = {'X-Slack-Request-Timestamp': '1000000', 'X-Slack-Signature': sig}
check('Slack\'s signature: good, wrong, replayed', (S.verify_slack(headers, body, secret, now),
                                                    S.verify_slack(headers, body, 'other', now),
                                                    S.verify_slack(headers, body, secret, now + 301)), (True, False, False))

check('the webhook\'s issue id', (S.issue_id_of('issue', {'action': 'created', 'data': {'issue': {'id': 7}}}),
                                  S.issue_id_of('event_alert', {'action': 'triggered', 'data': {'event': {'issue_id': '8'}}}),
                                  S.issue_id_of('installation', {'action': 'created'})), ('7', '8', None))

last = {'kind': 'archived', 'epoch': 100}
check('the echo: same kind within the window, archive and ignore one kind; later or another kind is not',
      (S.is_echo('ignored', last, 150), S.is_echo('archived', last, 161), S.is_echo('resolved', last, 120)), (True, False, False))

check('the assignee by first name', (S.assignee_for([{'name': 'Pat Q', 'user': {'id': '9'}, 'id': '1'}], 'pat'),
                                     S.assignee_for([], 'pat')), ('user:9', ''))


class Doc:
    def __init__(self):
        self.data = None

    def create(self, record):
        if self.data is not None:
            raise type('AlreadyExists', (Exception,), {})()
        self.data = dict(record)

    def set(self, fields, merge=False):
        self.data = dict(self.data or {}, **fields)

    def get(self):
        d = self.data
        return type('Snap', (), {'exists': d is not None, 'to_dict': lambda self: d})()


doc = Doc()
first, second = S.claim(doc, {'short_id': 'APP-3'}), S.claim(doc, {'short_id': 'APP-3'})
S.keep(doc, {'channel': 'C1', 'ts': '1.2', 'text': 'the alarm'})
calls = []
drawn = S.redraw(ISSUE, S.kept(doc), 'xoxb', slack_api=lambda m, t, p: calls.append((m, p['ts'])) or {'ok': True})
check('the claim once, the record kept, the redraw with it; none without a record',
      (first, second, drawn, calls, S.redraw(ISSUE, {}, 'xoxb', slack_api=None)), (True, False, True, [('chat.update', '1.2')], False))

check('a link_shared event\'s issues and a message\'s first section',
      (S.issue_ids({'links': [{'url': 'https://o.sentry.io/issues/5/'}, {'url': 'https://example.com'}]}),
       S.first_section({'blocks': [{'type': 'context'}, {'type': 'section', 'text': {'text': 'hi'}}]})),
      ([('https://o.sentry.io/issues/5/', '5')], 'hi'))

print(f'{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
