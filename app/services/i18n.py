import json
from functools import lru_cache
from pathlib import Path


_LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"
_DEFAULT_LOCALE = "en"
_SUPPORTED = {"ru", "en", "es"}
_RU_GROUP = {"ru", "be", "uk", "kk"}


def resolve_locale(language_code: str | None) -> str:
    if not language_code:
        return _DEFAULT_LOCALE
    code = language_code.lower().split("-")[0]
    if code in _RU_GROUP:
        return "ru"
    if code == "es":
        return "es"
    return _DEFAULT_LOCALE


@lru_cache(maxsize=8)
def _load_locale(locale: str) -> dict[str, str]:
    locale_key = locale if locale in _SUPPORTED else _DEFAULT_LOCALE
    path = _LOCALES_DIR / f"{locale_key}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def t(key: str, locale: str = _DEFAULT_LOCALE, **kwargs) -> str:
    current = _load_locale(locale)
    text = current.get(key)
    if text is None and locale != _DEFAULT_LOCALE:
        text = _load_locale(_DEFAULT_LOCALE).get(key)
    if text is None:
        text = key
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text
