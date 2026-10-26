"""Раздел «Вики» для окна чата одним скриптом: стили и код из wiki/ui/.

pywebview отдаёт страницы окон со своего HTTP-сервера, корень которого — ui/; файлы из wiki/ui/ по ссылке
оттуда не загрузить. Поэтому ui/app.py после загрузки окна чата выполняет этот скрипт через run_js.
"""

from __future__ import annotations

import json
from pathlib import Path

UI_DIR = Path(__file__).resolve().parent / "ui"
SCRIPTS = ("graph.js", "wiki.js")  # порядок важен: wiki.js использует WikiGraph


# ----------------------------------------
# <style> с wiki.css + graph.js + wiki.js; второй раз на той же странице не подключается
# ----------------------------------------
def page_script() -> str:
    css = (UI_DIR / "wiki.css").read_text(encoding="utf-8")
    code = "\n;\n".join((UI_DIR / name).read_text(encoding="utf-8") for name in SCRIPTS)
    return ("if (!window.WikiGraph) {\n"
            f"  const style = document.createElement('style');\n  style.id = 'wiki-css';\n"
            f"  style.textContent = {json.dumps(css, ensure_ascii=False)};\n  document.head.append(style);\n"
            "}\n"
            f"if (!window.WikiGraph) {{\n{code}\n}}\n")
