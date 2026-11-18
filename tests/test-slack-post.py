#!/usr/bin/env python3
"""chuang_platform_kit.slack_post: a Slack post as an event, delivered once by the token's holder (0.36.0).

Pinned (the positive case first): deliver() claims the key, posts to the resolved channel and keeps the post's ts; a
repeated delivery posts nothing; thread_of posts under the kept post and update_of redraws it; a refusal a redelivery
may fix is a Retry with the claim released, a final one an ERROR line with the claim kept; a file goes up with the
blocks and its share ts is read from files.info as a form call, again while Slack has not filled it, its id kept when
it never comes; event_data builds the one shape and refuses a file over FILE_MAX; upload() makes Slack's three calls;
share_ts reads a public or private share.

Run:  python -B tests/test-slack-post.py
"""
import base64
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import slack_post as SP  # noqa: E402

fails = []
NOW = datetime(2026, 11, 18, 23, 0, tzinfo=timezone.utc)  # utc-ok: an instant for the record's stamp
COL = 'posts'


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


class Store:
    def __init__(self, down=False):
        self.docs, self.down = {}, down

    def _check(self):
        if self.down:
            raise ConnectionError('down')

    def get(self, c, k):
        self._check()
        return self.docs.get((c, k))

    def set(self, c, k, v):
        self._check()
        self.docs[(c, k)] = dict(v)

    def create(self, c, k, v):
        self._check()
        if (c, k) in self.docs:
            return False
        self.docs[(c, k)] = dict(v)
        return True

    def delete(self, c, k):
        self._check()
        self.docs.pop((c, k), None)


def roles(channel):
    return {'reports': 'C-REPORTS'}.get(channel, channel if channel.startswith('C') else '')


def poster(answer=None, sent=None):
    def post(token, channel, text, blocks, thread_ts):
        if sent is not None:
            sent.append((channel, text, thread_ts))
        return answer or {'ok': True, 'ts': '1.1'}
    return post


# ------------------------------------------------------------------ the positive case
store, sent = Store(), []
data = SP.event_data('k1', 'reports', 'hello', who='deploy')
lines = SP.deliver(data, 'xoxb', store, COL, resolve=roles, now=NOW, post=poster(sent=sent))
check("the positive case: the post goes to the resolved channel and its ts is kept under the key",
      (lines, sent, store.docs[(COL, 'k1')]['ts'], store.docs[(COL, 'k1')]['channel']),
      (['slack post: deploy: k1 posted in C-REPORTS'], [('C-REPORTS', 'hello', '')], '1.1', 'C-REPORTS'))
again = SP.deliver(data, 'xoxb', store, COL, resolve=roles, now=NOW, post=poster(sent=sent))
check("a repeated delivery is acknowledged and posts nothing", (again, len(sent)),
      (['slack post: deploy: k1 already posted; acknowledged'], 1))

# ------------------------------------------------------------------ threads and redraws
under = SP.deliver(SP.event_data('k2', 'reports', 'reply', thread_of='k1', who='deploy'), 'xoxb', store, COL,
                   resolve=roles, now=NOW, post=poster(sent=sent))
check("thread_of posts under the kept post, in its channel", (under, sent[-1]),
      (['slack post: deploy: k2 posted in C-REPORTS (threaded)'], ('C-REPORTS', 'reply', '1.1')))
redraws = []
redrew = SP.deliver(SP.event_data('k1', 'reports', 'done', update_of='k1', who='deploy'), 'xoxb', store, COL,
                    resolve=roles, now=NOW, update=lambda m, t, p: redraws.append((m, p)) or {'ok': True})
check("update_of redraws the kept post with chat.update and posts nothing new", (redrew, redraws),
      (['slack post: deploy: k1 redrew k1 in C-REPORTS'],
       [('chat.update', {'channel': 'C-REPORTS', 'ts': '1.1', 'text': 'done'})]))
orphan = SP.deliver(SP.event_data('k3', 'reports', 'x', update_of='none', who='deploy'), 'xoxb', store, COL, resolve=roles, now=NOW)
check("a redraw of a post never kept updates nothing, a WARNING", orphan,
      ['WARNING: slack post: deploy: k3 redraws none, which has no post on record; nothing updated'])

# ------------------------------------------------------------------ refusals
store = Store()
try:
    SP.deliver(SP.event_data('r1', 'C1', 'x'), 'xoxb', store, COL, now=NOW, post=poster({'ok': False, 'error': 'HTTP 503'}))
    retried = 'no retry'
except SP.Retry as e:
    retried = str(e)
check("a refusal a redelivery may fix is a Retry, the claim released", (retried, (COL, 'r1') in store.docs),
      ('Slack refused the post (HTTP 503)', False))
final = SP.deliver(SP.event_data('r2', 'C1', 'x'), 'xoxb', store, COL, now=NOW, post=poster({'ok': False, 'error': 'not_in_channel'}))
check("a final refusal is an ERROR line and the claim stays, so nothing posts twice", (final, (COL, 'r2') in store.docs),
      (['ERROR: slack post: notice: r2 was not posted (not_in_channel)'], True))
