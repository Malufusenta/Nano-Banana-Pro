from datetime import datetime

from aiogram import Bot, F, Router, types
from aiogram.filters import Command
from aiogram.types import LabeledPrice, PreCheckoutQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from app import config
from app.database import async_session
from app.models import PaymentAttempt, Purchase, User
from app.packages import PACKAGES, STARS_PACKAGES
from app.services.admin_logger import log_payment
from app.services.i18n import resolve_locale, t
from app.services.payment_api import create_yoo_payment, get_yoo_payment_details
from app.services.payment_service import (
    create_purchase_record,
    create_payment_attempt_record,
    finalize_yookassa_purchase,
    get_latest_purchase_attempt,
    mark_purchase_as_succeeded,
    update_purchase_analytics,
)
from app.services.user_service import (
    add_paid_balance,
    admin_change_balance,
    find_user_by_input,
    get_bot_stats,
    get_user_admin_card_data,
    get_user_balance,
    get_user_financial_stats,
    get_user_profile_data,
    set_last_payment_method,
)
from app.services import yandex_metrica
from app.utils.telegram_locale import effective_locale


router = Router()

# 👇👇👇 ВСТАВЬ СЮДА СВОИ ЮЗЕРНЕЙМЫ 👇👇👇
CHANNEL_ID = "@nanobanan_promt"
CHAT_ID = "@nanabanan_chat"


def _menu_labels(key: str) -> set[str]:
    return {t(key, "ru"), t(key, "en"), t(key, "es")}


# 👇 ВСТАВИТЬ В НАЧАЛО ФАЙЛА (после списков PACKAGES)

def get_banana_word(n: int) -> str:
    """Склоняет слово банан в зависимости от числа"""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11: return "банан"
    if 2 <= n % 10 <= 4 and (n % 100 < 10 or n % 100 >= 20): return "банана"
    return "бананов"

def get_banana_suffix(count):
    """Возвращает правильное окончание для слова 'банан'"""
    if count % 10 == 1 and count % 100 != 11:
        return "банан"
    elif count % 10 in [2, 3, 4] and count % 100 not in [12, 13, 14]:
        return "банана"
    else:
        return "бананов"


def get_user_locale_from_event(event_user: types.User | None) -> str:
    return resolve_locale(event_user.language_code if event_user else None)


def get_banana_label(locale: str, count: int) -> str:
    if locale == "ru":
        return get_banana_suffix(count)
    if count == 1:
        return t("banana.one", locale)
    return t("banana.many", locale)


def _parse_yookassa_captured_at(captured_at_str: str | None) -> datetime | None:
    if not captured_at_str:
        return None
    try:
        return datetime.fromisoformat(captured_at_str.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


async def _run_yookassa_post_commit_effects(
    bot: Bot,
    *,
    user_id: int,
    amount_rub: int,
    item_name: str,
    purchase_id: int,
    is_first_purchase: bool,
):
    db_user = None
    stats = None
    new_bal = None

    try:
        async with async_session() as session:
            stats = await get_user_financial_stats(session, user_id)
            new_bal = await get_user_balance(session, user_id)
            db_user = await session.scalar(select(User).where(User.telegram_id == user_id))
    except Exception as e:
        print(f"Payment side effects preload error: {e}")

    if db_user and stats is not None and new_bal is not None:
        try:
            await log_payment(bot, db_user, amount_rub, item_name, new_bal, stats=stats)
        except Exception as e:
            print(f"Log Error: {e}")

    await yandex_metrica._run_first_purchase_metrika_effects(
        db_user,
        purchase_id=purchase_id,
        original_revenue=float(amount_rub),
        currency="RUB",
        payment_system=item_name,
        is_first_purchase=is_first_purchase,
    )


def build_shop_keyboard(locale: str) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    is_ru = locale == "ru"

    if is_ru:
        for key, pkg in PACKAGES.items():
            p = pkg["price"] / pkg["gens"]
            s = f"{p:.2f}".replace(".", ",").rstrip("0").rstrip(",")
            btn = f"{pkg['emoji']}{pkg['gens']} {pkg['suffix']} - {pkg['price']}₽ | {s}₽/🍌"
            builder.button(text=btn, callback_data=f"buy_rub_{key}")

    builder.button(text=t("shop.crypto_usd_button", locale), callback_data="buy_bananas_crypto")
    builder.button(text=t("shop.stars_button", locale), callback_data="open_stars_menu")
    builder.adjust(1)
    return builder


@router.message(F.text.in_(_menu_labels("menu.free")))
@router.message(Command("free"))
async def show_freebies(message: types.Message, bot: Bot):
    await _show_freebies_logic(message, message.from_user.id, bot)

async def _show_freebies_logic(message, user_id: int, bot: Bot, locale: str | None = None):
    locale = await effective_locale(bot, message, user_id, locale)

    bot_info = await bot.me()
    
    ref_link = f"https://t.me/{bot_info.username}?start={user_id}"
    
    text = t("freebies.text", locale, link=ref_link)
    
    builder = InlineKeyboardBuilder()
    builder.button(text=t("menu.buy", locale), callback_data="buy_menu")
    builder.button(text=t("shop.back_to_methods", locale), callback_data="main_menu")
    builder.adjust(1)  # Кнопки друг под другом
    
    await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())
    
