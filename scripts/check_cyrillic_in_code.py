#!/usr/bin/env python3
"""Search for Cyrillic in Python sources (not in locale JSON). Run from repo root."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.cyrillic_text_scan import collect_cyrillic_hits  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Find Cyrillic in .py under app/ (+ main.py).")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with code 1 if any Cyrillic found (for CI once codebase is clean).",
    )
    parser.add_argument(
        "--include-admin",
        action="store_true",
        help="Also scan admin_panel/.",
    )
    args = parser.parse_args()

    hits = collect_cyrillic_hits(ROOT, include_admin_panel=args.include_admin)
    if not hits:
        print("OK: no Cyrillic in scanned Python files.")
        return 0

    for line in hits:
        print(line)
    print(f"\nTotal: {len(hits)} line(s) with Cyrillic.", file=sys.stderr)

    if args.strict:
        print(
            "Strict mode: failing. Remove hardcoded text or unset --strict.",
            file=sys.stderr,
        )
        return 1
    print(
        "Hint: use --strict in CI after moving strings to locales; "
        "set STRICT_CYRILLIC=1 for unittest.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
