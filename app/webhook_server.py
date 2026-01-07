from aiohttp import web
from aiogram import Bot
from app.database import async_session
from app.services.payment_service import mark_purchase_as_succeeded
from app.services.user_service import add_paid_balance, get_user_balance, get_user_financial_stats
from app.services.admin_logger import log_payment
from app.models import User
from sqlalchemy import select

# Настройки
WEBHOOK_PORT = 5001
WEBHOOK_PATH = "/yookassa_webhook"

async def handle_yookassa(request):
    try:
        data = await request.json()
        event = data.get("event")
        object_ = data.get("object", {})
        
        if event == "payment.succeeded":
            payment_id = object_.get("id")
            metadata = object_.get("metadata", {})
            
            # Ищем ID юзера
            user_id = int(metadata.get("user_id", 0)) if metadata.get("user_id") else 0
            amount = float(object_.get("amount", {}).get("value", 0.0))

            print(f"🔔 WEBHOOK: Пришла оплата {amount}р от {user_id}")

            if user_id:
                async with async_session() as session:
                    # 1. Записываем покупку
                    await mark_purchase_as_succeeded(session, user_id, amount)
                    
                    # 2. Начисляем бананы (по цене)
                    gens_to_add = 0
                    item_name = object_.get("description", "Покупка бананов")

                    if amount == 79.0: 
                        gens_to_add = 8
                        item_name = "Start: 8 бананов"
                    elif amount == 299.0: 
                        gens_to_add = 44
                        item_name = "Medium: 44 банана"
                    elif amount == 699.0: 
                        gens_to_add = 140
                        item_name = "Big: 140 бананов 🔥"
                    elif amount == 1499.0: 
                        gens_to_add = 340
                        item_name = "Mega: 340 бананов"
                    elif amount == 3499.0: 
                        gens_to_add = 832
                        item_name = "Whale: 832 банана 👑"
                    
                    if gens_to_add > 0:
                        await add_paid_balance(session, user_id, gens_to_add)
                        await session.commit()

                    # ======================================================
                    # 📊 ВОТ ЭТОГО НЕ ХВАТАЛО В СТАРОЙ ВЕРСИИ
                    # ======================================================
                    
                    # Получаем статистику через user_service (он у нас теперь исправлен)
                    stats = await get_user_financial_stats(session, user_id)
                    
                    # Шлем лог
                    bot_instance = request.app['bot']
                    new_bal = await get_user_balance(session, user_id)
                    
                    # Ищем объект юзера для лога
                    u_res = await session.execute(select(User).where(User.telegram_id == user_id))
                    db_user = u_res.scalar_one_or_none()

                    await log_payment(
                        bot_instance, 
                        db_user, 
                        amount, 
                        item_name, 
                        new_bal, 
                        stats=stats # <--- ПЕРЕДАЕМ СТАТИСТИКУ
                    )
                    
# Уведомление юзеру
                    try:
                        await bot_instance.send_message(
                            user_id,
                            f"✅ <b>Оплата прошла успешно!</b>\n\n🍌 Начислено: <b>+{gens_to_add} бананов</b>\nСпасибо за покупку! Можно снова творить 🎨",
                            parse_mode="HTML"
                        )
                    except:
                        pass

        return web.Response(status=200)
    except Exception as e:
        print(f"🔴 Webhook Error: {e}")
        return web.Response(status=500)

async def start_webhook_server(bot: Bot):
    app = web.Application()
    app['bot'] = bot
    app.router.add_post(WEBHOOK_PATH, handle_yookassa)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT)
    await site.start()
    print(f"🚀 Сервер оплат запущен на порту {WEBHOOK_PORT}")