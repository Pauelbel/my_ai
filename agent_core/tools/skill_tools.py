"""Скиллы как файлы: чтение и точечная правка. Папка skills/ лежит вне папки проекта, файловым инструментам она не видна."""

from __future__ import annotations

from pathlib import Path

from ..schemas import SkillCode
from ..skills import load_skills, save_skill
from .base import Workspace, always, tool
from .files import read_page, replace_once


# ----------------------------------------
# Папка скилла по имени; такого нет — ошибка со списком имён
# ----------------------------------------
def skill_folder(name: str) -> Path:
    skills = load_skills()
    if name not in skills:
        raise ValueError(f"скилла {name} нет. Есть: {', '.join(sorted(skills)) or 'ничего'}")
    return skills[name].folder


# ----------------------------------------
# SKILL.md и код скилла; большой run.py — страницами
# ----------------------------------------
@tool("Чтение скилла", "Показывает SKILL.md и run.py скилла из списка; большой run.py — дальше со строки start")
def read_skill(ws: Workspace, name: str, start: int = 1) -> str:
    folder = skill_folder(name)
    head = f'Правка: {{"name": "edit_skill", "args": {{"name": "{name}", "old": "…", "new": "…"}}}}\n'
    if start == 1:
        head += f"--- SKILL.md ---\n{(folder / 'SKILL.md').read_text(encoding='utf-8')}\n"
    head += "--- run.py ---\n"
    return head + read_page(folder / "run.py", start, None, ws.config.output_limit - len(head))


# ----------------------------------------
# Точечная правка скилла; результат с синтаксической ошибкой не сохраняется
# ----------------------------------------
@tool("Правка скилла", "Заменяет точный кусок old на new в файле скилла: file — run.py или SKILL.md; "
      "прежняя версия уходит в .bak", confirm=always)
def edit_skill(ws: Workspace, name: str, old: str, new: str, file: str = "run.py") -> str:
    fields = {"SKILL.md": "skill_md", "run.py": "run_py"}
    if file not in fields:
        raise ValueError("file — только run.py или SKILL.md")
    folder = skill_folder(name)
    data = {field: (folder / part).read_text(encoding="utf-8") for part, field in fields.items()}
    data[fields[file]] = replace_once(data[fields[file]], old, new)
    save_skill(folder.name, SkillCode.model_validate(data, context={"skill": name}))
    return f"изменён skills/{folder.name}/{file}. Проверь: запусти {name} с теми же аргументами, что и раньше"
