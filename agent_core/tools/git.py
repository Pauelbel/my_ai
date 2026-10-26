"""Git: коммит изменений в папке проекта."""

from __future__ import annotations

import shlex
import shutil
import subprocess

from .base import Workspace, always, tool


# ----------------------------------------
# Одна команда git в папке проекта; ошибка — исключением
# ----------------------------------------
def git(ws: Workspace, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=ws.config.project_dir, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if done.returncode != 0:
        output = (done.stderr.strip() or done.stdout.strip()) or f"код {done.returncode}"
        raise RuntimeError(f"git {args[0]}: {output}")
    return done.stdout.strip()


# ----------------------------------------
# Коммит: files — пути через пробел; пусто — все изменения, включая новые и удалённые файлы
# ----------------------------------------
@tool("Коммит", "Делает git-коммит в папке проекта с сообщением message; files — пути через пробел, "
      "пусто — все изменения; вызывать, только когда пользователь просит закоммитить", confirm=always)
def git_commit(ws: Workspace, message: str, files: str = "") -> str:
    if shutil.which("git") is None:
        raise RuntimeError("git не найден в PATH")
    if not message.strip():
        raise ValueError("пустое сообщение коммита")
    paths = [ws.rel(ws.path(item)) for item in shlex.split(files)]
    git(ws, "add", "-A", "--", *(paths or ["."]))
    staged = git(ws, "diff", "--cached", "--stat")
    if not staged:
        raise RuntimeError("нечего коммитить: изменений нет")
    git(ws, "commit", "-m", message.strip())
    return f"{git(ws, 'log', '-1', '--pretty=format:%h %s')}\n\n{staged}"
