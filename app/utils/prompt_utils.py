"""Утилиты для анализа промптов пользователя."""

BLEND_TRIGGERS = [
    "смешай", "смешать", "микс", "mix", "blend",
    "соедини", "соединить", "объедини", "объединить", "обьедини", "обьедени", "объедени", "обьединить", "объеденить",
    "скрестить", "скрести", "составь", "совмести",
    "вариация", "variation", "комбинируй", "combine",
    "слей", "merge", "креатив", "creative",
]


def is_blend_request(prompt: str) -> bool:
    """Проверяет, хочет ли пользователь смешивание (а не замену лица)."""
    prompt_lower = prompt.lower()
    return any(trigger in prompt_lower for trigger in BLEND_TRIGGERS)
