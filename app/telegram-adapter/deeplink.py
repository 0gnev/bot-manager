"""
Telegram deeplink utilities.

Planerka is configured to show the student a deeplink:
  t.me/{bot_username}?start={booking_id}

When the student opens it, Telegram sends /start {booking_id} to the bot.
The booking_id may be base64url-encoded if it contains special characters.
"""

from __future__ import annotations

import base64


def parse_start_payload(payload: str) -> str:
    """
    Extract booking_id from /start payload.
    Handles plain IDs and base64url-encoded IDs.
    """
    payload = payload.strip()
    try:
        # Attempt base64url decode; if result is printable ASCII use it
        padded = payload + "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
        if decoded.isprintable() and decoded:
            return decoded
    except Exception:
        pass
    return payload


def encode_start_payload(booking_id: str) -> str:
    """
    Encode booking_id for use in a deeplink start parameter.
    Only encodes if the ID contains characters outside [A-Za-z0-9_-].
    """
    if all(c.isalnum() or c in "-_" for c in booking_id):
        return booking_id
    return base64.urlsafe_b64encode(booking_id.encode()).rstrip(b"=").decode()