@router.callback_query(F.data == "buy_menu")
async def cb_buy_from_freebies(callback: types.CallbackQuery):
    """Открывает магазин из раздела халявы"""
    await callback.message.delete()
    await cmd_shop(callback.message, user_id=callback.from_user.id, locale=get_user_locale_from_event(callback.from_user))
    await callback.answer()

@router.callback_query(F.data == "main_menu")
async def cb_main_menu_from_freebies(callback: types.CallbackQuery):
    """Возврат в главное меню"""
    from app.handlers.start import get_main_kb
    locale = get_user_locale_from_event(callback.from_user)
    await callback.message.delete()
    await callback.message.answer(
        t("menu.main", locale),
        reply_markup=get_main_kb(locale)
    )
    await callback.answer()


# =====================================================================
# 💰 МАГАЗИН И ПРОФИЛЬ
# =====================================================================
@router.message(F.text.in_(_menu_labels("menu.buy")))
@router.message(Command("buy"))
async def cmd_shop(message: types.Message, user_id: int | None = None, locale: str | None = None):
    real_user_id = user_id or (message.from_user.id if message.from_user else None)
    if not real_user_id:
        return
    async with async_session() as session:
        if locale is None:
            locale = await effective_locale(message.bot, message, real_user_id, None, session=session)
        result = await session.execute(
            select(User).where(User.telegram_id == real_user_id)
        )
        user = result.scalar_one_or_none()
        if user and not user.visited_shop_at:
            user.visited_shop_at = datetime.now()
            await session.commit()

    builder = build_shop_keyboard(locale)
    await message.answer(
        t("shop.message", locale),
        reply_markup=builder.as_markup(),
        parse_mode="Markdown",
    )

