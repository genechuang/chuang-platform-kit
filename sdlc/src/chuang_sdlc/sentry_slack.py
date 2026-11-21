"""A Sentry alarm as a Slack card: the post, its buttons, what a tap changes in Sentry, and the redraw when the issue
changes anywhere. Sentry's own Slack integration needs its Team plan; this is what a host's function rebuilds it from.

The card is the alarm's text, a status line (where the issue stands, who it is assigned to, the last thing done and by
whom) and, unless left out, the buttons: Resolve, Resolve in next release, Archive (until it escalates), Unresolve once
it is either, an Assign menu of the host's people (a Sentry member, assigned in Sentry) and its sessions (no Sentry
user: a note on the issue names who it was handed to), and Open in Sentry.

The post's record (its channel, ts, text and last change) is one document a issue, claimed create-only so a retry or a
rule firing twice posts once; the host hands this module the document (any object with `create`, `get`, `set`). A
change made from Slack comes back from Sentry's webhook within `ECHO_SECONDS` under the token owner's name, and is
known by the record's last change, so it never redraws the card a second time.

Every name a person reads that is the host's (its people, its sessions, the app's name) is an argument. Slack calls go
through the kit's `slack` client, and Slack's signature through its `verify_request`.
"""
import re
import urllib.request

from chuang_platform_kit import slack

TIMEOUT = 8      # seconds: a function answering Slack's own request has three to spare
MAX_SKEW = 300   # seconds: Slack's own replay window
ECHO_SECONDS = 60
LIFECYCLE = ('resolved', 'ignored', 'archived', 'assigned', 'unresolved')
ISSUE_LINK = re.compile(r'https://[\w.-]*sentry\.io/(?:organizations/[\w-]+/)?issues/(\d+)')


# -- what arrives --------------------------------------------------------------------------------------------------

def verify_slack(headers, body: bytes, secret: str, now=None) -> bool:
    """True when the request is Slack's: `X-Slack-Signature` is v0=HMAC-SHA256 of `v0:<timestamp>:<body>` under the
    app's signing secret, and the timestamp is within five minutes (a replayed request is refused). The kit's one copy,
    `slack.verify_request` (chuang-platform-kit 0.45.0)."""
    return slack.verify_request(headers, body, secret, now=now, max_skew=MAX_SKEW)


def issue_id_of(resource: str, payload: dict):
    """The Sentry issue id a Sentry webhook is about (a new issue, a lifecycle change, a fired alert rule), or None
    for one a card does not act on. The action itself is `payload['action']`."""
    data = payload.get('data') or {}
    action = payload.get('action')
    if resource == 'issue' and (action == 'created' or action in LIFECYCLE):
        found = (data.get('issue') or {}).get('id')
    elif resource == 'event_alert' and action == 'triggered':
        found = (data.get('event') or {}).get('issue_id')
    else:
        return None
    return str(found) if found and str(found).isdigit() else None


def is_feedback(issue: dict) -> bool:
    """A Sentry User Feedback item (an issue of category `feedback`): never an alarm card."""
    return str(issue.get('issueCategory') or issue.get('issueType') or '').lower() == 'feedback'


def tapped(payload: dict):
    """(action_id, value, who, channel, ts) of a Slack block_actions payload, or None."""
    if (payload or {}).get('type') != 'block_actions' or not payload.get('actions'):
        return None
    a = payload['actions'][0]
    value = a.get('value') or ((a.get('selected_option') or {}).get('value')) or ''
    user = payload.get('user') or {}
    return (a.get('action_id') or '', value, user.get('name') or user.get('username') or 'someone',
            (payload.get('channel') or {}).get('id') or (payload.get('container') or {}).get('channel_id') or '',
            (payload.get('message') or {}).get('ts') or (payload.get('container') or {}).get('message_ts') or '')


def issue_ids(event: dict) -> list:
    """The Sentry issue ids in a Slack link_shared event's links, each with its link as Slack sent it: [(url, id)]."""
    out = []
    for link in (event or {}).get('links') or []:
        m = ISSUE_LINK.match(str(link.get('url') or ''))
        if m:
            out.append((link['url'], m.group(1)))
    return out


