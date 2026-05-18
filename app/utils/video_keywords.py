"""Детектор запросов на оживление / видео по тексту пользователя."""

VIDEO_KEYWORDS = [
    "оживи",
    "оживить",
    "анимация",
    "видео",
    "видио",
    "video",
    "animate",
    "анимируй",
    "ожевить",
]


def is_video_request(text: str | None) -> bool:
    if not text or not text.strip():
        return False
    lowered = text.lower()
    return any(keyword in lowered for keyword in VIDEO_KEYWORDS)
