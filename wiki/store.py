"""Вики на диске по идее LLM Wiki (Карпатый): сырые источники, заметки, каталог и журнал — их ведёт код.

    <папка вики>/
      notes/<раздел>/<имя>.md   заметки: frontmatter + Markdown, ссылки [[имя]]
      raw/<дата>_<время>.md      сырые тексты как есть — их никто не правит
      index.md                   каталог: раздел → заметки с описанием (пишется после каждой правки)
      log.md                     журнал: что и когда создано, дополнено, поправлено
      .history/                  прежние версии заметок перед каждой правкой
      .trash/                    удалённые заметки

Модель пишет только текст заметки; frontmatter, источник, «См. также», каталог и журнал — забота кода.
"""

from __future__ import annotations

import re
import shutil
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

NOTES_DIR = "notes"
RAW_DIR = "raw"
HISTORY_DIR = ".history"
TRASH_DIR = ".trash"
INDEX_FILE = "index.md"
LOG_FILE = "log.md"
SEE_ALSO = "## См. также"
NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")  # имя файла заметки: латиница через дефис
LINK = re.compile(r"\[\[([^\[\]|#]+?)(?:\|[^\]]*)?\]\]")
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n?", re.S)
SEARCH_LIMIT = 20


# ----------------------------------------
# Заметка, как её видят дерево, граф и каталог
# ----------------------------------------
@dataclass
class Note:
    name: str
    folder: str
    title: str
    summary: str
    updated: str
    sources: list[str]
    body: str
    links: list[str] = field(default_factory=list)  # все [[имя]] из текста, в том числе на несуществующие


