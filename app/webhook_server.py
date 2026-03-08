from aiohttp import web
from aiogram import Bot
from app.database import async_session
from app.services.payment_service import mark_purchase_as_succeeded
from app.services.user_service import add_paid_balance, get_user_balance, get_user_financial_stats
from app.services.admin_logger import log_payment
from app.kling_webhook import handle_kling_callback  # ← Добавь в начало
from app.models import User
from app.packages import PACKAGES
from sqlalchemy import select
from app.services.payment_service import mark_purchase_as_succeeded, update_purchase_analytics
from app.services.yandex_metrica import metrica_service
from app.models import User, Purchase  # ← В начале файла
from sqlalchemy import select     
from sqlalchemy.exc import IntegrityError     # ← В начале файла



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
                    # Проверяем дубль ПЕРЕД обработкой
                    # Проверяем дубль
                    existing = await session.execute(
                        select(Purchase).where(
                            Purchase.payment_id == payment_id,
                            Purchase.status == 'succeeded'
                        )
                    )
                    if existing.scalar_one_or_none():
                        print(f"⚠️ WEBHOOK: Дубль {payment_id}, пропускаем")
                        return web.Response(status=200)
        
                    # Определяем тариф
                   
                    tariff_map = {
                        float(pkg['price']): (pkg['gens'], f"{pkg['gens']} {pkg['suffix']}")
                        for pkg in PACKAGES.values()
                    }
                    
                    tariff_data = tariff_map.get(amount)
                    
                    if not tariff_data:
                        print(f"⚠️ WEBHOOK: Неизвестная сумма оплаты: {amount}₽ от юзера {user_id}")
                        return web.Response(status=200)
                    
                    gens_to_add, tariff_name = tariff_data
                    
                    # 1. Записываем покупку
                    try:
                        await mark_purchase_as_succeeded(session, user_id, amount)

                        income_amount = float(object_.get("income_amount", {}).get("value", 0.0))
                        payment_method = object_.get("payment_method", {}).get("type", None)
                        
                        # 2. Обновляем аналитику
                        await update_purchase_analytics(session, user_id, amount, tariff_name, payment_id,
                            income_amount=income_amount,
                            payment_method=payment_method
)                        
                        # 3. Начисляем бананы
                        await add_paid_balance(session, user_id, gens_to_add)
                        await session.commit()
                    except IntegrityError as e:
                        await session.rollback()
                        if "payment_id" in str(e).lower():
                            print(f"⚠️ WEBHOOK: Дубль payment_id={payment_id}, пропускаем")
                            return web.Response(status=200)
                        print(f"🔴 WEBHOOK ERROR (Integrity): {e}")
                        raise
                    except Exception as e:
                        await session.rollback()
                        print(f"🔴 WEBHOOK ERROR: {e}")
                        raise

# Дальше код без изменений (получение stats, отправка логов)

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
                        tariff_name, 
                        new_bal, 
                        stats=stats
                    )
                    
                    # ✨ ОТПРАВКА КОНВЕРСИИ В ЯНДЕКС.МЕТРИКУ
                    if db_user and db_user.yandex_client_id:
                        from app.services.yandex_metrica import metrica_service
                        from sqlalchemy import desc
                        
                        # Получаем ID последней покупки
                        purchase_result = await session.execute(
                            select(Purchase)
                            .where(Purchase.user_id == user_id, Purchase.status == "succeeded")
                            .order_by(desc(Purchase.id))
                            .limit(1)
                        )
                        last_purchase = purchase_result.scalar_one_or_none()
                        
                        if last_purchase and metrica_service:
                            await metrica_service.send_purchase_conversion(
                                client_id=db_user.yandex_client_id,
                                order_id=last_purchase.id,
                                revenue=amount,
                                tariff_name=tariff_name
                            )
                        
                        # ✨ ОБНОВЛЯЕМ СТАТИСТИКУ РЕКЛАМНОГО СЦЕНАРИЯ
                        if db_user.active_scenario_id:
                            from app.models import AdScenario
                            scenario_result = await session.execute(
                                select(AdScenario).where(AdScenario.id == db_user.active_scenario_id)
                            )
                            scenario = scenario_result.scalar_one_or_none()
                            if scenario:
                                scenario.total_purchases += 1
                                await session.commit()
                    
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
    app.router.add_post("/kling_webhook", handle_kling_callback)  # ← Добавь эту строку

    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT)
    await site.start()
    print(f"🚀 Сервер оплат запущен на порту {WEBHOOK_PORT}")