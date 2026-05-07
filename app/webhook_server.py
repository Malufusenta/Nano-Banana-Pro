import hashlib
import hmac
import json
import logging
import os
import ssl

from aiohttp import web
from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import config
from app.database import async_session
from app.kling_webhook import handle_kling_callback
from app.models import Purchase, User
from app.packages import PACKAGES
from app.services.admin_logger import log_payment
from app.services.crypto_fiat_service import fulfill_crypto_fiat_invoice, parse_fiat_invoice_payload
from app.services.crypto_pay import crypto_pay
from app.services.i18n import t
from app.services.payment_service import mark_purchase_as_succeeded, update_purchase_analytics
from app.services.user_service import (
    add_paid_balance,
    get_user_balance,
    get_user_financial_stats,
    set_last_payment_method,
)
from app.services.yandex_metrica import metrica_service


WEBHOOK_PORT = 5001
WEBHOOK_PATH = "/yookassa_webhook"
WEBHOOK_CRYPTO_PAY_PATH = "/webhooks/crypto-pay"

logger = logging.getLogger(__name__)


async def handle_crypto_pay_webhook(request):
    raw = await request.read()
    sig = request.headers.get("crypto-pay-api-signature") or request.headers.get("Crypto-Pay-Api-Signature")
    token = crypto_pay.token or ""
    secret = hashlib.sha256(token.encode()).digest()
    expected = hmac.new(secret, raw, hashlib.sha256).hexdigest()
    if not crypto_pay.verify_webhook_signature(raw, sig):
        return web.Response(status=403, text="invalid signature")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return web.json_response({"ok": True})

    inv = None
    if data.get("update_type") == "invoice_paid":
        inv = data.get("payload")
    elif isinstance(data.get("update"), dict):
        inv = data["update"]
    elif data.get("invoice_id") is not None:
        inv = data

    if not isinstance(inv, dict):
        return web.json_response({"ok": True})

    if (inv.get("status") or "").lower() != "paid":
        return web.json_response({"ok": True})

    invoice_id = inv.get("invoice_id")
    if invoice_id is None:
        invoice_id = inv.get("id")
    if invoice_id is None:
        return web.json_response({"ok": True})
    try:
        invoice_id = int(invoice_id)
    except (TypeError, ValueError):
        return web.json_response({"ok": True})

    logger.info(
        f"💎 Crypto Pay webhook: invoice_id={invoice_id}, status={inv.get('status')}, "
        f"paid_asset={inv.get('paid_asset')}, paid_amount={inv.get('paid_amount')}, "
        f"fiat={inv.get('amount')} {inv.get('fiat')}"
    )

    parsed = parse_fiat_invoice_payload(inv.get("payload"))
    if not parsed:
        return web.json_response({"ok": True})

    pkg = parsed["pkg"]
    if pkg not in config.BANANA_PACKAGES:
        return web.json_response({"ok": True})

    price_usd = float(config.BANANA_PACKAGES[pkg]["usdt"])

    try:
        bot_instance = request.app["bot"]
        async with async_session() as session:
            result = await fulfill_crypto_fiat_invoice(
                session,
                invoice_id=invoice_id,
                parsed=parsed,
                price_usd=price_usd,
                bot=bot_instance,
            )
            if result:
                logger.info(
                    f"✅ Crypto Pay начислено: user_id={parsed['user_id']}, bananas={parsed['bananas']}, "
                    f"invoice_id={invoice_id}, paid_asset={inv.get('paid_asset')}, "
                    f"paid_amount={inv.get('paid_amount')}"
                )
                user_id = parsed["user_id"]
                stats = await get_user_financial_stats(session, user_id)
                new_bal = await get_user_balance(session, user_id)
                u_res = await session.execute(select(User).where(User.telegram_id == user_id))
                db_user = u_res.scalar_one_or_none()
                item_name = f"💎 Crypto Pay: +{parsed['bananas']} 🍌"
                await log_payment(
                    bot_instance,
                    db_user,
                    price_usd,
                    item_name,
                    new_bal,
                    stats=stats,
                )
            else:
                logger.warning(
                    f"⚠️ Crypto Pay дубликат (уже начислено): invoice_id={invoice_id}, "
                    f"user_id={parsed['user_id']}"
                )
    except Exception as e:
        print(f"🔴 Crypto Pay webhook error: {e}")
        return web.Response(status=500)

    return web.json_response({"ok": True})


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
                        if payment_method == "bank_card":
                            await set_last_payment_method(session, user_id, "yookassa_card")
                        elif payment_method == "sbp":
                            await set_last_payment_method(session, user_id, "yookassa_sbp")
                        else:
                            await set_last_payment_method(session, user_id, f"yookassa_{payment_method or 'unknown'}")
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
                        locale = db_user.locale if db_user and db_user.locale else "en"
                        await bot_instance.send_message(
                            user_id,
                            t("payment.success", locale, amount=gens_to_add, suffix="bananas" if locale != "ru" else "бананов"),
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
    app["bot"] = bot
    app.router.add_post(WEBHOOK_PATH, handle_yookassa)
    app.router.add_post(WEBHOOK_CRYPTO_PAY_PATH, handle_crypto_pay_webhook)
    app.router.add_post("/kling_webhook", handle_kling_callback)

    runner = web.AppRunner(app)
    await runner.setup()
    ssl_context = None
    ssl_cert = os.getenv("SSL_CERT")
    ssl_key = os.getenv("SSL_KEY")
    if ssl_cert and ssl_key:
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(ssl_cert, ssl_key)

    site = web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT, ssl_context=ssl_context)
    await site.start()
    print(f"🚀 Сервер оплат запущен на порту {WEBHOOK_PORT}")