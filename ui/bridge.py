"""Мост Python ↔ JS: js_api для окон чата и виджета, события ядра — в окна через run_js.

С ядром мост говорит только через контракт: Agent(config), send, confirm, stop, status, skills, history.
"""

from __future__ import annotations

import base64
import difflib
import inspect
import json
import logging
import queue
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from agent_core.agent import Agent
from agent_core.config import ROOT, Config
from agent_core.events import Event

log = logging.getLogger(__name__)

UI_DIR = Path(__file__).resolve().parent
STATE_FILE = UI_DIR / "state.json"  # настройки окна: позиция виджета, пространства, шрифт, тема (не история)
TOKEN_INTERVAL = 1 / 20  # склеенные token уходят в окно не чаще 20 раз/с
WIDGET_INTERVAL = 1 / 30  # состояние и громкость — в виджет ~30 раз/с
IS_WINDOWS = sys.platform == "win32"
CONNECTION_KEYS = ("base_url", "model", "reasoning_effort", "skill_reasoning_effort")  # вкладка «Подключение»
EFFORTS = ("none", "low", "medium", "high")
HIDDEN_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache"}  # не в дереве
UI_KEYS = ("font_size", "theme", "sidebar", "settings_tab", "sidebar_width", "editor_width")  # настройки окна чата
MAX_OPEN_BYTES = 2_000_000  # больше — редактор не открывает
IMAGES_DIR = ROOT / "logs" / "images"  # картинки из чата: журнал сессии ссылается на них путями
IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
MAX_IMAGE_BYTES = 10_000_000


# ----------------------------------------
# Маленькое JSON-хранилище настроек интерфейса в ui/state.json
# ----------------------------------------
class UiState:

    # ----------------------------------------
    # Читает файл; битый или пустой — начинаем с чистого
    # ----------------------------------------
    def __init__(self, path: Path = STATE_FILE) -> None:
        self.path = path
        self._lock = threading.Lock()
        try:
            self.data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    # ----------------------------------------
    # Значение по ключу
    # ----------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    # ----------------------------------------
    # Обновляет ключи и сразу пишет файл
    # ----------------------------------------
    def update(self, **values: Any) -> None:
        with self._lock:
            self.data.update(values)
            try:
                self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError as error:
                log.warning("Не сохранил %s: %s", self.path.name, error)


# ----------------------------------------
# Доставка событий в окно из своего потока; token склеиваются, чтобы не душить WebView
# ----------------------------------------
class Emitter:

    # ----------------------------------------
    # window — окно pywebview; func — имя JS-функции, которая принимает событие
    # ----------------------------------------
    def __init__(self, window: Any, func: str = "onEvent") -> None:
        self.window = window
        self.func = func
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._tokens: dict[str, str] = {}
        self._next_flush = 0.0
        threading.Thread(target=self._run, name=f"emit-{func}", daemon=True).start()

    # ----------------------------------------
    # Событие в очередь; можно из любого потока
    # ----------------------------------------
    def put(self, event: dict[str, Any]) -> None:
        self._queue.put(event)

    # ----------------------------------------
    # Цикл доставки: обычные события сразу, token — пачкой раз в TOKEN_INTERVAL
    # ----------------------------------------
    def _run(self) -> None:
        while True:
            timeout = max(0.0, self._next_flush - time.monotonic()) if self._tokens else None
            try:
                event = self._queue.get(timeout=timeout)
            except queue.Empty:
                event = None
            if event is not None and event["type"] == "token" and not event["data"].get("reset"):
                self._tokens[event["session"]] = self._tokens.get(event["session"], "") + event["text"]
            elif event is not None:
                self._flush()
                self._send(event)
            if self._tokens and time.monotonic() >= self._next_flush:
                self._flush()

    # ----------------------------------------
    # Отправляет накопленные куски ответа по сессиям
    # ----------------------------------------
    def _flush(self) -> None:
        tokens, self._tokens = self._tokens, {}
        for session, text in tokens.items():
            self._send({"session": session, "type": "token", "text": text, "data": {}, "id": ""})
        self._next_flush = time.monotonic() + TOKEN_INTERVAL

    # ----------------------------------------
    # Вызов JS; только ASCII (\\uXXXX) — GTK-бэкенд обрезает скрипты с кириллицей
    # ----------------------------------------
    def _send(self, event: dict[str, Any]) -> None:
        script = f"window.{self.func} && window.{self.func}({json.dumps(event, default=str)})"
        try:
            self.window.run_js(script)
        except Exception as error:
            log.debug("Событие не доставлено в окно: %s", error)


