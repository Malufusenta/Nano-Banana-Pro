import asyncio
from datetime import datetime
from aiogram import Bot, html
from app import config
import logging
logger = logging.getLogger(__name__)
import re
import html 
from aiogram.exceptions import TelegramRetryAfter
# Если config.py нет, раскомментируй и вставь
# ADMIN_CHANNEL_ID = -100xxxxxxxxxx

# 👇 КАРТА ИКОНОК ДЛЯ КНОПОК (Делаем логи красивыми)
CALLBACK_ICONS = {
    "buy_": "💳 Покупка",
    "check_": "✅ Проверка",
    "pf_": "⚙️ Настройки",
    "cat_": "📂 Категория",
    "reroll_": "🔄 Реролл",
    "download_": "📥 Скачивание",
    "edit_": "🎨 Редактирование",
    "invoice_": "🧾 Счет",
    "goto_": "👉 Переход"
}

# 👇 ВСТАВИТЬ ПОСЛЕ CALLBACK_ICONS = { ... }

def translate_callback(code: str) -> str:
    """Переводит технический код кнопки в человеческий язык"""
    
    # 1. Точные совпадения (статичные кнопки)
    translations = {
        "pf_start": "🚀 Запуск генерации",
        "pf_toggle_model": "💎 Смена модели",
        "pf_toggle_quality": "🌟 Смена качества",
        "pf_select_ratio": "📐 Меню форматов",
        "pf_back": "🔙 Назад",
        "goto_shop": "💰 Переход в магазин",
        "goto_free": "🎁 Переход в 'Заработать'",
        "check_channel": "📢 Проверка подписки (Канал)",
        "check_chat": "💬 Проверка подписки (Чат)",
        "admin_stats": "📊 Просмотр статистики",
        "admin_add_balance": "💰 Выдача баланса",
        "close_admin": "❌ Выход из админки",
        "open_stars_menu": "⭐️ Меню Stars",
        "open_rub_menu": "₽ Меню Рублей",
        "open_shop_menu": "🛍 Меню способов оплаты",
        "buy_bananas_crypto": "💵 Crypto Pay (USD) — выбор пакета",
        "cancel_wizard": "❌ Отмена действия"
    }
    
    if code in translations:
        return translations[code]

    # 2. Динамические кнопки (с параметрами)
    if code.startswith("set_ratio_"):
        return f"📐 Выбрал формат {code.split('_')[2]}"
    
    if code.startswith("buy_stars_"):
        count = code.split('_')[2]
        return f"⭐️ Выбор пакета: {count} бананов (Stars)"

    if code.startswith("buy_rub_"):
        tariff = code.split("_", 2)[2].upper()
        return f"💳 Выбор рублевого тарифа: {tariff}"

    if code.startswith("buy_pkg:"):
        tier = code.split(":", 1)[1]
        return f"💵 Crypto Pay (USD): пакет {tier}"

    if code.startswith("buy_"):
        tariff = code.split('_')[1].upper()
        return f"💳 Выбор тарифа: {tariff}"
        
    if code.startswith("invoice_"):
        return "🧾 Запросил счет на оплату"
        
    if code.startswith("check_"):
        return "✅ Нажал 'Я оплатил'"
        
    if code.startswith("reroll_"):
        return "🔄 Нажал 'Ещё раз'"
        
    if code.startswith("edit_"):
        return "🎨 Нажал 'Изменить'"
        
    if code.startswith("download_"):
        return "📥 Нажал 'Скачать'"

    # Если перевода нет, возвращаем код как есть
    return code

 # 👈 Обязательно добавь импорт в начале файла