# ----------------------------------------
# Вики в папке: чтение, запись, каталог, журнал, граф
# ----------------------------------------
class Wiki:

    # ----------------------------------------
    # Папка вики; создаётся при первой записи
    # ----------------------------------------
    def __init__(self, root: Path) -> None:
        self.root = root
        self.notes_dir = root / NOTES_DIR
        self._lock = threading.RLock()

    # ======== чтение ========

    # ----------------------------------------
    # Все заметки: имя → Note; при одинаковых именах в разных разделах берётся первая
    # ----------------------------------------
    def notes(self) -> dict[str, Note]:
        found: dict[str, Note] = {}
        if self.notes_dir.is_dir():
            for path in sorted(self.notes_dir.rglob("*.md")):
                hidden = any(part.startswith(".") for part in path.relative_to(self.notes_dir).parts)
                if path.stem not in found and not hidden:
                    found[path.stem] = self._parse(path)
        return found

    # ----------------------------------------
    # Дерево для интерфейса: разделы по алфавиту, в каждом — заметки по названию
    # ----------------------------------------
    def tree(self) -> list[dict[str, Any]]:
        groups: dict[str, list[Note]] = {}
        for note in self.notes().values():
            groups.setdefault(note.folder, []).append(note)
        return [{"folder": folder,
                 "notes": [{"name": n.name, "title": n.title, "summary": n.summary, "updated": n.updated}
                           for n in sorted(groups[folder], key=lambda n: n.title.casefold())]}
                for folder in sorted(groups, key=str.casefold)]

    # ----------------------------------------
    # Одна заметка целиком: текст файла, связи в обе стороны, время изменения (для проверки при сохранении)
    # ----------------------------------------
    def note(self, name: str) -> dict[str, Any]:
        notes = self.notes()
        note = self._get(notes, name)
        path = self._path(note)
        return {"name": note.name, "folder": note.folder, "title": note.title, "summary": note.summary,
                "updated": note.updated, "sources": note.sources, "text": path.read_text(encoding="utf-8"),
                "body": note.body, "path": path.relative_to(self.root).as_posix(), "mtime": str(path.stat().st_mtime_ns),
                "links": [self._ref(notes[n]) for n in note.links if n in notes and n != name],
                "missing": [n for n in note.links if n not in notes],
                "backlinks": [self._ref(other) for other in notes.values() if name in other.links and other.name != name],
                "titles": {n.name: n.title for n in notes.values()}}

    # ----------------------------------------
    # Сырой источник по пути из заметки (raw/…) — только чтение
    # ----------------------------------------
    def source(self, relative: str) -> str:
        path = (self.root / relative).resolve()
        if not path.is_relative_to((self.root / RAW_DIR).resolve()) or not path.is_file():
            raise ValueError(f"источника {relative} нет")
        return path.read_text(encoding="utf-8")

    # ----------------------------------------
    # Граф: точка — заметка (размер — число связей), линия — ссылка [[…]] в любую сторону
    # ----------------------------------------
    def graph(self) -> dict[str, Any]:
        notes = self.notes()
        edges = {tuple(sorted((note.name, target))) for note in notes.values()
                 for target in note.links if target in notes and target != note.name}
        degree = {name: 0 for name in notes}
        for left, right in edges:
            degree[left] += 1
            degree[right] += 1
        folders = sorted({note.folder for note in notes.values()}, key=str.casefold)
        return {"nodes": [{"id": n.name, "title": n.title, "folder": n.folder, "group": folders.index(n.folder),
                           "degree": degree[n.name]} for n in notes.values()],
                "edges": [{"source": left, "target": right} for left, right in sorted(edges)],
                "folders": folders}

    # ----------------------------------------
    # Поиск по названию, описанию и тексту: сначала совпадения в названии
    # ----------------------------------------
    def search(self, query: str) -> list[dict[str, str]]:
        words = query.casefold().split()
        if not words:
            return []
        hits = []
        for note in self.notes().values():
            head = f"{note.name} {note.title} {note.summary}".casefold()
            text = f"{head} {note.body.casefold()}"
            if all(word in text for word in words):
                line = next((row.strip() for row in note.body.splitlines() if words[0] in row.casefold()), note.summary)
                hits.append((not all(word in head for word in words), {**self._ref(note), "line": line[:160]}))
        return [hit for _, hit in sorted(hits, key=lambda item: (item[0], item[1]["title"].casefold()))][:SEARCH_LIMIT]

    # ----------------------------------------
    # Каталог текстом — он же index.md и он же часть промпта агента вики
    # ----------------------------------------
    def catalog(self) -> str:
        notes = self.notes()
        lines = ["# Каталог вики", "", "Пишется кодом после каждой правки — руками не править.", ""]
        if not notes:
            return "\n".join([*lines, "(заметок пока нет)", ""])
        for group in self.tree():
            lines.append(f"## {group['folder']}")
            lines += [f"- [[{item['name']}]] — {item['title']}: {item['summary'] or '(без описания)'}"
                      for item in group["notes"]]
            lines.append("")
        linked = {name for note in notes.values() for name in note.links if name in notes}
        lonely = [name for name, note in notes.items()
                  if name not in linked and not any(link in notes for link in note.links)]
        lines += ["## Без связей", "", ", ".join(f"[[{name}]]" for name in lonely) or "нет", ""]
        return "\n".join(lines)

    # ======== запись ========

    # ----------------------------------------
    # Сырой текст как есть → raw/<дата>_<время>.md; возвращает путь от корня вики
    # ----------------------------------------
    def save_raw(self, text: str) -> str:
        with self._lock:
            folder = self.root / RAW_DIR
            folder.mkdir(parents=True, exist_ok=True)
            stamp = f"{datetime.now():%Y-%m-%d_%H%M%S}"
            path = folder / f"{stamp}.md"
            number = 1
            while path.exists():
                number += 1
                path = folder / f"{stamp}-{number}.md"
            path.write_text(text.strip() + "\n", encoding="utf-8", newline="\n")
            relative = path.relative_to(self.root).as_posix()
            self._log("источник", relative)
            return relative

    # ----------------------------------------
    # Новая заметка: frontmatter и «См. также» добавляет код; имя занято — ошибка с подсказкой
    # ----------------------------------------
    def create(self, folder: str, name: str, title: str, summary: str, text: str,
               links: list[str] | None = None, source: str = "") -> str:
        folder, name, title, summary = clean_folder(folder), name.strip().lower(), one_line(title), one_line(summary)
        if not NAME.match(name):
            raise ValueError(f"имя «{name}» — латиницей через дефис: redis, load-testing")
        if not title:
            raise ValueError("нужен title — название по-русски")
        with self._lock:
            notes = self.notes()
            if name in notes:
                raise ValueError(f"заметка {name} уже есть (раздел «{notes[name].folder}») — дополни её через wiki_append")
            today = f"{datetime.now():%Y-%m-%d}"
            meta = {"title": title, "summary": summary, "sources": source, "created": today, "updated": today}
            known, unknown = split_links(links or [], notes, name)
            body = with_see_also(re.sub(r"\A#\s[^\n]*\n+", "", text.strip()), known)  # «# Заголовок» — уже в title
            path = self.notes_dir / folder / f"{name}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(render(meta, body), encoding="utf-8", newline="\n")
            self._changed("создана", name, title, source)
        return done(f"создана заметка {name} в разделе «{folder}»", unknown)

    # ----------------------------------------
    # Дописывает текст в конец заметки (перед «См. также»); старое не переписывается
    # ----------------------------------------
    def append(self, name: str, text: str, links: list[str] | None = None, source: str = "") -> str:
        with self._lock:
            notes = self.notes()
            note = self._get(notes, name)
            path = self._path(note)
            meta, body = split(path.read_text(encoding="utf-8"))
            main, linked = cut_see_also(body)
            known, unknown = split_links(links or [], notes, name)
            body = with_see_also(f"{main.rstrip()}\n\n{text.strip()}".strip(), [*linked, *known])
            meta["updated"] = f"{datetime.now():%Y-%m-%d}"
            if source:
                meta["sources"] = ", ".join(dict.fromkeys([*note.sources, source]))
            self._backup(path)
            path.write_text(render(meta, body), encoding="utf-8", newline="\n")
            self._changed("дополнена", name, note.title, source)
        return done(f"дополнена заметка {name}", unknown)

    # ----------------------------------------
    # Точечная правка: old встречается в заметке ровно один раз
    # ----------------------------------------
    def edit(self, name: str, old: str, new: str) -> str:
        with self._lock:
            note = self._get(self.notes(), name)
            path = self._path(note)
            text = path.read_text(encoding="utf-8")
            count = text.count(old) if old else 0
            if count != 1:
                raise ValueError(f"в заметке {name} фрагмент old найден {count} раз — нужен ровно один, прочитай её через wiki_read")
            meta, body = split(text.replace(old, new, 1))
            meta["updated"] = f"{datetime.now():%Y-%m-%d}"
            self._backup(path)
            path.write_text(render(meta, body), encoding="utf-8", newline="\n")
            self._changed("поправлена", name, note.title)
        return f"поправлена заметка {name}"

    # ----------------------------------------
    # Сохранение из редактора: файл как есть; изменился на диске после открытия — ошибка
    # ----------------------------------------
    def write(self, name: str, text: str, mtime: str = "") -> str:
        with self._lock:
            note = self._get(self.notes(), name)
            path = self._path(note)
            if mtime and str(path.stat().st_mtime_ns) != mtime:
                raise ValueError("заметку изменили после открытия (агент?) — открой её заново")
            self._backup(path)
            path.write_text(text.replace("\r\n", "\n").rstrip() + "\n", encoding="utf-8", newline="\n")
            self._changed("правка вручную", name, self._parse(path).title)
            return str(path.stat().st_mtime_ns)

    # ----------------------------------------
    # Удаление — в .trash/, оттуда можно вернуть руками
    # ----------------------------------------
    def delete(self, name: str) -> None:
        with self._lock:
            note = self._get(self.notes(), name)
            path = self._path(note)
            trash = self.root / TRASH_DIR / f"{datetime.now():%Y-%m-%d_%H%M%S}_{path.name}"
            trash.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(path, trash)
            if path.parent != self.notes_dir and not any(path.parent.iterdir()):
                path.parent.rmdir()
            self._changed("удалена", name, note.title)

    # ======== внутреннее ========

    # ----------------------------------------
    # Файл → Note; раздел — папка внутри notes/
    # ----------------------------------------
    def _parse(self, path: Path) -> Note:
        meta, body = split(path.read_text(encoding="utf-8"))
        folder = path.parent.relative_to(self.notes_dir).as_posix()
        return Note(name=path.stem, folder="" if folder == "." else folder, title=meta.get("title") or path.stem,
                    summary=meta.get("summary", ""), updated=meta.get("updated", ""),
                    sources=[s.strip() for s in meta.get("sources", "").split(",") if s.strip()], body=body,
                    links=list(dict.fromkeys(match.strip() for match in LINK.findall(body))))

    # ----------------------------------------
    # Заметка по имени; нет такой — ошибка, понятная модели
    # ----------------------------------------
    def _get(self, notes: dict[str, Note], name: str) -> Note:
        name = name.strip().removesuffix(".md").strip("[]")
        if name not in notes:
            raise ValueError(f"заметки {name} нет — смотри имена в каталоге")
        return notes[name]

    # ----------------------------------------
    # Путь к файлу заметки
    # ----------------------------------------
    def _path(self, note: Note) -> Path:
        return self.notes_dir / note.folder / f"{note.name}.md"

    # ----------------------------------------
    # Короткая ссылка на заметку для интерфейса
    # ----------------------------------------
    def _ref(self, note: Note) -> dict[str, str]:
        return {"name": note.name, "title": note.title, "folder": note.folder, "summary": note.summary}

    # ----------------------------------------
    # Прежняя версия — в .history/ перед перезаписью
    # ----------------------------------------
    def _backup(self, path: Path) -> None:
        if path.exists():
            copy = self.root / HISTORY_DIR / f"{path.stem}_{datetime.now():%Y-%m-%d_%H%M%S_%f}.md"
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, copy)

    # ----------------------------------------
    # После любой правки: каталог заново, строка в журнал
    # ----------------------------------------
    def _changed(self, action: str, name: str, title: str, source: str = "") -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / INDEX_FILE).write_text(self.catalog(), encoding="utf-8", newline="\n")
        self._log(action, f"[[{name}]] — {title}" + (f" (из {source})" if source else ""))

    # ----------------------------------------
    # Строка журнала: «## [2026-10-26 15:30] создана | [[redis]] — Redis»
    # ----------------------------------------
    def _log(self, action: str, text: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / LOG_FILE).open("a", encoding="utf-8", newline="\n") as file:
            file.write(f"## [{datetime.now():%Y-%m-%d %H:%M}] {action} | {text}\n")


