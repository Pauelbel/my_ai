"""Поиск по именам и содержимому файлов проекта."""

from __future__ import annotations

import re
from pathlib import Path

from .base import SKIP_DIRS, Workspace, is_secret, tool


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
    hits: list[str] = []
    for file in ws.path(path).rglob(glob):
        if len(hits) >= 200:
            hits.append("…[больше 200 совпадений, уточни запрос]")
            break
        if not file.is_file() or is_secret(file) or SKIP_DIRS & set(file.parts):
            continue
        name = ws.rel(file)
        if regex.search(file.name):
            hits.append(f"{name} (имя файла)")
        hits.extend(search_file(file, name, regex))
    return "\n".join(hits) or "ничего не найдено"


# ----------------------------------------
# Совпадения внутри одного файла: путь:строка: текст
# ----------------------------------------
def search_file(file: Path, name: str, regex: re.Pattern[str]) -> list[str]:
    try:
        text = file.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    return [f"{name}:{number}: {line.strip()[:200]}"
            for number, line in enumerate(text.splitlines(), 1) if regex.search(line)]
