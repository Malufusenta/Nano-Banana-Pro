"""Проверка JSON-локалей: ключи относительно en, плейсхолдеры, явные копии en."""

from __future__ import annotations

import json
import re
from pathlib import Path

LOCALES_DIR = Path(__file__).resolve().parent / "locales"
REFERENCE_LOCALE = "en"
# Синхронно с app.services.i18n._SUPPORTED
OTHER_LOCALES = ("ru", "es")

# Ключи, где совпадение с en допустимо (бренд, заимствование в UI).
ALLOW_IDENTICAL_TO_EN: frozenset[str] = frozenset(
    {
        "shop.crypto_usd_button",
        "banana.one",
        "banana.few",
        "banana.many",
    }
)

_PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")


def _placeholder_names(s: str) -> frozenset[str]:
    names: set[str] = set()
    for m in _PLACEHOLDER_RE.finditer(s):
        inner = m.group(1)
        if inner.startswith("{") or inner.startswith("}"):
            continue
        field = inner.split("!", 1)[0].split(":", 1)[0].strip()
        if field:
            names.add(field)
    return frozenset(names)


def load_locale_map(path: Path) -> dict[str, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"{path}: ожидается JSON-объект со строковыми ключами"
        raise ValueError(msg)
    out: dict[str, str] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, str):
            msg = f"{path}: ключи и значения должны быть строками, сейчас {k!r}"
            raise ValueError(msg)
        out[k] = v
    return out


def collect_locale_errors() -> list[str]:
    """Собрать список ошибок; пустой список = всё ок."""
    errors: list[str] = []
    ref_path = LOCALES_DIR / f"{REFERENCE_LOCALE}.json"
    if not ref_path.exists():
        return [f"Нет эталона {ref_path}"]

    try:
        ref = load_locale_map(ref_path)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        return [f"{REFERENCE_LOCALE}.json: {e}"]

    ref_keys = set(ref.keys())

    for loc in OTHER_LOCALES:
        path = LOCALES_DIR / f"{loc}.json"
        if not path.exists():
            errors.append(f"[{loc}] файл отсутствует: {path}")
            continue
        try:
            cur = load_locale_map(path)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            errors.append(f"[{loc}] {path.name}: {e}")
            continue

        cur_keys = set(cur.keys())
        for k in sorted(ref_keys - cur_keys):
            errors.append(f"[{loc}] нет ключа из {REFERENCE_LOCALE}.json: {k!r}")
        for k in sorted(cur_keys - ref_keys):
            errors.append(f"[{loc}] лишний ключ (нет в {REFERENCE_LOCALE}.json): {k!r}")

        for k in sorted(ref_keys & cur_keys):
            if k not in ALLOW_IDENTICAL_TO_EN and cur[k].strip() == ref[k].strip():
                errors.append(
                    f"[{loc}] ключ {k!r}: текст совпадает с {REFERENCE_LOCALE} (вероятно не переведено)"
                )
            p_ref = _placeholder_names(ref[k])
            p_loc = _placeholder_names(cur[k])
            if p_ref != p_loc:
                errors.append(
                    f"[{loc}] ключ {k!r}: плейсхолдеры {sorted(p_ref)} ≠ {sorted(p_loc)}"
                )

    return errors
