"""
Planerka REST API client.

Auth: x-auth header (PLANERKA_API_KEY).
Base URL: PLANERKA_BASE_URL

Endpoints:
  GET /rest/v1/           — health check
  GET /rest/v1/type/      — event types
  GET /rest/v1/event/?date=d.m.Y  — bookings for a date
"""

from __future__ import annotations

from datetime import date, datetime

import httpx

_TIMEOUT = httpx.Timeout(10.0)


class PlanerkaClient:
    def __init__(self, base_url: str, token: str) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"x-auth": token}

    async def health(self) -> dict:
        return await self._get("/rest/v1/")

    async def event_types(self) -> list[dict]:
        return await self._get("/rest/v1/type/")

    async def events(self, for_date: date | None = None) -> list[dict]:
        params = {}
        if for_date:
            params["date"] = for_date.strftime("%d.%m.%Y")
        else:
            params["date"] = date.today().strftime("%d.%m.%Y")
        return await self._get("/rest/v1/event/", params=params)

    async def _get(self, path: str, params: dict | None = None) -> list | dict:
        url = f"{self._base}{path}"
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            resp = await http.get(url, headers=self._headers, params=params)
            resp.raise_for_status()
            return resp.json()
