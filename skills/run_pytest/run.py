"""Скилл run_pytest: прогон pytest и краткая сводка результата."""

import importlib.util
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path


# ----------------------------------------
# Путь как есть; строка с флагами (-m, -k) разбивается на аргументы
# ----------------------------------------
def pytest_args(path: str) -> list[str]:
    path = path.strip() or "."
    return shlex.split(path) if path.startswith("-") or " -" in path else [path]


# ----------------------------------------
# Последняя строка вида «2 failed, 5 passed in 0.31s»
# ----------------------------------------
def find_summary(lines: list[str]) -> str:
    for line in reversed(lines):
        if re.search(r"\b(passed|failed|error|errors|skipped|no tests ran)\b", line):
            return line.strip("= ").strip()
    return "сводка не найдена"


# ----------------------------------------
# Точка входа скилла
# ----------------------------------------
def main(path: str = ".") -> str:
    if importlib.util.find_spec("pytest") is None:
        raise RuntimeError("pytest не установлен в venv агента: pip install pytest")
    project = Path(os.environ.get("AGENT_PROJECT", "."))
    command = [sys.executable, "-m", "pytest", *pytest_args(path), "-q", "-rfE", "--tb=line", "--color=no"]
    done = subprocess.run(command, cwd=project, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=115)
    lines = (done.stdout + done.stderr).splitlines()
    if done.returncode not in (0, 1, 5):
        raise RuntimeError(f"pytest завершился с кодом {done.returncode}:\n" + "\n".join(lines[-30:]))
    failed = [line for line in lines if line.startswith(("FAILED ", "ERROR "))]
    report = [f"Итог: {find_summary(lines)}"]
    if failed:
        report.append("Упали:")
        report += [f"- {line.split(' ', 1)[1]}" for line in failed]
    return "\n".join(report)
