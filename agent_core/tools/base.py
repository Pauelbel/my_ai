"""Основа инструментов: описание Tool, декоратор @tool и реестр TOOLS, Workspace — всё, что нужно инструментам.

Инструменты — по модулю на группу (files, search, shell, git, remember, browser); каждый регистрируется в TOOLS
декоратором @tool при импорте. Новый инструмент — функция с @tool в своём модуле и его импорт в agent.py
(порядок импорта — порядок в промпте).
"""

from __future__ import annotations

import inspect
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ..config import Config
from ..memory import Memory

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
# Окружение подпроцесса: папка venv первой в PATH, чтобы python и pip были из venv
# ----------------------------------------
def venv_env() -> dict[str, str]:
    bin_dir = str(Path(sys.executable).parent)
    return {**os.environ, "PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""), "VIRTUAL_ENV": sys.prefix}