# ----------------------------------------
# Последнее состояние виджета уходит в окно не чаще WIDGET_INTERVAL; промежуточные уровни выкидываются
# ----------------------------------------
class WidgetPort:

    # ----------------------------------------
    # Поток доставки стартует сразу; первое состояние — покой
    # ----------------------------------------
    def __init__(self, window: Any) -> None:
        self.window = window
        self._state: dict[str, Any] = {"state": "idle", "seq": 0, "level": 0.0}
        self._lock = threading.Lock()
        self._dirty = threading.Event()
        threading.Thread(target=self._run, name="emit-widget", daemon=True).start()

    # ----------------------------------------
    # Меняет поля состояния (state, level, text, wake, blink, look) и будит доставку.
    # Каждая смена state получает новый seq: виджет применяет её один раз, а не при каждой повторной посылке
    # ----------------------------------------
    def set(self, **values: Any) -> None:
        with self._lock:
            if "state" in values:
                values["seq"] = self._state["seq"] + 1
            self._state = {**self._state, **values}
        self._dirty.set()

    # ----------------------------------------
    # Текущее состояние — для первой загрузки страницы
    # ----------------------------------------
    def snapshot(self) -> dict[str, Any]:
        return dict(self._state)

    # ----------------------------------------
    # Доставка с ограничением частоты
    # ----------------------------------------
    def _run(self) -> None:
        while True:
            self._dirty.wait()
            self._dirty.clear()
            try:
                self.window.run_js(f"window.onState && window.onState({json.dumps(self._state)})")
            except Exception as error:
                log.debug("Состояние не доставлено в виджет: %s", error)
            time.sleep(WIDGET_INTERVAL)


# ----------------------------------------
# Дифф для подтверждения write_file / edit_file: что станет с файлом
# ----------------------------------------
def make_diff(project: Path, name: str, args: dict[str, Any]) -> str:
    if name not in ("write_file", "edit_file") or not isinstance(args.get("path"), str):
        return ""
    path = args["path"]
    full = (project / path).resolve()
    if not full.is_relative_to(project.resolve()):
        return ""
    try:
        before = full.read_text(encoding="utf-8") if full.is_file() else ""
    except (OSError, UnicodeDecodeError):
        return ""
    if name == "write_file":
        after = str(args.get("content", ""))
    else:
        old, new = str(args.get("old", "")), str(args.get("new", ""))
        if not old or old not in before:  # кусок не найден — ядро вернёт ошибку; показываем саму замену
            return "\n".join(difflib.unified_diff(old.splitlines(), new.splitlines(), f"a/{path}", f"b/{path}",
                                                  lineterm=""))
        after = before.replace(old, new, 1)
    lines = difflib.unified_diff(before.splitlines(), after.splitlines(),
                                 f"a/{path}" if full.exists() else "/dev/null", f"b/{path}", lineterm="")
    return "\n".join(lines)


# ----------------------------------------
# Путь от папки проекта (agent_core/tools/files.py), а если вне её — полный
# ----------------------------------------
def relative(path: Path) -> str:
    path = path.resolve()
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)


