from aiogram import Router, types, F, Bot
from aiogram.types import LabeledPrice, PreCheckoutQuery
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.database import async_session
from app.services.user_service import get_bot_stats, find_user_by_input, admin_change_balance, get_user_admin_card_data, add_paid_balance
from app.services.user_service import get_user_profile_data, admin_change_balance, get_user_balance, get_user_financial_stats
from app.services.payment_service import create_purchase_record, mark_purchase_as_succeeded
from app import config
from app.services.payment_api import create_yoo_payment, check_yoo_payment
from app.services.admin_logger import log_payment
# 👇 ДОБАВИТЬ ЭТУ СТРОКУ
from app.packages import PACKAGES, STARS_PACKAGES


router = Router()

# 👇👇👇 ВСТАВЬ СЮДА СВОИ ЮЗЕРНЕЙМЫ 👇👇👇
CHANNEL_ID = "@nanobanan_promt"
CHAT_ID = "@nanabanan_chat"


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

@router.message(F.text == "Заработать🍌")
@router.message(Command("free"))
async def show_freebies(message: types.Message, bot: Bot):
    await _show_freebies_logic(message, message.from_user.id, bot)

async def _show_freebies_logic(message, user_id: int, bot: Bot):
   
    bot_info = await bot.me()
    
    ref_link = f"https://t.me/{bot_info.username}?start={user_id}"
    
    text = (
        "<b>Хочешь бананы, но не хочешь платить?</b> 😉\n\n"
        "Мы начисляем <b>+2 банана</b> на баланс за каждого нового пользователя, который придет от тебя.\n\n"
        "Количество приглашений не ограничено!\n\n"
        "<b>10 человек = 20 бананов 🔥</b>\n\n"
        "👇<b> Твоя личная ссылка</b> (нажми на нее, чтобы скопировать):\n\n"
        f"<code>{ref_link}</code>\n\n"
        "<i>Отправляй в чаты, группы и сторис!</i>" 
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🍌 Купить бананы", callback_data="buy_menu")
    builder.button(text="🔙 В меню", callback_data="main_menu")
    builder.adjust(1)  # Кнопки друг под другом
    
    await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())
    
@router.callback_query(F.data == "buy_menu")
async def cb_buy_from_freebies(callback: types.CallbackQuery):
    """Открывает магазин из раздела халявы"""
    await callback.message.delete()
    await cmd_shop(callback.message)
    await callback.answer()

@router.callback_query(F.data == "main_menu")
async def cb_main_menu_from_freebies(callback: types.CallbackQuery):
    """Возврат в главное меню"""
    from app.handlers.start import get_main_kb
    await callback.message.delete()
    await callback.message.answer(
        "🏠 Главное меню",
        reply_markup=get_main_kb()
    )
    await callback.answer()


# =====================================================================
# 💰 МАГАЗИН И ПРОФИЛЬ
# =====================================================================
@router.message(F.text == "🍌 Купить бананы")
@router.message(Command("buy"))
async def cmd_shop(message: types.Message):
    builder = InlineKeyboardBuilder()
    
    # Рублевые пакеты
    for key, pkg in PACKAGES.items():
        # Расчет цены за 1 шт
        p = pkg['price'] / pkg['gens']
        s = f"{p:.2f}".replace('.', ',').rstrip('0').rstrip(',')
        if s.endswith(','): s = s[:-1]
        
        btn = f"{pkg['emoji']}{pkg['gens']} {pkg['suffix']} - {pkg['price']}₽ | {s}₽/🍌"
        builder.button(text=btn, callback_data=f"buy_{key}")
    
    # Кнопка перехода на Stars
    builder.button(text="⭐️ Оплатить Stars", callback_data="open_stars_menu")
    
    builder.adjust(1)
    await message.answer(
        "🍌 *Магазин Бананов*\n\nПополни баланс и твори без ограничений!\n\n*Стоимость:*\n🍌 Standard: 1 банан\n💎 PRO: 4 банана\n\nВыбери пакет👇",
        reply_markup=builder.as_markup(), parse_mode="Markdown"
    )

