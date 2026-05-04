from aiogram import F, Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app import config
from app.handlers.payment import get_banana_label
from app.services.crypto_pay import crypto_pay
from app.services.i18n import resolve_locale, t

router = Router()


def _locale(user: types.User | None) -> str:
    return resolve_locale(user.language_code if user else None)


@router.callback_query(F.data == "buy_bananas_crypto")
async def cb_buy_bananas_crypto(callback: types.CallbackQuery):
    locale = _locale(callback.from_user)
    builder = InlineKeyboardBuilder()
    # По числу бананов (а не по алфавиту ключей s/m/l/xl/xxl)
    for key, pkg in sorted(
        config.BANANA_PACKAGES.items(),
        key=lambda item: int(item[1]["bananas"]),
    ):
        bananas = pkg["bananas"]
        usdt = pkg["usdt"]
        suffix = get_banana_label(locale, bananas)
        btn = f"{bananas} {suffix} — {usdt} USDT/TON"
        builder.button(text=btn, callback_data=f"buy_pkg:{key}")
    builder.button(text=t("shop.back_to_methods", locale), callback_data="open_shop_menu")
    builder.adjust(1)
    await callback.message.edit_text(
        t("shop.banana_crypto_title", locale).strip(),
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("buy_pkg:"))
async def cb_buy_pkg_fiat(callback: types.CallbackQuery):
    locale = _locale(callback.from_user)
    key = callback.data.split(":", 1)[1]
    pkg = config.BANANA_PACKAGES.get(key)
    if not pkg:
        await callback.answer(t("shop.tariff_not_found", locale), show_alert=True)
        return

    bananas = pkg["bananas"]
    amount_str = pkg["usdt"]
    description = f"Bananas {bananas} (pack {key})"
    try:
        invoice = await crypto_pay.create_invoice(
            telegram_user_id=callback.from_user.id,
            package_key=key,
            bananas=bananas,
            fiat_amount_usd=amount_str,
            description=description,
        )
    except Exception as e:
        print(f"Crypto Pay invoice error: {e}")
        await callback.answer(t("shop.payment_error", locale), show_alert=True)
        return

    pay_url = invoice.get("bot_invoice_url") or invoice.get("pay_url")
    text = t(
        "payment.crypto_fiat_invoice",
        locale,
        usd=amount_str,
        bananas=bananas,
        suffix=get_banana_label(locale, bananas),
    )
    builder = InlineKeyboardBuilder()
    if pay_url:
        builder.button(text=t("payment.crypto_open", locale), url=pay_url)
    builder.button(text=t("shop.back_to_methods", locale), callback_data="open_shop_menu")
    builder.adjust(1)
    await callback.message.edit_text(
        text,
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await callback.answer()
