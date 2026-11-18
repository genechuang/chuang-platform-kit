"""One Slack post as an event: a producer that holds no Slack token publishes the post's data on its own bus, and the
one function that holds the token delivers it (0.36.0, out of a host's own module, SMADPickleBot #1729).

The post's ts is kept in the host's store under the post's key, {channel, ts, at}, so a later event threads under it
(`thread_of`) or redraws it in place (`update_of`), and the key is the claim: a delivery the bus repeats never posts
twice.

    from chuang_platform_kit import slack_post
    data = slack_post.event_data('deploy-42', 'reports', 'Deployed *v1*', who='deploy')   # the producer publishes it
    lines = slack_post.deliver(data, token, store, 'slack_posts', resolve=channel_of)       # the token's holder

The event's data (`event_data` builds it):
    key        the post's name, claimed once at <collection>/<key>; the thread key for later events
    channel    a channel id, or a role the host's `resolve` turns into one
    text       the fallback text, Slack mrkdwn (the notification's words when there are blocks)
    blocks     optional Block Kit
    thread_of  optional: the key of the post this one goes under
    update_of  optional: the key of the post this one redraws in place (chat.update); nothing new is posted
    file       optional {name, content_type, b64, title}: the picture on the post itself (the upload's share message
               carrying the blocks, `upload`); at most FILE_MAX bytes decoded
    who        the log line's owner

`store` is anything with get(collection, id), set(collection, id, fields), create(collection, id, fields) -> bool and
delete(collection, id) (`firestore.Client`). Everything sent passes `redact`. Nothing here names a channel, a
workspace, a bus or a project: the host hands in its collection, its channel resolver and how it publishes.
"""
import base64
import json
import time
import urllib.request
from datetime import datetime, timezone

from . import slack

FILE_MAX = 4 * 1024 * 1024
SHARE_READS, SHARE_WAIT_S = 4, 1.0   # files.info reads for an upload's share ts, and the wait between them
# Slack's refusals that a redelivery would not change: the post is given up with an ERROR line, not retried.
FINAL_ERRORS = ('missing_scope', 'not_in_channel', 'channel_not_found', 'invalid_blocks', 'invalid_arguments',
                'msg_too_long', 'no_text', 'message_not_found', 'cant_update_message', 'invalid_auth', 'not_authed',
                'account_inactive', 'token_revoked')


class Retry(Exception):
    """The post was not made and a redelivery may make it: Slack unreachable or refusing for the moment, the store out
    of reach. The listener answers its bus's retry status."""


class Final(Exception):
    """A refusal no redelivery would change; its line is the ERROR the host's alarm reads."""


def _redact(text: str) -> str:
    from .redact import redact
    return redact(text)


def event_data(key: str, channel: str, text: str, blocks=None, thread_of: str = '', update_of: str = '',
               file: dict = None, who: str = 'notice') -> dict:
    """The event's data, the one copy of its shape. `file` is {name, content_type, data: bytes, title?}, encoded here;
    ValueError when it is over FILE_MAX."""
    data = {'key': key, 'channel': channel, 'text': text, 'who': who}
    if blocks:
        data['blocks'] = blocks
    if thread_of:
        data['thread_of'] = thread_of
    if update_of:
        data['update_of'] = update_of
    if file and file.get('data'):
        if len(file['data']) > FILE_MAX:
            raise ValueError(f'the file is over {FILE_MAX // (1024 * 1024)} MB')
        data['file'] = {'name': file.get('name') or 'file', 'content_type': file.get('content_type') or 'application/octet-stream',
                        'b64': base64.b64encode(file['data']).decode(), 'title': file.get('title') or ''}
    return data


def form_answer(method: str, token: str, params: dict, opener=None) -> dict:
    """A Web API call that takes form fields (`files.getUploadURLExternal`, `files.info`) through slack.form, as an
    answer that never raises: Slack's own, or {'ok': False, 'error': …} when Slack refuses or cannot be reached."""
    try:
        return slack.form(method, token, params, opener=opener, timeout=8)
    except slack.SlackError as e:   # the caller says what was not done
        return {'ok': False, 'error': e.error}


