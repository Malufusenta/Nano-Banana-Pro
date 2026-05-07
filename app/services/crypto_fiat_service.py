from __future__ import annotations

import json
from typing import Any

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import config
from app.models import CryptoPayInvoice, User
from app.services.i18n import t
from app.services.payment_service import mark_purchase_as_succeeded, update_purchase_analytics
from app.services.user_service import add_paid_balance, set_last_payment_method


def _banana_suffix(locale: str, count: int) -> str:
    if locale == "ru":
        n = abs(count)
        if n % 10 == 1 and n % 100 != 11:
            return "банан"
        if 2 <= n % 10 <= 4 and (n % 100 < 10 or n % 100 >= 20):
            return "банана"
        return "бананов"
    if count == 1:
        return t("banana.one", locale)
    return t("banana.many", locale)


def parse_fiat_invoice_payload(raw: str | dict | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    try:
        uid = int(data["user_id"])
        pkg = str(data["pkg"])
        bananas = int(data["bananas"])
    except (KeyError, TypeError, ValueError):
        return None
    expected = config.BANANA_PACKAGES.get(pkg)
    if not expected or int(expected["bananas"]) != bananas:
        return None
    return {"user_id": uid, "pkg": pkg, "bananas": bananas}


async def fulfill_crypto_fiat_invoice(
    session,
    *,
    invoice_id: int,
    parsed: dict[str, Any],
    price_usd: float,
    bot: Bot | None = None,
) -> bool:
    """
    Идемпотентное начисление по fiat-инвойсу. True — начисление выполнено впервые.
    """
    row = CryptoPayInvoice(
        invoice_id=invoice_id,
        user_id=parsed["user_id"],
        package_key=parsed["pkg"],
        bananas=parsed["bananas"],
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return False

    user_id = parsed["user_id"]
    payment_id = f"crypto_{invoice_id}"
    await mark_purchase_as_succeeded(session, user_id, price_usd, parsed["bananas"])
    await update_purchase_analytics(
        session,
        user_id,
        price_usd,
        f"{parsed['bananas']} bananas (Crypto USD)",
        payment_id=payment_id,
        payment_method="crypto_pay",
    )
    await add_paid_balance(session, user_id, parsed["bananas"])
    await set_last_payment_method(session, user_id, "crypto_pay")
    await session.commit()

    if bot:
        res = await session.execute(select(User).where(User.telegram_id == user_id))
        db_user = res.scalar_one_or_none()
        locale = db_user.locale if db_user and db_user.locale else "en"
        suffix = _banana_suffix(locale, parsed["bananas"])
        try:
            await bot.send_message(
                user_id,
                t("payment.success", locale, amount=parsed["bananas"], suffix=suffix),
                parse_mode="HTML",
            )
        except Exception:
            pass

    return True
