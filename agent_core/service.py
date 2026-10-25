"""Служебные команды агента: /помощь, /скиллы, /память, /проект, /модель, /продолжить, /новый."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Generator

from .events import Event, show_args
from .journal import Journal
from .prompts import SERVICE_HELP
from .schemas import NeedSkill


# ----------------------------------------
# Часть Agent: служебные команды, которые работают без модели
# ----------------------------------------
class ServiceCommands:

    # ----------------------------------------
    # /продолжить: поднимает последнюю сессию и доделывает незавершённый шаг
    # ----------------------------------------
    def _resume(self) -> Generator[Event, None, None]:
        journal = Journal.latest(exclude=self.journal.path)
        if journal is None:
            yield Event("info", "В logs/ нет сессий.")
            return
        self.journal = journal
        self.approved.clear()
        self.started = time.monotonic()
        task, records = journal.current_task()
        yield Event("info", f"Поднята сессия {journal.path.name}, записей: {len(journal.records)}")
        if not task or any(record["type"] == "end" for record in records):
            yield Event("info", "Последняя задача в ней завершена — можно продолжать разговор.")
            return
        yield Event("info", f"Продолжаю задачу: {task}")
        last = records[-1] if records else {}
        steps = sum(1 for record in records if record["type"] == "call")
        if last.get("type") == "need_skill":
            need = NeedSkill(kind="need_skill", name=last["name"], what=last["what"], libs=last["libs"])
            yield from self._need_skill(need, task)
            return
        if last.get("type") == "call":
            yield Event("info", f"Повторяю незавершённый шаг: {last['name']} {show_args(last['args'])}")
            result, error = yield from self._execute(last["name"], last["args"])
            self.journal.write({"type": "result", "step": last.get("step", steps), "name": last["name"],
                                "result": result, "error": error})
            yield Event("tool_result", f"ОШИБКА: {error}" if error else result, {"name": last["name"], "error": error})
        yield from self._loop(task, step=steps)

    # ----------------------------------------
    # Встроенные служебные команды (/помощь, /память, /модель …)
    # ----------------------------------------
    def _service(self, text: str) -> Generator[Event, None, None]:
        command, _, arg = text.partition(" ")
        arg = arg.strip()
        if command == "/продолжить":
            yield from self._resume()
        elif command == "/новый":
            self.journal = Journal.new()
            self.approved.clear()
            self.started = time.monotonic()
            yield Event("info", "Новая сессия.")
        elif command == "/помощь":
            yield Event("info", self._help())
        elif command == "/скиллы":
            lines = [f"{s.signature} — {s.description}" for s in self._skills.values()]
            yield Event("info", "\n".join(lines) or "Скиллов пока нет.")
        elif command == "/скилл":
            yield Event("info", self._show_skill(arg))
        elif command == "/память":
            yield Event("info", self.memory.read() or "Память пуста.")
        elif command == "/запомни":
            yield Event("info", "Запомнил." if arg and self.memory.add(arg) else "Нечего добавлять или уже есть.")
        elif command == "/проект":
            yield Event("info", self._set_project(arg))
        elif command == "/модель":
            if arg:
                self.config.model = arg
            yield Event("info", f"Модель: {self.config.model}")

    # ----------------------------------------
    # Текст для /помощь: служебные команды + быстрые из commands.toml
    # ----------------------------------------
    def _help(self) -> str:
        lines = [f"{key} — {value}" for key, value in SERVICE_HELP.items()]
        lines += [f"{' | '.join(c.patterns)} → скилл {c.skill}" for c in self.commands]
        return "\n".join(lines)

    # ----------------------------------------
    # SKILL.md и код скилла для /скилл <имя>
    # ----------------------------------------
    def _show_skill(self, name: str) -> str:
        skill = self._skills.get(name)
        if skill is None:
            return f"Скилла {name or '(без имени)'} нет. Есть: {', '.join(self._skills) or 'ничего'}"
        md = (skill.folder / "SKILL.md").read_text(encoding="utf-8")
        code = (skill.folder / "run.py").read_text(encoding="utf-8")
        return f"{md}\n--- run.py ---\n{code}"

    # ----------------------------------------
    # Смена папки проекта для /проект <путь>
    # ----------------------------------------
    def _set_project(self, path: str) -> str:
        if not path:
            return f"Папка проекта: {self.config.project_dir}"
        folder = Path(path).expanduser().resolve()
        if not folder.is_dir():
            return f"Папки {folder} нет."
        self.config.project_dir = folder
        return f"Папка проекта: {folder}"
