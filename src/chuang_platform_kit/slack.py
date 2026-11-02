"""Slack, one client for every project: a message posted as a bot to a channel, a post redrawn, any other Web API
call, and the incoming-webhook send kept as the second door.

Lifted from SMAD PickleBot's `webhook/sentry-alerts/slack_app.py` and the five hand-written `chat.postMessage` calls
beside it, and ChuangFinance's webhook `send_slack` (kit 0.22.0, SMAD PickleBot #683: "every third-party API call goes
through chuang-platform-kit's one client for that vendor"). Everything takes the bot `token` (`xoxb-…`, `chat:write`;
`chat:write.public` to post where the bot was never invited) and a channel id; `opener` is the seam (anything called as
urlopen(request, timeout=...)), so the suites run offline. Standard library only: it rides into functions and jobs that
install nothing else.

    from chuang_platform_kit import slack
    got = slack.post(token, 'C0123', 'Deployed *v1*', blocks=blocks)      # Slack's answer: 'ts', 'channel'
    slack.post(token, 'C0123', 'and a reply', thread_ts=got['ts'])
    slack.update(token, 'C0123', got['ts'], 'Deployed *v1* · done', blocks=blocks)
    slack.call('chat.unfurl', token, {'channel': c, 'ts': ts, 'unfurls': unfurls})
    slack.webhook_post(url, {'text': 'hello', 'blocks': blocks})       # an incoming webhook, no token

A refusal is SlackError: `status` (the HTTP status, 0 when Slack could not be reached) and `error` (Slack's own error
string, `not_in_channel`, else the failure's kind, `TimeoutError`). Neither the token nor a webhook URL ever appears
in an exception's text, and nothing here prints or logs.

Nothing here names a channel, a workspace or a project: a host passes the channel it read from its own settings.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

API = 'https://slack.com/api/'
TIMEOUT = 15


class SlackError(RuntimeError):
    """A call Slack refused or could not answer: `status` (the HTTP status; 200 with Slack's `ok: false`; 0 for no
    answer) and `error` (Slack's error string, else the failure's kind). The text never carries a token or a URL."""

    def __init__(self, status: int, error: str, method: str = ''):
        super().__init__(f"Slack{' ' + method if method else ''}: HTTP {status} ({error})")
        self.status, self.error, self.method = status, error, method

    @property
    def unreachable(self) -> bool:
        """True when nothing answered (a connection, DNS or timeout failure), as opposed to a refusal."""
        return self.status == 0


def _send(req, method: str, opener=None, timeout=TIMEOUT) -> dict:
    """One request to Slack, its JSON answer; SlackError for a non-2xx status, an `ok: false`, or no answer."""
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
            status = getattr(r, 'status', 200)
            text = (r.read() or b'').decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', 'replace') if e.fp else ''
        raise SlackError(e.code, _error_in(body) or str(e.reason or 'HTTPError'), method) from None
    except urllib.error.URLError as e:
        reason = e.reason
        raise SlackError(0, type(reason).__name__ if isinstance(reason, BaseException) else type(e).__name__, method) from None
    except (OSError, ValueError) as e:   # a socket's own error, a timeout raised bare
        raise SlackError(0, type(e).__name__, method) from None
    try:
        got = json.loads(text) if text else {}
    except ValueError:
        got = {}
    if not isinstance(got, dict):
        got = {}
    if not 200 <= status < 300:
        raise SlackError(status, got.get('error') or f'HTTP {status}', method)
    if not got.get('ok'):
        raise SlackError(status, got.get('error') or 'no ok', method)
    return got


def _error_in(body: str) -> str:
    """Slack's `error` from a refusal's body when it is JSON; the body's own words when it is a short text
    (`no_service`, `invalid_payload`, an incoming webhook's answers); '' otherwise."""
    body = (body or '').strip()
    if not body:
        return ''
    try:
        got = json.loads(body)
        return str(got.get('error') or '') if isinstance(got, dict) else ''
    except ValueError:
        return body if len(body) <= 80 and '\n' not in body and '<' not in body else ''


def call(method: str, token: str, payload: dict, opener=None, timeout=TIMEOUT) -> dict:
    """One Web API call (`chat.postMessage`, `chat.update`, `chat.unfurl`, `files.completeUploadExternal`, …) as JSON
    under the bot token; Slack's answer. SlackError when Slack refuses (`ok: false`, its `error`), answers a non-2xx
    status, or cannot be reached; a blank token is refused before any call."""
    token = (token or '').strip()
    if not token:
        raise SlackError(0, 'no token', method)
    req = urllib.request.Request(API + method, data=json.dumps(payload).encode('utf-8'), method='POST',
                                 headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json; charset=utf-8'})
    return _send(req, method, opener, timeout)


def form(method: str, token: str, params: dict, opener=None, timeout=TIMEOUT) -> dict:
    """A Web API call that takes form fields, not JSON (`files.getUploadURLExternal`); the same answer and refusals as
    call()."""
    token = (token or '').strip()
    if not token:
        raise SlackError(0, 'no token', method)
    req = urllib.request.Request(API + method, data=urllib.parse.urlencode(params).encode('utf-8'), method='POST',
                                 headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/x-www-form-urlencoded'})
    return _send(req, method, opener, timeout)


def post(token: str, channel: str, text: str, blocks=None, thread_ts: str = None, unfurl_links: bool = False,
         unfurl_media: bool = None, opener=None, timeout=TIMEOUT) -> dict:
    """One message to `channel` (an id, or a name the bot can resolve) as the bot: `text` the notification's own
    words and the message when there are no `blocks`; `blocks` (Block Kit) draw it instead; `thread_ts` makes it a
    reply under that post. Links are not unfurled unless asked (`unfurl_links`; `unfurl_media` is sent only when
    given). Slack's answer, with the message's `ts` and `channel`; SlackError as call()."""
    payload = {'channel': channel, 'text': text, 'unfurl_links': bool(unfurl_links)}
    if unfurl_media is not None:
        payload['unfurl_media'] = bool(unfurl_media)
    if blocks:
        payload['blocks'] = blocks
    if thread_ts:
        payload['thread_ts'] = thread_ts
    return call('chat.postMessage', token, payload, opener, timeout)


def update(token: str, channel: str, ts: str, text: str, blocks=None, opener=None, timeout=TIMEOUT) -> dict:
    """The bot's own post at `ts` in `channel` redrawn with `text` and `blocks` (the whole message is replaced, so a
    post with blocks is sent its blocks again). Slack's answer; SlackError as call()."""
    payload = {'channel': channel, 'ts': ts, 'text': text}
    if blocks is not None:
        payload['blocks'] = blocks
    return call('chat.update', token, payload, opener, timeout)


def webhook_post(url: str, payload: dict, opener=None, timeout=TIMEOUT) -> None:
    """The second door: `payload` ({'text', 'blocks', …}) to an incoming webhook URL, which answers `ok` as text and
    needs no token. Nothing on success; SlackError on a refusal (`status` and the webhook's word, `invalid_payload`,
    `no_service`) or no answer; the URL, a secret, is never in the error. A blank URL is refused before any call."""
    url = (url or '').strip()
    if not url:
        raise SlackError(0, 'no webhook url', 'webhook')
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), method='POST',
                                 headers={'Content-Type': 'application/json; charset=utf-8'})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
            status = getattr(r, 'status', 200)
            text = (r.read() or b'').decode('utf-8', 'replace').strip()
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', 'replace') if e.fp else ''
        raise SlackError(e.code, _error_in(body) or str(e.reason or 'HTTPError'), 'webhook') from None
    except urllib.error.URLError as e:
        reason = e.reason
        raise SlackError(0, type(reason).__name__ if isinstance(reason, BaseException) else type(e).__name__, 'webhook') from None
    except (OSError, ValueError) as e:
        raise SlackError(0, type(e).__name__, 'webhook') from None
    if not 200 <= status < 300:
        raise SlackError(status, _error_in(text) or f'HTTP {status}', 'webhook')
    try:   # a webhook answers the word `ok`; a JSON answer saying ok false is a refusal all the same
        got = json.loads(text) if text.startswith('{') else None
    except ValueError:
        got = None
    if isinstance(got, dict) and not got.get('ok', True):
        raise SlackError(status, str(got.get('error') or 'no ok'), 'webhook')
