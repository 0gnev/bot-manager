"""
Bridge entrypoint.

Runs two concurrent tasks in a single process:
  1. FastAPI (uvicorn) — listens for Planerka webhooks on :8081
  2. aiogram polling    — polls Telegram for both student and tutor bots
                          (only started when both bot tokens are configured)
"""

from __future__ import annotations

import asyncio
import logging
import sys

import uvicorn
from fastapi import FastAPI

from bridge.config import get_settings
from bridge.webhook.router import router as webhook_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(title="Bot Manager Bridge", docs_url=None, redoc_url=None)
    app.include_router(webhook_router)

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    return app


async def run_polling(settings) -> None:
    from bridge.bot.setup import create_bots_and_dispatcher

    bot_student, bot_tutor, dp = create_bots_and_dispatcher(settings)
    logger.info("Starting Telegram polling (student + tutor bots)")
    try:
        await dp.start_polling(
            bot_student, bot_tutor,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        await bot_student.session.close()
        await bot_tutor.session.close()


async def run_server(app: FastAPI, settings) -> None:
    config = uvicorn.Config(
        app=app,
        host=settings.host,
        port=settings.port,
        log_level="info",
        access_log=True,
    )
    server = uvicorn.Server(config)
    logger.info("Starting webhook server on %s:%s", settings.host, settings.port)
    await server.serve()


def _telegram_configured(settings) -> bool:
    return bool(
        getattr(settings, "telegram_bot_token_default", None)
        and getattr(settings, "telegram_bot_token_manager", None)
    )


async def main() -> None:
    settings = get_settings()
    app = create_app()

    tasks = [run_server(app, settings)]

    if _telegram_configured(settings):
        tasks.append(run_polling(settings))
    else:
        logger.warning(
            "Telegram bot tokens not set — webhook server only, polling disabled"
        )

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
