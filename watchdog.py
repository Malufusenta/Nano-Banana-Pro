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

CHECK_INTERVAL_SEC = 5 * 60
STALE_AFTER = timedelta(minutes=5)
ALERT_TEXT = "🚨 БОТ ЗАВИС! Пульс не обновлялся 5+ мин"


def _require_env(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


async def _send_telegram_alert(
    client: httpx.AsyncClient, token: str, chat_id: str, message: str
) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = await client.post(
        url,
        json={"chat_id": chat_id, "text": message},
        timeout=30.0,
    )
    response.raise_for_status()


async def _one_check(
    session_maker: async_sessionmaker, token: str, admin_chat_id: str
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
        try:
            async with httpx.AsyncClient() as client:
                await _send_telegram_alert(client, token, admin_chat_id, ALERT_TEXT)
            logger.info("Telegram alert sent to chat_id=%s", admin_chat_id)
        except Exception:
            logger.exception("Failed to send Telegram alert")
    else:
        logger.info("Pulse OK: last_heartbeat=%s age=%s", last, age)


async def watchdog_loop() -> None:
    database_url = _require_env("DATABASE_URL")
    token = _require_env("WATCHDOG_BOT_TOKEN")
    admin_chat_id = _require_env("ADMIN_TG_ID")

    engine = create_async_engine(
        database_url,
        echo=False,
        pool_pre_ping=True,
    )
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    logger.info(
        "Watchdog started: check every %ss, alert if last_heartbeat older than %s min",
        CHECK_INTERVAL_SEC,
        int(STALE_AFTER.total_seconds() // 60),
    )

    try:
        while True:
            try:
                await _one_check(session_maker, token, admin_chat_id)
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
