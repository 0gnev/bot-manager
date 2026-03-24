from __future__ import annotations

from datetime import datetime
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class PlanerkaOrganizer(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    email: str
    time_zone: str | None = Field(None, alias="timeZone")


class PlanerkaAttendee(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    email: str | None = None
    phone: str | None = None
    telegram: str | None = None
    time_zone: str | None = Field(None, alias="timeZone")


class PlanerkaLocation(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None
    id: str | None = None
    url: str | None = None
    password: str | None = None


class PlanerkaCustomInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    label: str | None = None
    value: str | None = None


class PlanerkaWebhookPayload(BaseModel):
    """
    Planerka webhook payload — based on observed live payload shape.

    All event fields (title, startTime, endTime) are top-level.
    location is an object with .url for the meeting link.
    Booking ID comes from the `uid` field.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    event: str                  # BOOKING_CREATED | BOOKING_CANCELLED | BOOKING_RESCHEDULED
    uid: str | None = None
    title: str | None = None
    description: str | None = None
    start_time: datetime | None = Field(None, alias="startTime")
    end_time: datetime | None = Field(None, alias="endTime")
    organizer: PlanerkaOrganizer | None = None
    attendees: list[PlanerkaAttendee] | None = None
    location: PlanerkaLocation | None = None
    event_type: str | None = Field(None, alias="eventType")
    custom_inputs: list[PlanerkaCustomInput] | None = Field(None, alias="customInputs")
    utm: dict[str, Any] | None = None

    def get_booking_id(self) -> str | None:
        return self.uid

    def get_attendee(self) -> PlanerkaAttendee | None:
        if self.attendees:
            return self.attendees[0]
        return None

    def get_meeting_url(self) -> str | None:
        if self.location and self.location.url:
            return self.location.url
        return None
