"""
FastAPI router for incoming Planerka webhooks.
Mounted at /webhook/planerka.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from bridge.config import Settings, get_settings
from bridge.webhook.handler import handle_webhook

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook")


def _verify_token(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    expected = f"Bearer {settings.planerka_webhook_secret}"
    if not authorization or authorization != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Authorization header",
        )


@router.post(
    "/planerka",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(_verify_token)],
)
async def planerka_webhook(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON body",
        ) from exc

    logger.info("Planerka webhook received: event=%s", body.get("event"))
    await handle_webhook(body, settings)
    return {"ok": True}