async def send_log(bot: Bot, text: str, disable_notification: bool = False):
    if not hasattr(config, "ADMIN_CHANNEL_ID") or not config.ADMIN_CHANNEL_ID:
        return

    try:
        formatted_text = re.sub(
            r'(ID:?\s*)(\d{6,})', 
            r'\1<code>\2</code>', 
            text, 
            flags=re.IGNORECASE
        )
        for attempt in range(3):
            try:
                await bot.send_message(
                    chat_id=config.ADMIN_CHANNEL_ID,
                    text=formatted_text,
                    parse_mode="HTML",
                    disable_notification=disable_notification,
                    disable_web_page_preview=True
                )
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
    except Exception as e:
        print(f"⚠️ Ошибка логгера: {e}")


async def send_photo_log(bot: Bot, photo, caption: str):
    if not hasattr(config, "ADMIN_CHANNEL_ID") or not config.ADMIN_CHANNEL_ID:
        return

    try:
        for attempt in range(3):
            try:
                await bot.send_photo(
                    chat_id=config.ADMIN_CHANNEL_ID,
                    photo=photo,
                    caption=caption,
                    parse_mode="HTML"
                )
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
    except Exception as e:
        print(f"⚠️ Ошибка логгера (фото): {e}")
        fallback_text = f"{caption}\n\n⚠️ <i>(Сам файл фото недоступен или удален сервером)</i>"
        await send_log(bot, fallback_text)

async def send_sales_log(bot: Bot, text: str):
    if not hasattr(config, "SALES_CHANNEL_ID") or not config.SALES_CHANNEL_ID:
        return

    try:
        formatted_text = re.sub(
            r'(ID:?\s*)(\d{6,})', 
            r'\1<code>\2</code>', 
            text, 
            flags=re.IGNORECASE
        )
        for attempt in range(3):
            try:
                await bot.send_message(
                    chat_id=config.SALES_CHANNEL_ID,
                    text=formatted_text,
                    parse_mode="HTML",
                    disable_notification=False,
                    disable_web_page_preview=True
                )
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
    except Exception as e:
        print(f"⚠️ Ошибка sales-логгера: {e}")


# 🟢 ТИП 1: НОВЫЙ ПОЛЬЗОВАТЕЛЬ
async def log_new_user(bot: Bot, user, deep_link: str = None):
    link_info = deep_link if deep_link else "Органика"
    username = f"@{user.username}" if user.username else "Нет"
    
    text = (
        "👤 <b>НОВЫЙ ПОЛЬЗОВАТЕЛЬ</b>\n"
        "➖➖➖➖➖➖➖\n"
        f"Имя: <a href='tg://user?id={user.id}'>{user.full_name}</a>\n"
        f"Username: {username}\n"
        f"ID: <code>{user.id}</code>\n"
        f"Источник: {link_info}\n"
        "#new_user"
    )
    asyncio.create_task(send_log(bot, text))

# 👇 ЗАМЕНИТЬ ФУНКЦИЮ log_payment НА ЭТУ

async def log_payment(bot: Bot, user, amount, item_name, new_balance, stats: dict = None, currency: str = None):
    username = f"@{user.username}" if user.username else "Нет"
    
    # Анализируем статус
    count = stats.get("count", 1) if stats else 1
    source = stats.get("source", "Неизвестно") if stats else "Неизвестно"

    if count == 1:
        status_line = "Покупка №: 1 (Новичок 👶)"
    elif count < 5:
        status_line = f"Покупка №: {count} (Растем 📈)"
    else:
        status_line = f"Покупка №: {count} (Постоянник! 🔥)"

    if currency is None:
        item_name_lc = (item_name or "").lower()
        if "stars" in item_name_lc:
            currency = "⭐️"
        elif "ton" in item_name_lc:
            currency = "TON"
        elif "crypto" in item_name_lc or "usdt" in item_name_lc:
            currency = "USDT"
        else:
            currency = "₽"

    if currency in ("USDT", "TON"):
        total_usdt = stats.get("total_spent_usd", 0) if stats else 0
        total_ton = stats.get("total_spent_ton", 0) if stats else 0
        if total_usdt and total_ton:
            total_display = f"{total_usdt} USDT | {total_ton} TON"
        elif total_usdt:
            total_display = f"{total_usdt} USDT"
        elif total_ton:
            total_display = f"{total_ton} TON"
        else:
            total_display = f"{amount} {currency}"
    else:
        total = stats.get("total_spent", amount) if stats else amount
        total_display = f"{total} {currency}"

    text = (
        "💰 <b>НОВАЯ ПРОДАЖА!</b>\n"
        "➖➖➖➖➖➖➖\n"
        # 👇 УБРАЛ <code>, так как твой send_log теперь делает это сам!
        f"Клиент: {username} (ID: {user.id})\n" 
        f"Сумма: <b>{amount} {currency}</b>\n"
        f"Товар: {item_name}\n"
        f"----------------\n"
        f"{status_line}\n"
        # 👇 ТУТ ОСТАВИЛ <code>, потому что это не ID, логгер это не тронет
        f"Источник: <code>{source}</code>\n"
        f"Всего принес денег: <b>{total_display}</b>\n"
        "#payment"
    )
    
    asyncio.create_task(send_log(bot, text))
    asyncio.create_task(send_sales_log(bot, text))  # 👈 вот это

