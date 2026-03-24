from __future__ import annotations

from datetime import datetime
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class PlanerkaOrganizer(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    email: str
    timezone: str | None = None


class PlanerkaAttendee(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    email: str | None = None
    timezone: str | None = None
    phone: str | None = None


class PlanerkaEventDetail(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str
    description: str | None = None
    type: str | None = None
    start_time: datetime | None = Field(None, alias="startTime")
    end_time: datetime | None = Field(None, alias="endTime")


class PlanerkaCustomField(BaseModel):
    model_config = ConfigDict(extra="allow")

    label: str
    value: str | None = None


class PlanerkaWebhookPayload(BaseModel):
    """
    Incoming webhook payload from Planerka.
    Field names follow Planerka's camelCase convention.
    Extra fields are preserved for forward compatibility.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    event: str  # BOOKING_CREATED | BOOKING_CANCELLED | BOOKING_RESCHEDULED
    booking_id: str | None = Field(None, alias="bookingId")
    organizer: PlanerkaOrganizer | None = None
    attendee: PlanerkaAttendee | None = None
    attendees: list[PlanerkaAttendee] | None = None
    event_detail: PlanerkaEventDetail | None = Field(None, alias="eventDetail")
    location: str | None = None
    meeting_url: str | None = Field(None, alias="meetingUrl")
    custom_fields: list[PlanerkaCustomField] | None = Field(None, alias="customFields")
    timestamp: datetime | None = None
    meta: dict[str, Any] | None = None

    def get_booking_id(self) -> str | None:
        """Return booking ID from whichever field Planerka uses."""
        return self.booking_id or (self.meta or {}).get("bookingId")

    def get_attendee(self) -> PlanerkaAttendee | None:
        """Return the primary attendee (student)."""
        if self.attendee:
            return self.attendee
        if self.attendees:
            return self.attendees[0]
        return None

    def get_meeting_url(self) -> str | None:
        """Return meeting URL from dedicated field or location fallback."""
        if self.meeting_url:
            return self.meeting_url
        if self.location and self.location.startswith("http"):
            return self.location
        return None
