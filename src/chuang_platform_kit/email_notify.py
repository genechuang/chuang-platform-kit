"""Supplemental email notifications for player reminders.

Players with an email address on file receive email copies of their
WhatsApp DM reminders. WhatsApp is always sent first; these are supplementary.

Uses Gmail API with OAuth 2.0 (same gmail-token.json as Gmail Watch).
For Cloud Functions, falls back to GMAIL_OAUTH_TOKEN_JSON env var.

Usage:
    from chuang_platform_kit.email_notify import maybe_send_extra_notifications

    # After sending WhatsApp DM:
    maybe_send_extra_notifications(player, message, subject="Vote Reminder", dry_run=dry_run)

The host names itself once: `configure(subject_prefix="SMAD Pickleball")`
(or EMAIL_SUBJECT_PREFIX in the environment) puts its name in front of every
subject; dated subjects use the kit's zone (clock.py). Nothing here knows
which project is sending.
"""

import base64
import json
import os
import re
import logging
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

# Global kill switch: email is ON by default. Set EMAIL_DISABLED=true to turn off.
EMAIL_DISABLED = os.environ.get('EMAIL_DISABLED', '').lower() in ('true', '1', 'yes')

# Gmail config
GMAIL_USERNAME = os.environ.get('GMAIL_USERNAME', '')
_reported_no_username = False   # see send_notification_email()
TOKEN_FILE = 'gmail-token.json'
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/contacts.readonly',
]

# The host's name in front of every subject, and the line under every
# notification's "This is an automated notification from <name>." footer:
# configure() or the environment.
SUBJECT_PREFIX = os.environ.get('EMAIL_SUBJECT_PREFIX', '')
FOOTER = os.environ.get('EMAIL_FOOTER', '')

from .clock import zone


def configure(subject_prefix: str = None, footer: str = None) -> None:
    """Tell the mailer who is sending: `subject_prefix` goes in front of every
    subject ("SMAD Pickleball - Vote Reminder") and in the footer's first line;
    `footer` is the plain-text line under it (a host's long name, say). A host
    calls this once, from its own config module, so the kit never carries a
    project's name."""
    global SUBJECT_PREFIX, FOOTER
    if subject_prefix is not None:
        SUBJECT_PREFIX = subject_prefix
    if footer is not None:
        FOOTER = footer


def subject_date() -> str:
    """Today's date in the kit's zone as MM/DD/YY, for dated email subjects."""
    return datetime.now(zone()).strftime('%m/%d/%y')


def _get_gmail_service():
    """Get authenticated Gmail API service.

    Tries gmail-token.json file first (CLI / GitHub Actions),
    falls back to GMAIL_OAUTH_TOKEN_JSON env var (Cloud Functions).
    """
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        logger.debug("Google API libraries not available, skipping email")
        return None

    creds = None

    # Try file-based token first
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception:
            pass

    # Fall back to env var (Cloud Functions)
    if not creds:
        token_json = os.environ.get('GMAIL_OAUTH_TOKEN_JSON', '')
        if token_json:
            try:
                token_data = json.loads(token_json)
                creds = Credentials.from_authorized_user_info(token_data, SCOPES)
            except Exception:
                pass

    if not creds:
        return None

    # Refresh if expired
    try:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Save refreshed token to file if possible (not in Cloud Functions)
            if os.path.exists(TOKEN_FILE):
                try:
                    with open(TOKEN_FILE, 'w') as f:
                        f.write(creds.to_json())
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"[Email] Gmail token refresh failed: {e}")

    if not creds.valid:
        logger.warning("[Email] Gmail credentials not valid after refresh attempt")
        return None

    try:
        return build('gmail', 'v1', credentials=creds)
    except Exception:
        return None


