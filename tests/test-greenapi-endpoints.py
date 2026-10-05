#!/usr/bin/env python3
"""greenapi + redact: the GREEN-API calls, their failure levels, and the token scrub.

GREEN-API puts its API token in the URL path, and on 2026-09-25 PT an
exception carried that URL into two alert emails (shared/redact.py exists
because of it). greenapi.py was 62% covered and redact.py 85% (2026-10-02 PT);
tests/test-greenapi-connect-retry.py and tests/test-redact.py pin the retry and
the main scrub. With requests.post/get stubbed (nothing reaches
api.green-api.com) this pins the rest:
  - each helper's exact endpoint and payload (sendMessage, sendPoll with
    {"optionName"} options, sendFileByUrl, getGroupData, removeGroupParticipant,
    getChatHistory) and the timeouts they pass;
  - parsing: admins are isAdmin OR isSuperAdmin; participants without an id are
    dropped; a group description is stripped and None when empty or unreadable;
  - levels: call_api() logs a failed call at ERROR with the body (truncated)
    and an "unauthenticated" hint when the credentials are empty; the library
    read get_state_instance() logs WARNING only, never ERROR (CLAUDE.md: a library
    never sets the level for its caller); no log line ever carries the token;
  - redact: secret values from a .env file and the environment (JSON
    credentials split into their secret fields, short values never matched),
    the per-process cache rebuilt when a secret changes, the stdin filter, and
    never raising. Every token here is invented; the suite runs from a temp
    directory so the repo's .env is never read.

Run:  python -B tests/test-greenapi-endpoints.py
"""
import io
import json
import logging
import os
import sys
import tempfile

import requests

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import greenapi as G, redact as R  # noqa: E402

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

    def text(self):
        return '\n'.join(r.getMessage() for r in self.recs)


class Resp:
    def __init__(self, status=200, data=None, text=''):
        self.status_code, self.data, self.text = status, data, text

    def json(self):
        return self.data


FAKE_TOKEN = 'faketok' + 'Z9' * 20       # invented, token-shaped
IID = '7105000001'
posts, gets, plan = [], [], {'post': [], 'get': []}


def next_answer(kind):
    a = plan[kind].pop(0) if plan[kind] else Resp(200, {})
    if isinstance(a, Exception):
        raise a
    return a


def fake_post(url, json=None, timeout=None):
    posts.append((url, json, timeout))
    return next_answer('post')


def fake_get(url, timeout=None):
    gets.append((url, timeout))
    return next_answer('get')


