"""
HTTP client for the openclaw AI service.

POST /chat  — text message
POST /image — image + optional caption

Both return: {action: "answer"|"clarify"|"escalate", content: str, confidence: float}
"""

from __future__ import annotations

import logging

import httpx

from bridge.config import Settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0)


class OpenclawClient:
    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.openclaw_base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {settings.gateway_auth_token}",
            "Content-Type": "application/json",
        }

    async def chat(
        self,
        message: str,
        booking_context: dict,
        history: list[dict],
    ) -> dict:
        payload = {
            "message": message,
            "booking_context": booking_context,
            "history": history,
        }
        return await self._post("/chat", payload)

    async def image(
        self,
        image_path: str,
        caption: str,
        booking_context: dict,
        history: list[dict],
    ) -> dict:
        payload = {
            "image_path": image_path,
            "caption": caption,
            "booking_context": booking_context,
            "history": history,
        }
        return await self._post("/image", payload)

    async def _post(self, path: str, payload: dict) -> dict:
        url = f"{self._base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(url, json=payload, headers=self._headers)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error("Openclaw %s error: %s", path, exc.response.text)
            return _fallback()
        except Exception as exc:
            logger.error("Openclaw %s unreachable: %s", path, exc)
            return _fallback()


def _fallback() -> dict:
    return {
        "action": "escalate",
        "content": "Не могу ответить прямо сейчас — передаю преподавателю.",
        "confidence": 0.0,
    }
