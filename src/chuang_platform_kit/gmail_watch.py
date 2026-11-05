"""Gmail's push as a webhook, and the labels and filters it rests on as code (SMAD PickleBot #969; Gene, 11/5/26 1:03 AM
PT: "We are using Gmail Watch as a Webhook in at least 3 places now. Can we extract this infrastructure out to
Chuang-kit so we reuse code and best practices? And can Gmail filter be programmatically created").

The flow every user shares: `watch` asks Gmail to publish a mailbox's changes (all of them, or a label's) on a Pub/Sub
topic that Google's own account may publish to (gmail-api-push@system.gserviceaccount.com); the topic's push
subscription calls the host; `push` decodes the delivery ({emailAddress, historyId}); `changes` reads what was added
since the host's stored cursor (users.history.list), and when Gmail answers 404 for a cursor too old to replay, catches
up with a search (`resync_query`) instead of losing the mail, the lesson of ChuangFinance's financewatch. A watch lasts
7 days: `renew` restarts it once `renew_due` says under RENEW_BEFORE_S is left, from whatever wakes the host daily or
hourly (a clock event, a scheduler), never a poll of the mailbox.

Filters as code: Gmail filters cannot be edited, only made and deleted, and no Terraform provider keeps them
(Google's manages none; the one community provider's last release is 2021). A host declares its labels and filters in
a JSON file, `[{"label": "Google Play Console", "from": "noreply-play-console@google.com"}]` (any of from, to,
subject, query, negatedQuery, hasAttachment; optional "archive": true, "markRead": true), and `apply` makes each label
that is missing and each filter whose criteria and label have no exact match; a filter that adds a declared label with
other criteria is the old version of a declared one and is deleted. Filters for labels the file never names are left
alone. `python -m chuang_platform_kit.gmail_watch apply FILE --secret PROJECT:SECRET [--dry-run]`.

All over REST with the standard library, on a `gmail.Mailbox`'s token and opener (its `access_token`), so a slim host
needs no client library. Scopes: reading gmail.readonly, filters and labels gmail.settings.basic (labels.create needs
gmail.labels or gmail.modify when settings.basic alone is refused). No message body, address list or token is logged.
"""
import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from .gmail import message

API = 'https://gmail.googleapis.com/gmail/v1/users/me'
RENEW_BEFORE_S = 36 * 3600     # renew a 7-day watch once less than this is left: a daily wake has a day and a half
GMAIL_PUSH = 'gmail-api-push@system.gserviceaccount.com'   # the account a watch's topic lets publish
CRITERIA = ('from', 'to', 'subject', 'query', 'negatedQuery', 'hasAttachment')

log = logging.getLogger(__name__)


class WatchFailed(RuntimeError):
    """Gmail refused or could not be reached: `status` Google's HTTP code (0 when unreached), never a token."""

    def __init__(self, text: str, status: int = 0):
        super().__init__(text)
        self.status = status


