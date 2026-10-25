"""Скилл git_status: ветка, изменённые файлы, последние коммиты."""

import os
import shutil
import subprocess
from pathlib import Path


# ----------------------------------------
# Одна команда git в папке проекта; ошибка — исключением
# ----------------------------------------
def git(*args: str) -> str:
    project = Path(os.environ.get("AGENT_PROJECT", "."))
    done = subprocess.run(["git", *args], cwd=project, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or f"git {' '.join(args)}: код {done.returncode}")
    return done.stdout.strip()


# ----------------------------------------
# Точка входа скилла
# ----------------------------------------
def main() -> str:
    if shutil.which("git") is None:
        raise RuntimeError("git не найден в PATH")
    branch = git("branch", "--show-current") or "(detached HEAD)"
    status = git("status", "--short") or "изменений нет"
    try:
        log = git("log", "-5", "--pretty=format:%h %ad %s", "--date=short")
    except RuntimeError:
        log = "коммитов пока нет"
    return f"Ветка: {branch}\n\nИзменения:\n{status}\n\nПоследние коммиты:\n{log}"
