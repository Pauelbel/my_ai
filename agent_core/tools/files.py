"""Файлы: чтение, запись, правка, дерево папки, удаление в корзину проекта."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from .base import SKIP_DIRS, Workspace, always, is_secret, tool


# ----------------------------------------
# Чтение файла по диапазону строк
# ----------------------------------------
@tool("Чтение файла", "Читает текстовый файл; большие — по диапазону строк start..end")
def read_file(ws: Workspace, path: str, start: int = 1, end: int | None = None) -> str:
    full = ws.path(path)
    if is_secret(full):
        raise PermissionError("файлы .env с секретами читать нельзя")
    lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
    page = 300
    last = min(end or start + page - 1, len(lines))
    text = "\n".join(lines[start - 1:last])
    if last < len(lines):
        text += f"\n…[показаны строки {start}-{last} из {len(lines)}; дальше: start={last + 1}]"
    return text or "(пустой файл)"


# ----------------------------------------
# Запись файла целиком, старая версия — в .bak
# ----------------------------------------
@tool("Запись файла", "Создаёт или перезаписывает файл целиком; старая версия сохраняется в .bak", confirm=always)
def write_file(ws: Workspace, path: str, content: str) -> str:
    full = ws.path(path)
    if full.exists():
        shutil.copy2(full, full.with_name(full.name + ".bak"))
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return f"записан {ws.rel(full)} ({len(content)} символов)"


# ----------------------------------------
# Точечная замена куска текста в файле
# ----------------------------------------
@tool("Правка файла", "Заменяет точный кусок текста old на new; ошибка, если old не найден "
      "или найден больше одного раза", confirm=always)
def edit_file(ws: Workspace, path: str, old: str, new: str) -> str:
    full = ws.path(path)
    text = full.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0:
        raise ValueError("кусок old не найден; прочитай файл и скопируй текст точно")
    if count > 1:
        raise ValueError(f"кусок old найден {count} раз; возьми кусок длиннее, чтобы он был уникальным")
    full.write_text(text.replace(old, new), encoding="utf-8")
    return f"изменён {ws.rel(full)}"


# ----------------------------------------
# Дерево папки до заданной глубины
# ----------------------------------------
@tool("Содержимое папки", "Показывает дерево папки до глубины depth")
def list_dir(ws: Workspace, path: str = ".", depth: int = 2) -> str:
    root = ws.path(path)
    lines = [f"{ws.rel(root)}/"]
    walk_tree(root, depth, "  ", lines)
    return "\n".join(lines)


# ----------------------------------------
# Рекурсивный обход для list_dir
# ----------------------------------------
def walk_tree(folder: Path, depth: int, indent: str, lines: list[str]) -> None:
    if depth <= 0:
        return
    entries = sorted(folder.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    for entry in entries:
        if entry.name in SKIP_DIRS:
            continue
        if entry.is_dir():
            lines.append(f"{indent}{entry.name}/")
            walk_tree(entry, depth - 1, indent + "  ", lines)
        else:
            lines.append(f"{indent}{entry.name}")


# ----------------------------------------
# Удаление в корзину проекта .trash/
# ----------------------------------------
@tool("Удаление", "Удаляет файл или папку (перемещает в .trash/ проекта)", confirm=always)
def delete(ws: Workspace, path: str) -> str:
    full = ws.path(path)
    if full == ws.config.project_dir.resolve():
        raise ValueError("нельзя удалить саму папку проекта")
    if not full.exists():
        raise FileNotFoundError(f"{path} не существует")
    trash = ws.path(".trash")
    trash.mkdir(exist_ok=True)
    target = trash / f"{datetime.now():%Y%m%d_%H%M%S}_{full.name}"
    shutil.move(full, target)
    return f"{ws.rel(full)} перемещён в {ws.rel(target)}"
