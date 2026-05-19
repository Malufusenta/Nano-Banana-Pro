"""
Мониторинг базы данных: периодические проверки целостности, алерты, ежедневные отчёты.
Все уведомления отправляются в DB_MONITOR_CHANNEL_ID.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, date, timezone
from typing import Optional

import pytz
from aiogram import Bot
from sqlalchemy import select, func, and_, or_, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models import User, Purchase, GenerationTask, VideoGenerationTask, Broadcast, BananaTransaction
from app import config

logger = logging.getLogger(__name__)
moscow_tz = pytz.timezone('Europe/Moscow')

# Дедупликация алертов: ключ -> (fingerprint, timestamp)
_alert_cache: dict[str, tuple[str, float]] = {}
DEDUP_COOLDOWN_SEC = 30 * 60


async def _notify(bot: Bot, text: str) -> None:
    """Отправить сообщение в канал мониторинга. Если канал не настроен — молча пропускаем."""
    if not config.DB_MONITOR_CHANNEL_ID:
        return
    try:
        await bot.send_message(config.DB_MONITOR_CHANNEL_ID, text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Не удалось отправить DB alert: {e}", exc_info=True)


def _should_send_alert(check_name: str, fingerprint: str) -> bool:
    """Проверить, нужно ли отправлять алерт (дедупликация по fingerprint и времени)."""
    now = time.time()
    if check_name in _alert_cache:
        cached_fp, cached_time = _alert_cache[check_name]
        if cached_fp == fingerprint and (now - cached_time) < DEDUP_COOLDOWN_SEC:
            return False
    _alert_cache[check_name] = (fingerprint, now)
    return True


async def check_stuck_generation_tasks(bot: Bot) -> None:
    """1. Зависшие задачи генерации (status=processing > 10 минут)."""
    async with async_session() as session:
        threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)
        result = await session.execute(
            select(GenerationTask.id, GenerationTask.user_id, GenerationTask.created_at)
            .where(
                GenerationTask.status == 'processing',
                GenerationTask.created_at < threshold
            )
            .order_by(GenerationTask.created_at)
            .limit(20)
        )
        rows = result.all()

    if not rows:
        return

    fingerprint = ",".join(str(r.id) for r in rows)
    if not _should_send_alert("stuck_gen_tasks", fingerprint):
        return

    lines = ["🚨 <b>DB ALERT: Зависшие задачи генерации</b>\n"]
    lines.append(f"Найдено: <b>{len(rows)}</b> задач висят &gt; 10 минут\n")
    for row in rows:
        age_min = int((datetime.now(timezone.utc).replace(tzinfo=None) - row.created_at).total_seconds() / 60)
        lines.append(f"Task {row.id} | User {row.user_id} | {age_min} мин")
    lines.append("\nПроверь: <code>generation_tasks WHERE status='processing'</code>")

    await _notify(bot, "\n".join(lines))


async def check_negative_balances(bot: Bot) -> None:
    """2. Отрицательные балансы."""
    async with async_session() as session:
        result = await session.execute(
            select(User.id, User.telegram_id, User.balance_free, User.balance_paid, User.generations_balance)
            .where(
                or_(
                    User.balance_free < 0,
                    User.balance_paid < 0,
                    User.generations_balance < 0
                )
            )
            .limit(20)
        )
        rows = result.all()

    if not rows:
        return

    fingerprint = ",".join(str(r.telegram_id) for r in rows)
    if not _should_send_alert("negative_balance", fingerprint):
        return

    lines = ["🚨 <b>DB ALERT: Отрицательные балансы</b>\n"]
    lines.append(f"Найдено: <b>{len(rows)}</b> юзеров\n")
    for row in rows:
        lines.append(
            f"User {row.telegram_id}: "
            f"free={row.balance_free}, paid={row.balance_paid}, gen={row.generations_balance}"
        )
    lines.append("\n⚠️ Это баг в списании — срочно проверь!")

    await _notify(bot, "\n".join(lines))


async def check_duplicate_payment_ids(bot: Bot) -> None:
    """3. Дублирующиеся payment_id (двойное начисление)."""
    async with async_session() as session:
        result = await session.execute(
            select(Purchase.payment_id, func.count(Purchase.id).label('cnt'))
            .where(Purchase.payment_id.isnot(None))
            .group_by(Purchase.payment_id)
            .having(func.count(Purchase.id) > 1)
            .limit(20)
        )
        rows = result.all()

    if not rows:
        return

    fingerprint = ",".join(r.payment_id for r in rows)
    if not _should_send_alert("duplicate_payment_id", fingerprint):
        return

    lines = ["🚨 <b>DB ALERT: Дублирующиеся payment_id</b>\n"]
    lines.append(f"Найдено: <b>{len(rows)}</b> платёж(ей) с дубликатами\n")
    for row in rows:
        lines.append(f"{row.payment_id}: {row.cnt} записей")
    lines.append("\n⚠️ Финансовый баг — возможно двойное начисление!")

    await _notify(bot, "\n".join(lines))


async def check_stuck_video_tasks(bot: Bot) -> None:
    """4. Зависшие видео задачи (status=waiting > 30 минут, не возвращены)."""
    async with async_session() as session:
        threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=30)
        result = await session.execute(
            select(VideoGenerationTask.id, VideoGenerationTask.user_id, VideoGenerationTask.task_id, VideoGenerationTask.created_at)
            .where(
                VideoGenerationTask.status == 'waiting',
                VideoGenerationTask.created_at < threshold,
                VideoGenerationTask.refunded == False
            )
            .order_by(VideoGenerationTask.created_at)
            .limit(20)
        )
        rows = result.all()

    if not rows:
        return

    fingerprint = ",".join(str(r.id) for r in rows)
    if not _should_send_alert("stuck_video_tasks", fingerprint):
        return

    lines = ["🚨 <b>DB ALERT: Зависшие видео задачи</b>\n"]
    lines.append(f"Найдено: <b>{len(rows)}</b> задач висят &gt; 30 минут\n")
    for row in rows:
        age_min = int((datetime.now(timezone.utc).replace(tzinfo=None) - row.created_at).total_seconds() / 60)
        lines.append(f"Task {row.id} | User {row.user_id} | Kling {row.task_id[:12]}... | {age_min} мин")
    lines.append("\nПроверь вебхуки и статус задач в Kling API")

    await _notify(bot, "\n".join(lines))


async def check_balance_mismatch(session: AsyncSession) -> tuple[int, list]:
    """5. Несоответствие балансов (generations_balance != balance_free + balance_paid)."""
    result = await session.execute(
        select(User.id, User.telegram_id, User.balance_free, User.balance_paid, User.generations_balance)
        .where(User.generations_balance != (User.balance_free + User.balance_paid))
        .limit(50)
    )
    rows = result.all()
    return len(rows), rows


async def check_purchases_zero_amount(session: AsyncSession) -> int:
    """6. Покупки с amount=0 (регрессия фикса) за последние 24 часа."""
    threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)
    result = await session.execute(
        select(func.count(Purchase.id))
        .where(
            Purchase.amount == 0,
            Purchase.status == 'succeeded',
            Purchase.created_at > threshold
        )
    )
    return result.scalar() or 0


async def check_succeeded_without_completed_at(session: AsyncSession) -> int:
    """7. Завершённые покупки без completed_at."""
    result = await session.execute(
        select(func.count(Purchase.id))
        .where(
            Purchase.status == 'succeeded',
            Purchase.completed_at.is_(None)
        )
    )
    return result.scalar() or 0


async def check_stuck_broadcasts(bot: Bot) -> None:
    """8. Зависшие рассылки (status=sending > 2 часа)."""
    async with async_session() as session:
        threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
        result = await session.execute(
            select(Broadcast.id, Broadcast.sent_count, Broadcast.total_users, Broadcast.started_at)
            .where(
                Broadcast.status == 'sending',
                Broadcast.started_at < threshold
            )
        )
        rows = result.all()

    if not rows:
        return

    fingerprint = ",".join(str(r.id) for r in rows)
    if not _should_send_alert("stuck_broadcast", fingerprint):
        return

    lines = ["🚨 <b>DB ALERT: Зависшие рассылки</b>\n"]
    lines.append(f"Найдено: <b>{len(rows)}</b> рассылок висят &gt; 2 часа\n")
    for row in rows:
        age_h = int((datetime.now(timezone.utc).replace(tzinfo=None) - row.started_at).total_seconds() / 3600)
        lines.append(f"Broadcast {row.id}: {row.sent_count}/{row.total_users} | {age_h}ч назад")
    lines.append("\nВозможно процесс broadcaster завис — проверь логи")

    await _notify(bot, "\n".join(lines))


async def build_daily_db_report(bot: Bot) -> None:
    """9. Ежедневный отчёт: статистика за 24 часа + предупреждения + размеры таблиц."""
    async with async_session() as session:
        threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)

        # Новые юзеры
        new_users = await session.scalar(
            select(func.count(User.id)).where(User.created_at > threshold)
        ) or 0

        # Покупки (рубли)
        purchases_rub = await session.execute(
            select(func.count(Purchase.id), func.sum(Purchase.price))
            .where(
                Purchase.status == 'succeeded',
                Purchase.completed_at > threshold,
                or_(Purchase.payment_method != 'crypto_pay', Purchase.payment_method.is_(None))
            )
        )
        purch_rub_row = purchases_rub.first()
        purch_rub_count = purch_rub_row[0] or 0
        purch_rub_sum = (purch_rub_row[1] or 0) / 100

        # Покупки (крипта)
        purchases_crypto = await session.execute(
            select(func.count(Purchase.id), func.sum(Purchase.price))
            .where(
                Purchase.status == 'succeeded',
                Purchase.completed_at > threshold,
                Purchase.payment_method == 'crypto_pay'
            )
        )
        purch_crypto_row = purchases_crypto.first()
        purch_crypto_count = purch_crypto_row[0] or 0
        purch_crypto_sum = purch_crypto_row[1] or 0

        # Генерации
        generations = await session.scalar(
            select(func.count(BananaTransaction.id))
            .where(
                BananaTransaction.transaction_type == 'spent',
                BananaTransaction.created_at > threshold
            )
        ) or 0

        # Предупреждения
        balance_mismatch_cnt, _ = await check_balance_mismatch(session)
        zero_amount_cnt = await check_purchases_zero_amount(session)
        no_completed_at_cnt = await check_succeeded_without_completed_at(session)

        # Размеры таблиц
        table_sizes = await session.execute(
            text("""
                SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) AS size
                FROM pg_catalog.pg_statio_user_tables
                ORDER BY pg_total_relation_size(relid) DESC
                LIMIT 10
            """)
        )
        sizes = table_sizes.all()

    msk_now = datetime.now(moscow_tz)
    date_str = msk_now.strftime("%d.%m.%Y")

    lines = [f"📊 <b>Ежедневный отчёт БД — {date_str}</b>\n"]
    lines.append(f"👥 Новых юзеров: <b>{new_users}</b>")

    if purch_crypto_count > 0:
        lines.append(
            f"💰 Покупок: <b>{purch_rub_count}</b> на сумму <b>{purch_rub_sum:.2f} ₽</b> "
            f"+ <b>{purch_crypto_count}</b> крипто ({purch_crypto_sum:.2f} USDT)"
        )
    else:
        lines.append(f"💰 Покупок: <b>{purch_rub_count}</b> на сумму <b>{purch_rub_sum:.2f} ₽</b>")

    lines.append(f"🎨 Генераций: <b>{generations}</b>\n")

    lines.append("⚠️ <b>Предупреждения:</b>")
    if balance_mismatch_cnt > 0:
        lines.append(f"  • Несоответствие балансов: <b>{balance_mismatch_cnt}</b> юзеров")
    else:
        lines.append("  • Несоответствие балансов: 0 ✅")

    if zero_amount_cnt > 0:
        lines.append(f"  • Покупки без amount: <b>{zero_amount_cnt}</b> ⚠️")
    else:
        lines.append("  • Покупки без amount: 0 ✅")

    if no_completed_at_cnt > 0:
        lines.append(f"  • Покупки без completed_at: <b>{no_completed_at_cnt}</b>")
    else:
        lines.append("  • Покупки без completed_at: 0 ✅")

    lines.append("\n💾 <b>Размер таблиц:</b>")
    for table_name, size in sizes:
        lines.append(f"  {table_name}: {size}")

    await _notify(bot, "\n".join(lines))


async def run_db_monitoring(bot: Bot) -> None:
    """Главный цикл мониторинга: проверки по расписанию + ежедневный отчёт в 09:00 MSK."""
    logger.info("🔍 DB monitoring started")

    if not config.DB_MONITOR_CHANNEL_ID:
        logger.warning("DB_MONITOR_CHANNEL_ID не настроен — мониторинг работает без уведомлений")

    last_run: dict[str, float] = {}
    last_daily_date: Optional[date] = None

    while True:
        try:
            now = time.monotonic()
            msk_now = datetime.now(moscow_tz)

            # Критические проверки с интервалами
            if now - last_run.get("stuck_gen", 0) > 5 * 60:
                await check_stuck_generation_tasks(bot)
                last_run["stuck_gen"] = now

            if now - last_run.get("stuck_video", 0) > 15 * 60:
                await check_stuck_video_tasks(bot)
                last_run["stuck_video"] = now

            if now - last_run.get("negative_balance", 0) > 30 * 60:
                await check_negative_balances(bot)
                last_run["negative_balance"] = now

            if now - last_run.get("stuck_broadcast", 0) > 30 * 60:
                await check_stuck_broadcasts(bot)
                last_run["stuck_broadcast"] = now

            if now - last_run.get("duplicate_payment", 0) > 60 * 60:
                await check_duplicate_payment_ids(bot)
                last_run["duplicate_payment"] = now

            # Ежедневный отчёт в 09:00 MSK (окно 2 минуты)
            if (msk_now.hour == 9 and msk_now.minute < 2 and 
                msk_now.date() != last_daily_date):
                await build_daily_db_report(bot)
                last_daily_date = msk_now.date()
                logger.info(f"✅ Daily DB report sent for {last_daily_date}")

        except asyncio.CancelledError:
            logger.info("DB monitoring cancelled")
            raise
        except Exception as e:
            logger.error(f"Ошибка в DB мониторинге: {e}", exc_info=True)

        await asyncio.sleep(60)