# 🎨 ТИП 3: ГЕНЕРАЦИЯ
async def log_generation(bot: Bot, user, prompt: str, model: str, photo_file_id: str):
    username = f"@{user.username}" if user.username else "Нет"
    # Обрезаем слишком длинный промпт, чтобы не засорять канал
    safe_prompt = prompt[:300] + "..." if len(prompt) > 300 else prompt
    
    caption = (
        "🎨 <b>Генерация</b>\n"
        f"Юзер: {username}\n"
        f"Модель: {model}\n"
        f"Промпт: <code>{safe_prompt}</code>\n"
        "#generation"
    )
    asyncio.create_task(send_photo_log(bot, photo_file_id, caption))

# 👇 ЗАМЕНИТЬ ФУНКЦИЮ log_action НА ЭТУ 👇

async def log_action(bot: Bot, user_id: int, username: str, action: str, is_message: bool = False):
    u_name = f"@{username}" if username else f"ID:{user_id}"
    
    if is_message:
        # Сообщения пользователя оставляем как есть
        text = f"💬 Сообщение: {action}\n👤 {u_name}\n#message"
    else:
        # КНОПКИ: Делаем красиво
        try:
            if "data=" in action:
                # Извлекаем сырой код: "Нажал кнопку [ data=buy_mini ]" -> "buy_mini"
                raw_code = action.split("data=")[1].strip(" ]")
                
                # 1. Ищем иконку категории
                prefix_label = "👣 Кнопка"
                for prefix, label in CALLBACK_ICONS.items():
                    if raw_code.startswith(prefix):
                        prefix_label = label
                        break
                
                # 2. Переводим код в текст
                readable_text = translate_callback(raw_code)
                
                # Формируем лог: "💳 Покупка: Выбор тарифа MINI"
                text = f"{prefix_label}: {readable_text}\n👤 {u_name}\n#action"
            else:
                text = f"👣 Действие: {action}\n👤 {u_name}\n#action"
        except Exception as e:
            logger.warning(f"log_action parse error: {e}")
            text = f"👣 Действие: {action}\n👤 {u_name}\n#action"

    asyncio.create_task(send_log(bot, text, disable_notification=True))

# ⚠️ ТИП 5: ОШИБКИ
async def log_error(bot: Bot, user_id: int, username: str, prompt: str, error_text: str):
    u_name = f"@{username}" if username else f"ID:{user_id}"
    text = (
        "🚨 <b>ОШИБКА / СБОЙ</b>\n"
        f"Юзер: {u_name}\n"
        f"Запрос: <code>{prompt}</code>\n"
        f"Статус: {error_text}\n"
        "#error"
    )
    asyncio.create_task(send_log(bot, text))


