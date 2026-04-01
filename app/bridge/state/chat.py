"""
Chat entity — wraps a conversation with metadata and operating mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class OperatingMode(str, Enum):
    AUTO = "auto"           # Bot answers autonomously
    SEMI_AUTO = "semi-auto" # Bot drafts, tutor approves
    MANUAL = "manual"       # Bot silent, tutor handles directly


@dataclass
class Chat:
    booking_id: str
    mode: OperatingMode = OperatingMode.AUTO
    messages: list = field(default_factory=list)
    draft: dict | None = None  # Pending draft in semi-auto mode
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "booking_id": self.booking_id,
            "mode": self.mode.value,
            "messages": self.messages,
            "draft": self.draft,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Chat:
        return cls(
            booking_id=data["booking_id"],
            mode=OperatingMode(data.get("mode", "auto")),
            messages=data.get("messages", []),
            draft=data.get("draft"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )
