"""Описания моделей для preflight UI."""
from app.services.i18n import t


def get_model_description(model: str, locale: str) -> str:
    key_map = {
        "standard": "generation.preflight.model_desc_standard",
        "nb2": "generation.preflight.model_desc_nb2",
        "pro": "generation.preflight.model_desc_pro",
    }
    key = key_map.get(model)
    if not key:
        return ""
    return t(key, locale)
