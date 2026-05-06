"""Resolve UI locale when a Telegram Message may be from the bot (inline callbacks)."""

from __future__ import annotations

from aiogram import Bot
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.services.i18n import resolve_locale
from app.services.user_service import get_user_locale


async def effective_locale(
    bot: Bot,
    message: Message,
    acting_telegram_id: int,
    locale: str | None = None,
    *,
    session: AsyncSession | None = None,
) -> str:
    """
    If `locale` is passed, use it.
    Else use Telegram `language_code` from the message sender when the sender is not the bot.
    Otherwise load `users.locale` for `acting_telegram_id` (private chats: chat.id == user id).
    """
    if locale is not None:
        return locale
    fu = message.from_user
    if fu is not None and fu.id != bot.id:
        return resolve_locale(fu.language_code)
    if session is not None:
        return await get_user_locale(session, acting_telegram_id)
    async with async_session() as session2:
        return await get_user_locale(session2, acting_telegram_id)
