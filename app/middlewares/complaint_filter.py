from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from typing import Callable, Dict, Any, Awaitable
import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[\w']+", re.UNICODE)


def _get_checkable_text(message: Message) -> str | None:
    """Текст сообщения или подпись к фото/документу."""
    raw = message.text or message.caption
    if not raw:
        return None
    return raw.strip()


def _normalize_text(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip().lower()


def _compile_word_pattern(word: str) -> re.Pattern[str]:
    """Целое слово (кириллица и латиница)."""
    return re.compile(rf"(?<!\w){re.escape(word)}(?!\w)", re.IGNORECASE | re.UNICODE)


def _compile_phrase_pattern(phrase: str) -> re.Pattern[str]:
    parts = phrase.split()
    inner = r"\s+".join(re.escape(p) for p in parts)
    return re.compile(rf"(?<!\w){inner}(?!\w)", re.IGNORECASE | re.UNICODE)


class ComplaintFilterMiddleware(BaseMiddleware):
    """
    Middleware для перехвата жалоб на качество генерации.
    Должен быть зарегистрирован ПОСЛЕДНИМ (внешний слой), чтобы видеть все сообщения,
    в т.ч. 2–4-е фото альбома до того, как AlbumMiddleware их поглотит.
    """

    COMPLAINT_TRIGGERS = [
        "не похоже",
        "не похож",
        "не я",
        "чужое лицо",
        "не знаю",
        "непохоже",
        "непохожи",
        "нет сходства",
        "сходство",
        "сходства",
        "ужас",
        "ужасно",
        "кошмар",
        "бред",
        "фигня",
        "хуйня",
        "халтура",
        "переделывайте",
        "переделывай",
        "это вообще не мы",
        "это не мы",
        "не мы",
        "не моё лицо",
        "это не я",
        "совсем не я",
        "вообще не я",
        "говно",
    ]

    GENERATION_VERBS = [
        "нарисуй",
        "сделай",
        "создай",
        "сгенерируй",
        "соедини",
        "обьедини",
        "объедини",
        "смешай",
        "draw",
        "create",
        "make",
        "gen",
        "mix",
        "blend",
    ]

    def __init__(self) -> None:
        singles: list[str] = []
        phrases: list[str] = []
        for trigger in self.COMPLAINT_TRIGGERS:
            if " " in trigger:
                phrases.append(trigger)
            else:
                singles.append(trigger)

        self._single_triggers = frozenset(s.lower() for s in singles)
        self._phrase_patterns = [_compile_phrase_pattern(p) for p in phrases]
        self._generation_verb_patterns = [
            _compile_word_pattern(v) for v in self.GENERATION_VERBS
        ]

    def _has_complaint(self, text_lower: str) -> bool:
        words = set(_WORD_RE.findall(text_lower))
        if words & self._single_triggers:
            return True
        return any(p.search(text_lower) for p in self._phrase_patterns)

    def _has_generation_verb(self, text_lower: str) -> bool:
        return any(p.search(text_lower) for p in self._generation_verb_patterns)

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)

        text = _get_checkable_text(event)
        if not text:
            return await handler(event, data)

        text_lower = _normalize_text(text)
        word_count = len(text_lower.split())

        has_complaint = self._has_complaint(text_lower)

        if word_count > 10:
            return await handler(event, data)

        if len(text) > 35 and not has_complaint:
            return await handler(event, data)

        if self._has_generation_verb(text_lower):
            logger.info(
                f"[FILTER_SKIP] Text: '{text[:50]}' | "
                f"Reason: Found generation verb | Passed to Generator"
            )
            return await handler(event, data)

        if has_complaint:
            logger.info(
                f"[COMPLAINT_FILTER] User: {event.from_user.id} | "
                f"Text: '{text}' (words: {word_count}) | Status: Intercepted"
            )
            from app.handlers.generation import send_complaint_instruction

            await send_complaint_instruction(event)
            return None

        return await handler(event, data)