# Меню Stars
@router.callback_query(F.data == "open_stars_menu")
async def show_stars_menu(callback: types.CallbackQuery):
    locale = get_user_locale_from_event(callback.from_user)
    builder = InlineKeyboardBuilder()
    
    for key, pkg in STARS_PACKAGES.items():
        suffix = get_banana_label(locale, pkg['bananas'])
        btn_text = f"{pkg['emoji']} {pkg['bananas']} {suffix} — {pkg['stars']} ⭐️"
        builder.button(text=btn_text, callback_data=f"buy_stars_{key}")

    builder.button(text=t("shop.back_to_methods", locale), callback_data="open_shop_menu")
    builder.adjust(1)
    
    await callback.message.edit_text(
        t("shop.stars_title", locale),
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()


# Возврат к рублевому меню
@router.callback_query(F.data == "open_shop_menu")
async def back_to_rub_menu(callback: types.CallbackQuery):
    await callback.answer()
    await cmd_shop(callback.message, user_id=callback.from_user.id, locale=get_user_locale_from_event(callback.from_user))

# ... (импорты сверху остаются те же)

# ... Импорты оставь те же, что были (убедись, что create_yoo_payment импортирован)
# from app.services.payment_api import create_yoo_payment

@router.callback_query(F.data.startswith("buy_"))
async def cb_buy_package(callback: types.CallbackQuery, bot: Bot):
    locale = get_user_locale_from_event(callback.from_user)
    parts = callback.data.split("_")
    if len(parts) < 3:
        await callback.answer(t("shop.tariff_not_found", locale))
        return

    payment_type = parts[1]
    if payment_type == "stars":
        pkg_key = "_".join(parts[2:])
        await handle_stars_purchase(callback, bot, pkg_key, locale)
        return
    if payment_type != "rub":
        await callback.answer(t("shop.tariff_not_found", locale))
        return

    pkg_key = "_".join(parts[2:])
    package = PACKAGES.get(pkg_key)
    if not package:
        await callback.answer(t("shop.tariff_not_found", locale))
        return

    sbp_url = None
    card_url = None

    try:
        async with async_session() as session:
            purchase = await create_purchase_record(
                session,
                callback.from_user.id,
                package["price"],
                package["gens"],
            )

            for payment_method in ("sbp", "bank_card"):
                try:
                    payment = create_yoo_payment(
                        package["price"],
                        f"Покупка {package['gens']} бананов",
                        callback.from_user.id,
                        purchase.id,
                        payment_method=payment_method,
                    )
                    await create_payment_attempt_record(
                        session,
                        purchase.id,
                        payment.id,
                        payment_method,
                    )
                    confirmation_url = payment.confirmation.confirmation_url
                    if payment_method == "sbp":
                        sbp_url = confirmation_url
                    else:
                        card_url = confirmation_url
                except Exception as e:
                    print(f"⚠️ Ошибка создания YooKassa платежа ({payment_method}): {e}")

            await session.commit()
    except Exception as e:
        print(f"⚠️ Ошибка подготовки Purchase/PaymentAttempt: {e}")
        await callback.answer(t("shop.payment_error", locale), show_alert=True)
        return

    if not sbp_url and not card_url:
        await callback.answer(t("shop.payment_error", locale), show_alert=True)
        return

    text = (
        "⚡️ <b>Отличный выбор!</b>\n\n"
        f"🍌 Пополнение: <b>+{package['gens']} {package['suffix']}</b>\n"
        f"💳 К оплате: <b>{package['price']}₽</b>\n\n"
        "📄 <i>Оплачивая, вы принимаете условия "
        "<a href='https://telegra.ph/PUBLICHNAYA-OFERTA-12-09-5'>Оферты</a>.</i>"
    )

    builder = InlineKeyboardBuilder()
    if sbp_url:
        builder.button(text="🚀 СБП", url=sbp_url)
    if card_url:
        builder.button(text="💳 Карта/другое", url=card_url)
    builder.button(text="🔙 Назад", callback_data="goto_shop")
    builder.adjust(1)

    await callback.message.edit_text(
        text,
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await callback.answer()

# Создание Stars инвойса
async def handle_stars_purchase(callback: types.CallbackQuery, bot: Bot, pkg_key: str, locale: str):
    package = STARS_PACKAGES.get(pkg_key)
    if not package:
        await callback.answer(t("shop.tariff_not_found", locale))
        return
    
    user_id = callback.from_user.id
    suffix = get_banana_label(locale, package['bananas'])
    
    # Формируем payload для идентификации платежа
    payload = f"{pkg_key}_{user_id}"
    
    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title=f"{package['bananas']} {suffix}",
        description=f"Пополнение баланса на {package['bananas']} {suffix}",
        payload=payload,
        currency="XTR",
        prices=[LabeledPrice(label=f"{package['bananas']} {suffix}", amount=package['stars'])],
        provider_token=""  # Для Stars пустой
    )
    
    await callback.answer()


# =====================================================================
# 3. ПРОВЕРКА ПЛАТЕЖА (ПО КНОПКЕ)
# =====================================================================
@router.callback_query(F.data.startswith("check_"))
async def cb_check_payment(callback: types.CallbackQuery, bot: Bot):
    locale = get_user_locale_from_event(callback.from_user)
    purchase_id = None
    payment_id = None
    bananas_count = 0
    price_rub = 0
    tariff_name = None

    if callback.data.startswith("check_purchase:"):
        try:
            purchase_id = int(callback.data.split(":", 1)[1])
        except (TypeError, ValueError):
            await callback.answer(t("payment.processing_error", locale), show_alert=True)
            return

        async with async_session() as session:
            purchase = await session.scalar(select(Purchase).where(Purchase.id == purchase_id))
        if not purchase:
            await callback.answer(t("payment.processing_error", locale), show_alert=True)
            return

        bananas_count = purchase.amount
        price_rub = purchase.price
        tariff_name = purchase.tariff_name or f"{purchase.amount} bananas"
        payment_id = purchase.payment_id

        if not payment_id:
            async with async_session() as session:
                latest_attempt = await get_latest_purchase_attempt(session, purchase_id)
            payment_id = latest_attempt.payment_id if latest_attempt else None

        if purchase.status == "succeeded":
            await callback.answer("✅")
            await callback.message.edit_text(
                t("payment.success", locale, amount=bananas_count, suffix=get_banana_label(locale, bananas_count)),
                parse_mode="HTML",
            )
            return

        if not payment_id:
            await callback.answer(t("payment.processing_error", locale), show_alert=True)
            return
    else:
        parts = callback.data.split("_")
        if len(parts) < 3:
            await callback.answer(t("payment.processing_error", locale), show_alert=True)
            return

        payment_id = parts[1]
        pkg_key = parts[2]
        package = PACKAGES.get(pkg_key)
        if not package:
            await callback.answer(t("shop.tariff_not_found", locale), show_alert=True)
            return

        bananas_count = package["gens"]
        price_rub = package["price"]
        tariff_name = f"{package['gens']} bananas"

    try:
        payment_details = get_yoo_payment_details(payment_id)
        status = payment_details["status"]

        if status == "succeeded":
            completed_at = _parse_yookassa_captured_at(payment_details["captured_at"])

            if purchase_id is None:
                metadata = payment_details.get("metadata", {}) or {}
                metadata_purchase_id = metadata.get("purchase_id")
                if metadata_purchase_id:
                    try:
                        purchase_id = int(metadata_purchase_id)
                    except (TypeError, ValueError):
                        purchase_id = None

            async with async_session() as session:
                if purchase_id is None:
                    attempt = await session.scalar(
                        select(PaymentAttempt).where(PaymentAttempt.payment_id == payment_id).limit(1)
                    )
                    if attempt:
                        purchase = await session.scalar(
                            select(Purchase).where(Purchase.id == attempt.purchase_id).limit(1)
                        )
                    else:
                        purchase = None
                    if not purchase:
                        purchase = await session.scalar(
                            select(Purchase)
                            .where(
                                Purchase.user_id == callback.from_user.id,
                                Purchase.price == price_rub,
                                Purchase.status == "pending",
                            )
                            .order_by(Purchase.created_at.desc())
                            .limit(1)
                        )
                    if not purchase:
                        purchase_id = None
                    else:
                        purchase_id = purchase.id

                result = await finalize_yookassa_purchase(
                    session,
                    payment_id=payment_id,
                    amount=payment_details["amount"],
                    payment_method=payment_details["payment_method"],
                    income_amount=payment_details["income_amount"],
                    completed_at=completed_at,
                    tariff_name=tariff_name,
                )

                if result.status in {"applied", "duplicate_ignored"}:
                    await session.commit()
                else:
                    await session.rollback()

            if result.status == "applied":
                bananas_count = result.amount
                await _run_yookassa_post_commit_effects(
                    bot,
                    user_id=result.user_id,
                    amount_rub=result.price,
                    item_name=result.tariff_name or tariff_name,
                    purchase_id=result.purchase_id,
                    is_first_purchase=result.is_first_purchase,
                )

            if result.status in {"applied", "already_processed", "duplicate_ignored"}:
                success_amount = result.amount or bananas_count
                await callback.answer("✅")
                await callback.message.edit_text(
                    t("payment.success", locale, amount=success_amount, suffix=get_banana_label(locale, success_amount)),
                    parse_mode="HTML",
                )
                return

            print(f"Check finalize skipped: status={result.status}, purchase_id={purchase_id}, payment_id={payment_id}")
            await callback.answer(t("payment.processing_error", locale), show_alert=True)
            return

        elif status == "pending":
            await callback.answer(t("payment.pending", locale), show_alert=True)

        elif status == "canceled":
            await callback.message.edit_text(t("payment.cancelled", locale), reply_markup=None)

    except Exception as e:
        print(f"Check Error: {e}")
        await callback.answer(t("payment.processing_error", locale), show_alert=True)


@router.message(F.text.in_(_menu_labels("menu.profile"))) 
@router.message(Command("profile"))
async def show_profile(message: types.Message):
    """
    Профиль пользователя (Clean UI по ТЗ)
    - Показывает ID, баланс, счетчик шедевров
    - 3 кнопки: Купить, Заработать, Техподдержка
    """
    user_id = message.from_user.id
    locale = get_user_locale_from_event(message.from_user)
    
    async with async_session() as session:
        data = await get_user_profile_data(session, user_id)

                # 🆕 ПОЛУЧАЕМ СТАТИСТИКУ РЕФЕРАЛОВ
        from app.services.user_service import get_referral_stats
        ref_stats = await get_referral_stats(session, user_id)
    
    if not data:
        await message.answer("❌ Ошибка загрузки профиля.")
        return
    
    user = data['user']
    
    # 📝 ТЕКСТ ПО ТЗ (HTML разметка для моноширинного ID)
    text = t(
        "profile.text",
        locale,
        user_id=user_id,
        balance=user.generations_balance,
        generated=user.total_generations_used,
        ref_count=ref_stats["referral_count"],
        ref_earn=ref_stats["referral_earnings"],
    )
    
    # ⌨️ КНОПКИ ПО ТЗ (3 ряда)
    builder = InlineKeyboardBuilder()
    
    # Ряд 1: Монетизация
    builder.button(text=t("profile.buy_button", locale), callback_data="goto_shop")
    
    # Ряд 2: Удержание
    builder.button(text=t("profile.earn_button", locale), callback_data="goto_free")
    
    # Ряд 3: Доверие (URL-кнопка)
    builder.button(text=t("profile.support_button", locale), url="https://t.me/nan0banana_help")
    
    builder.adjust(1)  # Каждая кнопка на новой строке
    
    await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())