def first_section(message: dict) -> str:
    """The text of a Slack message's first section block (a card posted before its record was kept)."""
    for b in (message or {}).get('blocks') or []:
        if b.get('type') == 'section':
            return (b.get('text') or {}).get('text') or ''
    return ''


# -- the card ------------------------------------------------------------------------------------------------------

def status_line(issue: dict, last: dict = None) -> str:
    """Where the issue stands, who it is assigned to, and the last thing done to it from Slack or Sentry and by whom."""
    status = (issue or {}).get('status') or 'unresolved'
    details = (issue or {}).get('statusDetails') or {}
    if status == 'resolved':
        state = ':white_check_mark: Resolved in the next release' if details.get('inNextRelease') else ':white_check_mark: Resolved'
    elif status == 'ignored':
        state = ':file_cabinet: Archived until it escalates'
    else:
        state = ':red_circle: Unresolved'
    assignee = (issue or {}).get('assignedTo')
    who = assignee.get('name') if isinstance(assignee, dict) else None   # Sentry's {type, id, name}
    parts = [state] + ([f'assigned to {who}'] if who else [])
    if (last or {}).get('text'):
        parts.append(f"{last['text']} by {last.get('by') or 'someone'}, {last.get('at') or ''}".rstrip(', '))
    return ' · '.join(parts)


def _button(text, action, value, style=None):
    b = {'type': 'button', 'text': {'type': 'plain_text', 'text': text}, 'action_id': action, 'value': value}
    return dict(b, style=style) if style else b


def blocks(issue: dict, text: str, last: dict = None, actions: bool = True, people=(), sessions=()) -> list:
    """The card: the alarm's text, where it stands, and (unless `actions` is False) the buttons and the assign menu,
    `people` [(name, key)] assigned in Sentry, then `sessions` handed a note."""
    issue_id = str((issue or {}).get('id') or '')
    link = (issue or {}).get('permalink') or ''
    out = [{'type': 'section', 'text': {'type': 'mrkdwn', 'text': text[:2900]}},
           {'type': 'context', 'elements': [{'type': 'mrkdwn', 'text': status_line(issue, last)}]}]
    if not actions:
        return out
    if (issue or {}).get('status') in ('resolved', 'ignored'):
        buttons = [_button('Unresolve', 'unresolve', issue_id)]
    else:
        buttons = [_button('Resolve', 'resolve', issue_id, 'primary'), _button('Resolve in next release', 'resolve_next', issue_id),
                   _button('Archive', 'archive', issue_id)]
    options = [{'text': {'type': 'plain_text', 'text': name}, 'value': f'{issue_id}|{value}'}
               for name, value in list(people) + [(s, f'session:{s}') for s in sessions]]
    menu = {'type': 'static_select', 'action_id': 'assign', 'placeholder': {'type': 'plain_text', 'text': 'Assign…'}, 'options': options}
    more = [{'type': 'button', 'text': {'type': 'plain_text', 'text': 'Open in Sentry'}, 'url': link, 'action_id': 'open'}] if link else []
    out.append({'type': 'actions', 'block_id': f'sentry-{issue_id}', 'elements': buttons + ([menu] if options else []) + more})
    return out


def change_for(action_id: str, value: str, people=(), sessions=()):
    """(issue id, Sentry change, what to say) for a tapped button or menu choice; (id, None, …) for a hand-off to a
    session, which is a note, not a change; None for anything else (Open in Sentry is only a link). A person's
    change is `{'assignedTo': <their key>}`, which the host turns into Sentry's `user:<id>` (assignee_for)."""
    issue_id, _, choice = (value or '').partition('|')
    if not issue_id.isdigit():
        return None
    if action_id == 'resolve':
        return issue_id, {'status': 'resolved', 'statusDetails': {}}, 'Resolved'
    if action_id == 'resolve_next':
        return issue_id, {'status': 'resolved', 'statusDetails': {'inNextRelease': True}}, 'Resolved in the next release'
    if action_id == 'archive':
        return issue_id, {'status': 'ignored', 'substatus': 'archived_until_escalating', 'statusDetails': {}}, 'Archived'
    if action_id == 'unresolve':
        return issue_id, {'status': 'unresolved', 'statusDetails': {}}, 'Unresolved'
    if action_id == 'assign':
        for name, key in people:
            if choice == key:
                return issue_id, {'assignedTo': key}, f'Assigned to {name}'
        if choice.startswith('session:') and choice[8:] in sessions:
            return issue_id, None, f'Handed to {choice[8:]}'
    return None


