"""Журнал сессии JSONL: каждая запись — строка; он же история чата для модели."""

from __future__ import annotations

import base64
import json
import mimetypes
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import LOGS_DIR

IMAGES_IN_HISTORY = 2  # картинки только из стольких последних сообщений — каждая стоит сотни токенов


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
        self._broken_tail = False
        if path.exists():
            lines = [(number, line) for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1) if line.strip()]
            for index, (number, line) in enumerate(lines):
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError("запись не является JSON-объектом")
                except (json.JSONDecodeError, ValueError) as error:
                    if index == len(lines) - 1:
                        # После аварийного выхода последняя JSONL-строка может остаться недописанной.
                        self._broken_tail = True
                        break
                    raise ValueError(f"Повреждён журнал {path}, строка {number}: {error}") from error
                self.records.append(record)

    # ----------------------------------------
    # Новая сессия: logs/<дата>_<id>.jsonl (файл появится при первой записи)
    # ----------------------------------------
    @classmethod
    def new(cls) -> Journal:
        now = datetime.now()
        return cls(LOGS_DIR / f"{now:%Y-%m-%d_%H%M%S}{secrets.token_hex(2)}.jsonl")

    # ----------------------------------------
    # Последний по времени журнал, кроме указанного
    # ----------------------------------------
    @classmethod
    def latest(cls, exclude: Path) -> Journal | None:
        files: list[tuple[float, Path]] = []
        for path in LOGS_DIR.glob("*.jsonl"):
            try:
                stat = path.stat()
            except OSError:
                continue
            if path != exclude and stat.st_size > 0:
                files.append((stat.st_mtime, path))
        return cls(max(files, key=lambda item: item[0])[1]) if files else None

    # ----------------------------------------
    # Дописывает запись сразу на диск
    # ----------------------------------------
    def write(self, record: dict[str, Any]) -> None:
        if self._broken_tail:
            # Перед новой записью убираем лишь невалидный хвост, иначе он станет повреждением в середине.
            self.path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in self.records),
                                 encoding="utf-8")
            self._broken_tail = False
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
    def messages(self, limit: int) -> list[dict[str, Any]]:
        records = self.records[-limit:]
        with_images = [i for i, record in enumerate(records) if record.get("images")][-IMAGES_IN_HISTORY:]
        messages = []
        for index, record in enumerate(records):
            message = record_to_message(record, images=index in with_images)
            if message:
                messages.append(message)
        return messages


# ----------------------------------------
# Одна запись журнала → одно сообщение для модели
# ----------------------------------------
def record_to_message(record: dict[str, Any], images: bool = False) -> dict[str, Any] | None:
    kind = record["type"]
    if kind == "user" and record.get("images"):
        if not images:
            return {"role": "user", "content": f"{record['text']}\n[картинок было: {len(record['images'])}]"}
        return {"role": "user", "content": [{"type": "text", "text": record["text"]},
                                            *(image_part(Path(path)) for path in record["images"])]}
    if kind == "user":
        return {"role": "user", "content": record["text"]}
    if kind == "call":
        action = {"kind": "tool", "name": record["name"], "args": record["args"]}
        return {"role": "assistant", "content": json.dumps({"action": action}, ensure_ascii=False)}
    if kind == "result":
        body = f"ОШИБКА: {record['error']}" if record.get("error") else record["result"]
        text = f"[результат {record['name']}]\n{body}"
        if not record.get("images"):
            return {"role": "user", "content": text}
        if not images:  # старый скриншот: модель помнит, что он был, но картинку уже не получает
            return {"role": "user", "content": f"{text}\n[снимок уже не показывается]"}
        return {"role": "user", "content": [{"type": "text", "text": text},
                                            *(image_part(Path(path)) for path in record["images"])]}
    if kind == "answer":
        action = {"kind": "answer", "text": record["text"]}
        return {"role": "assistant", "content": json.dumps({"action": action}, ensure_ascii=False)}
    if kind == "need_skill":
        action = {"kind": "need_skill", "name": record["name"], "what": record["what"], "libs": record["libs"]}
        return {"role": "assistant", "content": json.dumps({"action": action}, ensure_ascii=False)}
    if kind == "note":
        return {"role": "user", "content": f"[система] {record['text']}"}
    return None


# ----------------------------------------
# Картинка для модели: data URL в формате OpenAI; файла уже нет — текстом
# ----------------------------------------
def image_part(path: Path) -> dict[str, Any]:
    try:
        data = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return {"type": "text", "text": f"[картинка {path.name} удалена]"}
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}