cap = Cap()
G.logger.addHandler(cap)
G.logger.setLevel(logging.DEBUG)
_keep = (G.requests.post, G.requests.get, G.time.sleep)
slept = []
G.requests.post, G.requests.get = fake_post, fake_get
G.time.sleep = slept.append
try:
    print("\n-- the send helpers: endpoint, payload, timeout")
    plan['post'] = [Resp(200, {'idMessage': 'BAE5'})]
    got = G.send_message(IID, FAKE_TOKEN, '16265551111@c.us', 'Courts booked')
    check("the positive case: sendMessage returns the API's JSON", got, {'idMessage': 'BAE5'})
    check("  posted to waInstance<id>/sendMessage/<token> with chatId and message, 10 s",
          posts[-1], (f"https://api.green-api.com/waInstance{IID}/sendMessage/{FAKE_TOKEN}",
                      {'chatId': '16265551111@c.us', 'message': 'Courts booked'}, 10))
    G.send_poll(IID, FAKE_TOKEN, 'g@g.us', 'Which nights?', ['Tue', 'Thu'], multiple_answers=False, timeout=20)
    check("sendPoll: options wrapped as optionName, single answer, its timeout",
          (posts[-1][0].endswith('/sendPoll/' + FAKE_TOKEN), posts[-1][1], posts[-1][2]),
          (True, {'chatId': 'g@g.us', 'message': 'Which nights?', 'options': [{'optionName': 'Tue'}, {'optionName': 'Thu'}],
                  'multipleAnswers': False}, 20))
    G.send_file_by_url(IID, FAKE_TOKEN, 'g@g.us', 'https://example.com/h.png', caption='Heatmap')
    check("sendFileByUrl: URL, default file name, caption, 60 s",
          (posts[-1][1], posts[-1][2]),
          ({'chatId': 'g@g.us', 'urlFile': 'https://example.com/h.png', 'fileName': 'image.jpg', 'caption': 'Heatmap'}, 60))
    G.remove_group_participant(IID, FAKE_TOKEN, 'g@g.us', '1@c.us')
    check("removeGroupParticipant: groupId and participantChatId",
          posts[-1][1], {'groupId': 'g@g.us', 'participantChatId': '1@c.us'})
    plan['post'] = [Resp(200, [{'idMessage': 'a'}, {'idMessage': 'b'}])]
    check("getChatHistory returns the list as is, count passed",
          (G.get_chat_history(IID, FAKE_TOKEN, 'g@g.us', count=2), posts[-1][1]),
          ([{'idMessage': 'a'}, {'idMessage': 'b'}], {'chatId': 'g@g.us', 'count': 2}))
    G.call_api(IID, FAKE_TOKEN, 'getSettings')
    check("call_api with no payload posts {}", posts[-1][1], {})

    print("\n-- the group readers: parsing getGroupData")
    GROUP = {'description': '  Welcome to SMAD!  ', 'participants': [
        {'id': '1@c.us', 'isAdmin': True, 'isSuperAdmin': False},
        {'id': '2@c.us', 'isAdmin': False, 'isSuperAdmin': True},
        {'id': '3@c.us', 'isAdmin': False, 'isSuperAdmin': False},
        {'isAdmin': False}]}
    plan['post'] = [Resp(200, GROUP)]
    check("the positive case: admins are isAdmin or isSuperAdmin", G.get_group_admin_ids(IID, FAKE_TOKEN, 'g@g.us'), {'1@c.us', '2@c.us'})
    plan['post'] = [Resp(200, GROUP)]
    check("participants: every id, an entry without one dropped",
          G.get_group_participant_ids(IID, FAKE_TOKEN, 'g@g.us'), {'1@c.us', '2@c.us', '3@c.us'})
    plan['post'] = [Resp(200, GROUP)]
    check("description: stripped", G.get_group_description(IID, FAKE_TOKEN, 'g@g.us'), 'Welcome to SMAD!')
    plan['post'] = [Resp(200, {'description': '   '})]
    check("a blank description -> None (callers fall back, never send an empty welcome)",
          G.get_group_description(IID, FAKE_TOKEN, 'g@g.us'), None)
    plan['post'] = [Resp(500, text='oops')] * 3
    check("an unreadable group -> None / empty sets ('unknown', not 'empty group')",
          (G.get_group_description(IID, FAKE_TOKEN, 'g@g.us'), G.get_group_admin_ids(IID, FAKE_TOKEN, 'g@g.us'),
           G.get_group_participant_ids(IID, FAKE_TOKEN, 'g@g.us')), (None, set(), set()))
    plan['post'] = [requests.exceptions.ConnectTimeout('connect timeout'), Resp(200, GROUP)]
    slept.clear()
    got = G.get_group_data(IID, FAKE_TOKEN, 'g@g.us', connect_retries=1)
    check("get_group_data passes connect_retries: one connect timeout, then the group, 5 s waited",
          (got is not None, slept), (True, [5]))

    print("\n-- call_api(): a failed call is None at ERROR, with the body and no token")
    cap.recs.clear()
    plan['post'] = [Resp(466, text='  ' + 'x' * 400 + '  ')]
    check("the positive case: a non-200 -> None", G.call_api(IID, FAKE_TOKEN, 'sendMessage', {'a': 1}), None)
    check("  one ERROR naming the method and status, the body cut to 300 chars",
          (cap.levels(), cap.recs[0].getMessage()), (['ERROR'], 'sendMessage returned 466: ' + 'x' * 300))
    cap.recs.clear()
    plan['post'] = [Resp(403, text='Forbidden')]
    G.call_api('', '', 'removeGroupParticipant')
    check("a 403 with empty credentials says the call was unauthenticated",
          cap.recs[0].getMessage(),
          'removeGroupParticipant returned 403: Forbidden — instance_id/api_token are empty, so this call was unauthenticated')
    plan['post'] = [Resp(401, text='')]
    cap.recs.clear()
    G.call_api(IID, FAKE_TOKEN, 'sendMessage')
    check("  but not when credentials were sent", 'unauthenticated' in cap.text(), False)
    plan['post'] = [requests.exceptions.ReadTimeout('read timed out')]
    cap.recs.clear()
    check("an exception -> None at ERROR, not retried",
          (G.call_api(IID, FAKE_TOKEN, 'sendMessage', connect_retries=2), cap.levels(), len(slept)), (None, ['ERROR'], 1))

    print("\n-- get_state_instance(): a library read, WARNING only")
    plan['get'] = [Resp(200, {'stateInstance': 'authorized'})]
    check("the positive case: the raw answer, by GET",
          (G.get_state_instance(IID, FAKE_TOKEN), gets[-1]),
          ({'stateInstance': 'authorized'}, (f"https://api.green-api.com/waInstance{IID}/getStateInstance/{FAKE_TOKEN}", 10)))
    cap.recs.clear()
    plan['get'] = [Resp(502, text='Bad Gateway'), Resp(403, text='Forbidden'), requests.exceptions.ReadTimeout('timed out')]
    got = (G.get_state_instance(IID, FAKE_TOKEN), G.get_state_instance('', ''), G.get_state_instance(IID, FAKE_TOKEN, timeout=3))
    check("502, 403 and a timeout -> None each ('could not ask', never 'healthy')", got, (None, None, None))
    check("  three WARNINGs and no ERROR: the caller decides when it is an outage",
          cap.levels(), ['WARNING', 'WARNING', 'WARNING'])
    check("  the empty-credentials hint on the 403, the exception type on the timeout",
          ('unauthenticated' in cap.recs[1].getMessage(), cap.recs[2].getMessage().startswith('Error calling getStateInstance: ReadTimeout')),
          (True, True))
    check("  the caller's timeout passed", gets[-1][1], 3)
    cap.recs.clear()
    G.call_api(IID, FAKE_TOKEN, 'x')
    check("no line logged by this module carries the API token", FAKE_TOKEN in cap.text(), False)