def call(mb, method: str, path: str, body=None, query=None) -> dict:
    """One Gmail REST call on the mailbox's token: the answer's JSON ({} for none), or WatchFailed."""
    url = f'{API}/{path}' + ('?' + urllib.parse.urlencode(query, doseq=True) if query else '')
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={'Authorization': f'Bearer {mb.access_token()}', 'Content-Type': 'application/json'})
    try:
        with (mb.opener or urllib.request.urlopen)(req, timeout=mb.timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise WatchFailed(f'Gmail answered {e.code} to {method} {path.split("?")[0]}', e.code)
    except Exception as e:   # noqa: BLE001 - unreached is one kind to the caller
        raise WatchFailed(f'Gmail could not be reached ({type(e).__name__})')
    return json.loads(raw.decode()) if raw else {}


def watch(mb, topic: str, labels=(), behavior: str = 'INCLUDE') -> dict:
    """Gmail's push of the mailbox's changes onto `topic` (projects/<p>/topics/<t>), limited to `labels` (ids) when
    given: {'history_id', 'expiration'} (expiration in epoch ms, as Gmail says it)."""
    body = {'topicName': topic}
    if labels:
        body.update(labelIds=list(labels), labelFilterBehavior=behavior)
    got = call(mb, 'POST', 'watch', body)
    return {'history_id': str(got.get('historyId') or ''), 'expiration': int(got.get('expiration') or 0)}


def renew_due(expiration_ms, now_ms: int, before_s: int = RENEW_BEFORE_S) -> bool:
    """Whether a watch needs (re)starting: none recorded, or under `before_s` left."""
    return int(expiration_ms or 0) - int(now_ms) < before_s * 1000


def renew(mb, state: dict, topic: str, labels=(), now_ms: int = 0, force: bool = False) -> dict:
    """The host's watch record ({'history_id', 'expiration', ...}) renewed when due (or `force`): the new record, the
    stored cursor kept when there is one (a renewal must not skip what arrived since), or {} when nothing was due."""
    import time
    now_ms = now_ms or int(time.time() * 1000)
    if not force and not renew_due((state or {}).get('expiration'), now_ms):
        return {}
    got = watch(mb, topic, labels)
    return {**(state or {}), 'expiration': got['expiration'], 'history_id': (state or {}).get('history_id') or got['history_id']}


def push(delivery: dict) -> dict:
    """A Pub/Sub push delivery's data ({emailAddress, historyId} for Gmail's; any JSON object for a host's own
    message) and its `attributes` under the key '_attributes'; {} for anything else."""
    msg = (delivery or {}).get('message') or {}
    if not msg:
        return {}
    try:
        data = json.loads(base64.b64decode(msg.get('data') or '') or b'{}')
    except ValueError:
        data = {}
    out = data if isinstance(data, dict) else {}
    return {**out, '_attributes': msg.get('attributes') or {}}


def is_gmail_push(data: dict) -> bool:
    """Whether a `push` result is Gmail's own notification."""
    return bool((data or {}).get('emailAddress') and (data or {}).get('historyId'))


def _meta(mb, mid: str) -> dict:
    got = call(mb, 'GET', f'messages/{mid}', query=[('format', 'metadata')] + [('metadataHeaders', h) for h in
                                                                            ('From', 'To', 'Subject', 'Date')])
    return message(got)


def changes(mb, cursor: str, label: str = '', resync_query: str = '') -> tuple:
    """([gmail.message() dicts], the new cursor) for the messages added since `cursor` (in `label` when given). A
    cursor Gmail can no longer replay (404) is caught up with `resync_query` (a Gmail search, e.g.
    'label:"Google Play Console" newer_than:8d'), its matches returned once each with the mailbox's current history id
    as the new cursor; with no `resync_query` the 404 is raised (WatchFailed, status 404)."""
    ids, newest, token = [], str(cursor or ''), ''
    try:
        while True:
            q = {'startHistoryId': cursor, 'historyTypes': 'messageAdded'}
            if label:
                q['labelId'] = label
            if token:
                q['pageToken'] = token
            got = call(mb, 'GET', 'history', query=q)
            ids += [m['message']['id'] for h in got.get('history') or [] for m in h.get('messagesAdded') or []]
            newest = str(got.get('historyId') or newest)
            token = got.get('nextPageToken') or ''
            if not token:
                break
    except WatchFailed as e:
        if e.status != 404 or not resync_query:
            raise
        log.warning('gmail_watch: the cursor is too old to replay; catching up by search')
        got = call(mb, 'GET', 'messages', query={'q': resync_query, 'maxResults': 100})
        ids = [m['id'] for m in got.get('messages') or []]
        newest = str(call(mb, 'GET', 'profile').get('historyId') or newest)
    return [_meta(mb, mid) for mid in dict.fromkeys(ids)], newest


# Labels and filters as code.

def labels(mb) -> dict:
    """{name: id} of the mailbox's labels."""
    return {lab['name']: lab['id'] for lab in call(mb, 'GET', 'labels').get('labels') or []}


def _criteria(spec: dict) -> dict:
    return {k: spec[k] for k in CRITERIA if spec.get(k) not in (None, '')}


def _action(spec: dict, label_id: str) -> dict:
    remove = (['INBOX'] if spec.get('archive') else []) + (['UNREAD'] if spec.get('markRead') else [])
    return {'addLabelIds': [label_id], **({'removeLabelIds': remove} if remove else {})}


def plan(declared: list, have_labels: dict, have_filters: list) -> dict:
    """What `apply` would do, as {'labels': [names to make], 'create': [(spec)], 'delete': [filter ids], 'kept': n}:
    pure, for a dry run and the suite. A label not yet made has the id '' until it is."""
    names = {d['label'] for d in declared}
    make = sorted(n for n in names if n not in have_labels)
    managed = {have_labels[n]: n for n in names if n in have_labels}
    create, matched = [], set()
    for d in declared:
        lid = have_labels.get(d['label'], '')
        want_c, want_a = _criteria(d), _action(d, lid) if lid else None
        hit = next((f for f in have_filters if lid and f.get('criteria') == want_c
                    and sorted((f.get('action') or {}).get('addLabelIds') or []) == sorted(want_a['addLabelIds'])
                    and sorted((f.get('action') or {}).get('removeLabelIds') or []) == sorted(want_a.get('removeLabelIds') or [])), None)
        if hit:
            matched.add(hit['id'])
        else:
            create.append(d)
    delete = [f['id'] for f in have_filters if f['id'] not in matched
              and any(l in managed for l in (f.get('action') or {}).get('addLabelIds') or [])]
    return {'labels': make, 'create': create, 'delete': delete, 'kept': len(matched)}


def apply(mb, declared: list, dry_run: bool = False) -> dict:
    """The declared labels and filters made so (module docstring); the plan, with 'done' True once carried out."""
    have_labels = labels(mb)
    have_filters = call(mb, 'GET', 'settings/filters').get('filter') or []
    p = plan(declared, have_labels, have_filters)
    if dry_run:
        return {**p, 'done': False}
    for name in p['labels']:
        have_labels[name] = call(mb, 'POST', 'labels', {'name': name, 'labelListVisibility': 'labelShow',
                                                         'messageListVisibility': 'show'})['id']
    for fid in p['delete']:
        call(mb, 'DELETE', f'settings/filters/{fid}')
    for d in p['create']:
        call(mb, 'POST', 'settings/filters', {'criteria': _criteria(d), 'action': _action(d, have_labels[d['label']])})
    return {**p, 'done': True}


def main(argv=None) -> int:
    import argparse
    from .gmail import Mailbox
    ap = argparse.ArgumentParser(description='Gmail labels and filters from a declared file')
    sub = ap.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('apply')
    a.add_argument('file')
    a.add_argument('--secret', default='', help='PROJECT:SECRET holding the authorized-user token')
    a.add_argument('--token-file', default='')
    a.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)
    with open(args.file, encoding='utf-8') as f:
        declared = json.load(f)
    declared = declared.get('filters', declared) if isinstance(declared, dict) else declared
    sources = {'rest': True}
    if args.secret:
        project, _, name = args.secret.partition(':')
        sources['secret'] = (project, name)
    if args.token_file:
        sources['token_file'] = args.token_file
    got = apply(Mailbox(**sources), declared, dry_run=args.dry_run)
    print(f"gmail_watch: labels to make {got['labels']}, filters to create {len(got['create'])}, to delete "
          f"{len(got['delete'])}, kept {got['kept']}" + ('' if got['done'] else ' (dry run: nothing changed)'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
