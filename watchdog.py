#!/usr/bin/env python3
"""
Санитар для Railway: отдельный сервис `python watchdog.py` (та же DATABASE_URL, что у бота).
Читает last_heartbeat из system_status; если старше 5 минут — шлёт алерт вторым ботом в Telegram.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

load_dotenv(Path(__file__).resolve().parent / ".env")

logger = logging.getLogger("watchdog")

CHECK_INTERVAL_SEC = int(os.getenv("CHECK_INTERVAL_SEC", str(5 * 60)))
STALE_AFTER = timedelta(minutes=5)
ALERT_TEXT = "🚨 БОТ ЗАВИС! Пульс не обновлялся 5+ мин"


def _require_env(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


def _parse_chat_id(raw: str) -> str | int:
    raw = raw.strip()
    if raw.lstrip("-").isdigit():
        return int(raw)
    return raw


def _resolve_alert_chat_ids() -> list[str | int]:
    """ALERT_CHAT_IDS (через запятую) или один ADMIN_TG_ID."""
    raw = os.getenv("ALERT_CHAT_IDS", "").strip()
    if raw:
        chat_ids = [_parse_chat_id(part) for part in raw.split(",") if part.strip()]
        if not chat_ids:
            raise RuntimeError("ALERT_CHAT_IDS is set but contains no valid IDs")
        return chat_ids
    return [_parse_chat_id(_require_env("ADMIN_TG_ID"))]


async def _send_telegram_message(
    client: httpx.AsyncClient, token: str, chat_id: str | int, message: str
) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = await client.post(
        url,
        json={"chat_id": chat_id, "text": message},
        timeout=30.0,
    )
    response.raise_for_status()


async def send_alert(
    client: httpx.AsyncClient,
    token: str,
    chat_ids: list[str | int],
    message: str,
) -> None:
    for chat_id in chat_ids:
        try:
            await _send_telegram_message(client, token, chat_id, message)
            logger.info("Telegram alert sent to chat_id=%s", chat_id)
        except Exception:
            logger.exception("Failed to send Telegram alert to chat_id=%s", chat_id)


async def _one_check(
    session_maker: async_sessionmaker, token: str, alert_chat_ids: list[str | int]
) -> None:
    now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    async with session_maker() as session:
        result = await session.execute(
            text("SELECT last_heartbeat FROM system_status WHERE id = 1")
        )
        last = result.scalar_one_or_none()

    if last is None:
        logger.info(
            "No row system_status id=1 (run alembic upgrade); skip alert this cycle"
        )
        return

    age = now_utc_naive - last
    if age > STALE_AFTER:
        logger.info(
            "Stale heartbeat: last_heartbeat=%s age=%s — sending Telegram alert",
            last,
            age,
        )
        async with httpx.AsyncClient() as client:
            await send_alert(client, token, alert_chat_ids, ALERT_TEXT)
    else:
        logger.info("Pulse OK: last_heartbeat=%s age=%s", last, age)


async def watchdog_loop() -> None:
    token = _require_env("WATCHDOG_BOT_TOKEN")
    alert_chat_ids = _resolve_alert_chat_ids()

    database_url = _require_env("DATABASE_URL")
    database_url = database_url.replace("postgresql://", "postgresql+asyncpg://")
    database_url = database_url.replace("postgres://", "postgresql+asyncpg://")
    logger.info(f"DB URL driver: {database_url[:30]}...")

    engine = create_async_engine(
        database_url,
        echo=False,
        pool_pre_ping=True,
    )
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    logger.info(
        "Watchdog started: check every %ss, alert if last_heartbeat older than %s min, "
        "alert chats=%s",
        CHECK_INTERVAL_SEC,
        int(STALE_AFTER.total_seconds() // 60),
        alert_chat_ids,
    )

    try:
        while True:
            try:
                await _one_check(session_maker, token, alert_chat_ids)
            except Exception:
                logger.exception("Watchdog check failed")
            await asyncio.sleep(CHECK_INTERVAL_SEC)
    finally:
        await engine.dispose()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    asyncio.run(watchdog_loop())


if __name__ == "__main__":
    main()