finally:
    G.requests.post, G.requests.get, G.time.sleep = _keep
    G.logger.removeHandler(cap)

print("\n-- redact.secret_values(): what is masked")
SA = json.dumps({'type': 'service_account', 'client_email': 'bot@proj.iam.gserviceaccount.com',
                 'private_key': '-----BEGIN KEY-----\nfakefakefakefake\n-----END KEY-----\n', 'private_key_id': 'short'})
vals = R.secret_values({'GREENAPI_API_TOKEN': FAKE_TOKEN, 'ATHENAEUM_PASSWORD': 'tiny', 'SMAD_GOOGLE_CREDENTIALS_JSON': SA,
                        'GMAIL_OAUTH_TOKEN_JSON': '{not json but long enough}', 'UNLISTED_NAME': 'x' * 40})
check("the positive case: the token, the JSON's private key (raw and as printed in JSON), longest first",
      vals, ['-----BEGIN KEY-----\\nfakefakefakefake\\n-----END KEY-----\\n',
             '-----BEGIN KEY-----\nfakefakefakefake\n-----END KEY-----\n',
             FAKE_TOKEN, '{not json but long enough}'])
check("  a short value, a short JSON field, the client email and unlisted names are never masked",
      any(v in ('tiny', 'short', 'bot@proj.iam.gserviceaccount.com', 'x' * 40) for v in vals), False)
check("redact(): the JSON key is masked inside a printed credential",
      R.redact(f'creds={SA}', env={'SMAD_GOOGLE_CREDENTIALS_JSON': SA}).count('***'), 1)
check("a non-string is scrubbed as text", R.redact(12345, env={}), '12345')
check("a scrub that fails returns the text with the URL pattern still applied",
      R.redact(f'waInstance1/sendMessage/{FAKE_TOKEN}', env={'GITHUB_TOKEN': 12345}), 'waInstance<id>/sendMessage/***')

print("\n-- redact(): the process's secrets, from ./.env and the environment, cached")
cwd = os.getcwd()
tmp = tempfile.mkdtemp()
names = R.SECRET_ENV_NAMES
saved_env = {n: os.environ.get(n) for n in names}
os.chdir(tmp)
try:
    for n in names:
        os.environ.pop(n, None)
    DOT_TOKEN = 'dotenvsecret' + 'Q7' * 10
    with open(os.path.join(tmp, '.env'), 'w', encoding='utf-8') as f:
        f.write(f'# comment\n\nNOT_A_PAIR\nGITHUB_PAT="{DOT_TOKEN}"\nOTHER=plain\n')
    check("the positive case: a .env value is masked", R.redact(f'push with {DOT_TOKEN}'), 'push with ***')
    check("_dotenv_values(): quotes stripped, comments and lines without '=' skipped",
          R._dotenv_values(os.path.join(tmp, '.env')), {'GITHUB_PAT': DOT_TOKEN, 'OTHER': 'plain'})
    check("_dotenv_values(): no file -> {}", R._dotenv_values(os.path.join(tmp, 'missing.env')), {})
    ENV_TOKEN = 'envsecret' + 'K3' * 10
    os.environ['ANTHROPIC_API_KEY'] = ENV_TOKEN
    check("a secret set after the first scrub is picked up (the cache key includes it)",
          R.redact(f'{ENV_TOKEN} and {DOT_TOKEN}'), '*** and ***')
    key = R._cache['key']
    R.redact('again')
    check("  an unchanged environment reuses the cached list", R._cache['key'] is key, True)
    os.remove(os.path.join(tmp, '.env'))
    check("  the .env removed -> its value no longer masked", R.redact(DOT_TOKEN), DOT_TOKEN)

    keep_io = (sys.stdin, sys.stdout)
    sys.stdin, sys.stdout = io.StringIO(f'Error calling sendMessage: waInstance1/sendMessage/{FAKE_TOKEN}\n'), io.StringIO()
    try:
        R.main()
        out = sys.stdout.getvalue()
    finally:
        sys.stdin, sys.stdout = keep_io
    check("main(): stdin scrubbed to stdout", out, 'Error calling sendMessage: waInstance<id>/sendMessage/***\n')
finally:
    os.chdir(cwd)
    for n, v in saved_env.items():
        if v is None:
            os.environ.pop(n, None)
        else:
            os.environ[n] = v
    R._cache['key'], R._cache['vals'] = None, []

print(f"\n{len(fails)} failure(s)")
if fails:
    sys.exit(1)
