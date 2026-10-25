"""Схемы ответа модели для Instructor: вызов инструмента, ответ, запрос нового скилла, код скилла."""

from __future__ import annotations

import ast
import re
import sys
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator

from .skills import parse_skill_md


NOT_AN_ANSWER = re.compile(
    r"^\W*(я |сейчас |теперь )?(открою|выполню|запущу|сделаю|прочитаю|создам|запишу|отправлю|напишу)\b|^\W*need_?s?_?skill\b|\"kind\"\s*:\s*\"need_skill\"|нет (такой |такого |подходящего |встроенного )?(возможност|инструмент|скилл|доступ)"
    r"|не име\w* (прямого |такой |никакого )?(доступа|возможност|инструмент)|не могу (выполнить|сделать|отправ|подключ)",
    re.IGNORECASE)


# ----------------------------------------
# Вызов инструмента или скилла из списка
# ----------------------------------------
class CallTool(BaseModel):
    kind: Literal["tool"]
    name: str = Field(description="имя инструмента или скилла строго из списка")
    args: dict[str, Any] = Field(default_factory=dict)

    # ----------------------------------------
    # Имя не из списка — ошибка, которую Instructor вернёт модели
    # ----------------------------------------
    @field_validator("name")
    @classmethod
    def known_name(cls, value: str, info: ValidationInfo) -> str:
        names = (info.context or {}).get("names")
        if names is not None and value not in names:
            raise ValueError(
                f"инструмента «{value}» нет. Доступны: {', '.join(sorted(names))}. "
                "Если ни один не подходит — верни need_skill"
            )
        return value


# ----------------------------------------
# Дважды экранированный текст («\\n» в JSON): нет ни одного настоящего переноса, но есть буквальные «\n»
# ----------------------------------------
def unescape_text(text: str) -> str:
    if "\n" not in text and "\\n" in text:
        text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
    return text.rstrip().removesuffix("\\").rstrip()


# ----------------------------------------
# Финальный ответ пользователю
# ----------------------------------------
class Answer(BaseModel):
    kind: Literal["answer"]
    text: str

    # ----------------------------------------
    # Модель иногда экранирует переносы дважды — в тексте буквальные «\n» вместо строк; чиним молча
    # ----------------------------------------
    @field_validator("text")
    @classmethod
    def real_newlines(cls, value: str) -> str:
        return unescape_text(value)

    # ----------------------------------------
    # Обещание «сейчас сделаю» или отказ «нет инструмента» вместо действия — ошибка, которая уходит модели
    # ----------------------------------------
    @field_validator("text")
    @classmethod
    def is_result(cls, value: str) -> str:
        if NOT_AN_ANSWER.search(value):
            raise ValueError("answer — только итог сделанного. Нужно действие — верни tool; нет инструмента — верни "
                             '{"action": {"kind": "need_skill", "name": "...", "what": "...", "libs": [...]}}')
        return value


# ----------------------------------------
# Подходящего инструмента нет — нужен новый скилл
# ----------------------------------------
class NeedSkill(BaseModel):
    kind: Literal["need_skill"]
    name: str = Field(description="имя скилла в snake_case")
    what: str = Field(description="что скилл должен делать")
    libs: list[str] = Field(default_factory=list, description="pip-пакеты, которых нет в стандартной библиотеке")

    # ----------------------------------------
    # Имя скилла — только snake_case латиницей
    # ----------------------------------------
    @field_validator("name")
    @classmethod
    def snake_case(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", value):
            raise ValueError("имя скилла должно быть в snake_case латиницей, например pdf_to_md")
        return value

    # ----------------------------------------
    # Модули стандартной библиотеки ставить не нужно — убираем их из libs
    # ----------------------------------------
    @field_validator("libs")
    @classmethod
    def drop_stdlib(cls, value: list[str]) -> list[str]:
        return [lib for lib in value if lib.strip() and lib.strip().lower() not in sys.stdlib_module_names]


Action = CallTool | Answer | NeedSkill


# ----------------------------------------
# Обёртка: модель возвращает ровно одно действие (вариант выбирается по полю kind)
# ----------------------------------------
class Reply(BaseModel):
    action: Annotated[Action, Field(discriminator="kind")]


# ----------------------------------------
# Код нового скилла, который пишет модель
# ----------------------------------------
class SkillCode(BaseModel):
    skill_md: str = Field(description="содержимое SKILL.md")
    run_py: str = Field(description="содержимое run.py с функцией main(**args) -> str")

    # ----------------------------------------
    # В SKILL.md должны быть name (совпадает с запрошенным), description, args
    # ----------------------------------------
    @field_validator("skill_md")
    @classmethod
    def check_skill_md(cls, value: str, info: ValidationInfo) -> str:
        meta = parse_skill_md(value)
        missing = [key for key in ("name", "description", "args") if key not in meta]
        if missing:
            raise ValueError(f"в SKILL.md нет строк: {', '.join(missing)}")
        expected = (info.context or {}).get("skill")
        if expected and meta["name"] != expected:
            raise ValueError(f"name в SKILL.md должно быть {expected}")
        return value

    # ----------------------------------------
    # run.py должен компилироваться и содержать def main
    # ----------------------------------------
    @field_validator("run_py")
    @classmethod
    def check_run_py(cls, value: str) -> str:
        try:
            tree = ast.parse(value)
        except SyntaxError as error:
            raise ValueError(f"run.py не компилируется: {error}") from error
        if not any(isinstance(node, ast.FunctionDef) and node.name == "main" for node in tree.body):
            raise ValueError("в run.py нет функции main(**args) -> str на верхнем уровне")
        return value

    # ----------------------------------------
    # pip-пакеты из строки libs в SKILL.md, без стандартной библиотеки
    # ----------------------------------------
    def libs(self) -> list[str]:
        names = re.split(r"[,\s]+", parse_skill_md(self.skill_md).get("libs", ""))
        return [name for name in names if name and name.lower() not in sys.stdlib_module_names]


# ----------------------------------------
# Факты для долговременной памяти из слов пользователя
# ----------------------------------------
class Facts(BaseModel):
    facts: list[str] = Field(default_factory=list)
