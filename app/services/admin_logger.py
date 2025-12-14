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

# 💰 ТИП 2: ФИНАНСЫ
async def log_payment(bot: Bot, user, amount, item_name: str, new_balance: int):
    username = f"@{user.username}" if user.username else "Нет"
    text = (
        "💸 <b>УСПЕШНАЯ ОПЛАТА</b>\n"
        "➖➖➖➖➖➖➖\n"
        f"Кто: {username} (<a href='tg://user?id={user.id}'>Ссылка</a>)\n"
        f"Сумма: <b>{amount}</b>\n"
        f"Товар: {item_name}\n"
        f"Баланс после: {new_balance} 🍌\n"
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

# 👣 ТИП 4: ДЕЙСТВИЯ (Без звука)
async def log_action(bot: Bot, user_id: int, username: str, action: str, is_message: bool = False):
    u_name = f"@{username}" if username else f"ID:{user_id}"
    
    if is_message:
        # Если это сообщение пользователя
        text = f"💬 Сообщение: {action}\n👤 {u_name}\n#message"
    else:
        # Если это КНОПКА (Callback) - делаем красиво
        # В action приходит строка "Нажал кнопку [ data=... ]"
        try:
            if "data=" in action:
                raw_code = action.split("data=")[1].strip(" ]")
                
                # Ищем красивую иконку
                prefix_label = "👣 Кнопка"
                for prefix, label in CALLBACK_ICONS.items():
                    if raw_code.startswith(prefix):
                        prefix_label = label
                        break
                
                # Формируем читаемый лог
                text = f"{prefix_label}: <code>{raw_code}</code>\n👤 {u_name}\n#action"
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
