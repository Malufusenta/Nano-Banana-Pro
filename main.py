import asyncio
import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import pytz

# Создаем константу московского времени для всего файла
moscow_tz = pytz.timezone('Europe/Moscow')

# Настройка логирования ПЕРВЫМ ДЕЛОМ
file_handler = TimedRotatingFileHandler(
    filename='bot.log',
    when='midnight',
    interval=1,
    backupCount=7,
    encoding='utf-8'
)
file_handler.suffix = "%Y%m%d"
file_handler.setFormatter(
    logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
)

console_handler = logging.StreamHandler()
console_handler.setFormatter(
    logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
)

logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, console_handler],
    force=True
)

logging.getLogger('aiogram').setLevel(logging.INFO)
logging.getLogger('aiogram.event').setLevel(logging.INFO)

logger = logging.getLogger(__name__)

class PrintLogger:
    def __init__(self, logger):
        self.logger = logger
        self.terminal = sys.__stdout__
        
    def write(self, message):
        if message.strip():
            self.logger.info(message.strip())
        self.terminal.write(message)
        
    def flush(self):
        self.terminal.flush()

sys.stdout = PrintLogger(logger)

# Импорты после настройки логирования
from aiogram import Bot, Dispatcher
from sqlalchemy import update

from app.database import engine, Base, async_session
from app.models import SystemStatus
from app.handlers import start, generation, payment, crypto_payment, menu_actions, admin, admin_scenarios
from app.handlers.generation_flow import preflight_router, video_router
from app.middlewares.album import AlbumMiddleware
from app.middlewares.admin_spy import AdminSpyMiddleware
from app.middlewares.antifraud import AntiFraudMiddleware
from app.middlewares.block_middleware import BlockCheckMiddleware
from app.middlewares.locale import LocaleMiddleware
from app.services.yandex_metrica import init_metrica_service
from app.services.analytics_service import get_analytics_report, format_report_message
from app.services.image_hash_service import cleanup_expired_hashes
from app.services.db_monitor import run_db_monitoring
from app.webhook_server import start_webhook_server
from app import config

print(f"🔥 РОУТЕР admin_scenarios загружен: {admin_scenarios.router}")

async def send_daily_report(bot):
    """
    Отправляет ежедневный отчёт администратору в 08:30
    За период: вчерашние сутки (00:00 - 23:59)
    """
    from app.config import ADMIN_IDS
    
    # Вчерашний день по Москве
    yesterday = datetime.now(moscow_tz) - timedelta(days=1)
    date_from = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
    date_to = yesterday.replace(hour=23, minute=59, second=59, microsecond=999999)
    
    # Конвертируем в UTC и убираем таймзону (чтобы матчилось с БД)
    date_from = date_from.astimezone(pytz.utc).replace(tzinfo=None)
    date_to = date_to.astimezone(pytz.utc).replace(tzinfo=None)
    
    # Собираем статистику
    async with async_session() as session:
        data = await get_analytics_report(session, date_from, date_to)
    
    # Форматируем сообщение
    date_str = yesterday.strftime("%d.%m.%Y") + " (вчера)"
    message = await format_report_message(data, date_str)
    
    # Отправляем всем админам
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, message, parse_mode="HTML")
        except Exception as e:
            logger.error(f"❌ Ошибка отправки отчёта админу {admin_id}: {e}", exc_info=True)


async def cleanup_image_hashes_job() -> None:
    async with async_session() as session:
        deleted_rows = await cleanup_expired_hashes(session)
    if deleted_rows:
        logger.info("🧹 Removed %s expired image hashes", deleted_rows)


async def heartbeat_worker() -> None:
    """Пишет UTC-время в system_status каждые 30 с; сбой не роняет основной процесс."""
    while True:
        try:
            await asyncio.sleep(30)
            async with async_session() as session:
                await session.execute(
                    update(SystemStatus)
                    .where(SystemStatus.id == 1)
                    .values(
                        last_heartbeat=datetime.now(timezone.utc).replace(tzinfo=None)
                    )
                )
                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Heartbeat update failed (ignored): %s", e, exc_info=True)


async def main():
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()

    # 🔥 ПРОВЕРКА БЛОКИРОВКИ (ПЕРВАЯ, САМАЯ ВАЖНАЯ!)
    dp.message.middleware(BlockCheckMiddleware())
    dp.callback_query.middleware(BlockCheckMiddleware())
    dp.message.middleware(LocaleMiddleware())
    dp.callback_query.middleware(LocaleMiddleware())

    dp.message.middleware(AntiFraudMiddleware())
    dp.callback_query.middleware(AntiFraudMiddleware())
    dp.message.middleware(AdminSpyMiddleware())
    dp.callback_query.middleware(AdminSpyMiddleware()) 
    # Альбом собирается внутренним слоем; жалобы — внешним (регистрируем последним = вызывается первым)
    dp.message.middleware(AlbumMiddleware())
    from app.middlewares.complaint_filter import ComplaintFilterMiddleware
    dp.message.middleware(ComplaintFilterMiddleware())

    dp.include_router(admin.router)
    dp.include_router(admin_scenarios.router)
    dp.include_router(start.router)
    dp.include_router(crypto_payment.router)
    dp.include_router(payment.router)
    dp.include_router(menu_actions.router)
    dp.include_router(preflight_router)
    dp.include_router(video_router)
    dp.include_router(generation.router)
    

    # Инициализация Яндекс.Метрики
    init_metrica_service(
        counter_id=config.YANDEX_METRICA_COUNTER_ID,
        token=config.YANDEX_METRICA_TOKEN,
        enabled=config.YANDEX_METRICA_ENABLED,  # False для теста
        bot_start_target=config.YANDEX_METRICA_BOT_START_TARGET,
        ms_token=config.YANDEX_METRICA_MS_TOKEN,
    )

    logger.info("✅ Бот запущен!")

    # Создаём планировщик для автоматических отчётов, ЖЕСТКО ЗАДАЕМ МОСКВУ
    scheduler = AsyncIOScheduler(timezone=moscow_tz)
    
    # Добавляем задачу: каждый день в 04:30
    scheduler.add_job(
        send_daily_report,
        CronTrigger(hour=4, minute=30, timezone=moscow_tz),
        args=[bot],
        id='daily_report',
        replace_existing=True
    )
    scheduler.add_job(
        cleanup_image_hashes_job,
        IntervalTrigger(hours=1, timezone=moscow_tz),
        id='image_hash_cleanup',
        replace_existing=True
    )
    
    # Запускаем планировщик
    scheduler.start()
    logger.info("📅 Планировщик запущен: отчёты будут отправляться в 04:30, image_hashes чистятся каждый час")

    asyncio.create_task(heartbeat_worker(), name="heartbeat_worker")
    logger.info("💓 Heartbeat worker scheduled (system_status every 30s)")

    asyncio.create_task(run_db_monitoring(bot), name="db_monitor")
    logger.info("🔍 DB monitoring started")

    # Сервер aiohttp на 5001: вебхуки оплат + публичный GET /health (см. app/webhook_server.py)
    await start_webhook_server(bot)

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())