"""
REST API for querying recent runtime logs.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from bridge.config import Settings, get_settings
from bridge.runtime_logs import read_recent_runtime_logs

router = APIRouter(prefix="/api/logs")


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


@router.get("/runtime", dependencies=[Depends(_verify_token)])
async def get_runtime_logs(
    limit: int = Query(default=200, ge=1, le=1000),
    logger_name: str | None = Query(default=None),
    level: str | None = Query(default=None),
    contains: str | None = Query(default=None),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    return await read_recent_runtime_logs(
        log_path=settings.log_path,
        limit=limit,
        logger_name=logger_name,
        level=level,
        contains=contains,
    )
