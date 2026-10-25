"""Журнал сессии JSONL: каждая запись — строка; он же история чата для модели."""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import LOGS_DIR


# ----------------------------------------
# Журнал сессии: каждая запись — строка JSON; он же история чата
# ----------------------------------------
class Journal:

    # ----------------------------------------
    # Открывает существующий журнал или готовит новый файл
    # ----------------------------------------
    def __init__(self, path: Path) -> None:
        self.path = path
        self.records: list[dict[str, Any]] = []
        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines()
            self.records = [json.loads(line) for line in lines if line.strip()]

    # ----------------------------------------
    # Новая сессия: logs/<дата>_<id>.jsonl (файл появится при первой записи)
    # ----------------------------------------
    @classmethod
    def new(cls) -> Journal:
        return cls(LOGS_DIR / f"{datetime.now():%Y-%m-%d}_{datetime.now():%H%M%S}{secrets.token_hex(2)}.jsonl")

    # ----------------------------------------
    # Последний по времени журнал, кроме указанного
    # ----------------------------------------
    @classmethod
    def latest(cls, exclude: Path) -> Journal | None:
        files = [p for p in LOGS_DIR.glob("*.jsonl") if p != exclude and p.stat().st_size > 0]
        return cls(max(files, key=lambda p: p.stat().st_mtime)) if files else None

    # ----------------------------------------
    # Дописывает запись сразу на диск
    # ----------------------------------------
    def write(self, record: dict[str, Any]) -> None:
        record = {"time": datetime.now().isoformat(timespec="seconds"), **record}
        self.records.append(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ----------------------------------------
    # Записи текущей задачи: всё после последнего сообщения пользователя
    # ----------------------------------------
    def current_task(self) -> tuple[str, list[dict[str, Any]]]:
        for index in range(len(self.records) - 1, -1, -1):
            if self.records[index]["type"] == "user":
                return self.records[index]["text"], self.records[index + 1:]
        return "", []

    # ----------------------------------------
    # История для модели из последних записей журнала
    # ----------------------------------------
    def messages(self, limit: int) -> list[dict[str, str]]:
        messages = []
        for record in self.records[-limit:]:
            message = record_to_message(record)
            if message:
                messages.append(message)
        return messages


# ----------------------------------------
# Одна запись журнала → одно сообщение для модели
# ----------------------------------------
def record_to_message(record: dict[str, Any]) -> dict[str, str] | None:
    kind = record["type"]
    if kind == "user":
        return {"role": "user", "content": record["text"]}
    if kind == "call":
        action = {"kind": "tool", "name": record["name"], "args": record["args"]}
        return {"role": "assistant", "content": json.dumps({"action": action}, ensure_ascii=False)}
    if kind == "result":
        body = f"ОШИБКА: {record['error']}" if record.get("error") else record["result"]
        return {"role": "user", "content": f"[результат {record['name']}]\n{body}"}
    if kind == "answer":
        action = {"kind": "answer", "text": record["text"]}
        return {"role": "assistant", "content": json.dumps({"action": action}, ensure_ascii=False)}
    if kind == "need_skill":
        action = {"kind": "need_skill", "name": record["name"], "what": record["what"], "libs": record["libs"]}
        return {"role": "assistant", "content": json.dumps({"action": action}, ensure_ascii=False)}
    if kind == "note":
        return {"role": "user", "content": f"[система] {record['text']}"}
    return None