# Меню Stars
@router.callback_query(F.data == "open_stars_menu")
async def show_stars_menu(callback: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    
    for key, pkg in STARS_PACKAGES.items():
        suffix = get_banana_suffix(pkg['bananas'])
        btn_text = f"{pkg['emoji']} {pkg['bananas']} {suffix} — {pkg['stars']} ⭐️"
        builder.button(text=btn_text, callback_data=f"buy_{key}")
    
    builder.button(text="🔙 Назад к рублям", callback_data="open_rub_menu")
    builder.adjust(1)
    
    await callback.message.edit_text(
        "⭐️ *Оплата Telegram Stars*\n\nВыбери пакет:",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )

# Возврат к рублевому меню
@router.callback_query(F.data == "open_rub_menu")
async def back_to_rub_menu(callback: types.CallbackQuery):
    await callback.answer()
    await cmd_shop(callback.message)

# ... (импорты сверху остаются те же)

# ... Импорты оставь те же, что были (убедись, что create_yoo_payment импортирован)
# from app.services.payment_api import create_yoo_payment

@router.callback_query(F.data.startswith("buy_"))
async def cb_buy_package(callback: types.CallbackQuery, bot: Bot):
    parts = callback.data.split("_")
    
    # 1. Если это Stars (оставляем старую логику)
    if len(parts) >= 3 and parts[1] == "stars":
        pkg_key = f"{parts[1]}_{parts[2]}"
        await handle_stars_purchase(callback, bot, pkg_key)
        return
    
    # 2. Получаем тариф
    pkg_key = parts[1]
    package = PACKAGES.get(pkg_key)
    if not package: 
        await callback.answer("Тариф не найден")
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
        await callback.answer("Ошибка платежной системы", show_alert=True)
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
async def handle_stars_purchase(callback: types.CallbackQuery, bot: Bot, pkg_key: str):
    package = STARS_PACKAGES.get(pkg_key)
    if not package:
        await callback.answer("Пакет не найден")
        return
    
    user_id = callback.from_user.id
    suffix = get_banana_suffix(package['bananas'])
    
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
                await mark_purchase_as_succeeded(session, callback.from_user.id, package['price'])
                # Начисляем бананы
                await add_paid_balance(session, callback.from_user.id, package['gens'])                
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
                f"✅ <b>Оплата прошла успешно!</b>\n\n"
                f"🍌 Начислено: <b>+{package['gens']} бананов</b>\n"
                f"Спасибо за покупку! Можно снова творить 🎨",
                parse_mode="HTML"
            )
            
        elif status == "pending":
            await callback.answer("⏳ Оплата еще не поступила. Завершите платеж в браузере.", show_alert=True)
            
        elif status == "canceled":
            await callback.message.edit_text("❌ Платеж отменен.", reply_markup=None)
            
    except Exception as e:
        print(f"Check Error: {e}")
        await callback.answer("Ошибка проверки.", show_alert=True)

@router.message(F.text == "👤 Профиль") 
@router.message(Command("profile"))
async def show_profile(message: types.Message):
    """
    Профиль пользователя (Clean UI по ТЗ)
    - Показывает ID, баланс, счетчик шедевров
    - 3 кнопки: Купить, Заработать, Техподдержка
    """
    user_id = message.from_user.id
    
    async with async_session() as session:
        data = await get_user_profile_data(session, user_id)
    
    if not data:
        await message.answer("❌ Ошибка загрузки профиля.")
        return
    
    user = data['user']
    
    # 📝 ТЕКСТ ПО ТЗ (HTML разметка для моноширинного ID)
    text = (
        "👤 <b>Твой профиль</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"🍌 Баланс: <b>{user.generations_balance} шт.</b>\n"
        f"🎨 Создано шедевров: <b>{user.total_generations_used}</b>\n\n"
        "👇 <b>Управление аккаунтом:</b>"
    )
    
    # ⌨️ КНОПКИ ПО ТЗ (3 ряда)
    builder = InlineKeyboardBuilder()
    
    # Ряд 1: Монетизация
    builder.button(text="🍌 КУПИТЬ БАНАНЫ", callback_data="goto_shop")
    
    # Ряд 2: Удержание
    builder.button(text="⚒️ Заработать бананы", callback_data="goto_free")
    
    # Ряд 3: Доверие (URL-кнопка)
    builder.button(text="👨‍💻 Техподдержка", url="https://t.me/nan0banana_help")
    
    builder.adjust(1)  # Каждая кнопка на новой строке
    
    await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())

# 👇 ЗАМЕНИТЬ ФУНКЦИЮ cmd_guide НА ЭТУ 👇

