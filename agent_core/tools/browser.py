"""Браузер Playwright: одно окно на процесс, свой поток, постоянный профиль, подсветка элемента."""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..config import BROWSER_PROFILE, LOGS_DIR
from .base import Workspace, tool

ACTION_TIMEOUT = 10_000  # клик, ввод: столько ждём, пока элемент появится
NAV_TIMEOUT = 60_000     # сайты документации без кэша открываются по 20–30 с
LOAD_WAIT = 10_000       # после DOMContentLoaded ещё столько ждём полной загрузки; не дождались — текст уже есть
FIND_TIMEOUT = 3_000     # text(selector): элемента нет — сказать сразу, а не ждать ACTION_TIMEOUT
SELECTOR_ITEMS = 50      # text(selector) отдаёт не больше стольких элементов


# ----------------------------------------
# Один браузер на процесс: все агенты (вкладки чата) работают в одном окне, оно живёт между сообщениями
# ----------------------------------------
class Browser:
    _shared: Browser | None = None
    _shared_lock = threading.Lock()

    # ----------------------------------------
    # Playwright привязан к потоку, где запущен, а UI шлёт каждое сообщение из нового — поэтому свой поток
    # ----------------------------------------
    def __init__(self, headless: bool, profile: Path = BROWSER_PROFILE) -> None:
        self.headless = headless
        self.profile = profile
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="browser")
        self._close_lock = threading.Lock()
        self._closed = False
        self._playwright: Any = None
        self._context: Any = None
        self._page: Any = None
        self._users = 0

    # ----------------------------------------
    # Общий браузер процесса; каждый агент берёт его здесь и отдаёт в release()
    # ----------------------------------------
    @classmethod
    def shared(cls, headless: bool) -> Browser:
        with cls._shared_lock:
            if cls._shared is None:
                cls._shared = cls(headless)
            cls._shared._users += 1
            return cls._shared

    # ----------------------------------------
    # Агент больше не нужен; последний закрывает окно
    # ----------------------------------------
    def release(self) -> None:
        with Browser._shared_lock:
            if self._users <= 0:
                return
            self._users -= 1
            if self._users > 0:
                return
            if Browser._shared is self:
                Browser._shared = None
        self.close()

    # ----------------------------------------
    # Выполняет func(page) в потоке браузера и возвращает результат; ошибки пробрасываются.
    # Пользователь нажал «стоп» — агент перестаёт ждать сразу, а начатое действие браузер доделывает сам
    # ----------------------------------------
    def call(self, func: Callable[[Any], Any], stop: threading.Event | None = None) -> Any:
        with self._close_lock:
            if self._closed:
                raise RuntimeError("браузер уже закрыт")
            future = self._pool.submit(lambda: func(self._live_page()))
        while not wait([future], timeout=0.2).done:
            if stop is not None and stop.is_set():
                raise InterruptedError("остановлено пользователем")
        return future.result()

    # ----------------------------------------
    # Живая страница: последняя открытая вкладка; закрыли окно — запускает заново
    # ----------------------------------------
    def _live_page(self) -> Any:
        if self._page is not None and not self._page.is_closed():
            return self._page
        if self._context is not None and self._context.pages:
            self._page = self._context.pages[-1]
            return self._page
        from playwright.sync_api import sync_playwright

        if self._playwright is None:
            self._playwright = sync_playwright().start()
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
        # Постоянный профиль хранит вход на сайты; окно на весь экран, страница — на всё окно
        self._context = self._playwright.chromium.launch_persistent_context(
            str(self.profile), headless=self.headless, no_viewport=True, args=["--start-maximized"])
        self._context.on("page", self._adopt)
        self._adopt(self._context.pages[0] if self._context.pages else self._context.new_page())
        return self._page

    # ----------------------------------------
    # Сайт открыл новую вкладку — дальше агент работает в ней
    # ----------------------------------------
    def _adopt(self, page: Any) -> None:
        page.set_default_timeout(ACTION_TIMEOUT)
        page.set_default_navigation_timeout(NAV_TIMEOUT)
        self._page = page

    # ----------------------------------------
    # Закрывает браузер (в его же потоке)
    # ----------------------------------------
    def close(self) -> None:

        # ----------------------------------------
        # Закрытие окна и Playwright; уже закрытое — не ошибка
        # ----------------------------------------
        def shutdown() -> None:
            for stop in (self._context and self._context.close, self._playwright and self._playwright.stop):
                try:
                    if stop:
                        stop()
                except Exception:
                    pass
            self._playwright = self._context = self._page = None

        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            future = self._pool.submit(shutdown)
            with Browser._shared_lock:
                if Browser._shared is self:
                    Browser._shared = None
                self._users = 0
        try:
            future.result()
        finally:
            # shutdown() объектов Playwright не завершает сам поток executor-а.
            self._pool.shutdown(wait=True)


