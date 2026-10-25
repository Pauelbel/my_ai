"""Скиллы: папки skills/<имя>/ с SKILL.md и run.py; загрузка, запуск в отдельном процессе, сохранение."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import Config, SKILLS_DIR
from .tools.base import venv_env

if TYPE_CHECKING:
    from .schemas import SkillCode


# Запускается в отдельном процессе: грузит run.py, вызывает main(**args), печатает результат
SKILL_RUNNER = (
    "import importlib.util, json, sys\n"
    "spec = importlib.util.spec_from_file_location('skill', sys.argv[1])\n"
    "module = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(module)\n"
    "result = module.main(**json.loads(sys.stdin.read() or '{}'))\n"
    "print('' if result is None else result)\n"
)


# ----------------------------------------
# Скилл: папка skills/<имя>/ с SKILL.md и run.py
# ----------------------------------------
@dataclass
class SkillInfo:
    name: str  # имя для модели (git_status)
    description: str
    args: str
    folder: Path
    title: str = ""  # название для человека в интерфейсе («Статус git»); нет — показывается name

    # ----------------------------------------
    # Строка для модели в том же виде, что у инструментов
    # ----------------------------------------
    @property
    def signature(self) -> str:
        return f"{self.name}({self.args})"


# ----------------------------------------
# Строки «ключ: значение» в начале SKILL.md (name, title, description, args, libs)
# ----------------------------------------
def parse_skill_md(text: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^(name|title|description|args|libs):\s*(.*)$", line.strip())
        if match:
            meta.setdefault(match.group(1), match.group(2).strip())
    return meta


# ----------------------------------------
# Сканирует папку skills/ при старте и после создания скилла
# ----------------------------------------
def load_skills(folder: Path = SKILLS_DIR) -> dict[str, SkillInfo]:
    skills: dict[str, SkillInfo] = {}
    for skill_md in sorted(folder.glob("*/SKILL.md")):
        meta = parse_skill_md(skill_md.read_text(encoding="utf-8"))
        name = meta.get("name") or skill_md.parent.name
        if (skill_md.parent / "run.py").exists():
            skills[name] = SkillInfo(name, meta.get("description", ""), meta.get("args", ""), skill_md.parent,
                                     meta.get("title") or name)
    return skills


# ----------------------------------------
# Секреты скилла из skills/<имя>/.env (KEY=VALUE)
# ----------------------------------------
def read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip("\"'")
    return env


# ----------------------------------------
# Запуск скилла тем же Python из venv в отдельном процессе
# ----------------------------------------
def run_skill(skill: SkillInfo, args: dict[str, Any], config: Config) -> str:
    env = {**venv_env(), **read_env(skill.folder / ".env"),
           "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
           "AGENT_PROJECT": str(config.project_dir), "AGENT_START_URL": config.start_url}
    done = subprocess.run([sys.executable, "-c", SKILL_RUNNER, str(skill.folder / "run.py")],
                          input=json.dumps(args, ensure_ascii=False), cwd=config.project_dir, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=config.skill_timeout)
    if done.returncode != 0:
        stderr = done.stderr.strip()
        reason = stderr.splitlines()[-1] if stderr else f"код {done.returncode}"
        raise RuntimeError(f"скилл {skill.name} упал: {reason}\n{stderr[-2000:]}")
    return (done.stdout.strip() + ("\n" + done.stderr.strip() if done.stderr.strip() else "")) or "(нет вывода)"


# ----------------------------------------
# Сохраняет код скилла; прежний run.py уходит в run.py.bak
# ----------------------------------------
def save_skill(name: str, code: SkillCode) -> Path:
    folder = SKILLS_DIR / name
    folder.mkdir(parents=True, exist_ok=True)
    if (folder / "run.py").exists():
        shutil.copy2(folder / "run.py", folder / "run.py.bak")
    (folder / "SKILL.md").write_text(code.skill_md.strip() + "\n", encoding="utf-8")
    (folder / "run.py").write_text(code.run_py.rstrip() + "\n", encoding="utf-8")
    return folder


# ----------------------------------------
# Ставит пакеты в общий venv (тот же Python, что запустил агента)
# ----------------------------------------
def pip_install(libs: list[str]) -> str:
    done = subprocess.run([sys.executable, "-m", "pip", "install", *libs], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=600)
    lines = [line for line in (done.stdout + done.stderr).splitlines() if line.strip() and "[notice]" not in line]
    tail = "\n".join(lines[-5:])
    if done.returncode != 0:
        raise RuntimeError(f"pip install не удался:\n{tail}")
    return tail