async def log_referral(bot: Bot, referrer_id: int, new_user):
    new_user_name = f"@{new_user.username}" if new_user.username else f"ID:{new_user.id}"
    
    text = (
        "🤝 <b>РЕФЕРАЛЬНАЯ ПРОГРАММА</b>\n"
        "➖➖➖➖➖➖➖\n"
        f"📢 Кто пригласил: <a href='tg://user?id={referrer_id}'>{referrer_id}</a> (ID: {referrer_id})\n"
        f"👤 Кто пришел: {new_user_name}\n"
        f"🎉 Друг сделал <b>первую генерацию</b>!\n"
        "🎁 Бонус: <b>+2 банана</b>\n"
        "#referral"
    )
    asyncio.create_task(send_log(bot, text))

    # 🚫 ТИП 7: ПЕРЕХВАТЧИК ЛЕНИВЫХ ПРОМПТОВ
async def log_lazy_prompt_interceptor(bot: Bot, user_id: int, username: str, lazy_text: str):
    u_name = f"@{username}" if username else f"ID:{user_id}"
    
    # Обрезаем текст если слишком длинный
    safe_text = lazy_text[:100] + "..." if len(lazy_text) > 100 else lazy_text
    
    text = (
        "🚫 <b>ПЕРЕХВАТЧИК: Ленивый промпт</b>\n"
        "➖➖➖➖➖➖➖\n"
        f"Юзер: {u_name} (<code>{user_id}</code>)\n"
        f"Текст: <code>{safe_text}</code>\n"
        f"💰 Экономия: 1-3 🍌 (Генерация не запущена)\n"
        "#lazy_prompt"
    )
    asyncio.create_task(send_log(bot, text, disable_notification=True))

    # 🚨 ТИП 8: ФИЛЬТР ЖАЛОБ НА СХОДСТВО
async def log_complaint_filter(bot: Bot, user_id: int, username: str, complaint_text: str):
    u_name = f"@{username}" if username else f"ID:{user_id}"
    
    # Обрезаем текст если слишком длинный
    safe_text = complaint_text[:100] + "..." if len(complaint_text) > 100 else complaint_text
    
    text = (
        "😡 <b>COMPLAINT FILTER: Жалоба перехвачена</b>\n"
        "➖➖➖➖➖➖➖\n"
        f"Юзер: {u_name} (<code>{user_id}</code>)\n"
        f"Текст: <code>{safe_text}</code>\n"
        f"Действие: Показана инструкция по качественной генерации\n"
        "#complaint_filter"
    )
    asyncio.create_task(send_log(bot, text, disable_notification=True))

# 🔄 ТИП 9: RETRY FLOW - Нажата кнопка "Повторить правильно"
async def log_retry_flow(bot: Bot, user_id: int):
    text = (
        "✅ <b>RETRY FLOW: Запущен правильный сценарий</b>\n"
        "➖➖➖➖➖➖➖\n"
        f"Юзер: <a href='tg://user?id={user_id}'>{user_id}</a>\n"
        f"Флаг: <code>force_pro_mode=True</code>\n"
        f"Статус: Ожидание фото + промпт с правилами\n"
        "#retry_flow"
    )
    asyncio.create_task(send_log(bot, text, disable_notification=True))

# 💎 ТИП 10: ЗАКАЗ СОЗДАН ПОСЛЕ RETRY FLOW
async def log_order_from_retry(bot: Bot, user_id: int, cost: int, model: str):
    model_display = "PRO" if model == "pro" else "Standard"
    text = (
        "💎 <b>ORDER CREATED: Генерация после Retry Flow</b>\n"
        "➖➖➖➖➖➖➖\n"
        f"Юзер: <a href='tg://user?id={user_id}'>{user_id}</a>\n"
        f"Модель: <b>{model_display}</b> (Source: Retry Flow)\n"
        f"Стоимость: {cost} 🍌\n"
        f"✅ Клиент прошел через отработку жалобы\n"
        "#order_retry"
    )
    asyncio.create_task(send_log(bot, text))

    # 🔒 ТИП 11: БЛОКИРОВКА/РАЗБЛОКИРОВКА ПОЛЬЗОВАТЕЛЯ