# 👇 ЗАМЕНИТЬ ФУНКЦИЮ cmd_guide НА ЭТУ 👇

@router.message(F.text.in_(_menu_labels("menu.about"))) 
@router.message(Command("about")) # ✅ ДОБАВИЛ ВОТ ЭТО
async def cmd_about(message: types.Message):
    locale = get_user_locale_from_event(message.from_user)
    text = t("about.text", locale)
    # disable_web_page_preview=True чтобы не вылезала превьюшка телеграфа
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)

@router.callback_query(F.data == "goto_shop")
async def cb_goto_shop(callback: types.CallbackQuery):
    await callback.answer()
    
    # Трекинг воронки: зашёл в магазин

    async with async_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
        user = result.scalar_one_or_none()
        if user and not user.visited_shop_at:
            user.visited_shop_at = datetime.now()
            await session.commit()
    
    await cmd_shop(callback.message, user_id=callback.from_user.id, locale=get_user_locale_from_event(callback.from_user))

@router.callback_query(F.data == "goto_free")
async def cb_goto_free(callback: types.CallbackQuery, bot: Bot):
    await callback.answer()
    # Вызываем функцию с заданиями (она тоже в этом файле)
    await _show_freebies_logic(callback.message, callback.from_user.id, bot)  # ✅


