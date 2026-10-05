"""Phone number utilities for WhatsApp and SMS integration."""
from typing import Optional


def normalize_us_phone(phone: str) -> Optional[str]:
    """
    Normalize a US phone number to 11-digit format (1XXXXXXXXXX).
    Strips formatting, adds country code if needed.
    Returns None if the phone number is invalid.
    """
    if not phone:
        return None

    # Remove all non-numeric characters
    digits = ''.join(c for c in phone if c.isdigit())

    # Handle US numbers
    if len(digits) == 10:
        digits = '1' + digits
    elif len(digits) == 11 and digits.startswith('1'):
        pass
    else:
        return None

    return digits


def format_phone_for_whatsapp(phone: str) -> Optional[str]:
    """
    Convert phone number to WhatsApp format (e.g., 12345678901@c.us).
    Handles US numbers (normalizes to 1XXXXXXXXXX) and international numbers
    (uses raw digits as-is, e.g. 85298011875 for Hong Kong).
    Returns None if the phone number is invalid.
    """
    # Try US normalization first
    digits = normalize_us_phone(phone)
    if digits:
        return f"{digits}@c.us"

    # Fall back to international: strip formatting and accept if 7-15 digits
    if phone:
        raw = ''.join(c for c in phone if c.isdigit())
        if 7 <= len(raw) <= 15:
            return f"{raw}@c.us"

    return None


