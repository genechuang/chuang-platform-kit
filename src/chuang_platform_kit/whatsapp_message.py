"""
WhatsApp Message Schema for Pub/Sub Event-Driven Architecture

Defines the standard message envelope for all WhatsApp messages sent via Pub/Sub.
This decouples message senders from GREEN-API implementation details.
"""

import json
from dataclasses import dataclass, asdict
from typing import Optional, List
from enum import Enum


class MessageType(str, Enum):
    """Types of WhatsApp messages supported."""
    TEXT_DM = "text_dm"           # Direct message to individual
    TEXT_GROUP = "text_group"     # Message to group chat
    POLL = "poll"                 # Create poll in group
    IMAGE = "image"               # Send image with caption


@dataclass
class WhatsAppMessage:
    """
    Standard message envelope for all WhatsApp messages.

    Attributes:
        message_type: Type of message (DM, group, poll, image)
        recipient_id: Phone ID (123...@c.us) or Group ID (123...@g.us)
        content: Text content or poll question
        correlation_id: Unique ID for tracking/debugging
        dry_run: If True, log but don't actually send
        include_signature: If True, the host's sender prepends its signature
        include_joke: If True, the host's sender appends one of its jokes
        poll_options: List of poll option strings (for POLL type)
        poll_multiple_answers: Allow multiple answers in poll
        image_url: URL of image to send (for IMAGE type)
        image_caption: Caption for image
        source: Identifier for the sending service (e.g., "court-booking")
        timestamp: ISO format timestamp when message was created
    """
    message_type: MessageType
    recipient_id: str
    content: str
    correlation_id: Optional[str] = None
    dry_run: bool = False
    include_signature: bool = False
    include_joke: bool = False
    poll_options: Optional[List[str]] = None
    poll_multiple_answers: bool = True
    image_url: Optional[str] = None
    image_caption: Optional[str] = None
    source: Optional[str] = None
    timestamp: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        d = asdict(self)
        # Convert enum to string value
        d['message_type'] = self.message_type.value
        return d

    def to_json(self) -> str:
        """Serialize to JSON for Pub/Sub message data."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict) -> 'WhatsAppMessage':
        """Create from dictionary."""
        # Convert string back to enum
        if isinstance(data.get('message_type'), str):
            data['message_type'] = MessageType(data['message_type'])
        return cls(**data)

    @classmethod
    def from_json(cls, json_str: str) -> 'WhatsAppMessage':
        """Deserialize from JSON."""
        data = json.loads(json_str)
        return cls.from_dict(data)

    def validate(self) -> None:
        """
        Validate the message has required fields for its type.

        Raises:
            ValueError: If required fields are missing
        """
        if not self.recipient_id:
            raise ValueError("recipient_id is required")

        if self.message_type == MessageType.POLL:
            if not self.poll_options or len(self.poll_options) < 2:
                raise ValueError("poll_options requires at least 2 options")
            if not self.content:
                raise ValueError("content (poll question) is required for POLL type")

        elif self.message_type == MessageType.IMAGE:
            if not self.image_url:
                raise ValueError("image_url is required for IMAGE type")

        elif self.message_type in (MessageType.TEXT_DM, MessageType.TEXT_GROUP):
            if not self.content:
                raise ValueError("content is required for text messages")


# Convenience factory functions
def create_dm(
    phone_id: str,
    message: str,
    source: str = None,
    dry_run: bool = False,
    include_signature: bool = False,
    include_joke: bool = False,
    correlation_id: str = None
) -> WhatsAppMessage:
    """Create a direct message to an individual."""
    return WhatsAppMessage(
        message_type=MessageType.TEXT_DM,
        recipient_id=phone_id,
        content=message,
        source=source,
        dry_run=dry_run,
        include_signature=include_signature,
        include_joke=include_joke,
        correlation_id=correlation_id
    )


def create_group_message(
    group_id: str,
    message: str,
    source: str = None,
    dry_run: bool = False,
    include_signature: bool = False,
    include_joke: bool = False,
    correlation_id: str = None
) -> WhatsAppMessage:
    """Create a message to a group chat."""
    return WhatsAppMessage(
        message_type=MessageType.TEXT_GROUP,
        recipient_id=group_id,
        content=message,
        source=source,
        dry_run=dry_run,
        include_signature=include_signature,
        include_joke=include_joke,
        correlation_id=correlation_id
    )


def create_poll(
    group_id: str,
    question: str,
    options: List[str],
    multiple_answers: bool = True,
    source: str = None,
    dry_run: bool = False
) -> WhatsAppMessage:
    """Create a poll in a group chat (signature/joke not applicable)."""
    return WhatsAppMessage(
        message_type=MessageType.POLL,
        recipient_id=group_id,
        content=question,
        poll_options=options,
        poll_multiple_answers=multiple_answers,
        source=source,
        dry_run=dry_run
    )


def create_image(
    recipient_id: str,
    image_url: str,
    caption: str = "",
    source: str = None,
    dry_run: bool = False,
    include_signature: bool = False,
    correlation_id: str = None
) -> WhatsAppMessage:
    """Create an image message with optional caption.

    correlation_id is the sender's dedup key, same as for text. It was missing
    here, so an image could only ever get a fresh UUID -- fine for a one-off,
    wrong for anything scheduled, where a re-run must not re-send.
    """
    return WhatsAppMessage(
        message_type=MessageType.IMAGE,
        recipient_id=recipient_id,
        content=caption,
        image_url=image_url,
        image_caption=caption,
        source=source,
        dry_run=dry_run,
        include_signature=include_signature,
        correlation_id=correlation_id
    )