try:
    SP.deliver(SP.event_data('r3', 'C1', 'x'), 'xoxb', Store(down=True), COL, now=NOW)
    down = 'no retry'
except SP.Retry as e:
    down = str(e)
check("the store out of reach is a Retry", down, 'Firestore could not be reached (ConnectionError)')
check("no key, no token and no channel each say so and post nothing",
      (SP.deliver({'who': 'w'}, 'xoxb', store, COL), SP.deliver(SP.event_data('n1', 'C1', 'x', who='w'), '', store, COL),
       SP.deliver(SP.event_data('n2', 'nowhere', 'x', who='w'), 'xoxb', store, COL, resolve=roles, now=NOW)),
      (['WARNING: slack post: w: no key; nothing posted'],
       ["ERROR: slack post: w: n1: the Slack app's function holds no SLACK_BOT_TOKEN; nothing posted"],
       ['ERROR: slack post: w: n2: no channel (nowhere names none); nothing posted']))

# ------------------------------------------------------------------ a file on the post
store, forms, waits = Store(), [], []
shot = {'name': 'f.png', 'content_type': 'image/png', 'data': b'x'}
up = lambda tok, name, raw, channel, thread, title, blocks=None: {'ok': True, 'files': [{'id': 'F1'}]}  # noqa: E731
answers = [{'ok': True, 'file': {}}, {'ok': True, 'file': {'shares': {'private': {'C1': [{'ts': '9.9'}]}}}}]
lines = SP.deliver(SP.event_data('f1', 'C1', 'card', file=shot), 'xoxb', store, COL, now=NOW, upload=up,
                   info=lambda m, t, p: forms.append((m, p)) or answers[min(len(forms), 2) - 1], sleep=waits.append)
check("a share Slack has not filled is read again from files.info, a form call, and the ts kept",
      (lines, forms, waits, store.docs[(COL, 'f1')]['ts']),
      (['slack post: notice: f1 posted in C1'], [('files.info', {'file': 'F1'})] * 2, [SP.SHARE_WAIT_S], '9.9'))
store = Store()
lines = SP.deliver(SP.event_data('f2', 'C1', 'card', file=shot), 'xoxb', store, COL, now=NOW, upload=up,
                   info=lambda m, t, p: {'ok': True, 'file': {}}, sleep=lambda s: None)
check("a share that never comes keeps the file's id for the post's next phase, a WARNING",
      (lines[0].startswith("WARNING: slack post: notice: f2: the post's ts was not read yet"),
       store.docs[(COL, 'f2')].get('file'), store.docs[(COL, 'f2')]['ts']),
      (True, 'F1', ''))
late = SP.kept(store, COL, 'f2', 'xoxb', info=lambda m, t, p: answers[1])
check("  and that next phase reads the ts and keeps it", (late['ts'], store.docs[(COL, 'f2')]['ts']), ('9.9', '9.9'))
try:
    SP.event_data('big', 'C1', 'x', file={'name': 'b', 'data': b'x' * (SP.FILE_MAX + 1)})
    big = 'no error'
except ValueError as e:
    big = str(e)
check("event_data refuses a file over FILE_MAX and encodes one under it",
      (big, SP.event_data('f', 'C1', 'x', file=shot)['file']['b64']), ('the file is over 4 MB', base64.b64encode(b'x').decode()))

# ------------------------------------------------------------------ the upload's three calls
calls = []


class Up:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener(req, timeout):
    calls.append(('PUT', req.full_url, req.data))
    return Up()


got = SP.upload('xoxb', 'f.png', b'abc', 'C1', '1.1', 'A title', opener=opener, blocks=[{'type': 'divider'}],
                form_=lambda m, t, p, o: calls.append((m, p)) or {'ok': True, 'upload_url': 'https://up.example/x', 'file_id': 'F9'},
                json_=lambda m, t, p, o: calls.append((m, p)) or {'ok': True, 'files': [{'id': 'F9', 'shares': {'public': {'C1': [{'ts': '5.5'}]}}}]})
check("upload: the URL asked for, the bytes sent, the upload completed into the channel and thread with the blocks; the share's ts answered",
      (calls, got['ts']),
      ([('files.getUploadURLExternal', {'filename': 'f.png', 'length': 3}), ('PUT', 'https://up.example/x', b'abc'),
        ('files.completeUploadExternal', {'files': [{'id': 'F9', 'title': 'A title'}], 'channel_id': 'C1', 'thread_ts': '1.1',
                                          'blocks': [{'type': 'divider'}]})], '5.5'))
check("upload: a refused first call is the answer", SP.upload('xoxb', 'f', b'a', 'C1', form_=lambda m, t, p, o: {'ok': False, 'error': 'missing_scope'}),
      {'ok': False, 'error': 'missing_scope'})
check("share_ts: none in an answer with no share", (SP.share_ts({}, 'C1'), SP.share_ts({'file': {'shares': {}}}, 'C1')), ('', ''))

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