def json_answer(method: str, token: str, payload: dict, opener=None) -> dict:
    """One Web API call as JSON through slack.call; the same answer shape as form_answer."""
    try:
        return slack.call(method, token, payload, opener=opener, timeout=8)
    except slack.SlackError as e:
        return {'ok': False, 'error': e.error}


def share_ts(answer: dict, channel: str) -> str:
    """The share message's `ts` for `channel` in a Slack file answer (files.completeUploadExternal, files.info: the
    file's `shares`, public or private, by channel, each share with its `ts`); '' when the answer has none."""
    for f in (answer or {}).get('files') or ([answer.get('file')] if (answer or {}).get('file') else []):
        shares = (f or {}).get('shares') or {}
        for kind in ('public', 'private'):
            for entry in (shares.get(kind) or {}).get(channel) or []:
                if entry.get('ts'):
                    return str(entry['ts'])
    return ''


def upload(token: str, name: str, data: bytes, channel: str, thread_ts: str = '', title: str = '',
           json_=None, form_=None, opener=None, blocks=None) -> dict:
    """One file into `channel` (under `thread_ts` when given) by Slack's three-step upload (`files:write`): the upload
    URL for the name and size, the bytes POSTed to it, then the upload completed into the channel. The last answer, or
    the first refusal ({'ok': False, 'error': …}: `missing_scope` until the app has files:write, `not_in_channel` until
    the bot is invited). `blocks` is the share message's own Block Kit card, so the one message carries the file,
    unfurled, and the card (Slack's `blocks` on files.completeUploadExternal), every text in them redacted; the answer
    then carries `ts`, the share message's, read from the file's `shares` when Slack returns them, for the caller to
    thread under."""
    opener = opener or urllib.request.urlopen
    got = (form_ or form_answer)('files.getUploadURLExternal', token, {'filename': name, 'length': len(data)}, opener)
    if not got.get('ok') or not got.get('upload_url') or not got.get('file_id'):
        return got if got.get('error') else {'ok': False, 'error': 'no upload url'}
    try:
        with opener(urllib.request.Request(got['upload_url'], data=data, method='POST',
                                           headers={'Content-Type': 'application/octet-stream'}), timeout=20):
            pass
    except Exception as e:   # noqa: BLE001 - the bytes did not get there
        return {'ok': False, 'error': f'upload: {type(e).__name__}'}
    done = {'files': [{'id': got['file_id'], 'title': title or name}], 'channel_id': channel}
    if thread_ts:
        done['thread_ts'] = thread_ts
    if blocks:
        done['blocks'] = json.loads(_redact(json.dumps(blocks)))
    answer = (json_ or json_answer)('files.completeUploadExternal', token, done, opener)
    ts = share_ts(answer, channel)
    return dict(answer, ts=ts) if ts else answer


def post_message(token: str, channel: str, text: str, blocks, thread_ts: str) -> dict:
    """chat.postMessage through slack.post, the text and blocks redacted; {'ok': False, 'error': …} on a refusal."""
    try:
        got = slack.post(token, channel, _redact(text), blocks=json.loads(_redact(json.dumps(blocks))) if blocks else None,
                         thread_ts=thread_ts or None, timeout=15)
    except slack.SlackError as e:
        return {'ok': False, 'error': e.error or f'HTTP {e.status}'}
    return dict(got, ok=True)


