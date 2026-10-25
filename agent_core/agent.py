"""Агент: луп «модель выбирает действие — код выполняет», подтверждения, скиллы, служебные команды."""

from __future__ import annotations

import platform
import re
import queue
import secrets
import threading
import time
from dataclasses import asdict
from datetime import datetime
from typing import Any, Generator

from .browser import Browser
from .commands import load_commands, match_command
from .config import MEMORY_FILE, Config
from .events import Event, Status, git_branch, parse_confirm, show_args
from .journal import Journal
from .memory import MEMORY_FILTER_PROMPT, Memory
from .model import Model, draft_text
from .prompts import SERVICE_HELP, SYSTEM_PROMPT
from .schemas import Action, Answer, Facts, NeedSkill, Reply
from .service import ServiceCommands
from .skill_flow import SkillFlow
from .skills import SkillInfo, load_skills, run_skill
from .tools import TOOLS, Tool, Workspace, trim


# Пользователь просит обойтись без новых скиллов: «не нужно устанавливать скрипты», «без установки пакетов»
NO_NEW_SKILLS = re.compile(
    r"\bне\s+(нужн\w*|надо|став\w*|устанавлива\w*|создава\w*|пиш\w*)\b[^.!?\n]{0,50}(скилл|скрипт|пакет|библиотек)"
    r"|\bбез\s+(\w+\s+)?(скилл|скрипт|пакет|установк|библиотек)", re.IGNORECASE)


