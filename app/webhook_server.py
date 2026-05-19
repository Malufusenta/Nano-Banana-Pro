import hashlib
import hmac
import json
import logging
import os
import ssl
import time
from datetime import datetime
from decimal import Decimal

from aiohttp import web
from aiogram import Bot
from sqlalchemy import func, select

from app import config
from app.database import async_session
from app.health_routes import handle_health
from app.kling_webhook import handle_kling_callback
from app.models import Purchase, User
from app.packages import PACKAGES
from app.services.admin_logger import log_payment
from app.services.crypto_fiat_service import fulfill_crypto_fiat_invoice, parse_fiat_invoice_payload
from app.services.crypto_pay import crypto_pay
from app.services.i18n import t
from app.services.payment_service import finalize_yookassa_purchase
from app.services.user_service import (
    get_user_balance,
    get_user_financial_stats,
)
from app.services import yandex_metrica


WEBHOOK_PORT = 5001
WEBHOOK_PATH = "/yookassa_webhook"
WEBHOOK_CRYPTO_PAY_PATH = "/webhooks/crypto-pay"

logger = logging.getLogger(__name__)


def _normalize_rub_amount(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _parse_yookassa_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _resolve_tariff_by_amount(amount: Decimal):
    for pkg in PACKAGES.values():
        package_price = _normalize_rub_amount(pkg["price"])
        if package_price == amount:
            return pkg["gens"], f"{pkg['gens']} {pkg['suffix']}", int(pkg["price"])
    return None


async def _run_yookassa_post_commit_effects(
    request,
    *,
    user_id: int,
    amount_rub: int,
    tariff_name: str,
    purchase_id: int,
    gens_to_add: int,
    is_first_purchase: bool,
):
    bot_instance = request.app["bot"]
    db_user = None
    stats = None
    new_bal = None
    successful_purchases_count = 0

    try:
        async with async_session() as session:
            stats = await get_user_financial_stats(session, user_id)
            new_bal = await get_user_balance(session, user_id)
            db_user = await session.scalar(select(User).where(User.telegram_id == user_id))
            if db_user and db_user.yandex_client_id:
                successful_purchases_count = await session.scalar(
                    select(func.count(Purchase.id)).where(
                        Purchase.user_id == user_id,
                        Purchase.status == "succeeded",
                    )
                )
    except Exception as e:
        print(f"Webhook side effects preload error: {e}")

    if db_user and stats is not None and new_bal is not None:
        try:
            await log_payment(
                bot_instance,
                db_user,
                amount_rub,
                tariff_name,
                new_bal,
                stats=stats,
            )
        except Exception as e:
            print(f"Webhook log payment error: {e}")

    if db_user and db_user.yandex_client_id and yandex_metrica.metrica_service:
        try:
            if is_first_purchase and successful_purchases_count == 1:
                await yandex_metrica.metrica_service.send_purchase_conversion(
                    client_id=db_user.yandex_client_id,
                    order_id=purchase_id,
                    revenue=amount_rub,
                    tariff_name=tariff_name,
                )
                print(f"✅ В Метрику отправлена ПЕРВАЯ покупка юзера {user_id}")
            else:
                print(
                    f"⏭️ Повторная покупка юзера {user_id} "
                    f"({successful_purchases_count}-я). В Метрику не отправляем."
                )
        except Exception as e:
            print(f"Webhook metrica error: {e}")

    if db_user:
        try:
            locale = db_user.locale if db_user.locale else "en"
            await bot_instance.send_message(
                user_id,
                t("payment.success", locale, amount=gens_to_add, suffix="bananas" if locale != "ru" else "бананов"),
                parse_mode="HTML",
            )
        except Exception as e:
            print(f"Webhook notify error: {e}")


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
                paid_asset=inv.get("paid_asset", "USDT"),
                paid_amount=float(inv.get("paid_amount", 0)),
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
                paid_asset = inv.get("paid_asset", "USDT")
                paid_amount = float(inv.get("paid_amount", 0))
                asset_emoji = "💎" if paid_asset == "USDT" else "💠"
                item_name = f"{parsed['bananas']} бананов ({asset_emoji} Crypto Pay {paid_asset})"
                await log_payment(
                    bot_instance,
                    db_user,
                    paid_amount,
                    item_name,
                    new_bal,
                    stats=stats,
                    currency=paid_asset,
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
            if not payment_id:
                return web.Response(status=200)

            metadata = object_.get("metadata", {}) or {}
            completed_at = _parse_yookassa_datetime(object_.get("captured_at")) or datetime.utcnow()

            raw_user_id = metadata.get("user_id")
            user_id = int(raw_user_id) if raw_user_id else 0

            amount = _normalize_rub_amount(object_.get("amount", {}).get("value", 0))
            print(f"🔔 WEBHOOK: Пришла оплата {amount}р от {user_id}")

            tariff_data = _resolve_tariff_by_amount(amount)
            if not tariff_data:
                print(f"⚠️ WEBHOOK: Неизвестная сумма оплаты: {amount}₽ от юзера {user_id}")
                return web.Response(status=200)

            gens_to_add, tariff_name, _price_rub = tariff_data
            income_amount = object_.get("income_amount", {}).get("value")
            payment_method = object_.get("payment_method", {}).get("type")

            async with async_session() as session:
                result = await finalize_yookassa_purchase(
                    session,
                    payment_id=payment_id,
                    amount=amount,
                    payment_method=payment_method,
                    income_amount=income_amount,
                    completed_at=completed_at,
                    tariff_name=tariff_name,
                )

                if result.status in {"applied", "duplicate_ignored"}:
                    await session.commit()
                else:
                    await session.rollback()

            if result.status == "applied":
                await _run_yookassa_post_commit_effects(
                    request,
                    user_id=result.user_id,
                    amount_rub=result.price,
                    tariff_name=result.tariff_name or tariff_name,
                    purchase_id=result.purchase_id,
                    gens_to_add=result.amount,
                    is_first_purchase=result.is_first_purchase,
                )
            elif result.status in {"already_processed", "duplicate_ignored", "conflict", "amount_mismatch", "not_found"}:
                print(f"⚠️ WEBHOOK: payment_id={payment_id}, result={result.status}")

        return web.Response(status=200)
    except Exception as e:
        print(f"🔴 Webhook Error: {e}")
        return web.Response(status=500)

async def start_webhook_server(bot: Bot):
    app = web.Application()
    app["bot"] = bot
    app["health_started_at"] = time.time()
    app.router.add_post(WEBHOOK_PATH, handle_yookassa)
    app.router.add_post(WEBHOOK_CRYPTO_PAY_PATH, handle_crypto_pay_webhook)
    app.router.add_post("/kling_webhook", handle_kling_callback)
    app.router.add_get("/health", handle_health)

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