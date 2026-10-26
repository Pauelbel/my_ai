"""Git для боковой колонки: изменённые файлы пространства и строки сравнения «HEAD | сейчас»."""

from __future__ import annotations

import difflib
import subprocess
from pathlib import Path
from typing import Any


MAX_ROWS = 5000  # больше строк в сравнении — обрезка, чтобы окно не зависло


# ----------------------------------------
# Одна команда git в папке root; ошибка или нет git — None
# ----------------------------------------
def git(root: Path, *args: str) -> str | None:
    try:
        done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


# ----------------------------------------
# Корень репозитория, в котором лежит root; не репозиторий — None
# ----------------------------------------
def repo_top(root: Path) -> Path | None:
    top = git(root, "rev-parse", "--show-toplevel")
    return Path(top.strip()).resolve() if top and top.strip() else None


# ----------------------------------------
# Буква статуса из пары XY porcelain: U — новый, M/A/D/R
# ----------------------------------------
def status_letter(xy: str) -> str:
    if xy == "??":
        return "U"
    letter = xy.strip()[:1]
    return {"A": "A", "C": "A", "D": "D", "R": "R"}.get(letter, "M")


# ----------------------------------------
# Изменённые файлы внутри root; пути от root
# ----------------------------------------
def changes(root: Path) -> dict[str, Any]:
    root = root.resolve()
    top = repo_top(root)
    out = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", ".") if top else None
    if out is None:
        return {"repo": False, "items": []}
    entries = out.split("\0")
    items = []
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        xy, path, orig = entry[:2], entry[3:], ""
        if xy[0] in "RC":  # у переименования следующий элемент — старый путь
            orig, i = entries[i], i + 1
        try:
            path = (top / path).relative_to(root).as_posix()
        except ValueError:  # вне пространства
            continue
        try:
            orig = (top / orig).relative_to(root).as_posix() if orig else ""
        except ValueError:  # переехал из-за пределов пространства — сравнение с пустым
            orig = ""
        items.append({"path": path, "status": status_letter(xy), "orig": orig})
    items.sort(key=lambda item: item["path"])
    return {"repo": True, "items": items}


# ----------------------------------------
# Строки сравнения: [№ слева, текст слева, № справа, текст справа, вид same|del|add|change]
# ----------------------------------------
def diff_rows(root: Path, path: str, orig: str = "") -> dict[str, Any]:
    root = root.resolve()
    top = repo_top(root)
    if top is None:
        return {"ok": False, "error": "не git-репозиторий"}
    head_path = (root / (orig or path)).resolve().relative_to(top).as_posix()
    before = git(top, "show", f"HEAD:{head_path}") or ""
    full = root / path
    try:
        after = full.read_text(encoding="utf-8", errors="replace") if full.is_file() else ""
    except OSError as error:
        return {"ok": False, "error": str(error)}
    if "\0" in before or "\0" in after:
        return {"ok": False, "error": "двоичный файл"}
    a, b = before.splitlines(), after.splitlines()
    rows: list[list[Any]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            rows += [[i + 1, a[i], j + 1, b[j], "same"] for i, j in zip(range(i1, i2), range(j1, j2))]
            continue
        pairs = min(i2 - i1, j2 - j1) if tag == "replace" else 0
        rows += [[i1 + k + 1, a[i1 + k], j1 + k + 1, b[j1 + k], "change"] for k in range(pairs)]
        rows += [[i + 1, a[i], None, "", "del"] for i in range(i1 + pairs, i2)]
        rows += [[None, "", j + 1, b[j], "add"] for j in range(j1 + pairs, j2)]
    if len(rows) > MAX_ROWS:
        rows = rows[:MAX_ROWS] + [[None, "…обрезано", None, "", "same"]]
    if a and b and before.endswith("\n") != after.endswith("\n"):  # splitlines не видит разницу в конце файла
        note = "⊘ нет перевода строки в конце файла"
        rows.append([None, "" if before.endswith("\n") else note, None, "" if after.endswith("\n") else note, "eol"])
    return {"ok": True, "rows": rows}
