"""Поиск по именам и содержимому файлов проекта."""

from __future__ import annotations

import re
from pathlib import Path

from .base import SKIP_DIRS, Workspace, is_secret, tool


MAX_HITS = 200


# ----------------------------------------
# Поиск по именам файлов и содержимому
# ----------------------------------------
@tool("Поиск", "Ищет pattern (регулярное выражение или текст) в именах и содержимом файлов, glob — фильтр имён, "
      "например *.py")
def search(ws: Workspace, pattern: str, path: str = ".", glob: str = "*") -> str:
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error:
        regex = re.compile(re.escape(pattern), re.IGNORECASE)
    root = ws.path(path)
    candidates = [root] if root.is_file() else root.rglob(glob)
    hits: list[str] = []
    for file in candidates:
        name = ws.rel(file)
        try:
            inside_workspace = file.resolve().is_relative_to(ws.config.project_dir.resolve())
        except OSError:
            inside_workspace = False
        if (not inside_workspace or not file.is_file() or is_secret(file)
                or SKIP_DIRS.intersection(Path(name).parts)):
            continue
        if regex.search(file.name):
            hits.append(f"{name} (имя файла)")
        if len(hits) > MAX_HITS:
            break
        hits.extend(search_file(file, name, regex, limit=MAX_HITS + 1 - len(hits)))
        if len(hits) > MAX_HITS:
            break
    if len(hits) > MAX_HITS:
        hits = hits[:MAX_HITS] + [f"…[больше {MAX_HITS} совпадений, уточни запрос]"]
    return "\n".join(hits) or "ничего не найдено"


# ----------------------------------------
# Совпадения внутри одного файла: путь:строка: текст
# ----------------------------------------
def search_file(file: Path, name: str, regex: re.Pattern[str], limit: int | None = None) -> list[str]:
    try:
        text = file.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    hits: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        if regex.search(line):
            hits.append(f"{name}:{number}: {line.strip()[:200]}")
            if limit is not None and len(hits) >= limit:
                break
    return hits
