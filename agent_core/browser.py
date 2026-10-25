"""Браузер Playwright: одно окно на процесс, свой поток, постоянный профиль, подсветка элемента."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .config import BROWSER_PROFILE, LOGS_DIR
from .tools import Workspace, tool


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
            self._users -= 1
            if self._users > 0:
                return
            if Browser._shared is self:
                Browser._shared = None
        self.close()

    # ----------------------------------------
    # Выполняет func(page) в потоке браузера и возвращает результат; ошибки пробрасываются
    # ----------------------------------------
    def call(self, func: Callable[[Any], Any]) -> Any:
        return self._pool.submit(lambda: func(self._live_page())).result()

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
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._page.set_default_timeout(10_000)
        return self._page

    # ----------------------------------------
    # Сайт открыл новую вкладку — дальше агент работает в ней
    # ----------------------------------------
    def _adopt(self, page: Any) -> None:
        page.set_default_timeout(10_000)
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

        self._pool.submit(shutdown).result()


# ----------------------------------------
# Браузер спрашивает подтверждение на всё, кроме open/text/screenshot
# ----------------------------------------
def browser_confirm(args: dict[str, Any]) -> bool:
    return args.get("action", "open") not in {"open", "text", "screenshot"}


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


# ----------------------------------------
# Рамка вокруг элемента, с которым агент сейчас работает: пользователь видит, куда он кликает и что читает
# ----------------------------------------
def highlight(page: Any, selector: str, label: str) -> Any:
    target = page.locator(selector).first
    target.wait_for(state="visible")
    try:
        target.evaluate(HIGHLIGHT_JS, label)
        page.wait_for_timeout(450)  # рамку успевают заметить до клика
    except Exception:
        pass  # подсветка — для глаз; не получилась — действие всё равно выполняется
    return target


# ----------------------------------------
# Действие в браузере Playwright; окно и вход на сайты сохраняются между сообщениями
# ----------------------------------------
@tool("Браузер", "Браузер Playwright (одно окно на всю сессию): action = open(url) | click(selector) | "
      "fill(selector, text) | text(selector?) | screenshot", confirm=browser_confirm)
def browser(ws: Workspace, action: str = "open", url: str | None = None, selector: str | None = None,
            text: str | None = None) -> str:
    if action not in {"open", "click", "fill", "text", "screenshot"}:
        raise ValueError("action должен быть open, click, fill, text или screenshot")

    # ----------------------------------------
    # Само действие — в потоке браузера
    # ----------------------------------------
    def act(page: Any) -> str:
        if action == "open":
            target = url or ws.config.start_url
            page.goto(target if "://" in target or target.startswith("about:") else "https://" + target)
            page.bring_to_front()
            return f"открыто: {page.url} — {page.title()}"
        if action == "click":
            highlight(page, selector, "клик").click()
            return f"клик по {selector}; сейчас {page.url}"
        if action == "fill":
            highlight(page, selector, "ввод").fill(text or "")
            return f"в {selector} введено {len(text or '')} символов"
        if action == "text":
            return highlight(page, selector, "читаю").inner_text() if selector else page.inner_text("body")
        folder = LOGS_DIR / "screenshots"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{datetime.now():%Y%m%d_%H%M%S}.png"
        page.screenshot(path=str(target))
        return f"скриншот: {target}"

    return ws.browser.call(act)
