import asyncio
import logging
from aiogram import Bot, Dispatcher
from app.database import engine, Base
from app.handlers import start, generation, payment, menu_actions, admin
from app.middlewares.album import AlbumMiddleware 
from app.middlewares.admin_spy import AdminSpyMiddleware
from app.middlewares.antifraud import AntiFraudMiddleware
from app.middlewares.block_middleware import BlockCheckMiddleware  # 👈 ДОБАВЬ
from app.services.yandex_metrica import init_metrica_service
from app.handlers import admin_scenarios
import sys
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timedelta
from app.services.analytics_service import get_analytics_report, format_report_message
from app.database import async_session

# 👇 ИМПОРТИРУЕМ НАШ НОВЫЙ СЕРВЕР
from app.webhook_server import start_webhook_server 
import logging

logger = logging.getLogger(__name__)
from app import config
print(f"🔥 РОУТЕР admin_scenarios загружен: {admin_scenarios.router}")
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler

# Настройка логирования с ротацией по дням
file_handler = TimedRotatingFileHandler(
    filename='bot.log',  # БЕЗ даты в имени!
    when='midnight',
    interval=1,
    backupCount=7,
    encoding='utf-8'
)
file_handler.suffix = "%Y%m%d"  # Суффикс добавится автоматически
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

# Перенаправляем print в логгер
class PrintLogger:
    def __init__(self, logger):
        self.logger = logger
        self.terminal = sys.stdout
        
    def write(self, message):
        if message.strip():
            self.logger.info(message.strip())
        self.terminal.write(message)
        
    def flush(self):
        self.terminal.flush()

sys.stdout = PrintLogger(logger)


# Перенаправляем print в логгер
class PrintLogger:
    def __init__(self, logger):
        self.logger = logger
        self.terminal = sys.stdout
        
    def write(self, message):
        if message.strip():  # Игнорируем пустые строки
            self.logger.info(message.strip())
        self.terminal.write(message)
        
    def flush(self):
        self.terminal.flush()

sys.stdout = PrintLogger(logger)

# Включаем подробные логи aiogram
logging.getLogger('aiogram').setLevel(logging.INFO)
logging.getLogger('aiogram.event').setLevel(logging.INFO)

logger = logging.getLogger(__name__)

async def send_daily_report(bot):
    """
    Отправляет ежедневный отчёт администратору в 08:30
    За период: вчерашние сутки (00:00 - 23:59)
    """
    from app.config import ADMIN_IDS
    
    # Вчерашний день
    yesterday = datetime.now() - timedelta(days=1)
    date_from = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
    date_to = yesterday.replace(hour=23, minute=59, second=59, microsecond=999999)
    
    # Собираем статистику
    async with async_session() as session:
        data = await get_analytics_report(session, date_from, date_to)
    
    # Форматируем сообщение
    date_str = yesterday.strftime("%d.%m.%Y") + " (вчера)"
    message = format_report_message(data, date_str)
    
    # Отправляем всем админам
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, message, parse_mode="HTML")
        except Exception as e:
            logger.error(f"❌ Ошибка отправки отчёта админу {admin_id}: {e}", exc_info=True)

async def main():
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()

    # 🔥 ПРОВЕРКА БЛОКИРОВКИ (ПЕРВАЯ, САМАЯ ВАЖНАЯ!)
    dp.message.middleware(BlockCheckMiddleware())
    dp.callback_query.middleware(BlockCheckMiddleware())

    dp.message.middleware(AntiFraudMiddleware())
    dp.callback_query.middleware(AntiFraudMiddleware())
    dp.message.middleware(AdminSpyMiddleware())
    dp.callback_query.middleware(AdminSpyMiddleware()) 
    # 🔥 Фильтр жалоб (должен быть ПЕРЕД AlbumMiddleware)
    from app.middlewares.complaint_filter import ComplaintFilterMiddleware
    dp.message.middleware(ComplaintFilterMiddleware())
    dp.message.middleware(AlbumMiddleware()) 

    dp.include_router(admin.router)
    dp.include_router(admin_scenarios.router)
    dp.include_router(start.router)
    dp.include_router(payment.router)
    dp.include_router(menu_actions.router)
    dp.include_router(generation.router)
    

    # Инициализация Яндекс.Метрики
    init_metrica_service(
        counter_id=config.YANDEX_METRICA_COUNTER_ID,
        token=config.YANDEX_METRICA_TOKEN,
        enabled=config.YANDEX_METRICA_ENABLED  # False для теста
    )

    logger.info("✅ Бот запущен!")

    # Создаём планировщик для автоматических отчётов
    scheduler = AsyncIOScheduler()
    
    # Добавляем задачу: каждый день в 04:30
    scheduler.add_job(
        send_daily_report,
        CronTrigger(hour=4, minute=30),
        args=[bot],
        id='daily_report',
        replace_existing=True
    )
    
    # Запускаем планировщик
    scheduler.start()
    logger.info("📅 Планировщик запущен: отчёты будут отправляться в 04:30")

    # Запускаем сервер оплат параллельно с ботом
    await start_webhook_server(bot)

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())