@router.message(F.text == "ℹ️ О нас") 
@router.message(Command("about")) # ✅ ДОБАВИЛ ВОТ ЭТО
async def cmd_about(message: types.Message):
    text = (
        "ℹ️ <b>О сервисе Nano Banana Pro</b>\n"
        "Сервис предоставляет доступ к облачной генерации изображений с помощью нейросети.\n"
        "🍌 <b>Бананы</b> — это внутренняя валюта, которая используется для оплаты генераций.\n\n"
        
        "👤 <b>Владелец сервиса:</b>\n"
        "Кузьмичева Диана Юрьевна\n"
        "📄 <b>Юридический статус:</b>\n"
        "Самозанятый (Плательщик НПД)\n"
        "🆔 <b>ИНН:</b> 025502709811\n\n"
        
        "📞 <b>Контакты:</b>\n"
        "Telegram: @nan0banana_help\n"
        "Email: help.nanobanan@gmail.com\n\n"
        
        "⚖️ <b>Документы:</b>\n"
        "• <a href='https://telegra.ph/PUBLICHNAYA-OFERTA-12-09-5'>Договор-оферта</a>\n"
        "• <a href='https://telegra.ph/POLITIKA-V-OTNOSHENII-OBRABOTKI-PERSONALNYH-DANNYH-12-09-5'>Политика конфиденциальности</a>"
    )
    # disable_web_page_preview=True чтобы не вылезала превьюшка телеграфа
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)

@router.callback_query(F.data == "goto_shop")
async def cb_goto_shop(callback: types.CallbackQuery):
    await callback.answer()
    # Вызываем функцию магазина (она выше в этом же файле)
    await cmd_shop(callback.message)

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
    await bot.answer_pre_checkout_query(
        pre_checkout_query_id=pre_checkout.id,
        ok=True
    )

# Успешная оплата Stars
@router.message(F.successful_payment)
async def process_successful_payment(message: types.Message, bot: Bot):
    payment = message.successful_payment
    
    # ✅ FIX 1: Определяем переменную total_amount, которой не хватало
    total_amount = payment.total_amount
    
    payload = payment.invoice_payload
    user_id = message.from_user.id 
    
    # Пытаемся распарсить payload
    package = None
    try:
        parts = payload.split("_")
        # Ожидаем формат stars_pack_120 (кол-во бананов)
        bananas_count = int(parts[2]) 
        
        # Ищем пакет по количеству бананов
        package = next((p for p in STARS_PACKAGES if p["gens"] == bananas_count), None)
    except:
        pass

    # Если по payload не нашли, ищем по цене (Plan B)
    if not package:
        package = next((p for p in STARS_PACKAGES if p["price"] == total_amount), None)

    if not package:
        await message.answer("❌ Ошибка обработки платежа (Тариф не найден)")
        return
    
    # ✅ FIX 2: Используем правильную функцию (get_banana_word)
    suffix = get_banana_word(package['gens'])
    
    # Начисляем бананы
    async with async_session() as session:
        # Записываем покупку в историю
        await create_purchase_record(session, user_id, total_amount, package['gens'])
        
        # ✅ FIX 3: Отмечаем статус как успех (Stars приходят сразу)
        await mark_purchase_as_succeeded(session, user_id, total_amount)
        
        # Начисляем баланс
        await admin_change_balance(session, user_id, package['gens'])
        
        # Логируем платеж С АНАЛИТИКОЙ
        try:
            new_bal = await get_user_balance(session, user_id)
            
            # 👇 ПОЛУЧАЕМ СТАТИСТИКУ ДЛЯ ЛОГА
            stats = await get_user_financial_stats(session, user_id)
            
            await log_payment(
                bot, 
                message.from_user, 
                f"{total_amount} ⭐️", 
                f"{package['gens']} {suffix} (Stars)", 
                new_bal, 
                stats=stats
            )
        except Exception as e:
            print(f"Log Stars Error: {e}")
    
    await message.answer(
        f"✅ <b>Оплата прошла успешно!</b>\n\n"
        f"🍌 Начислено: <b>+{package['gens']} {suffix}</b>\n"
        f"Спасибо за покупку! 🎨",
        parse_mode="HTML"
    )

# 👇 ВСТАВИТЬ ЭТУ ФУНКЦИЮ В app/handlers/payment.py (ВМЕСТО СТАРОЙ cmd_guide)

