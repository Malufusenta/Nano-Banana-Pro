from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from app.database import async_session
from app.services.i18n import resolve_locale
from app.services.user_service import get_user, set_user_locale


class LocaleMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        user = None
        if isinstance(event, Message):
            user = event.from_user
        elif isinstance(event, CallbackQuery):
            user = event.from_user

        locale = "en"
        if user:
            locale = resolve_locale(getattr(user, "language_code", None))
            async with async_session() as session:
                db_user = await get_user(session, user.id)
                if db_user:
                    await set_user_locale(session, user.id, locale)

        data["locale"] = locale
        return await handler(event, data)
