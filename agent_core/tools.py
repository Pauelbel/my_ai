"""Встроенные инструменты: файлы, поиск, удаление в корзину, команды оболочки, память. Реестр TOOLS."""

from __future__ import annotations

import inspect
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .config import Config, IS_WINDOWS
from .memory import Memory

if TYPE_CHECKING:
    from .browser import Browser


SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".trash", ".pytest_cache", ".mypy_cache"}


# ----------------------------------------
# Описание встроенного инструмента
# ----------------------------------------
@dataclass
class Tool:
    name: str  # имя для модели (read_file) — на нём держатся промпт и журнал
    description: str
    func: Callable[..., str]
    confirm: Callable[[dict[str, Any]], bool]
    signature: str
    title: str  # название для человека в интерфейсе («Чтение файла»)


TOOLS: dict[str, Tool] = {}


# ----------------------------------------
# Правило подтверждения: спрашивать всегда
# ----------------------------------------
def always(args: dict[str, Any]) -> bool:
    return True


# ----------------------------------------
# Правило подтверждения: не спрашивать
# ----------------------------------------
def never(args: dict[str, Any]) -> bool:
    return False


# ----------------------------------------
# Сигнатура для модели: параметры функции без первого (ws)
# ----------------------------------------
def describe_signature(func: Callable[..., str]) -> str:
    params = list(inspect.signature(func).parameters.values())[1:]
    parts = []
    for param in params:
        text = f"{param.name}: {param.annotation}"
        if param.default is not inspect.Parameter.empty:
            text += f" = {param.default!r}"
        parts.append(text)
    return f"{func.__name__}({', '.join(parts)})"


# ----------------------------------------
# Декоратор: регистрирует функцию как инструмент — русское название для интерфейса и описание в одну строку
# ----------------------------------------
def tool(title: str, description: str, confirm: Callable[[dict[str, Any]], bool] = never) -> Callable:

    # ----------------------------------------
    # Кладёт функцию в реестр TOOLS
    # ----------------------------------------
    def register(func: Callable[..., str]) -> Callable[..., str]:
        TOOLS[func.__name__] = Tool(func.__name__, description, func, confirm, describe_signature(func), title)
        return func

    return register


# ----------------------------------------
# Обрезает длинный вывод с пометкой, сколько срезано
# ----------------------------------------
def trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…[обрезано {len(text) - limit} символов]"


# ----------------------------------------
# Всё, что нужно инструментам: конфиг, браузер, память
# ----------------------------------------
@dataclass
class Workspace:
    config: Config
    browser: Browser
    memory: Memory

    # ----------------------------------------
    # Путь внутри папки проекта; выход за её пределы — ошибка
    # ----------------------------------------
    def path(self, relative: str) -> Path:
        project = self.config.project_dir.resolve()
        full = (project / relative).resolve()
        if not full.is_relative_to(project):
            raise ValueError(f"путь {relative} вне папки проекта {project}")
        return full

    # ----------------------------------------
    # Путь от корня проекта для вывода модели
    # ----------------------------------------
    def rel(self, full: Path) -> str:
        return full.relative_to(self.config.project_dir.resolve()).as_posix() or "."


# ----------------------------------------
# Секреты скиллов (.env) модели не показываются
# ----------------------------------------
def is_secret(path: Path) -> bool:
    return path.name == ".env" or path.name.startswith(".env.")


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


# ----------------------------------------
# Окружение подпроцесса: папка venv первой в PATH, чтобы python и pip были из venv
# ----------------------------------------
def venv_env() -> dict[str, str]:
    bin_dir = str(Path(sys.executable).parent)
    return {**os.environ, "PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""), "VIRTUAL_ENV": sys.prefix}


# ----------------------------------------
# Команда оболочки: PowerShell на Windows, bash на Linux
# ----------------------------------------
@tool("Команда", "Выполняет команду в оболочке (PowerShell на Windows, bash на Linux) в папке проекта, "
      "возвращает вывод", confirm=always)
def run(ws: Workspace, cmd: str, timeout: int = 120) -> str:
    utf8 = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    shell = ["powershell", "-NoProfile", "-Command", utf8 + cmd] if IS_WINDOWS else ["bash", "-c", cmd]
    done = subprocess.run(shell, cwd=ws.config.project_dir, env=venv_env(), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    output = (done.stdout + done.stderr).strip() or "(нет вывода)"
    if done.returncode != 0:
        raise RuntimeError(f"код выхода {done.returncode}\n{output}")
    return output


# ----------------------------------------
# Запись факта в долговременную память
# ----------------------------------------
@tool("Запомнить", "Запоминает факт навсегда; вызывать, только когда пользователь прямо просит запомнить")
def remember(ws: Workspace, fact: str) -> str:
    return "запомнил" if ws.memory.add(fact) else "это уже есть в памяти"
