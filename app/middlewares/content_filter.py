"""
🔞 NSFW Logger - Логирование 18+ запросов в админ-чат
+ Поддержка режимов работы из .env
"""

import re
from datetime import datetime
from enum import Enum
from aiogram import Bot
from app.config import FILTER_LOG_CHANNEL_ID


# =====================================================================
# РЕЖИМЫ РАБОТЫ
# =====================================================================

class FilterMode(Enum):
    """Режимы работы фильтра"""
    SHADOW = "shadow"    # Только логирование, не блокирует
    ACTIVE = "active"    # Блокирует + логирует
    DISABLED = "disabled"  # Выключен


# =====================================================================
# NSFW-СЛОВА
# =====================================================================

NSFW_WORDS = [
    r'голая', r'голый', r'голые', r'голых', r'голой',
    r'раздет', r'раздень', r'раздевать', r'раздевается', r'раздеть',
    r'обнажен', r'обнаженн', r'обнажить',
    r'секс', r'порно', r'xxx', r'эротик',
    r'nude', r'naked', r'nsfw', r'topless', r'sexy',
    r'сиськ', r'соск', r'жоп',
]


# =====================================================================
# КЛАСС ФИЛЬТРА
# =====================================================================

class ContentFilter:
    """
    NSFW-фильтр с поддержкой режимов работы
    """
    
    def __init__(self, mode: FilterMode = FilterMode.SHADOW):
        self.mode = mode
        self.nsfw_patterns = [re.compile(word, re.IGNORECASE) for word in NSFW_WORDS]
    
    def check(self, text: str) -> tuple[bool, str | None]:
        """
        Проверяет текст на NSFW-слова
        
        Returns:
            (should_block, matched_word)
            - should_block: True если нужно заблокировать (зависит от режима)
            - matched_word: Слово которое сработало
        """
        
        if self.mode == FilterMode.DISABLED:
            return False, None
        
        # Проверяем на NSFW
        for pattern in self.nsfw_patterns:
            match = pattern.search(text)
            if match:
                # В режиме ACTIVE - блокируем, в SHADOW - только логируем
                should_block = (self.mode == FilterMode.ACTIVE)
                return should_block, match.group()
        
        return False, None
    
    def set_mode(self, mode: FilterMode):
        """Изменить режим"""
        self.mode = mode
    
    def get_mode(self) -> FilterMode:
        """Получить текущий режим"""
        return self.mode


# =====================================================================
# ФУНКЦИИ ЛОГИРОВАНИЯ
# =====================================================================

async def log_nsfw_to_chat(bot: Bot, text: str, matched_word: str, user_id: int, username: str, first_name: str):
    """
    Отправляет уведомление о NSFW-запросе в админ-чат
    """
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    display_text = text[:200] + "..." if len(text) > 200 else text
    
    message = (
        f"🔞 <b>NSFW ЗАПРОС</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Юзер:</b> {first_name}\n"
        f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
        f"📱 <b>Username:</b> @{username or 'нет'}\n"
        f"⏰ <b>Время:</b> {timestamp}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔴 <b>Слово:</b> <code>{matched_word}</code>\n"
        f"📝 <b>Текст:</b>\n"
        f"<blockquote>{display_text}</blockquote>"
    )
    
    try:
        await bot.send_message(FILTER_LOG_CHANNEL_ID, message, parse_mode="HTML")
    except Exception as e:
        print(f"❌ Ошибка отправки в NSFW-чат: {e}")


def get_filter_message() -> str:
    """
    Сообщение для пользователя при блокировке
    """
    return (
        "🔞 <b>Обнаружен запрещенный контент</b>\n\n"
        "Генерация контента 18+ запрещена правилами сервиса.\n"
        "Пожалуйста, измените запрос.\n\n"
        "💰 Ваш банан 🍌 <b>не списан</b>."
    )