def change_kind(update: dict) -> str:
    """The Sentry webhook action a change will echo back as ('assigned', 'resolved', 'archived', 'unresolved'), ''
    for none."""
    if not update:
        return ''
    if 'assignedTo' in update:
        return 'assigned'
    return {'resolved': 'resolved', 'ignored': 'archived', 'unresolved': 'unresolved'}.get(update.get('status'), '')


def assignee_for(members, first_name: str) -> str:
    """Sentry's `user:<id>` for the project member whose name starts with `first_name`, '' when none (a membership
    row's own `id` is the membership's; the assignee is its `user.id`)."""
    for m in members if isinstance(members, list) else []:
        user_id = (m.get('user') or {}).get('id')
        if str(m.get('name') or '').split(' ')[0].lower() == first_name.lower() and user_id:
            return f'user:{user_id}'
    return ''


def is_echo(action: str, last: dict, now: float, window: int = ECHO_SECONDS) -> bool:
    """Whether a Sentry lifecycle webhook is the echo of the card's own last change from Slack: the same kind (archive
    and ignore being one) within `window` seconds of it."""
    last = last or {}
    same = action == last.get('kind') or {action, last.get('kind')} == {'ignored', 'archived'}
    return bool(same and now - int(last.get('epoch') or 0) < window)


# -- the post record and Slack ---------------------------------------------------------------------------------------

def api(method: str, token: str, payload: dict, opener=urllib.request.urlopen) -> dict:
    """One Slack Web API call as JSON through the kit's client; {'ok': False, 'error': …} (Slack's error, else the
    failure's kind) when Slack refuses or cannot be reached."""
    try:
        return slack.call(method, token, payload, opener=opener, timeout=TIMEOUT)
    except slack.SlackError as e:
        return {'ok': False, 'error': e.error}


def claim(doc, record: dict) -> bool:
    """True when this call is the first to post the issue (the document did not exist and now does)."""
    try:
        doc.create(record)
        return True
    except Exception as e:   # noqa: BLE001 - AlreadyExists is the expected refusal
        if type(e).__name__ in ('AlreadyExists', 'Conflict'):
            return False
        raise


def kept(doc, warn=None) -> dict:
    """What the record keeps about a post ({channel, ts, text, last}); {} when there is none or it cannot be read
    (`warn(line)` says so)."""
    try:
        snap = doc.get()
        return (snap.to_dict() or {}) if snap.exists else {}
    except Exception as e:   # noqa: BLE001 - a redraw that cannot find its post is skipped, not failed
        if warn:
            warn(f'could not read the post record: {type(e).__name__}')
        return {}


def keep(doc, fields: dict, warn=None) -> None:
    """Merge `fields` into the record (the post's channel and ts, its text, the last change)."""
    try:
        doc.set(fields, merge=True)
    except Exception as e:   # noqa: BLE001 - the post stands; only a later redraw loses it
        if warn:
            warn(f'could not keep the post record: {type(e).__name__}')


def redraw(issue: dict, record: dict, token: str, slack_api=api, people=(), sessions=(), warn=None) -> bool:
    """Draw the issue's card again from Sentry's current state; False when there is no post to redraw or Slack
    refused it."""
    if not (token and record.get('channel') and record.get('ts') and record.get('text')):
        return False
    got = slack_api('chat.update', token, {'channel': record['channel'], 'ts': record['ts'], 'text': record['text'],
                                           'blocks': blocks(issue, record['text'], record.get('last'),
                                                            people=people, sessions=sessions)})
    if not got.get('ok') and warn:
        warn(f"Slack did not redraw {issue.get('shortId')}: {got.get('error')}")
    return bool(got.get('ok'))