# ----------------------------------------
# Текст файла → (frontmatter словарём, тело)
# ----------------------------------------
def split(text: str) -> tuple[dict[str, str], str]:
    text = text.replace("\r\n", "\n").lstrip("﻿")
    match = FRONTMATTER.match(text)
    if not match:
        return {}, text
    meta = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip():
            meta[key.strip()] = value.strip().strip("\"'")
    return meta, text[match.end():].lstrip("\n")


# ----------------------------------------
# frontmatter + тело → текст файла; пустые поля не пишутся
# ----------------------------------------
def render(meta: dict[str, str], body: str) -> str:
    head = "\n".join(f"{key}: {one_line(value)}" for key, value in meta.items() if one_line(value))
    return f"---\n{head}\n---\n\n{body.strip()}\n"


# ----------------------------------------
# Тело → (текст без «См. также», имена из «См. также»)
# ----------------------------------------
def cut_see_also(body: str) -> tuple[str, list[str]]:
    head, sep, tail = body.partition(SEE_ALSO)
    if not sep:
        return body, []
    rest = re.search(r"^## ", tail, re.M)
    section, after = (tail[:rest.start()], tail[rest.start():]) if rest else (tail, "")
    return f"{head.rstrip()}\n\n{after}".strip(), [m.strip() for m in LINK.findall(section)]