# ----------------------------------------
# Агент: ядро без UI; send() отдаёт генератор событий
# ----------------------------------------
class Agent(ServiceCommands, SkillFlow):

    # ----------------------------------------
    # Загружает конфиг, скиллы, команды; начинает новую сессию
    # ----------------------------------------
    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config.load()
        self.model = Model(self.config)
        self.memory = Memory()
        self.ws = Workspace(self.config, Browser.shared(self.config.headless), self.memory)
        self._skills = load_skills()
        self.commands = load_commands()
        self.journal = Journal.new()
        self.approved: set[str] = set()
        self.state = "idle"
        self.step = 0
        self.started = time.monotonic()
        self._stop = threading.Event()
        self._closed = False
        self._answers: dict[str, str] = {}
        self._answered = threading.Condition()
        self._context_used = 0
        self._context_max = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._speed = 0.0

    # ----------------------------------------
    # Главный вход: текст пользователя → генератор событий
    # ----------------------------------------
    def send(self, text: str) -> Generator[Event, None, None]:
        text = text.strip()
        if text == "/стоп":
            self.stop()
            yield Event("info", "Останавливаю текущую задачу.")
            return
        self._stop.clear()
        self.state, self.step = "working", 0
        try:
            yield from self._dispatch(text)
        finally:
            self.state = "done" if self.journal.records else "idle"
        yield Event("status", data=asdict(self.status()))

    # ----------------------------------------
    # Разбор сообщения: служебная команда, быстрая команда или задача для модели
    # ----------------------------------------
    def _dispatch(self, text: str) -> Generator[Event, None, None]:
        if len(self.memory.read()) > self.config.memory_limit:
            yield Event("warning", f"Память больше {self.config.memory_limit} символов — почисти {MEMORY_FILE}")
        if text.split(" ", 1)[0] in {key.split(" ")[0] for key in SERVICE_HELP}:
            yield from self._service(text)
            return
        quick = match_command(text, self.commands)
        if quick:
            yield from self._quick(text, *quick)
            return
        self.journal.write({"type": "user", "text": text})
        yield from self._loop(text, step=0)

    # ----------------------------------------
    # Ответ UI на confirm_request или need_skill: "yes" | "no" | "session"
    # ----------------------------------------
    def confirm(self, event_id: str, answer: str) -> None:
        with self._answered:
            self._answers[event_id] = answer
            self._answered.notify_all()

    # ----------------------------------------
    # Просьба прервать луп (из другого потока или командой /стоп); ждущее подтверждение считается «нет»
    # ----------------------------------------
    def stop(self) -> None:
        self._stop.set()
        with self._answered:
            self._answered.notify_all()

    # ----------------------------------------
    # Состояние для строки статуса UI
    # ----------------------------------------
    def status(self) -> Status:
        return Status(model=self.config.model, state=self.state, step=self.step, max_steps=self.config.max_steps,
                      context_used=self._context_used, context_max=self._context_max,
                      prompt_tokens=self._prompt_tokens, completion_tokens=self._completion_tokens,
                      tokens_per_sec=round(self._speed, 1), session=self.journal.path.stem,
                      session_seconds=int(time.monotonic() - self.started), project=str(self.config.project_dir),
                      branch=git_branch(self.config.project_dir), approved=sorted(self.approved))

    # ----------------------------------------
    # Скиллы, которые видит модель
    # ----------------------------------------
    def skills(self) -> list[SkillInfo]:
        return list(self._skills.values())

    # ----------------------------------------
    # Встроенные инструменты: имя, сигнатура, описание, правило подтверждения
    # ----------------------------------------
    def tools(self) -> list[Tool]:
        return list(TOOLS.values())

    # ----------------------------------------
    # Долговременная память: факты списком (общая для всех агентов)
    # ----------------------------------------
    def memory_facts(self) -> list[str]:
        return self.memory.facts()

    # ----------------------------------------
    # Память после правки пользователем: заменяет целиком, возвращает сохранённое
    # ----------------------------------------
    def save_memory(self, facts: list[str]) -> list[str]:
        return self.memory.replace(facts)

    # ----------------------------------------
    # Записи журнала текущей сессии — по ним UI восстанавливает ленту
    # ----------------------------------------
    def history(self) -> list[dict[str, Any]]:
        return list(self.journal.records)

    # ----------------------------------------
    # Отдаёт общий браузер при закрытии агента; последний агент закрывает окно
    # ----------------------------------------
    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self.ws.browser.release()

    # ----------------------------------------
    # Быстрая команда: сразу запускает скилл, модель не вызывается
    # ----------------------------------------
    def _quick(self, text: str, name: str, args: dict[str, str]) -> Generator[Event, None, None]:
        self.journal.write({"type": "user", "text": text})
        if name not in self._skills and name not in TOOLS:
            yield Event("error", f"Команда ссылается на скилл {name}, а его нет в skills/")
            self.journal.write({"type": "end"})
            return
        self.journal.write({"type": "call", "step": 0, "name": name, "args": args})
        yield Event("step", f"{name} {show_args(args)}", {"step": 0, "name": name, "args": args})
        result, error = yield from self._execute(name, args)
        self.journal.write({"type": "result", "step": 0, "name": name, "result": result, "error": error})
        self.journal.write({"type": "end"})
        yield Event("answer", f"ОШИБКА: {error}" if error else result)

    # ----------------------------------------
    # Луп: модель выбирает действие, код выполняет, пока не будет ответа или лимита шагов
    # ----------------------------------------
    def _loop(self, task: str, step: int) -> Generator[Event, None, None]:
        last_error: tuple[str, str] | None = None
        # новая ли задача; сколько шагов инструментами; сколько раз отговорили от нового скилла
        fresh, tried, nudged = step == 0, 0, 0
        while step < self.config.max_steps:
            if self._stop.is_set():
                self.journal.write({"type": "end", "reason": "stop"})
                yield Event("info", "Остановлено.")
                return
            step += 1
            self.step = step
            try:
                action = yield from self._decide()
            except Exception as error:
                self.journal.write({"type": "end", "reason": "model_error"})
                yield Event("error", f"Модель не смогла ответить по схеме: {error}")
                return
            if action is None:
                continue  # остановлено посреди ответа модели — выход в начале цикла
            if isinstance(action, Answer):
                self.journal.write({"type": "answer", "text": action.text})
                self.journal.write({"type": "end"})
                yield Event("answer", action.text)
                self._remember_in_background(task)
                return
            if isinstance(action, NeedSkill):
                forbidden = bool(NO_NEW_SKILLS.search(task))
                if (fresh and not tried and not nudged) or (forbidden and nudged < 2):
                    nudged += 1
                    yield from self._try_tools_first(action, forbidden)
                    continue
                yield from self._need_skill(action, task)
                return
            tried += 1
            result, error = yield from self._step(action.name, action.args, step)
            if error and last_error == (action.name, error):
                yield from self._force_need_skill(task, action.name, error)
                return
            last_error = (action.name, error) if error else None
        self.journal.write({"type": "end", "reason": "max_steps"})
        yield Event("error", f"Достигнут лимит в {self.config.max_steps} шагов, задача не закончена.")

    # ----------------------------------------
    # Модель попросила скилл, ничего не попробовав (или пользователь запретил новые скиллы) — вернуть к инструментам
    # ----------------------------------------
    def _try_tools_first(self, need: NeedSkill, forbidden: bool) -> Generator[Event, None, None]:
        note = (f"Ты предложил новый скилл {need.name}, но сначала реши задачу встроенными инструментами: "
                "посмотри папку (list_dir), прочитай нужные файлы и скрипты, копируй, переименовывай и запускай "
                "через run. need_skill — только если они точно не справятся.")
        if forbidden:
            note += " Пользователь прямо попросил обойтись без новых скиллов и скриптов."
        self.journal.write({"type": "note", "text": note})
        yield Event("info", f"Модель хотела новый скилл {need.name} — прошу сначала встроенные инструменты.")

    # ----------------------------------------
    # Вызов модели: черновик ответа уходит событиями token по мере генерации, возвращается проверенное действие
    # ----------------------------------------
    def _decide(self) -> Generator[Event, None, Action | None]:
        pieces: queue.Queue[tuple[str, str] | None] = queue.Queue()
        outcome: dict[str, Any] = {}
        threading.Thread(target=self._call_model, args=(self._messages(), pieces, outcome), daemon=True).start()
        raw, draft = "", ""
        while True:
            try:
                item = pieces.get(timeout=0.1)
            except queue.Empty:
                if self._stop.is_set():
                    return None
                continue
            if item is None:
                break
            kind, piece = item
            if kind == "attempt" and draft:  # Instructor повторяет запрос после ошибки валидации
                yield Event("token", data={"reset": True})
            if kind == "attempt":
                raw, draft = "", ""
            elif kind == "thinking":
                yield Event("thinking", piece)
            else:
                raw += piece
                text = draft_text(raw)
                if len(text) > len(draft) and text.startswith(draft):
                    yield Event("token", text[len(draft):])
                    draft = text
        if self._stop.is_set():
            return None
        if "error" in outcome:
            raise outcome["error"]
        self._update_usage()
        yield Event("status", data=asdict(self.status()))
        return outcome["reply"].action

    # ----------------------------------------
    # Поток для вызова модели: куски текста — в очередь, итог или ошибка — в outcome
    # ----------------------------------------
    def _call_model(self, messages: list[dict[str, str]], pieces: queue.Queue[tuple[str, str] | None],
                    outcome: dict[str, Any]) -> None:

        # ----------------------------------------
        # Кусок от модели → в очередь; после /стоп обрывает поток генерации
        # ----------------------------------------
        def on_piece(kind: str, piece: str) -> None:
            if self._stop.is_set():
                raise InterruptedError("остановлено пользователем")
            pieces.put((kind, piece))

        try:
            outcome["reply"] = self.model.stream(messages, Reply, {"names": self._names()}, on_piece)
        except Exception as error:
            outcome["error"] = error
        finally:
            pieces.put(None)

    # ----------------------------------------
    # Токены контекста и скорость по данным последнего вызова
    # ----------------------------------------
    def _update_usage(self) -> None:
        usage = self.model.usage
        if usage:
            self._context_used = usage.total_tokens
            self._prompt_tokens, self._completion_tokens = usage.prompt_tokens, usage.completion_tokens
            self._speed = usage.completion_tokens / self.model.seconds if self.model.seconds else 0.0
        self._context_max = self.model.context_window() or self._context_max

    # ----------------------------------------
    # Один шаг: пишет в журнал до и после, спрашивает подтверждение, выполняет
    # ----------------------------------------
    def _step(self, name: str, args: dict[str, Any], step: int = 0) -> Generator[Event, None, tuple[str, str | None]]:
        self.journal.write({"type": "call", "step": step, "name": name, "args": args})
        yield Event("step", f"{name} {show_args(args)}", {"step": step, "name": name, "args": args})
        result, error = yield from self._execute(name, args)
        self.journal.write({"type": "result", "step": step, "name": name, "result": result, "error": error})
        yield Event("tool_result", f"ОШИБКА: {error}" if error else result, {"name": name, "error": error})
        return result, error

    # ----------------------------------------
    # Выполняет инструмент или скилл; любая ошибка становится текстом для модели
    # ----------------------------------------
    def _execute(self, name: str, args: dict[str, Any]) -> Generator[Event, None, tuple[str, str | None]]:
        limit = self.config.output_limit
        try:
            if name in TOOLS:
                item = TOOLS[name]
                if item.confirm(args):
                    allowed = yield from self._confirm(name, args)
                    if not allowed:
                        return "пользователь отклонил это действие", None
                return trim(str(item.func(self.ws, **args)), limit), None
            if name in self._skills:
                return trim(run_skill(self._skills[name], args, self.config), limit), None
            return "", f"нет инструмента или скилла {name}"
        except Exception as error:
            return "", trim(f"{type(error).__name__}: {error}", limit)

    # ----------------------------------------
    # Подтверждение изменяющего действия: да / нет / да на всю сессию
    # ----------------------------------------
    def _confirm(self, name: str, args: dict[str, Any]) -> Generator[Event, None, bool]:
        if name in self.approved:
            return True
        answer = yield from self._ask_user("confirm_request", f"{name} {show_args(args, 1500)}",
                                           {"name": name, "args": args})
        if answer == "session":
            self.approved.add(name)
        return answer != "no"

    # ----------------------------------------
    # Отдаёт событие с id и ждёт agent.confirm(id, …) — из этого же или другого потока
    # ----------------------------------------
    def _ask_user(self, kind: str, text: str, data: dict[str, Any]) -> Generator[Event, None, str]:
        event = Event(kind, text, data, id=secrets.token_hex(4))
        self.state = "waiting"
        yield event
        with self._answered:
            self._answered.wait_for(lambda: event.id in self._answers or self._stop.is_set())
            answer = self._answers.pop(event.id, "no")
        self.state = "working"
        return parse_confirm(answer)

    # ----------------------------------------
    # Имена, которые модель может вызвать: инструменты + скиллы
    # ----------------------------------------
    def _names(self) -> set[str]:
        return set(TOOLS) | set(self._skills)

    # ----------------------------------------
    # Системный промпт + история из журнала
    # ----------------------------------------
    def _messages(self) -> list[dict[str, str]]:
        tools = "\n".join(f"- {t.signature} — {t.description}" for t in TOOLS.values())
        skills = "\n".join(f"- {s.signature} — {s.description}" for s in self._skills.values()) or "(пока нет)"
        system = SYSTEM_PROMPT.format(date=f"{datetime.now():%Y-%m-%d}", os=platform.system(),
                                      project=self.config.project_dir.name, tools=tools, skills=skills,
                                      memory=self.memory.read() or "(пусто)")
        return [{"role": "system", "content": system}, *self.journal.messages(self.config.history_limit)]

    # ----------------------------------------
    # Фоновый шаг после ответа: строгий фильтр фактов из слов пользователя
    # ----------------------------------------
    def _remember_in_background(self, task: str) -> None:
        if self.config.auto_memory:
            threading.Thread(target=self._auto_memory, args=(task,), daemon=True).start()

    # ----------------------------------------
    # Отдельный вызов модели: только собственные слова пользователя, без выводов
    # ----------------------------------------
    def _auto_memory(self, task: str) -> None:
        messages = [{"role": "system", "content": MEMORY_FILTER_PROMPT},
                    {"role": "user", "content": f"Уже в памяти:\n{self.memory.read() or '(пусто)'}\n\n"
                                                f"Сообщение пользователя:\n{task}"}]
        try:
            facts = self.model.ask(messages, Facts).facts
        except Exception:
            return
        for fact in facts[:3]:
            self.memory.add(fact)