@router.message(F.text == "📚 Гайд") 
@router.message(Command("guide")) # ✅ ДОБАВИЛ ВОТ ЭТО
async def cmd_guide(message: types.Message):
    # Твой новый ID картинки
    guide_image_id = "AgACAgIAAxkBAAINf2k-n4BsQHY-hpG5xWHmjyDS878NAAI4C2sbbRj4Sbbtx_VnA3xWAQADAgADeAADNgQ" 
    
    text = (
        "🍌 <b>Гайд: Как стать повелителем Nano Banana</b>\n\n"
        "Наш бот — это ваш личный цифровой художник. Он понимает вас с полуслова, если знать, как просить.\n\n"
        
        "🔥 <b>Что умеет бот? (3 главных режима)</b>\n\n"
        "1️⃣ <b>Генерация с нуля (Текст → Картинка)</b> 🎨\n"
        "Опишите идею словами — бот нарисует.\n"
        "<i>Совет:</i> Не пишите просто «кот». Пишите как режиссер: <i>«Рыжий кот в скафандре сидит на поверхности Марса, кинематографичный свет, 4k».</i>\n\n"
        
        "2️⃣ <b>Фотошоп словами (Редактирование)</b> 🛠\n"
        "Не нравится деталь на фото? Исправьте её!\n"
        "Пришлите фото и напишите: <i>«Убери людей с фона», «Замени костюм на вечернее платье» или «Преврати день в ночь».</i>\n\n"
        
        "3️⃣ <b>Объединение и Перенос лица</b> 🎭\n"
        "Хотите стать героем фильма или сделать коллаж?\n"
        "• Прикрепите <b>от 2 до 4 фото</b> (например: ваше селфи + фото пляжа).\n"
        "• Напишите: <i>«Помести меня на этот пляж» или «Сделай из нас Деда Мороза и Снегурочку».</i>\n\n"
        
        "— — —\n\n"
        "⚠️ <b>ВАЖНО: Секрет идеального сходства</b>\n\n"
        "💎 <b>Выбор модели решает всё!</b>\n"
        "Если вам нужна точная копия лица (фотореализм) — обязательно переключитесь на модель PRO.\n\n"
        "• <b>Standard</b> — создает художественные образы, может слегка менять черты.\n"
        "• <b>PRO</b> — сохраняет максимальную портретную схожесть.\n\n"
        "📸 <b>Требования к фото:</b>\n\n"
        "✅ <b>ИДЕАЛЬНОЕ ФОТО:</b>\n"
        "• Селфи крупным планом (анфас).\n"
        "• Дневное освещение (свет падает на лицо, нет жестких теней).\n"
        "• Без очков, масок и рук у лица.\n\n"
        "❌ <b>ПЛОХОЕ ФОТО:</b>\n"
        "• Размытое, темное, засвеченное.\n"
        "• Лицо далеко или прикрыто волосами.\n"
        "• Групповое фото (бот не поймет, кто из них вы).\n\n"
        
        "— — —\n\n"
        "🏆 <b>Золотые правила запроса (Промпта)</b>\n\n"
        "1️⃣ <b>Забудьте про набор слов.</b>\n"
        "Не пишите: <i>«Девушка, красиво, лес».</i>\n"
        "Пишите предложениями: <i>«Красивая девушка гуляет по осеннему лесу на закате».</i>\n\n"
        "2️⃣ <b>Давайте контекст.</b>\n"
        "Бот умный. Скажите ему: <i>«Сделай фото сэндвича для дорогого меню»</i> — и он сам добавит правильный свет и тарелку.\n\n"
        "3️⃣ <b>Уточняйте детали.</b>\n"
        "Описывайте материалы (<i>«шелковое платье»</i>), стиль (<i>«киберпанк», «аниме»</i>) и настроение.\n\n"
        "— — —\n\n"
        "🚀 <b>Где брать идеи?</b>\n\n"
        "🎨 <b>Банк промптов:</b> @nanobanan_promt\n"
        "Смотрите примеры работ и копируйте готовые описания.\n\n"
        "👥 <b>Комьюнити творцов:</b> @nanabanan_chat\n"
        "Делись своими шедеврами, вдохновляйся работами других и находи новые идеи.\n\n"
        "👇 <b>Попробуйте прямо сейчас!</b>\n"
        "Пришлите фото или текст."
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="✨ Начать творить", callback_data="start_creation_from_guide")
    
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
@router.message(F.text == "💬 Поддержка")
@router.message(Command("support")) # ✅ ДОБАВИЛ ВОТ ЭТО
async def cmd_support(message: types.Message):
    text = (
        "💬 <b>Возникли вопросы или проблемы?</b>\n\n"
        "Напишите нам, мы поможем:\n"
        "@nan0banana_help"
    )
    await message.answer(text, parse_mode="HTML")