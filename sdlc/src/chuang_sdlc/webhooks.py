"""What one of GitHub's webhook deliveries means for the ticket tools, decided from the delivery alone: the pure half of
a host's receiver, which then does what the decision says (a sweep, a letter, an event on its bus) and says its line.

  comment_route   an `issue_comment`: an owner's `Scrum:` answer (or the lead's plain reply on a card assigned to
                  them), a `Triage:` line, or nothing to do, with the line that says why
  issue_route     an `issues` delivery: a close, a feedback ticket to its triager, an alert to its investigator, or
                  nothing (an investigation's own output, a work ticket a session filed)
  dock_landed     a push to the dock branch as the `dock.landed` event's data: the tickets its commits' `Ticket:`
                  trailers name
  project_item    a Projects (v2) item edit as the `ticket.moved` event's data before its one read, or why it is
                  ignored; `item_route` after the read: a placeholder put back, a draft that is no move, or a move
  delivery_age    how long after the item's change the delivery came, as a clause for the move's line

Standard library only (and this package's board for the trailers): a receiver's webhook imports it at once.
"""
import re
from datetime import datetime, timezone

from . import board as _board

# A `Triage:` line anywhere in a comment (a quote, a bullet, bold): the ticket sweep's to apply.
TRIAGE_LINE = re.compile(r'^[\s*_>]*Triage:', re.I | re.M)
SCRUM_MARKER = '**Scrum master**'   # the scrum master's own comments open with it (chuang_sdlc.scrum.MARKER)


def comment_route(payload: dict, owner: str, scrum_re, marker: str = SCRUM_MARKER, triage_re=TRIAGE_LINE) -> dict:
    """{route, number, comment, line}: `scrum` for an owner's answer to the scrum master (a `Scrum:` line, `scrum_re`
    a regex or a callable answering one, read only when needed; or the repository owner's plain reply on an issue
    assigned to them, which is no sweep's own comment, `marker`), `triage` for a `Triage:` line, `ignored` with the line
    otherwise. Only the repository owner's comment counts (`owner`, its login)."""
    comment = payload.get('comment') or {}
    number = (payload.get('issue') or {}).get('number')
    out = {'route': 'ignored', 'number': number, 'comment': comment, 'line': ''}
    if payload.get('action') != 'created':
        return dict(out, line=f"ignored: an issue_comment {payload.get('action') or 'event'}")
    author = ((comment.get('user') or {}).get('login') or '')
    if author.lower() != owner.lower():
        return dict(out, line=f'ignored: a comment on #{number} by {author or "nobody"}, not the owner')
    body = comment.get('body') or ''
    assigned = any((a.get('login') or '').lower() == author.lower()
                   for a in (payload.get('issue') or {}).get('assignees') or [])
    # A card assigned to the lead is waiting on them with its ask on record, so their plain reply on it (no Triage
    # line, no scrum master marker) is their answer, read by the scrum master at once.
    answer = assigned and marker not in body
    if (answer or (scrum_re() if callable(scrum_re) else scrum_re).search(body)) and not triage_re.search(body):
        return dict(out, route='scrum')
    if not triage_re.search(comment.get('body') or ''):
        return dict(out, line=f'ignored: a comment on #{number} with no Triage line')
    return dict(out, route='triage')


def _label_names(issue: dict) -> set:
    return {(label.get('name') or '').lower() for label in (issue.get('labels') or []) if isinstance(label, dict)}


def issue_route(payload: dict, skip_titles=(), feedback_mark=None, feedback_label: str = 'feedback',
                machine_label: str = 'machine', area_prefix: str = 'area:') -> dict:
    """{route, number, title, url, body, labels, reason, by, line}: `closed` for any close (`reason` its state reason,
    'completed' when none; `feedback` True for a feedback ticket's), `feedback` for a feedback ticket opened or reopened
    (its label, or a body `feedback_mark` matches, since the label may arrive a second later), `alert` for any other
    issue opened or reopened, `ignored` with the line for any other action, an investigation's own output (the
    `machine_label`, or a title opening with one of `skip_titles`), or a work ticket a session filed (an `area_prefix`
    label set at opening)."""
    action = payload.get('action') or 'event'
    issue = payload.get('issue') or {}
    number = issue.get('number')
    labels = _label_names(issue)
    out = {'route': 'ignored', 'number': number, 'title': (issue.get('title') or '').strip(),
           'url': issue.get('html_url') or '', 'body': issue.get('body') or '', 'labels': labels, 'reason': '',
           'by': ((payload.get('sender') or {}).get('login') or ''), 'line': '', 'feedback': feedback_label in labels}
    if action == 'closed':
        return dict(out, route='closed', reason=issue.get('state_reason') or 'completed')
    if action not in ('opened', 'reopened'):
        return dict(out, line=f'ignored: an issues {action}')
    if machine_label in labels or out['title'].startswith(tuple(skip_titles)):
        return dict(out, line=f'ignored: #{number} is an investigation\'s own output')
    if feedback_label in labels or (feedback_mark is not None and feedback_mark.search(out['body'])):
        return dict(out, route='feedback')
    if any(label.startswith(area_prefix) for label in labels):
        return dict(out, line=f'ignored: #{number} is a filed work ticket, not an alert')
    return dict(out, route='alert')


