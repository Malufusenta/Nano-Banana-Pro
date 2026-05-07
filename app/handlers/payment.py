from aiogram import Router, types, F, Bot
from aiogram.types import LabeledPrice, PreCheckoutQuery
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.database import async_session
from app.services.user_service import get_bot_stats, find_user_by_input, admin_change_balance, get_user_admin_card_data, add_paid_balance
from app.services.user_service import get_user_profile_data, admin_change_balance, get_user_balance, get_user_financial_stats, set_last_payment_method
from app.services.payment_service import create_purchase_record, mark_purchase_as_succeeded, update_purchase_analytics
from app import config
from datetime import datetime
from app.services.payment_api import create_yoo_payment, check_yoo_payment
from app.services.i18n import resolve_locale, t
from app.utils.telegram_locale import effective_locale
from app.services.admin_logger import log_payment
from app.models import Purchase, User# ← Добавь в начало
from sqlalchemy import select     # ← Добавь в начало
from app.packages import PACKAGES, STARS_PACKAGES


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

    user_id = callback.from_user.id
    desc = f"Покупка {package['gens']} бананов"

    # Чтобы пользователь не скучал, пока мы делаем запросы к ЮКассе (это занимает 1-2 сек)
    # мы можем (опционально) показать часики в кнопке, но пока просто делаем магию.

    # 3. ГЕНЕРИРУЕМ ССЫЛКИ ЗАРАНЕЕ
    sbp_url = None
    card_url = None

    # Попытка создать ссылку на СБП
    try:
        # Важно: если в тестовом магазине СБП выключен, тут будет ошибка, мы её подавим
        payment_sbp = create_yoo_payment(package['price'], desc, user_id, payment_method="sbp")
        sbp_url = payment_sbp.confirmation.confirmation_url
    except Exception as e:
        print(f"⚠️ СБП не создан (возможно отключен в настройках): {e}")

    # Попытка создать ссылку на Карту
    try:
        payment_card = create_yoo_payment(package['price'], desc, user_id, payment_method="bank_card")
        card_url = payment_card.confirmation.confirmation_url
    except Exception as e:
        print(f"⚠️ Оплата картой не создана: {e}")
        await callback.answer(t("shop.payment_error", locale), show_alert=True)
        return

    # 4. СОБИРАЕМ КРАСИВЫЙ ТЕКСТ
    text = (
        "⚡️ <b>Отличный выбор!</b>\n\n"
        f"🍌 Пополнение: <b>+{package['gens']} {package['suffix']}</b>\n"
        f"💳 К оплате: <b>{package['price']}₽</b>\n\n"
        "📄 <i>Оплачивая, вы принимаете условия <a href='https://telegra.ph/PUBLICHNAYA-OFERTA-12-09-5'>Оферты</a>.</i>"
    )

    # 5. СОБИРАЕМ КНОПКИ
    builder = InlineKeyboardBuilder()

    # Кнопка СБП (показываем, только если ссылка успешно создалась)
    if sbp_url:
        builder.button(text="🚀 СБП", url=sbp_url)
    
    # Кнопка Карты (основная)
    if card_url:
        builder.button(text="💳 Карта/другое", url=card_url)
        
    builder.button(text="🔙 Назад", callback_data="goto_shop")
    builder.adjust(1) # Все кнопки в столбик

    # Отправляем
    await callback.message.edit_text(
        text, 
        reply_markup=builder.as_markup(), 
        parse_mode="HTML", 
        disable_web_page_preview=True
    )

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
    parts = callback.data.split("_")
    payment_id = parts[1]
    pkg_key = parts[2]
    package = PACKAGES.get(pkg_key)
    if not package: return

    try:
        # Проверяем статус в ЮКассе
        status = check_yoo_payment(payment_id)
        
        if status == "succeeded":
            async with async_session() as session:
                # Проверяем - может уже обработано?
                existing = await session.execute(
                    select(Purchase).where(
                        Purchase.payment_id == payment_id,
                        Purchase.status == 'succeeded'
                    )
                )
                if existing.scalar_one_or_none():
                    await callback.answer("✅")
                    await callback.message.edit_text(
                        t("payment.success", locale, amount=package["gens"], suffix=get_banana_label(locale, package["gens"])),
                        reply_markup=None
                    )
                    return
        
                await mark_purchase_as_succeeded(session, callback.from_user.id, package['price'], package['gens'])
                await update_purchase_analytics(
                    session,
                    callback.from_user.id,
                    package["price"],
                    f"{package['gens']} bananas",
                    payment_id=payment_id,
                    payment_method="yookassa_card",
                )
                # Начисляем бананы
                await add_paid_balance(session, callback.from_user.id, package['gens'])
                await set_last_payment_method(session, callback.from_user.id, "yookassa_card")
                await session.commit()
                # Логируем С АНАЛИТИКОЙ
                try:
                    new_bal = await get_user_balance(session, callback.from_user.id)
                    # 👇 1. Получаем статистику по юзеру
                    stats = await get_user_financial_stats(session, callback.from_user.id)
                    
                    # 👇 2. Передаем её в логгер (параметр stats)
                    await log_payment(
                        bot, 
                        callback.from_user, 
                        package['price'], 
                        f"{package['gens']} Бананов", 
                        new_bal, 
                        stats=stats 
                    )
                except Exception as e: 
                    print(f"Log Error: {e}")

            # Поздравляем
            await callback.message.edit_text(
                t("payment.success", locale, amount=package["gens"], suffix=get_banana_label(locale, package["gens"])),
                parse_mode="HTML"
            )
            
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
        await create_purchase_record(session, user_id, total_amount, bananas_count)
        await mark_purchase_as_succeeded(session, user_id, total_amount, bananas_count)
        await session.commit()  # ← ДОБАВЬ ЭТУ СТРОКУ!

        # Обновляем аналитику для Stars
        await update_purchase_analytics(
            session, 
            user_id, 
            total_amount,
            "Telegram Stars",  # ← Чтобы отличить от рублей
            payment_id=None,
            payment_method="telegram_stars"
        )
        await admin_change_balance(session, user_id, bananas_count)
        await set_last_payment_method(session, user_id, "telegram_stars")
        await session.commit()  # ← И ЕЩЁ ОДИН COMMIT В КОНЦЕ

        
        # Логируем платеж
        try:
            new_bal = await get_user_balance(session, user_id)
            stats = await get_user_financial_stats(session, user_id)
            
            await log_payment(
                bot, 
                message.from_user, 
                f"{total_amount} ⭐️", 
                f"{bananas_count} {suffix} (Stars)", 
                new_bal, 
                stats=stats
            )
        except Exception as e:
            print(f"Log Error: {e}")
    
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