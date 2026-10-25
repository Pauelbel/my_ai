"""Десктопный интерфейс агента: мини-виджет с голосом и окно чата (pywebview).

Запуск — через main.py в папке проекта: python main.py [--chat | --shortcut]
Связывает окна (ui/), голос (voice/) и ядро (agent_core/); ядро и голос об интерфейсе не знают.
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import os
import signal
import sys
import threading
import tomllib
from pathlib import Path
from typing import Any, Callable

from agent_core.config import ROOT, Config

from .bridge import UI_DIR, ChatApi, UiState, WidgetApi, WidgetPort
from .cursor import CursorTracker

log = logging.getLogger("ui")

WIDGET_SIZE = 140
MENU_TUCK = 26  # на столько меню заходит на угол головы — выглядит прикреплённым к ней
MENU_ROOM = (310, 200)  # запас под меню вокруг головы на Qt (меню с тенью меньше этого)


# ----------------------------------------
# Секция [ui] или [voice] из общего config.toml; ядро такие секции пропускает
# ----------------------------------------
def load_section(name: str, path: Path = ROOT / "config.toml") -> dict[str, Any]:
    data = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    section = data.get(name, {})
    return section if isinstance(section, dict) else {}


# ----------------------------------------
# Приложение: два окна, голос, мост; один процесс, без веб-сервера
# ----------------------------------------
class App:

    # ----------------------------------------
    # Конфиг ядра, секции [ui] и [voice], сохранённое состояние окна
    # ----------------------------------------
    def __init__(self, show_chat: bool) -> None:
        self.core = Config.load()
        self.ui = load_section("ui")
        self.state = UiState()
        self.show_chat = show_chat
        self.chat_visible = show_chat
        self.chat_api = ChatApi(self.core, self._folders(), self.state)
        self.voice: Any = None
        try:
            from voice.assistant import VoiceAssistant

            self.voice = VoiceAssistant(load_section("voice"), self.core.base_url, self.core.model)
        except ImportError as error:
            log.warning("Голос недоступен: %s", error)
        self.widget: Any = None
        self.chat: Any = None
        self.port: WidgetPort | None = None
        self._drag_from = (0, 0)
        self._drag_to = (0, 0)
        self.cursor: CursorTracker | None = None
        self._ring_offset = (0, 0)  # где голова внутри окна виджета (сдвигается, пока открыто меню)
        self._head_pos = (0, 0)  # где голова стоит на экране, пока открыто меню
        self.mask: Any = None  # Qt: окно с запасом под меню и маской — меню открывается без resize/move
        self.masked = False  # окно виджета на Qt и с маской
        self._quitting = False

    # ----------------------------------------
    # Пространства при старте: папка проекта ядра, [ui] spaces, открытые в прошлый раз
    # ----------------------------------------
    def _folders(self) -> list[Path]:
        folders = [self.core.project_dir]
        for item in [*self.ui.get("spaces", []), *self.state.get("spaces", [])]:
            folder = (ROOT / Path(item).expanduser()).resolve()
            if folder.is_dir() and folder not in folders:
                folders.append(folder)
        return folders

    # ----------------------------------------
    # Создаёт окна и запускает цикл GUI (возвращается, когда окна закрыты)
    # ----------------------------------------
    def run(self, debug: bool = False) -> None:
        gui = pick_gui()
        import webview

        size = int(self.ui.get("widget_size", WIDGET_SIZE))
        x, y = self.state.get("widget_pos") or default_position(webview, size)  # где голова на экране
        width = height = size
        self.masked = gui == "qt"
        if self.masked:  # окно сразу с запасом под меню: голова в середине, вокруг — сквозная прозрачность
            self._ring_offset = (MENU_ROOM[0] - MENU_TUCK, MENU_ROOM[1] - MENU_TUCK)
            width, height = size + 2 * self._ring_offset[0], size + 2 * self._ring_offset[1]
            x, y = x - self._ring_offset[0], y - self._ring_offset[1]
        self.widget = webview.create_window(
            "Агент", url=str(UI_DIR / "widget" / "index.html"), js_api=WidgetApi(self._widget_actions()),
            width=width, height=height, x=x, y=y, resizable=False, frameless=True, easy_drag=False, shadow=False,
            transparent=True, on_top=True, background_color="#000000", focus=False, hidden=self.show_chat)
        self.chat = webview.create_window(
            "Агент — чат", url=str(UI_DIR / "chat" / "index.html"), js_api=self.chat_api,
            width=int(self.ui.get("chat_width", 1100)), height=int(self.ui.get("chat_height", 750)),
            min_size=(640, 420), hidden=not self.show_chat, background_color="#0d1117", text_select=True)
        self.chat.events.closing += self._chat_closing
        self.chat.events.minimized += self._show_head  # чат свернули — голова снова на экране
        self.chat.events.restored += self._sync_head
        self.widget.events.closing += self._widget_closing
        if self.masked:
            self.widget.events.before_show += self._unmanage
            self.widget.events.shown += self._mask_head
        dictate = self.voice.dictate if self.voice else lambda on_partial: None
        self.chat_api._attach(self.chat, self._on_confirm, dictate, self._on_connection)
        port = WidgetPort(self.widget)
        if self.voice:
            self.voice.on_state = port.set
            threading.Thread(target=self._start_voice, name="voice-warmup", daemon=True).start()
        else:
            port.set(state="error", text="Голос недоступен: поставь зависимости из requirements.txt")
        self.port = port
        if self.ui.get("follow_mouse", True):
            self.cursor = CursorTracker(self._cursor_moved)
            self.cursor.start()
        icon = UI_DIR / ("icon.ico" if sys.platform == "win32" else "icon.png")  # WinForms понимает только .ico
        webview.start(gui=gui, debug=debug, private_mode=True, icon=str(icon))  # иконка в заголовке и на панели задач

    # ----------------------------------------
    # Курсор сдвинулся: глазам — где он относительно центра виджета
    # ----------------------------------------
    def _cursor_moved(self, x: int, y: int) -> None:
        if self.widget is None or self.widget.x is None:
            return
        half = int(self.ui.get("widget_size", WIDGET_SIZE)) // 2
        rx, ry = self._ring_offset  # при открытом меню голова сдвинута внутри окна
        self.port.set(look=[x - self.widget.x - rx - half, y - self.widget.y - ry - half])

    # ----------------------------------------
    # Фон: загрузка Whisper и, если включено, прослушивание имя агента
    # ----------------------------------------
    def _start_voice(self) -> None:
        voice = self.voice
        wake = self.state.get("wake", voice.settings.get("wake", False))
        if wake:
            voice.set_wake(True)
        voice.warmup()

    # ----------------------------------------
    # Действия виджета для WidgetApi
    # ----------------------------------------
    def _widget_actions(self) -> dict[str, Callable[..., Any]]:
        return {"toggle_record": self._toggle_record, "toggle_chat": self.toggle_chat, "open_chat": self.open_chat,
                "set_wake": self._set_wake, "send_last_to_chat": self._send_last, "quit": self.quit,
                "menu": self._menu, "drag": self._drag, "drag_end": self._drag_end, "ready": self._ready}

    # ----------------------------------------
    # Клик по кольцу
    # ----------------------------------------
    def _toggle_record(self) -> None:
        if self.voice:
            self.voice.toggle()

    # ----------------------------------------
    # Двойной клик: чат виден — спрятать, иначе показать
    # ----------------------------------------
    def toggle_chat(self) -> None:
        if self.chat_visible:
            self.chat.hide()
            self.chat_visible = False
            self._show_head()
        else:
            self.open_chat()

    # ----------------------------------------
    # Показывает окно чата; голова на это время прячется
    # ----------------------------------------
    def open_chat(self) -> None:
        self.chat.show()
        self.chat_visible = True
        self.widget.hide()
        self.chat.run_js("window.focusInput && window.focusInput()")

    # ----------------------------------------
    # Голова снова на экране (чат закрыт или свёрнут)
    # ----------------------------------------
    def _show_head(self) -> None:
        self.widget.show()

    # ----------------------------------------
    # Чат развернули из свёрнутого — голову снова прячем
    # ----------------------------------------
    def _sync_head(self) -> None:
        if self.chat_visible:
            self.widget.hide()

    # ----------------------------------------
    # «Слушать фон: вкл/выкл» — запоминается между запусками
    # ----------------------------------------
    def _set_wake(self, on: bool) -> dict[str, Any]:
        if not self.voice:
            return {"ok": False, "error": "голос недоступен"}
        result = self.voice.set_wake(on)
        if result.get("ok"):
            self.state.update(wake=on)
        return result

    # ----------------------------------------
    # Последний текст из голоса — сообщением в активную сессию чата
    # ----------------------------------------
    def _send_last(self) -> None:
        text = self.voice.last_text if self.voice else ""
        if text:
            self.open_chat()
            self.chat_api.send(text)

    # ----------------------------------------
    # Ждёт ли агент подтверждения: открыть чат и мигать виджетом жёлтым
    # ----------------------------------------
    def _on_confirm(self, waiting: bool) -> None:
        self.port.set(blink=waiting)
        if waiting and not self.chat_visible:
            self.open_chat()

    # ----------------------------------------
    # Правое меню не помещается в 140×140 — окно растёт на время меню
    # ----------------------------------------
    def _menu(self, opened: bool, width: int = 0, height: int = 0) -> dict[str, Any]:
        size = int(self.ui.get("widget_size", WIDGET_SIZE))
        if self.masked and self._mask_ready():
            return self._menu_masked(opened, width, height, size)
        if not opened:
            if self._ring_offset != (0, 0) or (self.widget.width, self.widget.height) != (size, size):
                self.widget.resize(size, size)
                self.widget.move(*self._head_pos)
            self._ring_offset = (0, 0)
            return {"ring": [0, 0]}
        if self._ring_offset == (0, 0):  # окно ещё размером с голову — запоминаем, где она стоит
            self._head_pos = (self.widget.x, self.widget.y)
        x, y = self._head_pos
        left, top, right, bottom = screen_bounds(x + size // 2, y + size // 2)
        # Меню справа сверху от головы; не помещается — слева и/или снизу
        dx = size - MENU_TUCK if x + size - MENU_TUCK + width <= right else MENU_TUCK - width
        dy = MENU_TUCK - height if y + MENU_TUCK - height >= top else size - MENU_TUCK
        min_x, min_y = min(0, dx), min(0, dy)
        self._ring_offset = (-min_x, -min_y)
        self.widget.resize(max(size, dx + width) - min_x, max(size, dy + height) - min_y)
        self.widget.move(x + min_x, y + min_y)
        return {"ring": [-min_x, -min_y], "menu": [dx - min_x, dy - min_y]}

    # ----------------------------------------
    # Qt: меню открывается сменой маски — окно не двигается и не меняет размер
    # ----------------------------------------
    def _menu_masked(self, opened: bool, width: int, height: int, size: int) -> dict[str, Any]:
        ox, oy = self._ring_offset
        if not opened:
            self._mask_head()
            return {"ring": [ox, oy]}
        width, height = min(width, MENU_ROOM[0]), min(height, MENU_ROOM[1])
        x, y = self.widget.x + ox, self.widget.y + oy  # голова на экране
        left, top, right, bottom = screen_bounds(x + size // 2, y + size // 2)
        # Меню справа сверху от головы; не помещается — слева и/или снизу
        dx = size - MENU_TUCK if x + size - MENU_TUCK + width <= right else MENU_TUCK - width
        dy = MENU_TUCK - height if y + MENU_TUCK - height >= top else size - MENU_TUCK
        menu = (ox + dx, oy + dy, width, height)
        self.mask.set([(ox, oy, size)], [menu])
        return {"ring": [ox, oy], "menu": [menu[0], menu[1]]}

    # ----------------------------------------
    # Маска «только голова»: всё вокруг пропускает клики к окнам под виджетом
    # ----------------------------------------
    def _mask_head(self) -> None:
        if self._mask_ready():
            ox, oy = self._ring_offset
            self.mask.set([(ox, oy, int(self.ui.get("widget_size", WIDGET_SIZE)))])

    # ----------------------------------------
    # Подключение сменили в настройках чата: чистка речи идёт туда же
    # ----------------------------------------
    def _on_connection(self, base_url: str, model: str) -> None:
        if self.voice:
            self.voice.base_url, self.voice.model = base_url, model

    # ----------------------------------------
    # Qt, до первого показа: окно виджета — неуправляемым, иначе KWin прижимает большое окно к краю экрана
    # ----------------------------------------
    def _unmanage(self) -> None:
        try:
            from .qt_mask import unmanage

            unmanage(self.widget)
        except Exception:
            log.warning("Окно виджета осталось управляемым — у края экрана его прижмёт", exc_info=True)

    # ----------------------------------------
    # Объект маски создаётся при первом обращении, когда окно Qt уже есть
    # ----------------------------------------
    def _mask_ready(self) -> bool:
        if self.mask is None:
            try:
                from .qt_mask import WindowMask

                self.mask = WindowMask(self.widget)
            except Exception:
                log.warning("Маска окна недоступна — меню будет открываться изменением окна", exc_info=True)
                self.mask = False
        return bool(self.mask)

    # ----------------------------------------
    # Перетаскивание виджета мышью
    # ----------------------------------------
    def _drag(self, dx: int, dy: int, start: bool) -> None:
        if start:
            self._drag_from = (self.widget.x, self.widget.y)
        self._drag_to = (self._drag_from[0] + dx, self._drag_from[1] + dy)
        self.widget.move(*self._drag_to)

    # ----------------------------------------
    # Позиция виджета запоминается в ui/state.json
    # ----------------------------------------
    def _drag_end(self) -> None:
        # widget.x обновляется не сразу после move; сохраняем, где голова, а не угол окна
        self.state.update(widget_pos=[self._drag_to[0] + self._ring_offset[0], self._drag_to[1] + self._ring_offset[1]])

    # ----------------------------------------
    # Начальное состояние для страницы виджета
    # ----------------------------------------
    def _ready(self) -> dict[str, Any]:
        state = self.port.snapshot()
        state["wake"] = bool(self.voice and self.voice.wake and self.voice.wake.active)
        state["ring"] = list(self._ring_offset)  # где голова внутри окна
        return state

    # ----------------------------------------
    # Закрытие чата только прячет его
    # ----------------------------------------
    def _chat_closing(self) -> bool:
        self.chat.hide()
        self.chat_visible = False
        self._show_head()
        return False

    # ----------------------------------------
    # Виджет закрыт (Alt+F4 и т.п.) — выходим целиком
    # ----------------------------------------
    def _widget_closing(self) -> None:
        threading.Thread(target=self.quit, daemon=True).start()

    # ----------------------------------------
    # «Выход»: триггер, агенты, процесс
    # ----------------------------------------
    def quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        if self.cursor:
            self.cursor.stop()
        if self.voice and self.voice.wake:
            self.voice.wake.stop()
        for session in self.chat_api._all_sessions():
            session.agent.stop()
            try:
                session.agent.close()
            except Exception:
                log.debug("Агент не закрылся", exc_info=True)
        logging.shutdown()
        # Разборка окон Qt WebEngine иногда падает на GL-контексте — всё сохранено, выходим сразу
        os._exit(0)


# ----------------------------------------
# Границы экрана, на котором точка (x, y): left, top, right, bottom
# ----------------------------------------
def screen_bounds(x: int, y: int) -> tuple[int, int, int, int]:
    import webview

    screens = list(webview.screens) or []
    for screen in screens:
        if screen.x <= x < screen.x + screen.width and screen.y <= y < screen.y + screen.height:
            return screen.x, screen.y, screen.x + screen.width, screen.y + screen.height
    if screens:
        s = screens[0]
        return s.x, s.y, s.x + s.width, s.y + s.height
    return 0, 0, 10 ** 5, 10 ** 5


# ----------------------------------------
# Первый запуск: виджет в правом нижнем углу главного экрана
# ----------------------------------------
def default_position(webview: Any, size: int) -> list[int | None]:
    try:
        screen = webview.screens[0]
        return [screen.x + screen.width - size - 40, screen.y + screen.height - size - 90]
    except Exception:
        return [None, None]


# ----------------------------------------
# Бэкенд окон: Windows — WebView2 (по умолчанию), Linux — Qt из pip, иначе системный GTK
# ----------------------------------------
def pick_gui() -> str | None:
    if not sys.platform.startswith("linux"):
        return None
    if importlib.util.find_spec("PyQt6") or importlib.util.find_spec("PySide6"):
        # Под Wayland окно нельзя двигать и держать поверх всех — Qt идёт через XWayland
        if os.environ.get("WAYLAND_DISPLAY"):
            os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
        return "qt"
    # WebKitGTK под Wayland (особенно с NVIDIA) падает на DMABUF-рендерере
    os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")
    return "gtk"


# ----------------------------------------
# Точка входа (её зовёт main.py): аргументы --chat, --debug, --shortcut
# ----------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Десктопный интерфейс агента")
    parser.add_argument("--chat", action="store_true", help="сразу открыть окно чата")
    parser.add_argument("--debug", action="store_true", help="инструменты разработчика в окнах")
    parser.add_argument("--shortcut", action="store_true", help="создать ярлык «Агент» в меню и выйти")
    args = parser.parse_args()
    if args.shortcut:
        from .shortcut import install

        print(f"Ярлык: {install()}")
        return
    (ROOT / "logs").mkdir(exist_ok=True)
    handlers: list[logging.Handler] = [logging.FileHandler(ROOT / "logs" / "app.log", encoding="utf-8")]
    if sys.stderr is not None:  # под pythonw stderr нет
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)
    app = App(args.chat)
    # Ctrl+C или kill — тоже штатный выход: иначе скрипт KWin и микрофон остаются занятыми
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: threading.Thread(target=app.quit, daemon=True).start())
    app.run(args.debug)
    logging.shutdown()
    # Нативные потоки Whisper/Vosk/PortAudio иногда держат интерпретатор при выходе — окна закрыты, выходим сразу
    os._exit(0)