# ----------------------------------------
# Тело + раздел «См. также» со ссылками (без повторов); ссылок нет — раздела нет
# ----------------------------------------
def with_see_also(body: str, links: list[str]) -> str:
    links = list(dict.fromkeys(links))
    return f"{body}\n\n{SEE_ALSO}\n\n" + "\n".join(f"- [[{name}]]" for name in links) if links else body


# ----------------------------------------
# Ссылки от модели (список или строка через запятую) → (есть в вики, нет в вики)
# ----------------------------------------
def split_links(links: list[str] | str, notes: dict[str, Note], own: str) -> tuple[list[str], list[str]]:
    items = links.split(",") if isinstance(links, str) else links
    names = [str(item).strip().strip("[]").removesuffix(".md") for item in items]
    names = [name for name in dict.fromkeys(names) if name and name != own]
    return [n for n in names if n in notes], [n for n in names if n not in notes]


# ----------------------------------------
# Итог для модели: ссылки на несуществующие заметки не ставятся — об этом говорится
# ----------------------------------------
def done(text: str, unknown: list[str]) -> str:
    return text + (f"; ссылок на несуществующие заметки не ставлю: {', '.join(unknown)}" if unknown else "")


# ----------------------------------------
# Раздел — одна папка внутри notes/: без «/», «..» и точки в начале
# ----------------------------------------
def clean_folder(folder: str) -> str:
    folder = one_line(folder).strip("/\\ ")
    if not folder or any(part in folder for part in ("/", "\\", "..")) or folder.startswith("."):
        raise ValueError(f"раздел «{folder}» — одно имя папки, например «Базы данных»")
    return folder


# ----------------------------------------
# Строка без переносов и лишних пробелов
# ----------------------------------------
def one_line(text: str) -> str:
    return " ".join(str(text).split())
