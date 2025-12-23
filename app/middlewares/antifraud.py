import re
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery

class AntiFraudMiddleware(BaseMiddleware):
    def __init__(self):
        # Коды языков для блокировки
        self.banned_langs = ['ar', 'fa', 'ur', 'ps']
        # Regex для поиска арабских символов
        self.arabic_pattern = re.compile(r'[\u0600-\u06FF]')

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any]
    ) -> Any:
        
        user = event.from_user
        if not user:
            return await handler(event, data)

        # 1. ПРОВЕРКА ЛОКАЛИ (Язык интерфейса)
        if user.language_code:
            for code in self.banned_langs:
                if user.language_code.startswith(code):
                    return await self.fake_error(event)

        # 2. ПРОВЕРКА ИМЕНИ И ФАМИЛИИ
        full_name = (user.first_name or "") + " " + (user.last_name or "")
        if self.arabic_pattern.search(full_name):
            return await self.fake_error(event)

        # 3. ПРОВЕРКА ТЕКСТА СООБЩЕНИЯ (Если это сообщение)
        if isinstance(event, Message):
            content = (event.text or "") + (event.caption or "")
            if content and self.arabic_pattern.search(content):
                return await self.fake_error(event)

        # Если все чисто — пропускаем к боту
        return await handler(event, data)

    async def fake_error(self, event):
        """Отправляет фейковую ошибку и блокирует выполнение"""
        error_text = "🇬🇧 Error 503: Service temporarily unavailable due to high load. Please try again later."
        
        try:
            if isinstance(event, Message):
                await event.answer(error_text)
            elif isinstance(event, CallbackQuery):
                await event.answer(error_text, show_alert=True)
        except:
            pass
        
        # Важно: мы НЕ вызываем handler(), поэтому бот ничего не сделает
        return None