def kept(store, collection: str, key: str, token: str = '', info=None) -> dict:
    """The post kept under `key` (its channel and ts). A post kept with no ts but its file's id (Slack had not filled
    the share when it was posted) is read once more from files.info now, a phase later, and the ts kept once it has
    come. The store out of reach is a Retry."""
    try:
        got = store.get(collection, key) or {}
    except Exception as e:   # noqa: BLE001 - the store out of reach: the delivery is tried again
        raise Retry(f'Firestore could not be read ({type(e).__name__})') from None
    if got and not got.get('ts') and got.get('file') and token:
        answer = (info or form_answer)('files.info', token, {'file': got['file']})
        ts = share_ts(answer, str(got.get('channel') or '')) if answer.get('ok') else ''
        if ts:
            got = dict(got, ts=ts)
            try:
                store.set(collection, key, got)
            except Exception:   # noqa: BLE001 - this phase has the ts; the next reads files.info again
                pass
    return got


def deliver(data: dict, token: str, store, collection: str, resolve=None, now=None, upload=None, post=None,
            update=None, info=None, sleep=None) -> list:
    """The listener's answer to one post event: the lines it logs. Claims <collection>/<key> first (a repeated
    delivery is acknowledged with nothing posted), resolves the channel (`resolve(channel) -> id`, the host's roles;
    the value as given without one) and the thread, posts (the upload with the blocks when there is a file, else
    chat.postMessage; chat.update for `update_of`), and keeps the post's ts under the key. Raises Retry for what a
    redelivery may fix (Slack or the store unreachable, a refusal not in FINAL_ERRORS), the claim released first; a
    refusal that will not change is an ERROR line and the claim stays, so nothing posts twice. `upload`, `post`,
    `update` (method, token, payload), `info` (method, token, params) and `sleep` are the suites' seams."""
    key = str(data.get('key') or '').strip()
    who = str(data.get('who') or 'slack post')
    if not key:
        return [f'WARNING: slack post: {who}: no key; nothing posted']
    token = (token or '').strip()
    if not token:
        return [f'ERROR: slack post: {who}: {key}: the Slack app\'s function holds no SLACK_BOT_TOKEN; nothing posted']
    now = now or datetime.now(timezone.utc)   # utc-ok: an instant, stamped on the record
    update_of = str(data.get('update_of') or '')
    claim_key = f'{key}--update-{now.strftime("%Y%m%dT%H%M%S")}' if update_of else key
    try:
        fresh = store.create(collection, claim_key, {'key': key, 'who': who, 'at': now.isoformat()})
    except Exception as e:   # noqa: BLE001
        raise Retry(f'Firestore could not be reached ({type(e).__name__})') from None
    if not fresh:
        return [f'slack post: {who}: {key} already posted; acknowledged']
    try:
        return _post(data, key, who, token, store, collection, resolve, upload, post, update, info, now, update_of, sleep)
    except Final as e:
        return [str(e)]
    except Retry:
        try:
            store.delete(collection, claim_key)
        except Exception:   # noqa: BLE001 - the claim left behind makes the redelivery a no-op: said in the raise
            raise Retry('the post was not made and its claim could not be released; a redelivery will be a no-op') from None
        raise