# =====================================================================
# ОБРАБОТЧИКИ STARS ПЛАТЕЖЕЙ
# =====================================================================

# Pre-checkout для Stars
@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout: PreCheckoutQuery, bot: Bot):
    locale = resolve_locale(pre_checkout.from_user.language_code if pre_checkout.from_user else None)
    try:
        await bot.answer_pre_checkout_query(
            pre_checkout_query_id=pre_checkout.id,
            ok=True
        )
    except Exception as e:
        await bot.answer_pre_checkout_query(
            pre_checkout_query_id=pre_checkout.id,
            ok=False,
            error_message=t("payment.processing_error", locale)
        )

# Успешная оплата Stars
@router.message(F.successful_payment)
async def process_successful_payment(message: types.Message, bot: Bot):
    locale = get_user_locale_from_event(message.from_user)
    payment = message.successful_payment
    total_amount = payment.total_amount
    payload = payment.invoice_payload
    user_id = message.from_user.id 
    
    # Парсим payload формата: stars_4_627352144
    package = None
    pkg_key = None
    
    try:
        parts = payload.rsplit("_", 1)
        pkg_key = parts[0]
        package = STARS_PACKAGES.get(pkg_key)
    except:
        pass

    # План B: ищем по цене
    if not package:
        package = next((p for p in STARS_PACKAGES.values() if p["stars"] == total_amount), None)

    if not package:
        await message.answer(t("shop.tariff_not_found", locale))
        return
    
    bananas_count = package['bananas']
    suffix = get_banana_label(locale, bananas_count)
    
    # Начисляем бананы
    async with async_session() as session:
        purchase = await create_purchase_record(session, user_id, total_amount, bananas_count)
        purchase_id = purchase.id
        await mark_purchase_as_succeeded(session, user_id, total_amount, bananas_count)
        await session.commit()

        # Обновляем аналитику для Stars (здесь выставляется is_first_purchase)
        await update_purchase_analytics(
            session,
            user_id,
            total_amount,
            "Telegram Stars",
            payment_id=None,
            payment_method="telegram_stars"
        )
        await admin_change_balance(session, user_id, bananas_count)
        await set_last_payment_method(session, user_id, "telegram_stars")
        await session.commit()

        db_user = None
        purchase_row = None
        try:
            new_bal = await get_user_balance(session, user_id)
            stats = await get_user_financial_stats(session, user_id)
            db_user = await session.scalar(select(User).where(User.telegram_id == user_id))
            purchase_row = await session.scalar(select(Purchase).where(Purchase.id == purchase_id))
            await log_payment(
                bot,
                db_user or message.from_user,
                f"{total_amount} ⭐️",
                f"{bananas_count} {suffix} (Stars)",
                new_bal,
                stats=stats,
            )
        except Exception as e:
            print(f"Log Error: {e}")

        try:
            if db_user and purchase_row:
                await yandex_metrica._run_first_purchase_metrika_effects(
                    db_user,
                    purchase_id=purchase_id,
                    original_revenue=float(total_amount),
                    currency="XTR",
                    payment_system="Telegram Stars",
                    is_first_purchase=bool(purchase_row.is_first_purchase),
                )
        except Exception as e:
            print(f"Metrica Error Stars: {e}")
    
    await message.answer(
        t("payment.success", locale, amount=bananas_count, suffix=suffix),
        parse_mode="HTML"
    )

