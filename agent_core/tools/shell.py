"""Команды оболочки: PowerShell на Windows, bash на Linux."""

from __future__ import annotations

import subprocess

from ..config import IS_WINDOWS
from .base import Workspace, always, tool, venv_env


# ----------------------------------------
# Команда оболочки: PowerShell на Windows, bash на Linux
# ----------------------------------------
@tool("Команда", "Выполняет команду в оболочке (PowerShell на Windows, bash на Linux) в папке проекта, "
      "возвращает вывод", confirm=always)
def run(ws: Workspace, cmd: str, timeout: int = 120) -> str:
    utf8 = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    shell = ["powershell", "-NoProfile", "-Command", utf8 + cmd] if IS_WINDOWS else ["bash", "-c", cmd]
    done = subprocess.run(shell, cwd=ws.config.project_dir, env=venv_env(), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    output = (done.stdout + done.stderr).strip() or "(нет вывода)"
    if done.returncode != 0:
        raise RuntimeError(f"код выхода {done.returncode}\n{output}")
    return output
