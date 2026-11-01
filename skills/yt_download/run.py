"""Скилл yt_download: скачивание аудио с YouTube."""
import os
import shutil
import subprocess
from pathlib import Path


# ----------------------------------------
# Точка входа скилла: качает аудио одного видео, возвращает путь к готовому файлу
# ----------------------------------------
def main(url: str, folder: str = ".", audio_format: str = "mp3") -> str:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg не найден в PATH: это системная программа, поставь её пакетным менеджером")
    if shutil.which("yt-dlp") is None:
        raise RuntimeError("yt-dlp не установлен: pip install yt-dlp")
    target = Path(os.environ.get("AGENT_PROJECT", ".")) / folder
    cmd = ["yt-dlp", "--no-playlist", "--extract-audio", f"--audio-format={audio_format}",
           "--paths", str(target), "--print", "after_move:filepath", url]
    done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if done.returncode != 0:
        raise RuntimeError(f"ошибка yt-dlp: {done.stderr.strip()}")
    lines = done.stdout.strip().splitlines()
    if not lines or not Path(lines[-1]).is_file():
        raise RuntimeError(f"yt-dlp не создал файл; проверь ссылку {url}\n{done.stderr.strip()}")
    return f"Аудио скачано: {lines[-1]}"
