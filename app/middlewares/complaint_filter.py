from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from typing import Callable, Dict, Any, Awaitable
import logging

logger = logging.getLogger(__name__)

class ComplaintFilterMiddleware(BaseMiddleware):
    """
    Middleware для перехвата жалоб на качество генерации
    Срабатывает ДО обработки сообщения как промпта
    """
    
    # Триггеры жалоб
    COMPLAINT_TRIGGERS = [
        'не похоже', 'не похож', 'не я', 'чужое лицо', 
        'нет сходства', 'ужасно', 'кошмар', 'бред', 
        'фигня', 'хуйня',
        'переделывайте', 'переделывай', 'переделай',
        'это вообще не мы', 'это не мы', 'не мы',
        'это не я', 'совсем не я', 'вообще не я'
    ]
    
    # Глаголы генерации (если есть - это промпт, не жалоба)
    GENERATION_VERBS = [
        'нарисуй', 'сделай', 'создай', 'сгенерируй', 
        'соедини', 'обьедини', 'объедини', 'смешай',
        'draw', 'create', 'make', 'gen', 'mix', 'blend'
    ]
    
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any]
    ) -> Any:
        """Проверяет сообщения на наличие жалоб"""
        
        # Middleware работает только для текстовых сообщений
        if not isinstance(event, Message) or not event.text:
            return await handler(event, data)
        
        text = event.text.strip()
        text_lower = text.lower()
        
        # ✅ Проверка ВСЕХ 3 условий
        
        # 1. Длина ≤ 35 символов
        if len(text) > 35:
            return await handler(event, data)
        
        # 2. НЕТ глаголов генерации
        has_generation_verb = any(verb in text_lower for verb in self.GENERATION_VERBS)
        if has_generation_verb:
            # Логируем пропуск
            logger.info(
                f"[FILTER_SKIP] Text: '{text[:50]}' | "
                f"Reason: Found generation verb | Passed to Generator"
            )
            return await handler(event, data)
        
        # 3. ЕСТЬ триггер жалобы
        has_complaint = any(trigger in text_lower for trigger in self.COMPLAINT_TRIGGERS)
        if not has_complaint:
            return await handler(event, data)
        
        # 🔥 ВСЕ 3 УСЛОВИЯ СОВПАЛИ - ПЕРЕХВАТЫВАЕМ!
        
        logger.info(
            f"[COMPLAINT_FILTER] User: {event.from_user.id} | "
            f"Text: '{text}' | Status: Intercepted (Generations stopped)"
        )
        
        # Отправляем сообщение с инструкцией
        from app.handlers.generation import send_complaint_instruction
        await send_complaint_instruction(event)
        
        # НЕ вызываем handler - сообщение перехвачено
        return None