# ----------------------------------------
# Загрузка и видеопамять NVIDIA через nvidia-smi (кэш 2 с); без неё — пусто
# ----------------------------------------
class GpuLoad:

    # ----------------------------------------
    # Ищет nvidia-smi один раз
    # ----------------------------------------
    def __init__(self) -> None:
        self._nvidia = shutil.which("nvidia-smi")
        self._value = ""
        self._at = 0.0

    # ----------------------------------------
    # «GPU 35% · 6.1/12G» или пустая строка
    # ----------------------------------------
    def text(self) -> str:
        if not self._nvidia:
            return ""
        if time.monotonic() - self._at < 2:
            return self._value
        self._at = time.monotonic()
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WINDOWS else {}
        try:
            done = subprocess.run([self._nvidia, "--query-gpu=utilization.gpu,memory.used,memory.total",
                                   "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3, **flags)
            load, used, total = (float(v) for v in done.stdout.splitlines()[0].split(","))
            self._value = f"GPU {load:.0f}% · {used / 1024:.1f}/{total / 1024:.0f}G"
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
            self._value = ""
        return self._value


# ----------------------------------------
# Сессия: свой агент (свой журнал), фоновый поток для send(), события — в окно чата
# ----------------------------------------
class Session:

    # ----------------------------------------
    # Агент на папку пространства; заголовок вкладки — по первому сообщению
    # ----------------------------------------
    def __init__(self, config: Config, folder: Path) -> None:
        self.id = secrets.token_hex(3)
        self.agent = Agent(replace(config, project_dir=folder))
        self.title = "новая"
        self.thread: threading.Thread | None = None
        self.pending: set[str] = set()  # id событий, которые ждут confirm()

    # ----------------------------------------
    # Идёт ли сейчас send()
    # ----------------------------------------
    @property
    def busy(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    # ----------------------------------------
    # Для вкладок: id, заголовок, состояние агента
    # ----------------------------------------
    def info(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "state": self.agent.status().state}


# ----------------------------------------
# Пространство: папка проекта и её сессии-вкладки
# ----------------------------------------
class Space:

    # ----------------------------------------
    # Первая сессия создаётся сразу
    # ----------------------------------------
    def __init__(self, config: Config, folder: Path) -> None:
        self.path = folder
        self.config = config
        self.sessions: list[Session] = [Session(config, folder)]
        self.active = self.sessions[0].id

    # ----------------------------------------
    # Состояние для точки: working, если хоть одна сессия работает; done, если что-то сделано
    # ----------------------------------------
    @property
    def state(self) -> str:
        states = {session.agent.status().state for session in self.sessions}
        for state in ("waiting", "working", "done"):
            if state in states:
                return "working" if state == "waiting" else state
        return "idle"

    # ----------------------------------------
    # Для левой колонки
    # ----------------------------------------
    def info(self) -> dict[str, Any]:
        return {"path": str(self.path), "name": self.path.name or str(self.path), "state": self.state}


# ----------------------------------------
# js_api окна чата: window.pywebview.api.<метод>(...) в JS; поля с «_» pywebview не публикует
# ----------------------------------------
class ChatApi:

    # ----------------------------------------
    # config — базовый конфиг ядра; folders — пространства при старте; hooks — колбэки ui/app.py
    # ----------------------------------------
    def __init__(self, config: Config, folders: list[Path], state: UiState) -> None:
        self._config = config
        self._state = state
        self._spaces: dict[str, Space] = {}
        self._active = ""
        self._emitter: Emitter | None = None
        self._gpu = GpuLoad()
        self._commands: list[dict[str, str]] | None = None
        self._lock = threading.RLock()
        self._on_confirm: Callable[[bool], None] = lambda waiting: None
        self._dictate: Callable[[Callable[[str], None]], str | None] = lambda on_partial: None
        self._on_connection: Callable[[str, str], None] = lambda base_url, model: None
        self._window: Any = None
        self._trash: list[str] = [str(path) for path in state.get("trash_spaces", [])]  # убранные пространства
        for folder in folders:
            if str(folder) not in self._trash:
                self._add_space(folder)
        if not self._spaces:  # всё в корзине — первое возвращается: без пространства чату не с чем работать
            self._trash.remove(str(folders[0]))
            self._add_space(folders[0])
        saved = str(state.get("active_space", ""))
        self._active = saved if saved in self._spaces else next(iter(self._spaces))

    # ----------------------------------------
    # Привязка к окну и колбэкам ui/app.py (вызывается до webview.start)
    # ----------------------------------------
    def _attach(self, window: Any, on_confirm: Callable[[bool], None],
                dictate: Callable[[Callable[[str], None]], str | None],
                on_connection: Callable[[str, str], None] | None = None) -> None:
        self._window = window
        self._emitter = Emitter(window)
        self._on_confirm = on_confirm
        self._dictate = dictate
        if on_connection:
            self._on_connection = on_connection

    # ======== контракт ТЗ ========

    # ----------------------------------------
    # Отправляет текст в активную сессию; agent.send идёт в фоновом потоке
    # ----------------------------------------
    def send(self, text: str, session_id: str = "", images: list[str] | None = None) -> dict[str, Any]:
        text = str(text).strip()
        session = self._session(session_id)
        if not text and not images:
            return {"ok": False}
        if session.busy:
            return {"ok": False, "error": "агент ещё работает — esc, чтобы остановить"}
        try:
            paths = [self._save_image(url, session.id) for url in images or []]
        except ValueError as error:
            return {"ok": False, "error": str(error)}
        text = text or "Посмотри на картинку."
        if session.title == "новая" and not text.startswith("/"):
            session.title = text[:24] + ("…" if len(text) > 24 else "")
        session.thread = threading.Thread(target=self._run, args=(session, text, paths), name="agent", daemon=True)
        session.thread.start()
        return {"ok": True, "session": session.id}

    # ----------------------------------------
    # Ответ на confirm_request / need_skill / continue_request: "yes" | "no" | "session"
    # ----------------------------------------
    def confirm(self, event_id: str, answer: str) -> None:
        for session in self._all_sessions():
            if event_id in session.pending:
                session.pending.discard(event_id)
                session.agent.confirm(event_id, answer)
        self._on_confirm(self._waiting())

    # ----------------------------------------
    # Esc: прервать луп активной сессии
    # ----------------------------------------
    def stop(self, session_id: str = "") -> None:
        self._session(session_id).agent.stop()

    # ----------------------------------------
    # Строка статуса: agent.status() + GPU + состояния вкладок и пространств
    # ----------------------------------------
    def status(self) -> dict[str, Any]:
        session = self._session()
        return {"status": asdict(session.agent.status()), "gpu": self._gpu.text(), "session": session.id,
                "busy": session.busy, "spaces": self.spaces(), "sessions": self._sessions_info()}

    # ----------------------------------------
    # Скиллы для левой колонки
    # ----------------------------------------
    def skills(self) -> list[dict[str, str]]:
        return [{"name": s.name, "title": s.title, "description": s.description, "args": s.args}
                for s in self._session().agent.skills()]

    # ----------------------------------------
    # Встроенные инструменты ядра: описание и спрашивают ли подтверждение
    # ----------------------------------------
    def tools(self) -> list[dict[str, str]]:
        result = []
        for tool in self._session().agent.tools():
            probes = [tool.confirm({}), tool.confirm({"action": "click"})]  # browser спрашивает не на всё
            confirm = "всегда" if all(probes) else "на изменяющие действия" if any(probes) else "нет"
            source, line = inspect.getsourcelines(tool.func)
            result.append({"name": tool.name, "title": tool.title, "signature": tool.signature,
                           "description": tool.description, "confirm": confirm, "source": "".join(source),
                           "path": f"{relative(Path(inspect.getsourcefile(tool.func)))}:{line}"})
        return result

    # ----------------------------------------
    # Долговременная память для настроек и редактора: факты, путь к файлу, лимит
    # ----------------------------------------
    def memory(self) -> dict[str, Any]:
        agent = self._session().agent
        facts = agent.memory_facts()
        return {"facts": facts, "path": relative(agent.memory.path), "limit": agent.config.memory_limit,
                "size": sum(len(fact) + 3 for fact in facts)}

    # ----------------------------------------
    # Сохраняет память из редактора
    # ----------------------------------------
    def save_memory(self, facts: list[str]) -> dict[str, Any]:
        self._session().agent.save_memory([str(fact) for fact in facts or []])
        return self.memory()

    # ----------------------------------------
    # SKILL.md и run.py для панели просмотра
    # ----------------------------------------
    def skill_source(self, name: str) -> dict[str, str]:
        for skill in self._session().agent.skills():
            if skill.name == name:
                read = lambda file: (skill.folder / file).read_text(encoding="utf-8") if (skill.folder / file).exists() else ""
                return {"name": name, "skill_md": read("SKILL.md"), "run_py": read("run.py"), "folder": str(skill.folder),
                        "path": relative(skill.folder) + "/"}
        return {"name": name, "skill_md": "", "run_py": "", "folder": "", "path": ""}

    # ======== файлы активного пространства: дерево и редактор ========

    # ----------------------------------------
    # Содержимое папки для дерева: папки первыми; path — от корня пространства («» — сам корень)
    # ----------------------------------------
    def files(self, path: str = "") -> dict[str, Any]:
        try:
            root, folder = self._space_path(path)
            entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except (OSError, ValueError) as error:
            return {"ok": False, "error": str(error)}
        items = [{"name": entry.name, "path": entry.relative_to(root).as_posix(), "dir": entry.is_dir()}
                 for entry in entries if not (entry.is_dir() and entry.name in HIDDEN_DIRS)]
        return {"ok": True, "root": str(root), "path": path, "items": items}

    # ----------------------------------------
    # Текст файла для редактора; mtime — чтобы при сохранении заметить, что файл поменяли на диске
    # ----------------------------------------
    def open_file(self, path: str, root: str = "") -> dict[str, Any]:
        try:
            _root, full = self._space_path(path, root)
            if full.stat().st_size > MAX_OPEN_BYTES:
                return {"ok": False, "error": f"файл больше {MAX_OPEN_BYTES // 1_000_000} МБ"}
            data = full.read_bytes()
            if b"\0" in data[:8192]:
                return {"ok": False, "error": "двоичный файл — не текст"}
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return {"ok": False, "error": "файл не в UTF-8"}
        except (OSError, ValueError) as error:
            return {"ok": False, "error": str(error)}
        return {"ok": True, "path": path, "root": str(_root), "text": text.replace("\r\n", "\n"), "crlf": "\r\n" in text,
                "mtime": str(full.stat().st_mtime_ns)}  # строкой: наносекунды не влезают в число JS

    # ----------------------------------------
    # Сохранение из редактора (root — пространство вкладки); файл поменяли после открытия — conflict, пока не придёт force
    # ----------------------------------------
    def save_file(self, path: str, text: str, mtime: str = "", crlf: bool = False, force: bool = False,
                  root: str = "") -> dict[str, Any]:
        try:
            _root, full = self._space_path(path, root)
            if not force and mtime and full.exists() and str(full.stat().st_mtime_ns) != mtime:
                return {"ok": False, "conflict": True, "error": "файл изменился на диске после открытия"}
            full.write_text(text.replace("\n", "\r\n") if crlf else text, encoding="utf-8", newline="")
            return {"ok": True, "mtime": str(full.stat().st_mtime_ns)}
        except (OSError, ValueError) as error:
            return {"ok": False, "error": str(error)}

    # ----------------------------------------
    # Картинка сообщения для ленты: data URL (страница открыта по http — file:// ей недоступен); только из logs/images/
    # ----------------------------------------
    def image(self, path: str) -> str:
        full = Path(str(path)).resolve()
        mime = {ext: mime for mime, ext in IMAGE_TYPES.items()}.get(full.suffix.lstrip("."))
        if not mime or not full.is_relative_to(IMAGES_DIR.resolve()) or not full.is_file():
            return ""
        return f"data:{mime};base64,{base64.b64encode(full.read_bytes()).decode('ascii')}"

    # ----------------------------------------
    # Картинка из чата (data URL) → файл в logs/images/; путь уходит ядру
    # ----------------------------------------
    def _save_image(self, url: str, session_id: str) -> str:
        head, _, data = str(url).partition(",")
        mime = head.removeprefix("data:").removesuffix(";base64")
        if mime not in IMAGE_TYPES or not head.endswith(";base64"):
            raise ValueError(f"картинка {mime or '?'} не поддерживается: только png, jpeg, webp, gif")
        raw = base64.b64decode(data)
        if len(raw) > MAX_IMAGE_BYTES:
            raise ValueError(f"картинка больше {MAX_IMAGE_BYTES // 1_000_000} МБ")
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        path = IMAGES_DIR / f"{datetime.now():%Y-%m-%d_%H%M%S}_{session_id}_{secrets.token_hex(3)}.{IMAGE_TYPES[mime]}"
        path.write_bytes(raw)
        return str(path)

    # ----------------------------------------
    # Путь внутри пространства root (пусто — активного); выход за его пределы — ошибка
    # ----------------------------------------
    def _space_path(self, path: str, root: str = "") -> tuple[Path, Path]:
        if root and root not in self._spaces:
            raise ValueError(f"пространства {root} нет")
        root = self._spaces[root or self._active].path.resolve()
        full = (root / path).resolve()
        if not full.is_relative_to(root):
            raise ValueError(f"{path} вне папки пространства")
        return root, full

    # ----------------------------------------
    # Пространства для левой колонки
    # ----------------------------------------
    def spaces(self) -> list[dict[str, Any]]:
        return [{**space.info(), "active": key == self._active} for key, space in self._spaces.items()]

    # ----------------------------------------
    # Открывает папку как пространство (пустой путь — диалог выбора) и делает активной
    # ----------------------------------------
    def open_space(self, path: str = "") -> dict[str, Any]:
        if not path and self._window is not None:
            import webview

            chosen = self._window.create_file_dialog(webview.FileDialog.FOLDER)
            if not chosen:
                return {"ok": False}
            path = chosen[0]
        folder = Path(str(path)).expanduser().resolve()
        if not folder.is_dir():
            return {"ok": False, "error": f"папки {folder} нет"}
        with self._lock:
            if str(folder) in self._trash:
                self._trash.remove(str(folder))
            self._add_space(folder)
            self._active = str(folder)
        self._save_spaces()
        return {"ok": True, **self.state()}

    # ----------------------------------------
    # Убирает пространство в корзину: папка на диске не трогается, сессии закрываются
    # ----------------------------------------
    def remove_space(self, path: str) -> dict[str, Any]:
        with self._lock:
            space = self._spaces.get(path)
            if space is None:
                return {"ok": False, **self.state()}
            if len(self._spaces) == 1:
                return {"ok": False, "error": "это последнее пространство — сначала откройте другое", **self.state()}
            if any(session.busy for session in space.sessions):
                return {"ok": False, "error": "в пространстве работает агент — esc, чтобы остановить", **self.state()}
            for session in space.sessions:
                session.agent.close()
            del self._spaces[path]
            self._trash.append(path)
            if self._active == path:
                self._active = next(iter(self._spaces))
        self._save_spaces()
        return {"ok": True, **self.state()}

    # ----------------------------------------
    # Возвращает пространство из корзины и делает активным
    # ----------------------------------------
    def restore_space(self, path: str) -> dict[str, Any]:
        if path in self._trash and Path(path).is_dir():
            return self.open_space(path)
        return {"ok": False, "error": f"папки {path} больше нет", **self.state()}

    # ----------------------------------------
    # Убирает из корзины насовсем (папку на диске не трогает)
    # ----------------------------------------
    def purge_space(self, path: str) -> dict[str, Any]:
        with self._lock:
            if path in self._trash:
                self._trash.remove(path)
        self._save_spaces()
        return self.state()

    # ----------------------------------------
    # Новая вкладка-сессия в активном пространстве
    # ----------------------------------------
    def new_session(self) -> dict[str, Any]:
        space = self._spaces[self._active]
        session = Session(space.config, space.path)
        space.sessions.append(session)
        space.active = session.id
        return {"ok": True, **self.state()}

    # ----------------------------------------
    # Голосовая диктовка в поле ввода: черновик по ходу речи, в конце — точный текст; не отправляется
    # ----------------------------------------
    def dictate(self) -> dict[str, Any]:

        # ----------------------------------------
        # Черновик по ходу речи — в окно чата событием dictation
        # ----------------------------------------
        def partial(text: str) -> None:
            if self._emitter is not None:
                self._emitter.put({"session": "", "type": "dictation", "text": text, "data": {}, "id": ""})

        try:
            return {"text": self._dictate(partial) or ""}
        except Exception as error:
            return {"text": "", "error": str(error)}

    # ======== дополнительно для окна ========

    # ----------------------------------------
    # Всё для первой отрисовки: пространства, вкладки, скиллы, команды, настройки окна
    # ----------------------------------------
    def state(self) -> dict[str, Any]:
        return {"spaces": self.spaces(), "sessions": self._sessions_info(), "session": self._session().id,
                "skills": self.skills(), "tools": self.tools(), "memory": self.memory(),
                "commands": self.commands(),
                "trash": [{"path": path, "name": Path(path).name or path} for path in self._trash],
                "ui": {key: self._state.get(key) for key in UI_KEYS}}

    # ----------------------------------------
    # Записи журнала сессии — лента восстанавливается из них, своей истории у UI нет
    # ----------------------------------------
    def history(self, session_id: str = "") -> list[dict[str, Any]]:
        return self._session(session_id).agent.history()

    # ----------------------------------------
    # Делает пространство активным
    # ----------------------------------------
    def select_space(self, path: str) -> dict[str, Any]:
        if path in self._spaces:
            self._active = path
            self._save_spaces()
        return self.state()

    # ----------------------------------------
    # Делает вкладку активной
    # ----------------------------------------
    def select_session(self, session_id: str) -> dict[str, Any]:
        space = self._spaces[self._active]
        if any(s.id == session_id for s in space.sessions):
            space.active = session_id
        return self.state()

    # ----------------------------------------
    # Закрывает вкладку (последнюю — заменяет новой)
    # ----------------------------------------
    def close_session(self, session_id: str) -> dict[str, Any]:
        space = self._spaces[self._active]
        session = next((s for s in space.sessions if s.id == session_id), None)
        if session is None:
            return self.state()
        session.agent.stop()
        session.agent.close()
        space.sessions.remove(session)
        if not space.sessions:
            space.sessions.append(Session(space.config, space.path))
        if space.active == session_id:
            space.active = space.sessions[-1].id
        return self.state()

    # ----------------------------------------
    # Быстрые команды для автодополнения по «/» — из /помощь ядра
    # ----------------------------------------
    def commands(self) -> list[dict[str, str]]:
        if self._commands is None:
            agent = Agent(self._config)
            text = "\n".join(e.text for e in agent.send("/помощь") if e.type == "info")
            agent.close()
            self._commands = []
            for line in text.splitlines():
                head, _, desc = line.partition(" — ") if " — " in line else line.partition(" → ")
                for pattern in head.split(" | "):
                    if pattern.startswith("/"):
                        self._commands.append({"command": pattern.strip(), "description": desc.strip()})
        return self._commands

    # ----------------------------------------
    # Модели из `ollama list` (GET /api/tags)
    # ----------------------------------------
    def models(self) -> list[str]:
        return self.check_connection(self._config.base_url).get("models", [])

    # ----------------------------------------
    # Вкладка «Подключение»: адрес Ollama, модель, размышления
    # ----------------------------------------
    def connection(self) -> dict[str, Any]:
        values = {key: getattr(self._config, key) for key in CONNECTION_KEYS}
        return {**values, "efforts": list(EFFORTS)}

    # ----------------------------------------
    # Проверка адреса Ollama: отвечает ли и какие модели есть (`ollama list`)
    # ----------------------------------------
    def check_connection(self, base_url: str) -> dict[str, Any]:
        url = base_url.strip().rstrip("/").removesuffix("/v1") + "/api/tags"
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                models = sorted(item["name"] for item in json.load(response).get("models", []))
        except (OSError, ValueError, KeyError) as error:
            return {"ok": False, "error": f"Ollama не отвечает: {error}"}
        return {"ok": True, "models": models}

    # ----------------------------------------
    # Сохранение подключения: config.toml и все агенты (новые сессии — тоже); пока агент работает — нельзя
    # ----------------------------------------
    def save_connection(self, values: dict[str, Any]) -> dict[str, Any]:
        changes = {key: str(values[key]).strip() for key in CONNECTION_KEYS if key in values}
        if not changes.get("base_url", self._config.base_url) or not changes.get("model", self._config.model):
            return {"ok": False, "error": "адрес и модель не могут быть пустыми"}
        for key in ("reasoning_effort", "skill_reasoning_effort"):
            if key in changes and changes[key] not in EFFORTS:
                return {"ok": False, "error": f"{key}: одно из {', '.join(EFFORTS)}"}
        with self._lock:
            if any(session.busy for session in self._all_sessions()):
                return {"ok": False, "error": "агент работает — дождись ответа"}
            Config.save(changes)
            for key, value in changes.items():
                setattr(self._config, key, value)
            for session in self._all_sessions():
                session.agent.configure(**changes)
        self._on_connection(self._config.base_url, self._config.model)
        return {"ok": True, **self.connection()}

    # ----------------------------------------
    # Смена модели для активной сессии командой /модель
    # ----------------------------------------
    def set_model(self, name: str) -> dict[str, Any]:
        session = self._session()
        if session.busy:
            return {"ok": False, "error": "агент работает — дождись ответа"}
        for _event in session.agent.send(f"/модель {name}"):
            pass
        return {"ok": True, "model": session.agent.status().model}

    # ----------------------------------------
    # Настройки окна: шрифт, тема, свёрнута ли левая колонка
    # ----------------------------------------
    def save_ui(self, values: dict[str, Any]) -> None:
        allowed = {key: values[key] for key in UI_KEYS if key in values}
        self._state.update(**allowed)

    # ----------------------------------------
    # Копирование текста в буфер обмена (кнопка «копировать» у блока кода)
    # ----------------------------------------
    def copy(self, text: str) -> dict[str, Any]:
        try:
            from voice.clipboard import copy

            copy(str(text))
        except Exception as error:
            return {"ok": False, "error": str(error)}
        return {"ok": True}

    # ======== внутреннее ========

    # ----------------------------------------
    # Фоновый поток одной задачи: каждое событие ядра → JSON → окно чата
    # ----------------------------------------
    def _run(self, session: Session, text: str, images: list[str]) -> None:
        self._emit(session, Event("user", text, {"images": images} if images else {}))
        try:
            for event in session.agent.send(text, images):
                if event.type == "confirm_request":
                    diff = make_diff(session.agent.config.project_dir, event.data.get("name", ""),
                                     event.data.get("args") or {})
                    event = replace(event, data={**event.data, "diff": diff})
                if event.type in ("confirm_request", "need_skill", "continue_request"):
                    session.pending.add(event.id)
                    self._emit(session, event)
                    self._on_confirm(True)
                    continue
                self._emit(session, event)
        except Exception as error:
            log.exception("Ошибка в сессии %s", session.id)
            self._emit(session, Event("error", f"{type(error).__name__}: {error}"))
        finally:
            session.pending.clear()
            self._emit(session, Event("done", data=asdict(session.agent.status())))
            self._on_confirm(self._waiting())

    # ----------------------------------------
    # Событие ядра → окно чата с пометкой сессии
    # ----------------------------------------
    def _emit(self, session: Session, event: Event) -> None:
        if self._emitter is not None:
            self._emitter.put({"session": session.id, "type": event.type, "text": event.text,
                               "data": event.data, "id": event.id})

    # ----------------------------------------
    # Пространства, корзина и активное — в ui/state.json, чтобы пережить перезапуск
    # ----------------------------------------
    def _save_spaces(self) -> None:
        self._state.update(active_space=self._active, spaces=list(self._spaces), trash_spaces=list(self._trash))

    # ----------------------------------------
    # Добавляет пространство, если такого ещё нет
    # ----------------------------------------
    def _add_space(self, folder: Path) -> None:
        if str(folder) not in self._spaces:
            self._spaces[str(folder)] = Space(self._config, folder)

    # ----------------------------------------
    # Сессия по id или активная в активном пространстве
    # ----------------------------------------
    def _session(self, session_id: str = "") -> Session:
        if session_id:
            for session in self._all_sessions():
                if session.id == session_id:
                    return session
        space = self._spaces[self._active]
        return next((s for s in space.sessions if s.id == space.active), space.sessions[0])

    # ----------------------------------------
    # Все сессии всех пространств
    # ----------------------------------------
    def _all_sessions(self) -> list[Session]:
        return [session for space in self._spaces.values() for session in space.sessions]

    # ----------------------------------------
    # Вкладки активного пространства
    # ----------------------------------------
    def _sessions_info(self) -> list[dict[str, Any]]:
        space = self._spaces[self._active]
        return [{**s.info(), "busy": s.busy, "active": s.id == space.active} for s in space.sessions]

    # ----------------------------------------
    # Есть ли неотвеченные подтверждения
    # ----------------------------------------
    def _waiting(self) -> bool:
        return any(session.pending for session in self._all_sessions())


# ----------------------------------------
# js_api виджета: все действия — колбэки из ui/app.py
# ----------------------------------------
class WidgetApi:

    # ----------------------------------------
    # actions: toggle_record, toggle_chat, set_wake, send_last_to_chat, quit, menu, drag, drag_end, ready
    # ----------------------------------------
    def __init__(self, actions: dict[str, Callable[..., Any]]) -> None:
        self._actions = actions

    # ----------------------------------------
    # Клик по кольцу: старт или стоп записи
    # ----------------------------------------
    def toggle_record(self) -> None:
        self._actions["toggle_record"]()

    # ----------------------------------------
    # Двойной клик: показать или спрятать чат
    # ----------------------------------------
    def toggle_chat(self) -> None:
        self._actions["toggle_chat"]()

    # ----------------------------------------
    # Меню: «Слушать фон: вкл/выкл»
    # ----------------------------------------
    def set_wake(self, on: bool) -> dict[str, Any]:
        return self._actions["set_wake"](bool(on))

    # ----------------------------------------
    # Меню: последний распознанный текст — сообщением в чат
    # ----------------------------------------
    def send_last_to_chat(self) -> None:
        self._actions["send_last_to_chat"]()

    # ----------------------------------------
    # Меню: «Открыть чат»
    # ----------------------------------------
    def open_chat(self) -> None:
        self._actions["open_chat"]()

    # ----------------------------------------
    # Меню: «Выход»
    # ----------------------------------------
    def quit(self) -> None:
        self._actions["quit"]()

    # ----------------------------------------
    # Меню открыто или закрыто: окно растёт под меню (голова на месте); ответ — где голова и меню в окне
    # ----------------------------------------
    def menu(self, opened: bool, width: int = 0, height: int = 0) -> dict[str, Any]:
        return self._actions["menu"](bool(opened), int(width), int(height))

    # ----------------------------------------
    # Перетаскивание: смещение от начальной точки в пикселях экрана
    # ----------------------------------------
    def drag(self, dx: int, dy: int, start: bool = False) -> None:
        self._actions["drag"](int(dx), int(dy), bool(start))

    # ----------------------------------------
    # Конец перетаскивания: позиция запоминается
    # ----------------------------------------
    def drag_end(self) -> None:
        self._actions["drag_end"]()

    # ----------------------------------------
    # Страница загрузилась: текущее состояние виджета
    # ----------------------------------------
    def ready(self) -> dict[str, Any]:
        return self._actions["ready"]()
