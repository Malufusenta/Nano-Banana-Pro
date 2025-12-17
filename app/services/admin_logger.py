import asyncio
from aiogram import Bot
from app import config

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

async def send_log(bot: Bot, text: str, disable_notification: bool = False):
    """Базовая функция отправки текста в канал"""
    if not hasattr(config, "ADMIN_CHANNEL_ID") or not config.ADMIN_CHANNEL_ID:
        return

    try:
        await bot.send_message(
            chat_id=config.ADMIN_CHANNEL_ID,
            text=text,
            parse_mode="HTML",
            disable_notification=disable_notification,
            disable_web_page_preview=True # Чтобы ссылки не разворачивались
        )
    except Exception as e:
        print(f"⚠️ Ошибка логгера (текст): {e}")

async def send_photo_log(bot: Bot, photo, caption: str):
    """Отправка фото-отчета с ПЛАНОМ Б (если фото битое)"""
    if not hasattr(config, "ADMIN_CHANNEL_ID") or not config.ADMIN_CHANNEL_ID:
        return

    try:
        await bot.send_photo(
            chat_id=config.ADMIN_CHANNEL_ID,
            photo=photo,
            caption=caption,
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"⚠️ Ошибка логгера (фото): {e}")
        # 🔥 ПЛАН Б: Если фото не ушло, шлем ТЕКСТ, чтобы админ знал о генерации
        fallback_text = f"{caption}\n\n⚠️ <i>(Сам файл фото недоступен или удален сервером)</i>"
        await send_log(bot, fallback_text)

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

async def log_payment(bot: Bot, user, amount, item_name, new_balance, stats: dict = None):
    username = f"@{user.username}" if user.username else "Нет"
    
    # Анализируем статус
    count = stats.get("count", 1) if stats else 1
    total = stats.get("total_spent", amount) if stats else amount
    source = stats.get("source", "Неизвестно") if stats else "Неизвестно"

    if count == 1:
        status_line = "Покупка №: 1 (Новичок 👶)"
    elif count < 5:
        status_line = f"Покупка №: {count} (Растем 📈)"
    else:
        status_line = f"Покупка №: {count} (Постоянник! 🔥)"

    text = (
        "💰 <b>НОВАЯ ПРОДАЖА!</b>\n"
        "➖➖➖➖➖➖➖\n"
        f"Клиент: {username} (<a href='tg://user?id={user.id}'>ID</a>)\n"
        f"Сумма: <b>{amount}₽</b>\n"
        f"Товар: {item_name}\n"
        f"----------------\n"
        f"{status_line}\n"
        f"Источник: <code>{source}</code>\n"
        f"Всего принес денег: <b>{total}₽</b>\n"
        "#payment"
    )
    asyncio.create_task(send_log(bot, text))

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
        except:
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


# 🤝 ТИП 6: РЕФЕРАЛ
async def log_referral(bot: Bot, referrer_id: int, new_user):
    new_user_name = f"@{new_user.username}" if new_user.username else f"ID:{new_user.id}"
    
    text = (
        "🤝 <b>РЕФЕРАЛЬНАЯ ПРОГРАММА</b>\n"
        "➖➖➖➖➖➖➖\n"
        f"📢 Кто пригласил: <a href='tg://user?id={referrer_id}'>{referrer_id}</a>\n"
        f"👤 Кто пришел: {new_user_name}\n"
        "🎁 Бонус: <b>+2 банана</b> (другу)\n"
        "#referral"
    )
    asyncio.create_task(send_log(bot, text))