def _post(data, key, who, token, store, collection, resolve, upload_, post, update, info, now, update_of, sleep=None) -> list:
    lines = []
    channel = str(data.get('channel') or '').strip()
    channel = resolve(channel) if resolve else channel
    text = str(data.get('text') or '')
    blocks = data.get('blocks') or None
    thread_ts = ''
    if update_of:
        was = kept(store, collection, update_of, token, info)
        if not (was.get('ts') and was.get('channel')):
            return [f'WARNING: slack post: {who}: {key} redraws {update_of}, which has no post on record; nothing updated']
        got = (update or json_answer)('chat.update', token, {'channel': was['channel'], 'ts': was['ts'], 'text': text,
                                                             **({'blocks': blocks} if blocks else {})})
        if not got.get('ok'):
            _refused(who, key, got.get('error') or 'refused', 'redrawn')
        return [f'slack post: {who}: {key} redrew {update_of} in {was["channel"]}']
    if data.get('thread_of'):
        was = kept(store, collection, str(data['thread_of']), token, info)
        if was.get('ts') and was.get('channel'):
            thread_ts, channel = was['ts'], was['channel']
        else:
            lines.append(f'WARNING: slack post: {who}: {key} threads under {data["thread_of"]}, which has no post on record; posted plain')
    if not channel:
        return lines + [f'ERROR: slack post: {who}: {key}: no channel ({data.get("channel") or "none"} names none); nothing posted']
    ts, ok, late_file = '', False, ''
    file = data.get('file') or {}
    if file.get('b64'):
        try:
            raw = base64.b64decode(file['b64'])
        except Exception:   # noqa: BLE001
            raw = b''
        if raw and len(raw) <= FILE_MAX:
            name = str(file.get('name') or 'file')
            up = (upload_ or upload)(token, name, raw, channel, thread_ts, str(file.get('title') or name), blocks=blocks)
            if up.get('ok'):
                ok, ts = True, str(up.get('ts') or '')
                if not ts:
                    fid = ((up.get('files') or [{}])[0] or {}).get('id') or ''
                    # files.info takes form or query parameters, never a JSON body (Slack answers invalid_arguments),
                    # so the form call, as files.getUploadURLExternal's is. Slack fills the file's shares a moment
                    # after the upload completes, so it is read again, SHARE_READS times, SHARE_WAIT_S apart.
                    got = {}
                    for attempt in range(SHARE_READS if fid else 0):
                        if attempt:
                            (sleep or time.sleep)(SHARE_WAIT_S)
                        got = (info or form_answer)('files.info', token, {'file': fid})
                        ts = share_ts(got, channel) if got.get('ok') else ''
                        if ts or (got.get('error') in FINAL_ERRORS):
                            break
                    if not ts and fid and got.get('error') not in FINAL_ERRORS:
                        # Slack took longer than SHARE_READS reads to fill the share: the file's id is kept under the
                        # key, and the post's next phase reads the ts then (kept).
                        late_file = fid
                        lines.append(f"WARNING: slack post: {who}: {key}: the post's ts was not read yet ({got.get('error') or 'no shares'}); "
                                     f"the file's id is kept and the post's next phase reads it")
                    elif not ts:
                        lines.append(f"ERROR: slack post: {who}: {key}: the post's ts was not read ({got.get('error') or 'no shares'}); "
                                     f"its later phases post plain and its state line is never redrawn")
            else:
                why = up.get('error') or 'refused'
                if why.startswith('upload:') or why not in FINAL_ERRORS:
                    raise Retry(f'the upload was refused ({why})')
                fix = {'missing_scope': '; add files:write to the Slack app and reinstall it',
                       'not_in_channel': '; invite the bot to the channel'}.get(why, '')
                lines.append(f'ERROR: slack post: {who}: {key}: the file was not put on the post ({why}{fix}); the card posts alone')
        else:
            lines.append(f'WARNING: slack post: {who}: {key}: the file was unreadable or over {FILE_MAX // (1024 * 1024)} MB; the card posts alone')
    if not ok:
        got = (post or post_message)(token, channel, text, blocks, thread_ts)
        if not got.get('ok'):
            _refused(who, key, got.get('error') or 'refused', 'posted')
        ok, ts = True, str(got.get('ts') or '')
    try:
        store.set(collection, key, {'key': key, 'who': who, 'channel': channel, 'ts': ts, 'at': now.isoformat(),
                                    **({'file': late_file} if late_file else {})})
    except Exception as e:   # noqa: BLE001 - the post stands; a later thread or redraw on it posts plain
        lines.append(f'WARNING: slack post: {who}: {key} posted but not kept ({type(e).__name__}); later events on it post plain')
    return lines + [f'slack post: {who}: {key} posted in {channel}' + (' (threaded)' if thread_ts else '')]


def _refused(who: str, key: str, why: str, what: str):
    """A Slack refusal: Retry when a redelivery may change it, else an ERROR raised as a final line."""
    if why in FINAL_ERRORS:
        raise Final(f'ERROR: slack post: {who}: {key} was not {what} ({why})')
    raise Retry(f'Slack refused the post ({why})')
