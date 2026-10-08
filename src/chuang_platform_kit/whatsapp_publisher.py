"""
WhatsApp Message Publisher for Pub/Sub Event-Driven Architecture

Provides fire-and-forget publishing of WhatsApp messages to Pub/Sub.
Clients call these functions without needing GREEN-API knowledge.

Usage:
    from chuang_platform_kit.whatsapp_publisher import send_dm, send_group_message

    # Fire and forget - returns immediately
    msg_id = send_dm("1234567890@c.us", "Hello!", source="my-service")
    msg_id = send_group_message("123...@g.us", "Hello group!", source="my-service")
"""

import os
import uuid
import logging
from datetime import datetime
from typing import List

from .whatsapp_message import (
    WhatsAppMessage,
    create_dm,
    create_group_message,
    create_poll,
    create_image
)

logger = logging.getLogger(__name__)

# Configuration: the topic and the project it lives in, from the environment
# or from configure(); a host names them once from its own config module.
WHATSAPP_TOPIC = os.environ.get('WHATSAPP_PUBSUB_TOPIC', 'whatsapp-messages')
GCP_PROJECT_ID = os.environ.get('GCS_PROJECT_ID') or os.environ.get('GCP_PROJECT_ID', '')


def configure(project_id: str = None, topic: str = None) -> None:
    """Tell the publisher where to publish: the GCP project and the Pub/Sub
    topic. A host calls this once from its config module, so the kit never
    carries a project's name."""
    global GCP_PROJECT_ID, WHATSAPP_TOPIC
    if project_id is not None:
        GCP_PROJECT_ID = project_id
    if topic is not None:
        WHATSAPP_TOPIC = topic


def publish_whatsapp_message(message: WhatsAppMessage) -> str:
    """
    Publish a WhatsApp message to Pub/Sub for async delivery.

    Fire-and-forget pattern - returns immediately without waiting for delivery.
    The consumer Cloud Function handles actual sending via GREEN-API.

    Args:
        message: WhatsAppMessage to send

    Returns:
        Message ID from Pub/Sub (for tracking/debugging)

    Raises:
        ValueError: If required fields are missing
        google.api_core.exceptions.GoogleAPIError: On Pub/Sub errors
    """
    # Validate message
    message.validate()

    # Add metadata if not present
    if not message.correlation_id:
        message.correlation_id = str(uuid.uuid4())
    if not message.timestamp:
        message.timestamp = datetime.utcnow().isoformat()  # utc-ok: an instant on the record, never a calendar day

    # Import here to allow module to load without google-cloud-pubsub
    # (useful for local development/testing)
    from google.cloud import pubsub_v1

    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(GCP_PROJECT_ID, WHATSAPP_TOPIC)

    # Serialize message
    data = message.to_json().encode('utf-8')

    # Publish with attributes for filtering/debugging
    future = publisher.publish(
        topic_path,
        data,
        message_type=message.message_type.value,
        source=message.source or "unknown",
        dry_run=str(message.dry_run).lower(),
        correlation_id=message.correlation_id
    )

    # Wait for publish to complete and get message ID
    message_id = future.result()

    logger.info(
        f"Published WhatsApp message: type={message.message_type.value}, "
        f"recipient={message.recipient_id[:20]}..., "
        f"source={message.source}, "
        f"msg_id={message_id}"
    )

    return message_id


# =============================================================================
# Convenience Functions - Fire and Forget
# =============================================================================

def send_dm(
    phone_id: str,
    message: str,
    source: str = None,
    dry_run: bool = False,
    include_signature: bool = False,
    include_joke: bool = False,
    correlation_id: str = None
) -> str:
    """
    Send a direct message to an individual.

    Args:
        phone_id: WhatsApp phone ID (e.g., "14155551234@c.us")
        message: Text message to send
        source: Identifier for the sending service
        dry_run: If True, log but don't actually send
        include_signature: If True, the host's sender prepends its signature
        include_joke: If True, the host's sender appends one of its jokes
        correlation_id: Deterministic dedup key (e.g., transaction ID). If None, a UUID is generated.

    Returns:
        Pub/Sub message ID
    """
    wa_message = create_dm(phone_id, message, source, dry_run, include_signature, include_joke, correlation_id)
    return publish_whatsapp_message(wa_message)


def send_group_message(
    group_id: str,
    message: str,
    source: str = None,
    dry_run: bool = False,
    include_signature: bool = False,
    include_joke: bool = False,
    correlation_id: str = None
) -> str:
    """
    Send a message to a group chat.

    Args:
        group_id: WhatsApp group ID (e.g., "123456789@g.us")
        message: Text message to send
        source: Identifier for the sending service
        dry_run: If True, log but don't actually send
        include_signature: If True, the host's sender prepends its signature
        include_joke: If True, the host's sender appends one of its jokes
        correlation_id: Deterministic dedup key (e.g., transaction ID). If None, a UUID is generated.

    Returns:
        Pub/Sub message ID
    """
    wa_message = create_group_message(group_id, message, source, dry_run, include_signature, include_joke, correlation_id)
    return publish_whatsapp_message(wa_message)


def send_poll(
    group_id: str,
    question: str,
    options: List[str],
    multiple_answers: bool = True,
    source: str = None,
    dry_run: bool = False
) -> str:
    """
    Create a poll in a group chat.

    Note: For polls where you need the poll ID for vote tracking,
    use direct GREEN-API calls instead. Signature/joke not applicable to polls.

    Args:
        group_id: WhatsApp group ID
        question: Poll question
        options: List of poll options (at least 2)
        multiple_answers: Allow selecting multiple options
        source: Identifier for the sending service
        dry_run: If True, log but don't actually send

    Returns:
        Pub/Sub message ID
    """
    wa_message = create_poll(group_id, question, options, multiple_answers, source, dry_run)
    return publish_whatsapp_message(wa_message)


def send_image(
    recipient_id: str,
    image_url: str,
    caption: str = "",
    source: str = None,
    dry_run: bool = False,
    include_signature: bool = False,
    correlation_id: str = None
) -> str:
    """
    Send an image with optional caption.

    Args:
        recipient_id: Phone ID or Group ID
        image_url: URL of the image to send
        caption: Optional caption for the image
        source: Identifier for the sending service
        dry_run: If True, log but don't actually send
        include_signature: If True, the host's sender prepends its signature to the caption
        correlation_id: Deterministic dedup key. If None, a UUID is generated.

    Returns:
        Pub/Sub message ID
    """
    wa_message = create_image(recipient_id, image_url, caption, source, dry_run,
                              include_signature, correlation_id)
    return publish_whatsapp_message(wa_message)


# =============================================================================
# Direct Publish (for advanced use cases)
# =============================================================================

def publish_raw(message: WhatsAppMessage) -> str:
    """
    Publish a pre-constructed WhatsAppMessage.

    Use this when you need full control over the message object.

    Args:
        message: Pre-constructed WhatsAppMessage

    Returns:
        Pub/Sub message ID
    """
    return publish_whatsapp_message(message)
