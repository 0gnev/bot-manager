"""
Bridge entrypoint.

Runs two concurrent tasks in a single process:
  1. FastAPI (uvicorn) — listens for Planerka webhooks on :8081
  2. aiogram polling    — polls Telegram for the student and tutor bots
"""

from __future__ import annotations

import asyncio
import logging

import uvicorn
from fastapi import FastAPI

from bridge.api.approvals import router as approvals_router
from bridge.api.audit import router as audit_router
from bridge.api.logs import router as logs_router
from bridge.api.tutor import router as tutor_router
from bridge.config import get_settings
from bridge.db import close_pool, init_pool
from bridge.db.migrate import run_migrations
from bridge.idempotency import init as init_idempotency, run_cleanup_loop
from bridge.runtime_logs import configure_runtime_logging
from bridge.webhook.router import router as webhook_router
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(title="Bot Manager Bridge", docs_url=None, redoc_url=None)
    app.include_router(webhook_router)
    app.include_router(tutor_router)
    app.include_router(audit_router)
    app.include_router(logs_router)
    app.include_router(approvals_router)

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    return app


async def run_polling(settings) -> None:
    from bridge.bot.setup import create_bots_and_dispatcher

    bot_student, bot_owner, dp = create_bots_and_dispatcher(settings)
    bots = [bot_student]
    if bot_owner is not None:
        bots.append(bot_owner)

    logger.info(
        "Starting Telegram polling (%s)",
        "student + tutor bots" if bot_owner is not None else "student bot only",
    )
    try:
        await dp.start_polling(
            *bots,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        await bot_student.session.close()
        if bot_owner is not None:
            await bot_owner.session.close()


async def run_server(app: FastAPI, settings) -> None:
    config = uvicorn.Config(
        app=app,
        host=settings.host,
        port=settings.port,
        log_level=str(getattr(settings, "log_level", "INFO")).lower(),
        access_log=True,
        log_config=None,
        ws="none",
    )
    server = uvicorn.Server(config)
    logger.info("Starting webhook server on %s:%s", settings.host, settings.port)
    await server.serve()


def _telegram_configured(settings) -> bool:
    return bool(getattr(settings, "telegram_bot_token_student", None))


async def main() -> None:
    settings = get_settings()
    configure_runtime_logging(settings)

    # Initialize database pool and run migrations
    pool = await init_pool(settings.database_url)
    await run_migrations(pool)

    await init_idempotency(settings.state_path)

    app = create_app()

    tasks = [run_server(app, settings), run_cleanup_loop()]

    if _telegram_configured(settings):
        tasks.append(run_polling(settings))
    else:
        logger.warning(
            "Telegram bot tokens not set — webhook server only, polling disabled"
        )

    try:
        await asyncio.gather(*tasks)
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