def dock_landed(payload: dict, numbers_of=None) -> dict:
    """The `dock.landed` data of a push to the dock branch, {sha, before, tickets, forced, by}, the tickets the commits'
    `Ticket:` trailers name (`numbers_of(message)`, else chuang_sdlc.board.ticket_numbers), sorted and each once; None
    for a push that deleted the branch."""
    sha = (payload.get('after') or '').strip()
    if not sha or sha == '0' * 40:
        return None
    numbers_of = numbers_of or _board.ticket_numbers
    tickets = sorted({n for c in payload.get('commits') or [] for n in numbers_of(c.get('message') or '')})
    return {'sha': sha, 'before': (payload.get('before') or '').strip(), 'tickets': tickets,
            'forced': bool(payload.get('forced')), 'by': ((payload.get('pusher') or {}).get('name') or '')}


def project_item(payload: dict, event_fields) -> tuple:
    """(data, line) for a Projects (v2) item delivery: the `ticket.moved` data before the item's read, {item, field,
    field_name, by, from}, and '' when it is a move to read; (None, the line) when it is ignored: any action but an
    edit, a field that is no single select, a single select outside `event_fields`, no node id."""
    action = payload.get('action') or 'event'
    item = payload.get('projects_v2_item') or {}
    change = ((payload.get('changes') or {}).get('field_value') or {})
    if action != 'edited':
        return None, f'ignored: a projects_v2_item {action}'
    if change.get('field_type') != 'single_select':
        return None, f'ignored: a projects_v2_item edited of a {change.get("field_type") or "non-field"} field'
    if change.get('field_name') and change.get('field_name') not in event_fields:
        return None, f'ignored: a projects_v2_item edited of its {change.get("field_name")}'
    node = item.get('node_id') or ''
    if not node:
        return None, 'ignored: a projects_v2_item with no node id'
    return ({'item': node, 'field': change.get('field_node_id') or '', 'field_name': change.get('field_name') or '',
             'by': ((payload.get('sender') or {}).get('login') or ''),
             'from': ((change.get('from') or {}).get('name') or '') if isinstance(change.get('from'), dict) else ''}, '')


def item_route(node: dict, field_name: str, moves, light_field: str = 'Light') -> str:
    """What a read item's move is, by the host's board helpers (`moves`: is_placeholder, title, number, column, stage,
    is_ship_card): `placeholder` for a column's placeholder card (put back, nothing sent), `app_status` for a draft
    card in the app status column that is not the ship card (no ticket, nobody to tell), `draft_light` for a draft's
    Light edit (never a move; an item that could not be read is taken for one), `send` for every other item."""
    if node and moves.is_placeholder(moves.title(node)):
        return 'placeholder'
    if (node and not moves.number(node) and moves.column(node) == moves.stage('app_status')
            and not moves.is_ship_card(node)):
        return 'app_status'
    if field_name == light_field and not moves.number(node):
        return 'draft_light'
    return 'send'


def delivery_age(updated_at, now=None) -> str:
    """How long after the item's change the webhook reached the receiver, as a clause for the move's line
    (`; GitHub delivered it 0.8 s after the change`): `updated_at` the payload's ISO stamp; '' when it is missing or
    unreadable."""
    if not updated_at:
        return ''
    try:
        changed = datetime.fromisoformat(str(updated_at).replace('Z', '+00:00'))
    except ValueError:
        return ''
    now = now or datetime.now(timezone.utc)   # utc-ok: an instant against GitHub's UTC stamp
    return f'; GitHub delivered it {max(0.0, (now - changed).total_seconds()):.1f} s after the change'
