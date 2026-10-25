"""Точка входа: десктопный агент — мини-виджет «Ася» с голосом и окно чата.

    python main.py              — виджет, чат скрыт
    python main.py --chat       — сразу с окном чата
    python main.py --shortcut   — ярлык «Агент» в меню приложений (Linux) или «Пуск» (Windows)

На Windows без консоли: pythonw main.py (так запускает и ярлык).
"""

import sys
from pathlib import Path

# Папка проекта в путях импорта — чтобы запуск работал из любой текущей папки
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ui.app import main  # noqa: E402

if __name__ == "__main__":
    main()
