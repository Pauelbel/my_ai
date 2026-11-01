name: yt_download
title: Скачать аудио с YouTube
description: Скачивает аудио одного видео с YouTube в папку проекта (плейлист из ссылки не качает), возвращает путь к файлу
args: url: str, folder: str = '.', audio_format: str = 'mp3'
libs: yt-dlp

folder — папка, а не имя файла; имя файла берётся из названия видео. Нужен системный ffmpeg.
