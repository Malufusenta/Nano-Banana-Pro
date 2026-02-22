import asyncio
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from app.services.admin_logger import log_action


class AdminSpyMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any]
    ) -> Any:

        bot = data.get("bot")
        user = event.from_user

        # ЛОГИКА ДЛЯ СООБЩЕНИЙ
        if isinstance(event, Message):
            if event.text and not event.text.startswith("/"):
                safe_text = event.text[:200] + "..." if len(event.text) > 200 else event.text
                asyncio.create_task(
                    log_action(bot, user.id, user.username, safe_text, is_message=True)
                )

        # ЛОГИКА ДЛЯ КНОПОК (CALLBACK)
        elif isinstance(event, CallbackQuery):
            asyncio.create_task(
                log_action(bot, user.id, user.username, f"Нажал кнопку [ data={event.data} ]", is_message=False)
            )

        return await handler(event, data)