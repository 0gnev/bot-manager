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
    booking_id: str | None = None
    contact_id: int | None = None
    mode: OperatingMode = OperatingMode.AUTO
    status: str = "active"
    automation_enabled: bool = True
    assigned_human: str | None = None
    escalation_state: str = "none"
    scenario_type: str = "general_support"
    current_stage: str = "new"
    confidence: float | None = None
    escalation_reason: str | None = None
    messages: list = field(default_factory=list)
    draft: dict | None = None  # Pending draft in semi-auto mode
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "booking_id": self.booking_id,
            "contact_id": self.contact_id,
            "mode": self.mode.value,
            "status": self.status,
            "automation_enabled": self.automation_enabled,
            "assigned_human": self.assigned_human,
            "escalation_state": self.escalation_state,
            "scenario_type": self.scenario_type,
            "current_stage": self.current_stage,
            "confidence": self.confidence,
            "escalation_reason": self.escalation_reason,
            "messages": self.messages,
            "draft": self.draft,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Chat:
        return cls(
            booking_id=data.get("booking_id"),
            contact_id=data.get("contact_id"),
            mode=OperatingMode(data.get("mode", "auto")),
            status=data.get("status", "active"),
            automation_enabled=data.get("automation_enabled", True),
            assigned_human=data.get("assigned_human"),
            escalation_state=data.get("escalation_state", "none"),
            scenario_type=data.get("scenario_type", "general_support"),
            current_stage=data.get("current_stage", "new"),
            confidence=data.get("confidence"),
            escalation_reason=data.get("escalation_reason"),
            messages=data.get("messages", []),
            draft=data.get("draft"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )
