"""Клиент локальной модели (Ollama) через Instructor: проверка схемы, повторы, потоковый черновик ответа."""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from typing import Any, Callable, TypeVar

import instructor
from openai import OpenAI
from openai.types import CompletionUsage
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from pydantic import BaseModel
from pydantic_core import from_json

from .config import Config
from .schemas import unescape_text


# Ошибки попыток Instructor и так приходят событием error; в stderr они лишние
logging.getLogger("instructor").setLevel(logging.CRITICAL)


# ----------------------------------------
# Текст черновика ответа из недописанного JSON, пока модель его пишет; не answer — пусто
# ----------------------------------------
def draft_text(raw: str) -> str:
    start = raw.find("{")
    try:
        data = from_json(raw[start:], allow_partial="trailing-strings") if start >= 0 else None
    except ValueError:
        return ""
    action = data.get("action") if isinstance(data, dict) else None
    if isinstance(action, dict) and action.get("kind") == "answer":
        return unescape_text(str(action.get("text") or ""))
    return ""


T = TypeVar("T", bound=BaseModel)


# ----------------------------------------
# Клиент локальной модели через Instructor в режиме JSON
# ----------------------------------------
class Model:

    # ----------------------------------------
    # OpenAI-совместимый адрес Ollama, ключ не нужен
    # ----------------------------------------
    def __init__(self, config: Config) -> None:
        self.config = config
        self.openai = OpenAI(base_url=config.base_url, api_key="ollama", timeout=600)
        self.client = instructor.from_openai(self.openai, mode=instructor.Mode.JSON)
        self.usage: CompletionUsage | None = None  # токены последнего потокового вызова
        self.seconds = 0.0  # время генерации последнего потокового вызова
        self._windows: dict[str, int] = {}

    # ----------------------------------------
    # Один вызов модели со схемой ответа; ошибки валидации уходят модели (2 повтора)
    # ----------------------------------------
    def ask(self, messages: list[dict[str, str]], schema: type[T], context: dict[str, Any] | None = None,
            effort: str | None = None) -> T:
        effort = effort or self.config.reasoning_effort
        return self.client.chat.completions.create(model=self.config.model, messages=messages,
                                                   response_model=schema, max_retries=2, context=context or {},
                                                   extra_body={"reasoning_effort": effort})

    # ----------------------------------------
    # Тот же вызов, но текст идёт в on_piece по мере генерации; проверка и повторы — как обычно, в Instructor
    # ----------------------------------------
    def stream(self, messages: list[dict[str, str]], schema: type[T], context: dict[str, Any],
               on_piece: Callable[[str, str], None]) -> T:
        create = instructor.patch(create=lambda **kwargs: self._collect(on_piece, kwargs), mode=instructor.Mode.JSON)
        return create(model=self.config.model, messages=messages, response_model=schema, max_retries=2,
                      context=context, extra_body={"reasoning_effort": self.config.reasoning_effort})

    # ----------------------------------------
    # Одна попытка: читает поток Ollama и собирает обычный ChatCompletion для Instructor
    # ----------------------------------------
    def _collect(self, on_piece: Callable[[str, str], None], kwargs: dict[str, Any]) -> ChatCompletion:
        on_piece("attempt", "")
        parts: list[str] = []
        usage, finish, first = None, "stop", 0.0
        request = {**kwargs, "stream": True, "stream_options": {"include_usage": True}}
        with self.openai.chat.completions.create(**request) as chunks:
            for chunk in chunks:
                usage = chunk.usage or usage
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                finish = chunk.choices[0].finish_reason or finish
                first = first or time.monotonic()
                if getattr(delta, "reasoning", None):
                    on_piece("thinking", delta.reasoning)
                if delta.content:
                    parts.append(delta.content)
                    on_piece("text", delta.content)
        self.usage = usage
        self.seconds = time.monotonic() - first if first else 0.0
        message = ChatCompletionMessage(role="assistant", content="".join(parts))
        return ChatCompletion(id="stream", object="chat.completion", created=int(time.time()), model=kwargs["model"],
                              choices=[Choice(index=0, finish_reason=finish, message=message)], usage=usage)

    # ----------------------------------------
    # Окно контекста загруженной модели из /api/ps Ollama; 0 — неизвестно
    # ----------------------------------------
    def context_window(self) -> int:
        if self.config.model in self._windows:
            return self._windows[self.config.model]
        url = self.config.base_url.rstrip("/").removesuffix("/v1") + "/api/ps"
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                models = json.load(response).get("models", [])
        except (OSError, ValueError):
            return 0
        for item in models:
            if item.get("name") == self.config.model and item.get("context_length"):
                self._windows[self.config.model] = int(item["context_length"])
                return self._windows[self.config.model]
        return 0