# ----------------------------------------
# Браузер спрашивает подтверждение на всё, кроме open/text/screenshot
# ----------------------------------------
def browser_confirm(args: dict[str, Any]) -> bool:
    return (args.get("action") or "open") not in {"open", "text", "screenshot"}


HIGHLIGHT_JS = """(el, label) => {
  el.scrollIntoView({block: "center", inline: "center", behavior: "instant"});
  const r = el.getBoundingClientRect();
  let box = document.getElementById("__agent_highlight");
  if (!box) {
    box = document.createElement("div");
    box.id = "__agent_highlight";
    box.appendChild(document.createElement("span"));
    document.documentElement.appendChild(box);
  }
  Object.assign(box.style, {position: "fixed", left: (r.left - 4) + "px", top: (r.top - 4) + "px",
    width: (r.width + 8) + "px", height: (r.height + 8) + "px", border: "3px solid #58a6ff", borderRadius: "6px",
    boxShadow: "0 0 0 4px rgba(88,166,255,.25), 0 0 18px rgba(88,166,255,.85)", zIndex: "2147483647",
    pointerEvents: "none", boxSizing: "border-box", transition: "all .15s ease-out"});
  Object.assign(box.firstChild.style, {position: "absolute", left: "-3px", top: "-24px", padding: "1px 8px",
    background: "#58a6ff", color: "#0d1117", font: "600 12px/18px system-ui, sans-serif", borderRadius: "4px 4px 0 0",
    whiteSpace: "nowrap"});
  box.firstChild.textContent = "агент: " + label;
  clearTimeout(window.__agentHighlightTimer);
  window.__agentHighlightTimer = setTimeout(() => box.remove(), 4000);
}"""


# Основной текст страницы: первый непустой из main, [role=main], article, #content, иначе body (без меню и дерева
# страниц сбоку). Заголовки помечаются #, как в Markdown: метки ставятся и убираются в одном вызове — их не видно
MAIN_TEXT_JS = """() => {
  const roots = ["main", "[role=main]", "article", "#content"].map((s) => document.querySelector(s));
  const root = roots.find((node) => node && node.innerText.trim().length > 200) || document.body;
  const marks = [...root.querySelectorAll("h1, h2, h3, h4, h5, h6")].map((h) => {
    const mark = document.createTextNode("#".repeat(Number(h.tagName[1])) + " ");
    h.prepend(mark);
    return mark;
  });
  try {
    return root.innerText;
  } finally {
    marks.forEach((mark) => mark.remove());
  }
}"""

# Текст только видимых элементов: скрытые шаблоны и меню не нужны
VISIBLE_TEXTS_JS = "(els) => els.filter((el) => el.getClientRects().length).map((el) => el.innerText)"


# ----------------------------------------
# Рамка вокруг элемента, с которым агент сейчас работает: пользователь видит, куда он кликает и что читает
# ----------------------------------------
def highlight(page: Any, selector: str, label: str, timeout: float | None = None) -> Any:
    target = page.locator(selector).filter(visible=True).first  # скрытый дубль (мобильное меню) не мешает
    target.wait_for(state="visible", timeout=timeout)
    try:
        target.evaluate(HIGHLIGHT_JS, label)
        page.wait_for_timeout(450)  # рамку успевают заметить до клика
    except Exception:
        pass  # подсветка — для глаз; не получилась — действие всё равно выполняется
    return target


