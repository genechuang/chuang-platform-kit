"""GREEN-API helper functions - shared between webhook, Cloud Functions, and CLI.

Provides reusable wrappers around GREEN-API REST endpoints.
Callers supply their own instance_id and api_token (from env vars or Secret Manager).

Usage:
    Cloud Functions: from shared.greenapi import call_api, get_group_admin_ids
    CLI tools:       from webhook.shared.greenapi import call_api, get_chat_history
"""

import logging
import time

from .lazy_import import lazy

requests = lazy('requests')   # imported on the first call (lazy_import.py)

logger = logging.getLogger(__name__)

BASE_URL = "https://api.green-api.com"
REQUEST_TIMEOUT = 10


def get_api_url(instance_id: str, api_token: str, method: str) -> str:
    """Build a GREEN-API endpoint URL.

    Args:
        instance_id: GREEN-API instance ID
        api_token: GREEN-API API token
        method: API method name (e.g., "sendMessage", "getGroupData")

    Returns:
        Full URL string
    """
    return f"{BASE_URL}/waInstance{instance_id}/{method}/{api_token}"


def get_state_instance(instance_id: str, api_token: str,
                       timeout: int = REQUEST_TIMEOUT) -> dict | None:
    """Ask GREEN-API whether the WhatsApp instance is still authorized.

    Returns the raw response -- `stateInstance` plus, when WhatsApp itself has
    applied a temporary restriction, `suspendedUntil`. **None means "could not
    ask", never "healthy"**: the caller has to be able to tell an unreachable
    API from an authorized instance, which is the whole difference between a
    monitor and a decoration.

    A GET, not call_api(). getStateInstance is a GET endpoint, and the first
    caller of this check -- greenapi-watch.yml, a workflow retired 2026-09-19
    PT once the keepalive poll had replaced it -- curled it that way.

    The URL carries the API token as a path segment. Nothing here logs it, and
    an exception that quotes it (requests does) is scrubbed on the way out by
    shared/redact.py in every log formatter, email sender and issue path.

    LOGS A FAILED CALL AT WARNING, NOT ERROR -- the one place in this module
    that does. ERROR is the page here: a GCP log-based policy emails Gene on
    every ERROR line from the four functions. This function returns None so that
    its CALLER decides what a failed read means, and only the caller can: a
    single 10s ReadTimeout on one poll is not an outage, the same timeout still
    standing 15 minutes later is. _poll_instance_state() makes that call and
    logs the ERROR when it is earned. Logging ERROR here as well paged Gene
    three times in September 2026 for three isolated timeouts -- twice for each,
    since this line and the caller's both fired on the same event.
    """
    url = get_api_url(instance_id, api_token, 'getStateInstance')
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        body = (resp.text or "").strip()[:300]
        hint = ""
        if resp.status_code in (401, 403) and not (instance_id and api_token):
            hint = " — instance_id/api_token are empty, so this call was unauthenticated"
        logger.warning(f"getStateInstance returned {resp.status_code}: {body}{hint}")
        return None
    except Exception as e:
        logger.warning(f"Error calling getStateInstance: {type(e).__name__}: {e}")
        return None


CONNECT_BACKOFF_SECONDS = 5


def call_api(instance_id: str, api_token: str, method: str,
             payload: dict = None, timeout: int = REQUEST_TIMEOUT,
             connect_retries: int = 0) -> dict | None:
    """Call a GREEN-API endpoint.

    Args:
        instance_id: GREEN-API instance ID
        api_token: GREEN-API API token
        method: API method name (e.g., "sendMessage", "getGroupData")
        payload: JSON body to send (default: empty dict)
        timeout: Request timeout in seconds
        connect_retries: extra attempts after a CONNECT timeout only, 5s then
            10s apart. A connect timeout means the request never reached
            GREEN-API, so repeating it cannot do anything twice; a read
            timeout is never retried. Off (0) by default: only read-only
            callers opt in (the member sync's get_group_data). A single 10s connect timeout
            failed the 9/29 1:55 PM PT sync-members run, which since a9873cf
            fails the run on any error and so emailed Gene.

    Returns:
        Response JSON dict on success, None on error
    """
    url = get_api_url(instance_id, api_token, method)
    try:
        for attempt in range(connect_retries + 1):
            try:
                resp = requests.post(url, json=payload or {}, timeout=timeout)
                break
            except requests.exceptions.ConnectTimeout:
                if attempt == connect_retries:
                    raise
                # Never log the URL -- the API token is a path segment in it.
                logger.warning(f"{method}: connect timeout (attempt {attempt + 1} of {connect_retries + 1}); "
                               f"retrying in {CONNECT_BACKOFF_SECONDS * (attempt + 1)}s")
                time.sleep(CONNECT_BACKOFF_SECONDS * (attempt + 1))
        if resp.status_code == 200:
            return resp.json()
        else:
            # Include the response body. The status code alone hid the cause of
            # every failed archive removal: picklebot was deployed without
            # GREENAPI_INSTANCE_ID/API_TOKEN and got a bare 403 from
            # removeGroupParticipant, which read as "GREEN-API refused" rather
            # than "we sent no credentials".
            #
            # Never log the URL -- the API token is a path segment in it.
            body = (resp.text or "").strip()[:300]
            hint = ""
            if resp.status_code in (401, 403) and not (instance_id and api_token):
                hint = " — instance_id/api_token are empty, so this call was unauthenticated"
            logger.error(f"{method} returned {resp.status_code}: {body}{hint}")
            return None
    except Exception as e:
        logger.error(f"Error calling {method}: {e}")
        return None


