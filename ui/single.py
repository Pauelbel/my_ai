"""Одна Ася на пользователя: первый запуск слушает локальный порт, следующие просят его показать чат и выходят.

Порт и случайный токен — в logs/instance.json. Файл остался от упавшего запуска или порт занят чужим — ответа «ok»
нет, и запуск идёт как обычно (файл перезапишется).
"""

from __future__ import annotations

import json
import logging
import secrets
import socket
import threading
from pathlib import Path
from typing import Callable

from agent_core.config import ROOT

INSTANCE_FILE = ROOT / "logs" / "instance.json"
TIMEOUT = 1.0

log = logging.getLogger("ui.single")


# ----------------------------------------
# Ася уже работает? Тогда просит её показать чат и возвращает True
# ----------------------------------------
def show_running(path: Path = INSTANCE_FILE) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        with socket.create_connection(("127.0.0.1", int(data["port"])), timeout=TIMEOUT) as connection:
            connection.sendall(f"{data['token']} show\n".encode("ascii"))
            return connection.recv(16).startswith(b"ok")
    except (OSError, ValueError, KeyError, TypeError):
        return False


# ----------------------------------------
# Слушает просьбы следующих запусков; on_show() — показать чат (вызывается из своего потока)
# ----------------------------------------
def serve(on_show: Callable[[], None], path: Path = INSTANCE_FILE) -> None:
    token = secrets.token_hex(16)
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"port": server.getsockname()[1], "token": token}), encoding="utf-8")

    # ----------------------------------------
    # Цикл: своя строка «<токен> show» — ответ ok и показ чата; чужое — молча закрыть
    # ----------------------------------------
    def loop() -> None:
        while True:
            connection, _ = server.accept()
            with connection:
                connection.settimeout(TIMEOUT)
                try:
                    line = connection.recv(128).decode("ascii", "replace").strip()
                except OSError:
                    continue
                if line == f"{token} show":
                    connection.sendall(b"ok\n")
                    try:
                        on_show()
                    except Exception:
                        log.exception("Чат по второму запуску не показан")

    threading.Thread(target=loop, name="single-instance", daemon=True).start()