# ----------------------------------------
# Текст страницы для модели: ячейки таблиц через « | », лишние пробелы и пустые строки убраны
# ----------------------------------------
def tidy(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        line = re.sub(r"[  ]+", " ", line).strip(" \t")
        line = re.sub(r" ?\t+ ?", " | ", line)
        if line or (lines and lines[-1]):
            lines.append(line)
    return "\n".join(lines).strip()


# ----------------------------------------
# Делит текст на части до size символов по границам строк; заполненную больше чем на 60% часть
# заголовок начинает заново — раздел не рвётся посередине
# ----------------------------------------
def split_parts(text: str, size: int) -> list[str]:
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        for piece in [line[i:i + size] for i in range(0, len(line), size)] or [""]:
            heading = piece.startswith("#") and len(current) > size * 0.6
            if current and (heading or len(current) + 1 + len(piece) > size):
                parts.append(current)
                current = piece
            else:
                current = f"{current}\n{piece}" if current else piece
    return [*parts, current] if current or not parts else parts


# ----------------------------------------
# Часть основного текста страницы: шапка «часть k из n» и подсказка, что читать дальше
# ----------------------------------------
def page_part(page: Any, part: int, size: int) -> str:
    parts = split_parts(tidy(page.evaluate(MAIN_TEXT_JS)), size)
    if not 1 <= part <= len(parts):
        raise ValueError(f"part — от 1 до {len(parts)}")
    # Модель идёт по подсказке в результате, а не по промпту: без «сначала допиши» она читает всё подряд и к концу
    # первые части уже вне истории
    tail = (f'[часть {part} из {len(parts)}; дальше — {{"action": "text", "part": {part + 1}}}. Составляешь документ '
            f"по странице — сначала допиши в файл то, что взял из этой части]" if part < len(parts) else "[конец страницы]")
    head = f"[{page.title()[:80]} — часть {part} из {len(parts)}]"
    return f"{head}\n{parts[part - 1] or '(на странице нет текста)'}\n{tail}"


# ----------------------------------------
# Текст всех видимых элементов по селектору; нет таких — ошибка сразу, без долгого ожидания
# ----------------------------------------
def selector_text(page: Any, selector: str) -> str:
    from playwright.sync_api import TimeoutError as PageTimeout

    found = page.locator(selector)
    try:
        found.first.wait_for(state="attached", timeout=FIND_TIMEOUT)
    except PageTimeout:
        raise LookupError(f"на странице нет {selector}; без selector text вернёт основной текст страницы") from None
    texts = [text for text in map(tidy, found.evaluate_all(VISIBLE_TEXTS_JS)) if text]
    if not texts:
        raise LookupError(f"все {selector} скрыты или пустые; без selector text вернёт основной текст страницы")
    try:
        highlight(page, selector, "читаю", FIND_TIMEOUT)
    except Exception:
        pass  # подсветка — для глаз
    more = f", показаны первые {SELECTOR_ITEMS}" if len(texts) > SELECTOR_ITEMS else ""
    return f"[{selector}: найдено {len(texts)}{more}]\n" + "\n---\n".join(texts[:SELECTOR_ITEMS])


# ----------------------------------------
# Действие в браузере Playwright; окно и вход на сайты сохраняются между сообщениями
# ----------------------------------------
@tool("Браузер", "Браузер Playwright (одно окно на всю сессию): action = open(url) | text(part) — основной текст "
      "страницы частями, читай по порядку part=1, 2, … | text(selector) — текст всех таких элементов | "
      "click(selector) | fill(selector, text) | screenshot — снимок окна, ты его увидишь", confirm=browser_confirm)
def browser(ws: Workspace, action: str | None = None, url: str | None = None, selector: str | None = None,
            text: str | None = None, part: int | None = None) -> str:
    if not action:  # модель забыла action: по аргументам видно, что она хотела (без этого open уводил на start_url)
        action = "text" if not url and (part is not None or selector or text) else "open"
    if action not in {"open", "click", "fill", "text", "screenshot"}:
        raise ValueError("action должен быть open, click, fill, text или screenshot")
    if action in {"click", "fill"} and not selector:
        raise ValueError(f"для action={action} нужен selector")
    size = max(1000, ws.config.output_limit - 400)  # часть с шапкой и подсказкой влезает в вывод инструмента целиком

    # ----------------------------------------
    # Само действие — в потоке браузера
    # ----------------------------------------
    def act(page: Any) -> str:
        from playwright.sync_api import TimeoutError as PageTimeout

        if action == "open":
            target = (url or ws.config.start_url).strip()
            if not target:
                raise ValueError("для action=open нужен url")
            page.goto(target if "://" in target or target.startswith("about:") else "https://" + target,
                      wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("load", timeout=LOAD_WAIT)
            except PageTimeout:
                pass  # картинки и счётчики ещё грузятся, а текст уже есть
            page.bring_to_front()
            body = tidy(page.evaluate(MAIN_TEXT_JS))
            count = len(split_parts(body, size))
            return (f"открыто: {page.url} — {page.title()}\nтекст страницы: {len(body)} символов, частей: {count}; "
                    f'читай по порядку: {{"action": "text", "part": 1}} … {{"action": "text", "part": {count}}}')
        if action == "click":
            highlight(page, selector, "клик").click()
            return f"клик по {selector}; сейчас {page.url}"
        if action == "fill":
            highlight(page, selector, "ввод").fill(text or "")
            return f"в {selector} введено {len(text or '')} символов"
        if action == "text":
            return selector_text(page, selector) if selector else page_part(page, int(part or 1), size)
        folder = LOGS_DIR / "screenshots"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{datetime.now():%Y%m%d_%H%M%S_%f}.png"
        page.screenshot(path=str(target))
        return str(target)

    result = ws.browser.call(act, ws.stop)
    if action != "screenshot":
        return result
    ws.images.append(result)  # агент приложит снимок к результату — модель увидит его следующим шагом
    return f"скриншот: {result}"