# --- Group helpers ---

def get_group_data(instance_id: str, api_token: str, group_id: str,
                   connect_retries: int = 0) -> dict | None:
    """Fetch group data including participants and admin status.

    `connect_retries` passes to call_api(): a read is safe to repeat after a
    connect timeout. The CLI member sync asks for 2; the Cloud Functions'
    callers (the vote webhook's admin check, picklebot's participant read)
    keep 0, since two retries can add ~35s (45s in all) against a 60s function timeout.

    Returns:
        Group data dict with 'participants' list, or None on error.
        Each participant has: id, isAdmin, isSuperAdmin
    """
    return call_api(instance_id, api_token, "getGroupData", {"groupId": group_id},
                    connect_retries=connect_retries)


def get_group_description(instance_id: str, api_token: str,
                          group_id: str) -> str | None:
    """Fetch a group's description — the text WhatsApp shows under Group Info.

    This is the master copy of the welcome text: it is what 45 members actually
    read, it is edited by hand in WhatsApp, and GREEN-API exposes no method to
    write it (checked again 2026-08-30 PT — getGroupData returns `description`,
    but UpdateGroupSettings only carries two permission booleans and
    UpdateGroupName sets the subject). So the DM follows the group, not the
    other way round.

    Returns:
        The description, or None when the call fails or the group has none.
        None means "could not read", so callers fall back rather than sending
        an empty welcome — never treat it as an empty description.
    """
    data = get_group_data(instance_id, api_token, group_id)
    if not data:
        return None
    desc = (data.get('description') or '').strip()
    return desc or None


def get_group_admin_ids(instance_id: str, api_token: str, group_id: str) -> set:
    """Get the set of phone IDs that are admins of a WhatsApp group.

    Returns:
        Set of admin phone IDs (e.g., {"16265551234@c.us", "16265555678@c.us"})
    """
    data = get_group_data(instance_id, api_token, group_id)
    if not data:
        return set()
    admins = set()
    for p in data.get('participants', []):
        if p.get('isAdmin') or p.get('isSuperAdmin'):
            admins.add(p['id'])
    return admins


def get_group_participant_ids(instance_id: str, api_token: str, group_id: str) -> set:
    """Get the set of chat IDs currently in a WhatsApp group.

    Returns:
        Set of participant chat IDs, or an empty set if the lookup fails.
        Callers must treat an empty set as "unknown", not "empty group".
    """
    data = get_group_data(instance_id, api_token, group_id)
    if not data:
        return set()
    return {p['id'] for p in data.get('participants', []) if p.get('id')}


def remove_group_participant(instance_id: str, api_token: str, group_id: str,
                             participant_chat_id: str) -> dict | None:
    """Remove a participant from a WhatsApp group.

    Args:
        group_id: WhatsApp group ID (e.g., "120363...@g.us")
        participant_chat_id: Participant's chat ID (e.g., "12345678901@c.us")

    NOTE: GREEN-API returns {"removeParticipant": true} even when the number was
    never in the group, so the flag is NOT proof of removal. Verify against
    get_group_participant_ids() before reporting success.

    Returns:
        Response dict on success, None on error.
    """
    return call_api(instance_id, api_token, "removeGroupParticipant", {
        "groupId": group_id,
        "participantChatId": participant_chat_id
    })


# --- Chat helpers ---

def get_chat_history(instance_id: str, api_token: str, chat_id: str,
                     count: int = 100) -> list | None:
    """Fetch recent chat messages.

    Args:
        instance_id: GREEN-API instance ID
        api_token: GREEN-API API token
        chat_id: WhatsApp chat ID (group or DM)
        count: Number of messages to retrieve (default: 100)

    Returns:
        List of message dicts, or None on error
    """
    result = call_api(instance_id, api_token, "getChatHistory",
                      {"chatId": chat_id, "count": count})
    # getChatHistory returns a list directly, not a dict
    return result


# --- Message sending helpers ---

def send_message(instance_id: str, api_token: str, chat_id: str,
                 message: str, timeout: int = REQUEST_TIMEOUT) -> dict | None:
    """Send a text message via GREEN-API.

    Returns:
        Response dict on success, None on error
    """
    return call_api(instance_id, api_token, "sendMessage",
                    {"chatId": chat_id, "message": message}, timeout=timeout)


def send_poll(instance_id: str, api_token: str, chat_id: str,
              question: str, options: list, multiple_answers: bool = True,
              timeout: int = REQUEST_TIMEOUT) -> dict | None:
    """Create a poll via GREEN-API.

    Args:
        options: List of option name strings (will be wrapped in {"optionName": ...})
    """
    poll_options = [{"optionName": opt} for opt in options]
    return call_api(instance_id, api_token, "sendPoll", {
        "chatId": chat_id,
        "message": question,
        "options": poll_options,
        "multipleAnswers": multiple_answers
    }, timeout=timeout)


def send_file_by_url(instance_id: str, api_token: str, chat_id: str,
                     file_url: str, caption: str = "", filename: str = "image.jpg",
                     timeout: int = 60) -> dict | None:
    """Send a file by URL via GREEN-API."""
    return call_api(instance_id, api_token, "sendFileByUrl", {
        "chatId": chat_id,
        "urlFile": file_url,
        "fileName": filename,
        "caption": caption
    }, timeout=timeout)
