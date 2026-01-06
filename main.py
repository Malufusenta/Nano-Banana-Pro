import asyncio
import logging
from aiogram import Bot, Dispatcher
from app.database import engine, Base
from app.handlers import start, generation, payment, menu_actions, admin
from app.middlewares.album import AlbumMiddleware 
from app.middlewares.admin_spy import AdminSpyMiddleware
from app.middlewares.antifraud import AntiFraudMiddleware

# 👇 ИМПОРТИРУЕМ НАШ НОВЫЙ СЕРВЕР
from app.webhook_server import start_webhook_server 

from app import config

async def main():
    logging.basicConfig(level=logging.INFO)
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()

    dp.message.middleware(AntiFraudMiddleware())
    dp.callback_query.middleware(AntiFraudMiddleware())
    dp.message.middleware(AdminSpyMiddleware())
    dp.callback_query.middleware(AdminSpyMiddleware()) 
    dp.message.middleware(AlbumMiddleware()) 

    dp.include_router(admin.router)
    dp.include_router(start.router)
    dp.include_router(payment.router)
    dp.include_router(menu_actions.router)
    dp.include_router(generation.router)

    print("✅ Бот запущен!")

    # 👇 ЗАПУСКАЕМ СЕРВЕР ПАРАЛЛЕЛЬНО С БОТОМ
    await start_webhook_server(bot)

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())