"""Public HTTP health check (GET /health) on the webhook aiohttp server."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from aiohttp import web
from sqlalchemy import text

from app import config
from app.database import async_session

logger = logging.getLogger(__name__)

BOT_GET_ME_TIMEOUT = 2.0
DB_PING_TIMEOUT = 3.0


def bot_id_from_token(token: str | None) -> int:
    if not token or ":" not in token:
        return 0
    try:
        return int(token.split(":", 1)[0])
    except (ValueError, TypeError):
        return 0


async def handle_health(request: web.Request) -> web.Response:
    logger.info("GET /health remote=%s", request.remote)

    started_at = float(request.app.get("health_started_at", time.time()))
    uptime_seconds = int(time.time() - started_at)

    bot_id = bot_id_from_token(config.BOT_TOKEN)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    bot = request.app["bot"]
    bot_ok = False
    db_ok = False

    async def ping_bot() -> None:
        nonlocal bot_ok
        try:
            await asyncio.wait_for(bot.get_me(), timeout=BOT_GET_ME_TIMEOUT)
            bot_ok = True
        except Exception:
            bot_ok = False

    async def ping_db() -> None:
        nonlocal db_ok
        try:

            async def _query() -> None:
                async with async_session() as session:
                    await session.execute(text("SELECT 1"))

            await asyncio.wait_for(_query(), timeout=DB_PING_TIMEOUT)
            db_ok = True
        except Exception:
            db_ok = False

    await asyncio.gather(ping_bot(), ping_db())

    database = "connected" if db_ok else "disconnected"
    status = "ok" if bot_ok and db_ok else "error"

    payload = {
        "status": status,
        "bot_id": bot_id,
        "timestamp": timestamp,
        "database": database,
        "uptime_seconds": uptime_seconds,
    }
    http_status = 200 if status == "ok" else 503
    return web.json_response(payload, status=http_status)
