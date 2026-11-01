"""Файлы: чтение, запись, правка, дерево папки, удаление в корзину проекта."""

from __future__ import annotations

import re
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
    return read_page(full, start, end, ws.config.output_limit)


# ----------------------------------------
# Страница файла с подсказкой, откуда читать дальше; не длиннее limit
# ----------------------------------------
def read_page(full: Path, start: int, end: int | None, limit: int) -> str:
    if start < 1:
        raise ValueError("start должен быть не меньше 1")
    if end is not None and end < start:
        raise ValueError("end должен быть не меньше start")
    lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return "(пустой файл)"
    if start > len(lines):
        return f"(строка {start} за пределами файла; всего строк: {len(lines)})"
    page = 300
    last = min(end if end is not None else start + page - 1, len(lines))
    # Страница не длиннее вывода инструмента: иначе обрезка срежет подсказку, откуда читать дальше
    budget, size = limit - 200, 0
    for index in range(start - 1, last):
        size += len(lines[index]) + 1
        if size > budget and index > start - 1:
            last = index
            break
    text = "\n".join(lines[start - 1:last])
    if last < len(lines):
        text += f"\n…[показаны строки {start}-{last} из {len(lines)}; дальше: start={last + 1}]"
    return text


# ----------------------------------------
# Markdown: модель пишет перенос в ячейке таблицы буквальным «\n» — в таблице это <br>
# ----------------------------------------
def md_cells(path: Path, text: str) -> str:
    if path.suffix.lower() not in {".md", ".markdown"}:
        return text
    return "\n".join(re.sub(r"\\n", "<br>", line) if line.lstrip().startswith("|") else line
                     for line in text.split("\n"))


# ----------------------------------------
# Запись файла целиком (старая версия — в .bak) или дописывание в конец
# ----------------------------------------
@tool("Запись файла", "Создаёт или перезаписывает файл целиком (старая версия сохраняется в .bak); "
      "append=true — дописывает content в конец файла", confirm=always)
def write_file(ws: Workspace, path: str, content: str, append: bool = False) -> str:
    full = ws.path(path)
    content = md_cells(full, content)
    if append and full.exists():
        glue = "" if full.read_bytes()[-1:] in (b"", b"\n") else "\n"  # дописанное начинается с новой строки
        with full.open("a", encoding="utf-8") as file:
            file.write(glue + content)
        return f"дописано в {ws.rel(full)} ({len(content)} символов)"
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
    full.write_text(replace_once(text, old, md_cells(full, new)), encoding="utf-8")
    return f"изменён {ws.rel(full)}"


# ----------------------------------------
# Заменяет кусок, который встречается в тексте ровно один раз
# ----------------------------------------
def replace_once(text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count == 0:
        raise ValueError("кусок old не найден; прочитай файл и скопируй текст точно")
    if count > 1:
        raise ValueError(f"кусок old найден {count} раз; возьми кусок длиннее, чтобы он был уникальным")
    return text.replace(old, new)


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
        # Не ходим по симлинку на каталог: он может вести за пределы workspace.
        if entry.is_dir() and not entry.is_symlink():
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
    stamp = f"{datetime.now():%Y%m%d_%H%M%S_%f}"
    target = trash / f"{stamp}_{full.name}"
    suffix = 1
    while target.exists():
        target = trash / f"{stamp}_{suffix}_{full.name}"
        suffix += 1
    shutil.move(full, target)
    return f"{ws.rel(full)} перемещён в {ws.rel(target)}"
