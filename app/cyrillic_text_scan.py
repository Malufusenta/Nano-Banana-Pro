"""Scan Python sources for Cyrillic (likely hardcoded user-facing or comment text)."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

# ASCII-only source; covers Russian letters + common Cyrillic letters in one block.
CYRILLIC_RE = re.compile("[\u0400-\u04FF]")


def _skip_dir(name: str) -> bool:
    return name in {"__pycache__", ".venv", "venv", "node_modules"} or name.startswith(".")


def iter_py_files(roots: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        if root.is_file() and root.suffix == ".py":
            out.append(root.resolve())
            continue
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root, topdown=True):
            dirnames[:] = [d for d in dirnames if not _skip_dir(d)]
            for fn in filenames:
                if fn.endswith(".py"):
                    out.append(Path(dirpath) / fn)
    return sorted(set(p.resolve() for p in out))


def file_cyrillic_hits(path: Path) -> list[tuple[int, str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if CYRILLIC_RE.search(line):
            hits.append((lineno, line.rstrip("\n")))
    return hits


def collect_cyrillic_hits(repo_root: Path, *, include_admin_panel: bool = False) -> list[str]:
    """Each entry: 'relative/path.py:line: trimmed content'."""
    roots: list[Path] = [repo_root / "app"]
    main_py = repo_root / "main.py"
    if main_py.is_file():
        roots.append(main_py)
    if include_admin_panel:
        roots.append(repo_root / "admin_panel")

    messages: list[str] = []
    for path in iter_py_files(roots):
        try:
            rel = path.relative_to(repo_root)
        except ValueError:
            rel = path
        rel_s = rel.as_posix()
        for lineno, line in file_cyrillic_hits(path):
            snippet = line.strip()
            if len(snippet) > 200:
                snippet = snippet[:197] + "..."
            messages.append(f"{rel_s}:{lineno}: {snippet}")
    return messages
