"""Ярлык «Ася» на рабочем столе: Linux — .desktop, Windows — .lnk. Запускает main.py --chat; если Ася уже
работает, этот запуск только показывает её чат (ui/single.py).

Пути — этой системы (Python из venv, папка проекта): ярлык не переносят между машинами, его создаёт сама Ася
при запуске (ensure), если ярлыка нет или папку перенесли; вручную — python main.py --shortcut.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "main.py"
ICON = Path(__file__).resolve().parent / "icon.png"  # голова виджета; для Windows — icon.ico рядом
NAME = "Ася"


# ----------------------------------------
# Создаёт ярлык на рабочем столе и возвращает путь к нему
# ----------------------------------------
def install() -> Path:
    return install_windows() if sys.platform == "win32" else install_linux()


# ----------------------------------------
# При запуске: ярлыка нет или он для другой папки/Python — создать заново; state — ui/state.json
# ----------------------------------------
def ensure(state: Any) -> None:
    target = shortcut_path()
    stamp = f"{sys.executable}|{MAIN}|{target}"
    if target.exists() and state.get("shortcut") == stamp:
        return
    state.update(shortcut=f"{sys.executable}|{MAIN}|{install()}")


# ----------------------------------------
# Где лежит ярлык этой системы
# ----------------------------------------
def shortcut_path() -> Path:
    return desktop() / (f"{NAME}.lnk" if sys.platform == "win32" else "asya.desktop")


# ----------------------------------------
# Папка рабочего стола: Windows — как её видит Проводник (в том числе OneDrive), Linux — XDG («Рабочий стол»)
# ----------------------------------------
def desktop() -> Path:
    if sys.platform == "win32":
        import ctypes

        buffer = ctypes.create_unicode_buffer(260)
        ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buffer)  # CSIDL_DESKTOPDIRECTORY
        return Path(buffer.value) if buffer.value else Path.home() / "Desktop"
    try:
        done = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=5)
        if done.returncode == 0 and done.stdout.strip() and Path(done.stdout.strip()) != Path.home():
            return Path(done.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return Path.home() / "Desktop"


# ----------------------------------------
# Linux: <рабочий стол>/asya.desktop, исполняемый (иначе KDE и GNOME не запустят его щелчком)
# ----------------------------------------
def install_linux() -> Path:
    target = shortcut_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={NAME}\n"
        "Comment=Локальный агент: голосовой виджет, чат и вики\n"
        f'Exec="{sys.executable}" "{MAIN}" --chat\n'
        f"Path={ROOT}\n"
        f"Icon={ICON}\n"
        "Terminal=false\n"
        "Categories=Utility;\n", encoding="utf-8")
    target.chmod(0o755)
    return target


# ----------------------------------------
# Windows: <рабочий стол>\\Ася.lnk, запуск через pythonw.exe — без окна консоли
# ----------------------------------------
def install_windows() -> Path:
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    target = shortcut_path()
    script = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{target}'); "
              f"$s.TargetPath = '{pythonw}'; $s.Arguments = '\"{MAIN}\" --chat'; "
              f"$s.WorkingDirectory = '{ROOT}'; $s.IconLocation = '{ICON.with_suffix('.ico')}'; "
              "$s.Description = 'Локальный агент'; $s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    return target
