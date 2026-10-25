"""Быстрые команды из commands.toml: фраза или шаблон → скилл, без модели."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .config import COMMANDS_FILE


# ----------------------------------------
# Быстрая команда: шаблоны фраз → скилл
# ----------------------------------------
@dataclass
class Command:
    patterns: list[str]
    skill: str


# ----------------------------------------
# Читает commands.toml
# ----------------------------------------
def load_commands(path: Path = COMMANDS_FILE) -> list[Command]:
    if not path.exists():
        return []
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return [Command(item["patterns"], item["skill"]) for item in data.get("command", [])]


# ----------------------------------------
# Шаблон «/тесты {path}» → регулярка с именованными группами
# ----------------------------------------
def pattern_regex(pattern: str) -> re.Pattern[str]:
    parts = re.split(r"\{(\w+)\}", pattern.strip())
    regex = ""
    for index, part in enumerate(parts):
        regex += f"(?P<{part}>.+?)" if index % 2 else re.escape(part)
    return re.compile(regex, re.IGNORECASE)


# ----------------------------------------
# Ищет команду, которая целиком совпадает с текстом
# ----------------------------------------
def match_command(text: str, commands: list[Command]) -> tuple[str, dict[str, str]] | None:
    for command in commands:
        for pattern in command.patterns:
            match = pattern_regex(pattern).fullmatch(text.strip())
            if match:
                return command.skill, {key: value.strip() for key, value in match.groupdict().items()}
    return None