def _get_people_service():
    """Get authenticated Google People API service (reuses gmail OAuth token)."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        return None

    creds = None

    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception:
            pass

    if not creds:
        token_json = os.environ.get('GMAIL_OAUTH_TOKEN_JSON', '')
        if token_json:
            try:
                token_data = json.loads(token_json)
                creds = Credentials.from_authorized_user_info(token_data, SCOPES)
            except Exception:
                pass

    if not creds:
        return None

    try:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            if os.path.exists(TOKEN_FILE):
                try:
                    with open(TOKEN_FILE, 'w') as f:
                        f.write(creds.to_json())
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"[People API] Google token refresh failed: {e}")

    if not creds.valid:
        logger.warning("[People API] Google credentials not valid after refresh attempt")
        return None

    try:
        return build('people', 'v1', credentials=creds)
    except Exception:
        return None


def lookup_contact_by_phone(phone: str) -> tuple:
    """Look up a Google Contact display name and email by phone number.

    Uses connections.list to fetch all contacts and compares phone numbers
    after normalizing to 10-digit, so format differences like (703) 967-4074
    vs +17039674074 are handled correctly.

    Args:
        phone: 11-digit phone number (e.g. '17039674074')

    Returns:
        (display_name, email) tuple — each is an empty string if not found.
    """
    service = _get_people_service()
    if not service:
        return '', ''

    # Normalize to 10-digit for comparison (strip leading '1' from US numbers)
    digits = ''.join(c for c in phone if c.isdigit())
    ten_digit = digits[1:] if len(digits) == 11 and digits.startswith('1') else digits

    try:
        next_page_token = None
        while True:
            kwargs = dict(
                resourceName='people/me',
                personFields='names,phoneNumbers,emailAddresses',
                pageSize=1000,
            )
            if next_page_token:
                kwargs['pageToken'] = next_page_token

            result = service.people().connections().list(**kwargs).execute()

            for person in result.get('connections', []):
                for pn in person.get('phoneNumbers', []):
                    pn_digits = ''.join(c for c in pn.get('value', '') if c.isdigit())
                    pn_10 = pn_digits[1:] if len(pn_digits) == 11 and pn_digits.startswith('1') else pn_digits
                    if pn_10 == ten_digit:
                        name = ''
                        names = person.get('names', [])
                        if names:
                            name = names[0].get('displayName', '')
                        email = ''
                        emails = person.get('emailAddresses', [])
                        if emails:
                            email = emails[0].get('value', '')
                        return name, email

            next_page_token = result.get('nextPageToken')
            if not next_page_token:
                break
    except Exception as e:
        logger.debug(f"[Contacts] Lookup failed for {phone}: {e}")

    return '', ''


def whatsapp_to_html(text: str) -> str:
    """Convert WhatsApp formatted text to HTML.

    Converts *bold*, _italic_, ~strikethrough~, URLs, newlines to HTML.
    Preserves emoji for rich email display.
    """
    import html as html_module

    # Extract URLs BEFORE formatting (protects underscores in URLs from italic regex).
    # _(?=\S) allows mid-URL underscores (e.g. /reel/DTQS7_2kWMn/) but not trailing _ (italic closer).
    urls = []

    def _save_url(match):
        urls.append(match.group(0))
        return f'\x00URL{len(urls) - 1}\x00'

    text = re.sub(r'https?://(?:[^\s_*~`]|_(?=\S))+', _save_url, text)

    # Escape HTML entities
    text = html_module.escape(text)

    # Convert WhatsApp formatting to HTML
    text = re.sub(r'\*([^*]+)\*', r'<strong>\1</strong>', text)
    text = re.sub(r'_([^_]+)_', r'<em>\1</em>', text)
    text = re.sub(r'~([^~]+)~', r'<del>\1</del>', text)
    text = re.sub(r'```([^`]*)```', r'<pre style="font-family:monospace;white-space:pre">\1</pre>', text)

    # Restore URLs as clickable <a> tags
    for i, url in enumerate(urls):
        escaped = html_module.escape(url)
        text = text.replace(f'\x00URL{i}\x00', f'<a href="{escaped}">{escaped}</a>')

    # Collapse triple+ newlines to double (fixes empty-section gaps, preserves paragraph breaks)
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Convert newlines to <br>, but not inside <pre> blocks (pre preserves whitespace)
    parts = re.split(r'(<pre[^>]*>.*?</pre>)', text, flags=re.DOTALL)
    for i, part in enumerate(parts):
        if not part.startswith('<pre'):
            parts[i] = part.replace('\n', '<br>\n')
    text = ''.join(parts)

    return text


def send_message(service, to: str, subject: str, text: str, *, html: str = None, sender: str = None) -> str:
    """Send one message through a Gmail `service` the HOST built, and return Gmail's message id.

    The other senders here are shaped for a player reminder: they build their own Gmail service from a
    fixed scope list, wrap the body in a template, and answer True/False. A host that keeps its own OAuth
    token (ChuangFinance's watch holds readonly+send+drive; its collector holds send-only) and that RECORDS
    delivery -- the id is the proof an email left -- needs the opposite: its service in, the id out, the
    failure raised so it can be stored. Subject and body pass redact(), like every other exit. No prefix,
    footer, template or EMAIL_DISABLED switch: the caller states the subject it wants and decides whether
    to send. `html`, when given, is added as the alternative part; `sender` sets From (Gmail overrides it
    with the authenticated address anyway)."""
    from email.message import EmailMessage

    from .redact import redact

    msg = EmailMessage()
    msg['To'] = to
    if sender:
        msg['From'] = sender
    msg['Subject'] = redact(subject)
    msg.set_content(redact(text))
    if html is not None:
        msg.add_alternative(redact(html), subtype='html')
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode('utf-8')
    sent = service.users().messages().send(userId='me', body={'raw': raw}).execute()
    return sent.get('id')


def send_notification_email(email: str, subject: str, body_html: str, dry_run: bool = False) -> bool:
    """Send email notification via Gmail API.

    Args:
        email: Recipient email address
        subject: Email subject line
        body_html: HTML body content
        dry_run: If True, log but don't send

    Returns:
        True if sent (or dry_run), False on error
    """
    if not email:
        return False
    # No token or key ever reaches an inbox (2026-09-25 PT: two alert emails
    # carried the GREEN-API token in full).
    from .redact import redact
    subject, body_html = redact(subject), redact(body_html)

    if EMAIL_DISABLED:
        logger.debug("[Email] EMAIL_DISABLED is set, skipping email")
        return False

    if dry_run:
        return True

    if not GMAIL_USERNAME:
        # ERROR, ONCE, NAMING THE VARIABLE -- CLAUDE.md: "An empty config value
        # must never take a silent branch." This was a per-email WARNING, which
        # is how three workflows (game-reminder, cancel-game,
        # payment-reminder-single) DMed every player and emailed nobody, with
        # green runs, until 2026-09-04 PT: the warning scrolled past once per
        # player and nothing summarised it. One ERROR at the first skip says
        # what every later skip in this process means.
        global _reported_no_username
        if not _reported_no_username:
            _reported_no_username = True
            logger.error("GMAIL_USERNAME is not set: EVERY email in this run is "
                         "being skipped. WhatsApp DMs still go; their email "
                         "copies do not. Wire vars.GMAIL_USERNAME and the two "
                         "GMAIL_OAUTH_* secrets into this workflow's .env step.")
        return False

    gmail_service = _get_gmail_service()
    if not gmail_service:
        logger.warning("Gmail API not available, skipping email")
        return False

    try:
        # Wrap body in basic HTML template
        full_html = f"""<html>
<head><style>
body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; padding: 20px; }}
.footer {{ margin-top: 20px; padding-top: 15px; border-top: 1px solid #dee2e6; font-size: 12px; color: #6c757d; }}
</style></head>
<body>
{body_html}
<div class="footer">
<p>This is an automated notification{(' from ' + SUBJECT_PREFIX) if SUBJECT_PREFIX else ''}.{('<br>' + chr(10) + FOOTER) if FOOTER else ''}</p>
</div>
</body>
</html>"""

        msg = MIMEMultipart('alternative')
        msg['From'] = GMAIL_USERNAME
        msg['To'] = email
        msg['Subject'] = subject
        msg.attach(MIMEText(full_html, 'html'))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode('utf-8')
        gmail_service.users().messages().send(
            userId='me',
            body={'raw': raw}
        ).execute()

        logger.info(f"[Email] Sent to {email}")
        return True

    except Exception as e:
        logger.error(f"[Email] Failed to send to {email}: {e}")
        return False


def maybe_send_email(player: dict, message: str, subject: str = "Notification",
                     dry_run: bool = False, append_date: bool = False) -> bool:
    """Send email to player if they have an email address on file.

    Call this after sending the WhatsApp DM.

    Args:
        player: Player dict with 'email' field
        message: WhatsApp message text (formatting will be converted to HTML)
        subject: Email subject label (e.g. "Vote Reminder", "Last Call")
        dry_run: If True, log but don't send
        append_date: Append today's PST date as MM/DD/YY. Use for recurring
            reminders — an identical subject every day makes Gmail collapse
            them into one thread, so the newest nag hides under older ones,
            and a repeated subject to the same recipient is exactly what
            spam filters score on.

    Returns:
        True if email was sent, False if no email or error
    """
    email = player.get('email', '')
    if not email:
        return False

    name = player.get('name', '?')
    full_subject = f"{SUBJECT_PREFIX} - {subject}"
    if append_date:
        full_subject += f" - {subject_date()}"
    body_html = whatsapp_to_html(message)

    result = send_notification_email(email, full_subject, body_html, dry_run)
    if result:
        label = "[DRY RUN] " if dry_run else ""
        logger.info(f"{label}[Email] {'Would also send' if dry_run else 'Also sent'} email to {name}")
    return result


def maybe_send_extra_notifications(player: dict, message: str, subject: str = "Notification",
                                   dry_run: bool = False, append_date: bool = False):
    """Send supplemental email notification to player if they have an email on file.

    Call this after sending the WhatsApp DM.

    Args:
        player: Player dict with 'email' field
        message: WhatsApp message text (converted to HTML for email)
        subject: Email subject label (e.g. "Vote Reminder", "Last Call")
        dry_run: If True, log but don't send
        append_date: Append today's PST date as MM/DD/YY -- see maybe_send_email
    """
    maybe_send_email(player, message, subject=subject, dry_run=dry_run,
                     append_date=append_date)
