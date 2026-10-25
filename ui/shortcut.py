"""Ярлык «Агент»: пункт меню приложений на Linux, ярлык в меню «Пуск» на Windows. Запускает main.py."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "main.py"
ICON = Path(__file__).resolve().parent / "icon.png"  # голова виджета; для Windows — icon.ico рядом


# ----------------------------------------
# Создаёт ярлык для этой системы и возвращает путь к нему
# ----------------------------------------
def install() -> Path:
    return install_windows() if sys.platform == "win32" else install_linux()


# ----------------------------------------
# Linux: ~/.local/share/applications/agent.desktop — тем же Python из venv
# ----------------------------------------
def install_linux() -> Path:
    target = Path.home() / ".local" / "share" / "applications" / "agent.desktop"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Агент\n"
        "Comment=Локальный агент: голосовой виджет и чат\n"
        f'Exec="{sys.executable}" "{MAIN}"\n'
        f"Path={ROOT}\n"
        f"Icon={ICON}\n"
        "Terminal=false\n"
        "Categories=Utility;\n", encoding="utf-8")
    return target


# ----------------------------------------
# Windows: «Пуск» → Агент.lnk, запуск через pythonw.exe — без окна консоли
# ----------------------------------------
def install_windows() -> Path:
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    programs = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    target = programs / "Агент.lnk"
    script = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{target}'); "
              f"$s.TargetPath = '{pythonw}'; $s.Arguments = '\"{MAIN}\"'; "
              f"$s.WorkingDirectory = '{ROOT}'; $s.IconLocation = '{ICON.with_suffix('.ico')}'; "
              "$s.Description = 'Локальный агент'; $s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    return target