# 👇 ВСТАВИТЬ ЭТУ ФУНКЦИЮ В app/handlers/payment.py (ВМЕСТО СТАРОЙ cmd_guide)

@router.message(F.text.in_(_menu_labels("menu.guide"))) 
@router.message(Command("guide")) # ✅ ДОБАВИЛ ВОТ ЭТО
async def cmd_guide(message: types.Message):
    # Твой новый ID картинки
    guide_image_id = "AgACAgIAAxkBAAINf2k-n4BsQHY-hpG5xWHmjyDS878NAAI4C2sbbRj4Sbbtx_VnA3xWAQADAgADeAADNgQ" 
    
    locale = get_user_locale_from_event(message.from_user)
    text = t("guide.text", locale)
    
    builder = InlineKeyboardBuilder()
    builder.button(text=t("menu.create", locale), callback_data="start_creation_from_guide")
    
    try:
        # 1. Сначала шлем фото (без текста, чтобы не превысить лимит)
        if len(guide_image_id) > 10:
            await message.answer_photo(photo=guide_image_id)
        
        # 2. Потом шлем текст с кнопкой
        await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())
        
    except Exception as e:
        print(f"Ошибка отправки гайда: {e}")
        # Если фото сломалось, шлем только текст
        await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())

    # 👇 ОБРАБОТЧИК КНОПКИ "ПОДДЕРЖКА"
@router.message(F.text.in_(_menu_labels("menu.support")))
@router.message(Command("support")) # ✅ ДОБАВИЛ ВОТ ЭТО
async def cmd_support(message: types.Message):
    locale = get_user_locale_from_event(message.from_user)
    text = t("support.text", locale, support="@nan0banana_help")
    await message.answer(text, parse_mode="HTML")