"""Конфиг агента и пути к его данным: config.toml, skills/, logs/, memory/, commands.toml."""

from __future__ import annotations

import json
import platform
import re
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent  # папка проекта: config.toml, skills/, logs/, memory/
SKILLS_DIR = ROOT / "skills"
LOGS_DIR = ROOT / "logs"
MEMORY_FILE = ROOT / "memory" / "memory.md"
COMMANDS_FILE = ROOT / "commands.toml"
BROWSER_PROFILE = ROOT / "browser_profile"  # куки и логины браузера живут между запусками
IS_WINDOWS = platform.system() == "Windows"


# ----------------------------------------
# Настройки агента; читаются из config.toml в папке проекта
# ----------------------------------------
@dataclass
class Config:
    model: str = "qwen3.5:9b-q8_0"
    base_url: str = "http://localhost:11434/v1"
    project_dir: Path = ROOT
    max_steps: int = 20
    skill_timeout: int = 120
    output_limit: int = 4000
    memory_limit: int = 4000
    history_limit: int = 40
    start_url: str = "https://www.google.com"
    headless: bool = False
    auto_memory: bool = True
    reasoning_effort: str = "none"
    skill_reasoning_effort: str = "medium"

    # ----------------------------------------
    # Читает toml; относительная папка проекта считается от папки агента; секции [..] — для интерфейсов
    # ----------------------------------------
    @classmethod
    def load(cls, path: Path = ROOT / "config.toml") -> Config:
        data = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        data = {key: value for key, value in data.items() if not isinstance(value, dict)}
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"Неизвестные ключи в {path.name}: {', '.join(sorted(unknown))}")
        if "project_dir" in data:
            data["project_dir"] = (ROOT / Path(data["project_dir"]).expanduser()).resolve()
        return cls(**data)

    # ----------------------------------------
    # Пишет ключи верхнего уровня в toml: строка «ключ = …» меняется, комментарий справа остаётся;
    # нового ключа нет — добавляется перед первой секцией [..]
    # ----------------------------------------
    @staticmethod
    def save(changes: dict[str, str | int | bool], path: Path = ROOT / "config.toml") -> None:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        head, sep, tail = text.partition("\n[")  # секции [ui], [voice] не трогаем
        for key, value in changes.items():
            literal = json.dumps(value, ensure_ascii=False) if isinstance(value, str) else str(value).lower()
            line = re.compile(rf"^{re.escape(key)}[ \t]*=[ \t]*(?:\"(?:[^\"\\\\]|\\\\.)*\"|[^#\n]*?)([ \t]*#.*)?$", re.M)
            if line.search(head):
                head = line.sub(lambda m: f"{key} = {literal}{m.group(1) or ''}", head, count=1)
            else:
                head = head.rstrip("\n") + f"\n{key} = {literal}\n" + ("\n" if sep else "")
        path.write_text(head + sep + tail, encoding="utf-8")