async def log_user_block(bot: Bot, admin_id: int, admin_username: str, user_id: int, user_name: str, user_username: str, is_blocked: bool):
    """
    Логирует действие блокировки/разблокировки пользователя
    
    Args:
        bot: экземпляр бота
        admin_id: ID админа который выполнил действие
        admin_username: username админа
        user_id: ID заблокированного пользователя
        user_name: Имя пользователя
        user_username: username пользователя
        is_blocked: True если заблокирован, False если разблокирован
    """
    admin_display = f"@{admin_username}" if admin_username else f"ID:{admin_id}"
    user_display = f"@{user_username}" if user_username else "без ника"
    
    emoji = "🔒" if is_blocked else "🔓"
    action_text = "ЗАБЛОКИРОВАН" if is_blocked else "РАЗБЛОКИРОВАН"
    
    from datetime import datetime
    timestamp = datetime.now().strftime('%d.%m.%Y %H:%M:%S')
    
    text = (
        f"{emoji} <b>ПОЛЬЗОВАТЕЛЬ {action_text}</b>\n"
        "➖➖➖➖➖➖➖\n"
        f"👑 Админ: {admin_display}\n"
        f"👤 Пользователь: <a href='tg://user?id={user_id}'>{user_name}</a>\n"
        f"🔗 Username: {user_display}\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"🕐 Время: {timestamp}\n"
        f"#user_block"
    )
    
    asyncio.create_task(send_log(bot, text))

async def log_content_filter(
    bot: Bot,
    user_id: int,
    username: str,
    text: str,
    trigger_type: str,
    matched_word: str,
    was_blocked: bool
):
    """
    Логирует срабатывание фильтра контента
    
    Args:
        bot: Экземпляр бота
        user_id: ID пользователя
        username: Username пользователя
        text: Текст который проверялся
        trigger_type: Тип триггера (question_mark, stop_word, nsfw_word)
        matched_word: Конкретное слово/символ который сработал
        was_blocked: Был ли заблокирован запрос (или только залогирован)
    """
    
    if not config.FILTER_LOG_CHANNEL_ID:
        return
    
    # Определяем эмодзи и статус
    status_emoji = "🚫" if was_blocked else "⚠️"
    status_text = "ЗАБЛОКИРОВАНО" if was_blocked else "ТЕСТ (разрешено)"
    
    # Маппинг типов триггеров
    trigger_names = {
        "question_mark": "Знак вопроса (?)",
        "stop_word": "Стоп-слово",
        "nsfw_word": "NSFW контент",
        "whitelist": "Белый список (разрешено)"
    }
    
    trigger_name = trigger_names.get(trigger_type, trigger_type)
    
    # Форматируем текст (обрезаем если длинный)
    display_text = text[:200] + "..." if len(text) > 200 else text
    
    message_text = (
        f"{status_emoji} <b>СРАБОТАЛ ФИЛЬТР</b> ({status_text})\n\n"
        f"👤 Юзер: <code>{user_id}</code> (@{username or 'нет'})\n"
        f"📝 Текст: <code>{html.escape(display_text)}</code>\n\n"
        f"🎯 Триггер: <b>{trigger_name}</b>\n"
        f"🔍 Найдено: <code>{html.escape(matched_word)}</code>\n"
        f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
    )
    
    try:
        await bot.send_message(
            chat_id=config.FILTER_LOG_CHANNEL_ID,
            text=message_text,
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"⚠️ Ошибка отправки лога фильтра: {e}")

