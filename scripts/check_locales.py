#!/usr/bin/env python3
"""Проверка app/locales/*.json. Запуск из корня репозитория: python scripts/check_locales.py"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.locale_validation import collect_locale_errors  # noqa: E402


def main() -> int:
    errors = collect_locale_errors()
    if errors:
        print("Ошибки локализации:", file=sys.stderr)
        for line in errors:
            print(line, file=sys.stderr)
        return 1
    print("OK: ключи, плейсхолдеры и отличие от en для ru/es")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
