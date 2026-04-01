"""
REST API for querying audit log entries.

Endpoints:
  GET /api/audit — query recent audit entries with optional filters
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from bridge.audit import read_recent
from bridge.config import Settings, get_settings

router = APIRouter(prefix="/api/audit")


def _verify_token(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    expected_token = settings.tutor_api_token or settings.gateway_auth_token
    expected = f"Bearer {expected_token}"
    if not authorization or authorization != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Authorization header",
        )


@router.get("", dependencies=[Depends(_verify_token)])
async def get_audit(
    booking_id: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict]:
    """Return recent audit entries, newest first."""
    return await read_recent(
        booking_id=booking_id,
        event_type=event_type,
        limit=limit,
    )