async def log_security_ban(
    bot: Bot,
    user_id: int,
    username: str,
    prompt: str,
    source: str = "Unknown"
):
    """
    Логирует срабатывание цензуры/фильтра безопасности
    
    Args:
        bot: Экземпляр бота
        user_id: ID пользователя
        username: Username пользователя
        prompt: Промпт который заблокирован
        source: Источник блокировки (API Filter, Local Filter и т.д.)
    """
    from app import config
    
    if not config.ADMIN_CHANNEL_ID:
        return
    
    message_text = (
        f"🔞 <b>ЦЕНЗУРА СРАБОТАЛА</b>\n\n"
        f"👤 Юзер: <code>{user_id}</code> (@{username or 'нет'})\n"
        f"🛡️ Источник: <b>{source}</b>\n"
        f"📝 Промпт: <code>{html.escape(prompt[:200])}</code>\n"
        f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
    )
    
    try:
        await bot.send_message(
            chat_id=config.ADMIN_CHANNEL_ID,
            text=message_text,
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"⚠️ Ошибка отправки лога цензуры: {e}")

        # 🎬 ТИП 12: ГЕНЕРАЦИЯ ВИДЕО
async def log_video_generation_start(bot: Bot, user_id: int, username: str, cost: int, task_id: str):
    u_name = f"@{username}" if username else f"ID:{user_id}"
    
    text = (
        "🎬 <b>ВИДЕО: Генерация запущена</b>\n"
        "➖➖➖➖➖➖➖\n"
        f"Юзер: {u_name} (<code>{user_id}</code>)\n"
        f"Стоимость: <b>{cost} 🍌</b>\n"
        f"Статус: ⏳ Ожидание (3-10 мин)\n"
        "#video_start"
    )
    asyncio.create_task(send_log(bot, text))

async def log_video_generation_success(bot: Bot, user_id: int, username: str, video_file_id: str, task_id: str):
    """
    Логирует успешную генерацию видео (с самим видео)
    """
    u_name = f"@{username}" if username else f"ID:{user_id}"
    
    caption = (
        "✅ <b>ВИДЕО: Генерация завершена!</b>\n"
        "➖➖➖➖➖➖➖\n"
        f"Юзер: {u_name}\n"
        f"Task ID: <code>{task_id}</code>\n"
        f"Статус: 🎉 Успешно отправлено\n"
        "#video_success"
    )
    
    # Отправляем видео в канал
    try:
        await bot.send_video(
            chat_id=config.ADMIN_CHANNEL_ID,
            video=video_file_id,
            caption=caption,
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"⚠️ Ошибка отправки видео в лог: {e}")
        # Fallback - отправляем текст
        asyncio.create_task(send_log(bot, caption))

async def log_video_generation_error(bot: Bot, user_id: int, username: str, task_id: str, error_msg: str):
    """
    Логирует ошибку генерации видео
    """
    u_name = f"@{username}" if username else f"ID:{user_id}"
    
    text = (
        "❌ <b>ВИДЕО: Ошибка генерации</b>\n"
        "➖➖➖➖➖➖➖\n"
        f"Юзер: {u_name} (<code>{user_id}</code>)\n"
        f"Task ID: <code>{task_id}</code>\n"
        f"Ошибка: <code>{error_msg[:200]}</code>\n"
        f"💰 Возврат: 12 🍌\n"
        "#video_error"
    )
    asyncio.create_task(send_log(bot, text))

    # 💸 ТИП 13: ВОЗВРАТ БАНАНОВ (REFUND)
async def log_banana_refund(bot: Bot, user_id: int, username: str, amount: int, reason: str):
    """
    Логирует возврат бананов при ошибке генерации
    
    Args:
        bot: Экземпляр бота
        user_id: ID пользователя
        username: Username пользователя
        amount: Количество возвращенных бананов
        reason: Причина возврата (короткое описание)
    """
    u_name = f"@{username}" if username else f"ID:{user_id}"
    
    text = (
        "💸 <b>ВОЗВРАТ БАНАНОВ</b>\n"
        "➖➖➖➖➖➖➖\n"
        f"Юзер: {u_name} (<code>{user_id}</code>)\n"
        f"Сумма: <b>+{amount} 🍌</b>\n"
        f"Причина: <code>{reason}</code>\n"
        "#refund"
    )
    asyncio.create_task(send_log(bot, text))