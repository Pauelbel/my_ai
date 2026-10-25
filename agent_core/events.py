"""Контракт с интерфейсом: события луппа и состояние для строки статуса."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .tools import trim


# ----------------------------------------
# Событие для интерфейса; на confirm_request и need_skill UI отвечает agent.confirm(event.id, …)
# ----------------------------------------
@dataclass
class Event:
    type: str  # step | tool_result | token | thinking | answer | confirm_request | need_skill | error | status | info | warning
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    id: str = ""


# ----------------------------------------
# Состояние агента для строки статуса в UI
# ----------------------------------------
@dataclass
class Status:
    model: str
    state: str  # idle | working | waiting | done
    step: int
    max_steps: int
    context_used: int  # токены последнего запроса: промпт + ответ
    context_max: int  # окно модели из Ollama; 0 — пока неизвестно
    prompt_tokens: int  # вход последнего вызова модели
    completion_tokens: int  # выход последнего вызова модели
    tokens_per_sec: float
    session: str
    session_seconds: int
    project: str
    branch: str
    approved: list[str]  # инструменты, разрешённые на всю сессию


# ----------------------------------------
# Текущая git-ветка папки проекта по .git/HEAD, без вызова git
# ----------------------------------------
def git_branch(folder: Path) -> str:
    for parent in (folder, *folder.parents):
        git = parent / ".git"
        if git.is_file():
            git = (parent / git.read_text(encoding="utf-8").partition("gitdir:")[2].strip()).resolve()
        if (git / "HEAD").is_file():
            head = (git / "HEAD").read_text(encoding="utf-8").strip()
            return head.removeprefix("ref: refs/heads/") if head.startswith("ref:") else head[:8]
    return ""


# ----------------------------------------
# Ответ пользователя на подтверждение → yes | no | session
# ----------------------------------------
def parse_confirm(answer: str | None) -> str:
    text = (answer or "").strip().lower()
    if text in {"session", "always", "всегда", "сессия", "на сессию", "да на всю сессию", "с", "а"}:
        return "session"
    if text in {"yes", "y", "да", "д", "ок", "ok"}:
        return "yes"
    return "no"


# ----------------------------------------
# Короткий вид аргументов для показа пользователю
# ----------------------------------------
def show_args(args: dict[str, Any], limit: int = 300) -> str:
    return trim(json.dumps(args, ensure_ascii=False), limit)
