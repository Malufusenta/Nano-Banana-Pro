from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from typing import Callable, Dict, Any, Awaitable
from app.database import async_session
from app.services.user_service import get_user


class BlockCheckMiddleware(BaseMiddleware):
    """
    Middleware для проверки блокировки пользователя.
    Если пользователь заблокирован - игнорируем его сообщения.
    """
    
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any]
    ) -> Any:
        # Получаем user_id в зависимости от типа события
        user_id = event.from_user.id if event.from_user else None
        
        if not user_id:
            return await handler(event, data)
        
        # Проверяем блокировку
        async with async_session() as session:
            user = await get_user(session, user_id)
            
            # Если пользователь заблокирован - молча игнорируем
            if user and user.is_blocked:
                return  # Просто возвращаемся, не вызывая handler
        
        # Если не заблокирован - обрабатываем как обычно
        return await handler(event, data)