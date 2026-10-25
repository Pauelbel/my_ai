"""Глобальная позиция курсора — чтобы глаза виджета следили за мышкой по всему экрану.

Windows — GetCursorPos; KDE на Wayland — скрипт KWin шлёт позицию по D-Bus (обычным приложениям Wayland её
не отдаёт); X11 — XQueryPointer. Ничего не вышло — глаза следят за мышкой только над самим виджетом.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

POLL_S = 0.05  # опрос курсора на Windows и X11 — 20 раз в секунду
DBUS_NAME = "org.myai.Cursor"
KWIN_PLUGIN = "myai_cursor"
KWIN_SCRIPT = Path(__file__).resolve().parent / "kwin_cursor.js"


# ----------------------------------------
# Следит за курсором в своём потоке и зовёт on_move(x, y) при каждом заметном сдвиге
# ----------------------------------------
class CursorTracker:

    # ----------------------------------------
    # on_move вызывается из фонового потока с координатами экрана
    # ----------------------------------------
    def __init__(self, on_move: Callable[[int, int], None]) -> None:
        self.on_move = on_move
        self.backend = ""
        self._stop = threading.Event()
        self._last = (-1, -1)

    # ----------------------------------------
    # Выбирает способ по системе и запускает поток; False — глобального курсора нет
    # ----------------------------------------
    def start(self) -> bool:
        if sys.platform == "win32":
            target, self.backend = self._poll_windows, "windows"
        elif os.environ.get("WAYLAND_DISPLAY") and "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", ""):
            target, self.backend = self._kwin, "kwin"
        elif os.environ.get("DISPLAY") and ctypes.util.find_library("X11"):
            target, self.backend = self._poll_x11, "x11"
        else:
            log.info("Глобальная позиция курсора недоступна — глаза следят за мышкой только над виджетом")
            return False
        threading.Thread(target=self._guard, args=(target,), name="cursor", daemon=True).start()
        return True

    # ----------------------------------------
    # Останавливает слежение; скрипт KWin выгружается
    # ----------------------------------------
    def stop(self) -> None:
        self._stop.set()
        if self.backend == "kwin":
            try:
                kwin_call("unloadScript", "s", (KWIN_PLUGIN,))
            except Exception:
                log.debug("Скрипт KWin не выгрузился", exc_info=True)

    # ----------------------------------------
    # Поток слежения: сбой — в лог, приложение работает дальше
    # ----------------------------------------
    def _guard(self, target: Callable[[], None]) -> None:
        try:
            target()
        except Exception as error:
            log.warning("Слежение за курсором (%s) не работает: %s", self.backend, error)

    # ----------------------------------------
    # Новая позиция → подписчику, если сдвиг заметный
    # ----------------------------------------
    def _moved(self, x: int, y: int) -> None:
        if abs(x - self._last[0]) + abs(y - self._last[1]) >= 2:
            self._last = (x, y)
            self.on_move(x, y)

    # ----------------------------------------
    # Windows: GetCursorPos 20 раз в секунду
    # ----------------------------------------
    def _poll_windows(self) -> None:
        point = (ctypes.c_long * 2)()
        while not self._stop.wait(POLL_S):
            if ctypes.windll.user32.GetCursorPos(point):
                self._moved(point[0], point[1])

    # ----------------------------------------
    # X11: XQueryPointer через своё соединение с дисплеем (под XWayland — только над окнами X11)
    # ----------------------------------------
    def _poll_x11(self) -> None:
        x11 = ctypes.cdll.LoadLibrary(ctypes.util.find_library("X11"))
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        display = x11.XOpenDisplay(None)
        if not display:
            raise RuntimeError("нет соединения с X11")
        root = x11.XDefaultRootWindow(display)
        window, child = ctypes.c_ulong(), ctypes.c_ulong()
        rx, ry, wx, wy, mask = (ctypes.c_int() for _ in range(5))
        x11.XQueryPointer.argtypes = [ctypes.c_void_p, ctypes.c_ulong] + [ctypes.c_void_p] * 7
        while not self._stop.wait(POLL_S):
            x11.XQueryPointer(display, root, ctypes.byref(window), ctypes.byref(child), ctypes.byref(rx),
                              ctypes.byref(ry), ctypes.byref(wx), ctypes.byref(wy), ctypes.byref(mask))
            self._moved(rx.value, ry.value)

    # ----------------------------------------
    # KDE Wayland: свой D-Bus сервис, скрипт KWin зовёт его Move(x, y) при движении мыши
    # ----------------------------------------
    def _kwin(self) -> None:
        from jeepney import HeaderFields, MessageType, new_method_return
        from jeepney.bus_messages import message_bus
        from jeepney.io.blocking import open_dbus_connection

        with open_dbus_connection(bus="SESSION") as connection:
            reply = connection.send_and_get_reply(message_bus.RequestName(DBUS_NAME, 4))  # 4 — сразу, без очереди
            if reply.body[0] not in (1, 4):  # 1 — имя наше, 4 — уже наше
                raise RuntimeError(f"имя {DBUS_NAME} на D-Bus занято")
            kwin_call("unloadScript", "s", (KWIN_PLUGIN,))  # остался от прошлого запуска — убираем
            kwin_call("loadScript", "ss", (str(KWIN_SCRIPT), KWIN_PLUGIN))
            kwin_call("start", "", ())
            log.info("Курсор: скрипт KWin загружен")
            while not self._stop.is_set():
                try:
                    message = connection.receive(timeout=1)
                except TimeoutError:
                    continue
                if message.header.message_type != MessageType.method_call:
                    continue
                if message.header.fields.get(HeaderFields.member) == "Move" and len(message.body) >= 2:
                    self._moved(int(message.body[0]), int(message.body[1]))
                connection.send(new_method_return(message))


# ----------------------------------------
# Вызов метода /Scripting у KWin; ответ — тело ответа
# ----------------------------------------
def kwin_call(method: str, signature: str, body: tuple) -> tuple:
    from jeepney import DBusAddress, new_method_call
    from jeepney.io.blocking import open_dbus_connection

    address = DBusAddress("/Scripting", bus_name="org.kde.KWin", interface="org.kde.kwin.Scripting")
    with open_dbus_connection(bus="SESSION") as connection:
        return connection.send_and_get_reply(new_method_call(address, method, signature, body), timeout=5).body
