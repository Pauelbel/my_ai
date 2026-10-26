"""Инструменты агента вики: прочитать, найти, создать, дополнить, поправить заметку, вставить картинку.

Не регистрируются в общем реестре TOOLS — их видит только агент вики. Источник (сырой текст)
к заметке привязывает код: модель его не пересказывает и не копирует.
"""

from __future__ import annotations

from typing import Any, Callable

from agent_core.tools.base import Tool, Workspace, describe_signature, never
from wiki.store import Wiki


# ----------------------------------------
# Инструменты для одной вики; source() — путь к сырому тексту текущей задачи (или пусто)
# ----------------------------------------
def wiki_tools(wiki: Wiki, source: Callable[[], str]) -> dict[str, Tool]:

    # ----------------------------------------
    # Заметка целиком и кто на неё ссылается
    # ----------------------------------------
    def wiki_read(ws: Workspace, name: str) -> str:
        note = wiki.note(name)
        back = ", ".join(f"[[{ref['name']}]]" for ref in note["backlinks"]) or "нет"
        return f"{note['text']}\n[ссылаются на неё: {back}]"

    # ----------------------------------------
    # Поиск по названию, описанию и тексту
    # ----------------------------------------
    def wiki_search(ws: Workspace, query: str) -> str:
        hits = wiki.search(query)
        return "\n".join(f"- [[{hit['name']}]] — {hit['title']}: {hit['line']}" for hit in hits) or "ничего не найдено"

    # ----------------------------------------
    # Новая заметка в разделе folder
    # ----------------------------------------
    def wiki_create(ws: Workspace, folder: str, name: str, title: str, summary: str, text: str,
                    links: str = "") -> str:
        return wiki.create(folder, name, title, summary, text, as_list(links), source())

    # ----------------------------------------
    # Дописать в существующую заметку
    # ----------------------------------------
    def wiki_append(ws: Workspace, name: str, text: str, links: str = "") -> str:
        return wiki.append(name, text, as_list(links), source())

    # ----------------------------------------
    # Вставить присланную картинку (images/…) в конец заметки
    # ----------------------------------------
    def wiki_add_image(ws: Workspace, name: str, image: str, caption: str = "") -> str:
        return wiki.add_image(name, image, caption, source())

    # ----------------------------------------
    # Заменить фрагмент old на new
    # ----------------------------------------
    def wiki_edit(ws: Workspace, name: str, old: str, new: str) -> str:
        return wiki.edit(name, old, new)

    described = [
        (wiki_read, "Чтение заметки", "Текст заметки по имени из каталога и кто на неё ссылается"),
        (wiki_search, "Поиск по вики", "Ищет слова в названиях, описаниях и текстах заметок"),
        (wiki_create, "Новая заметка",
         "Создаёт заметку: folder — раздел по-русски, name — имя файла латиницей через дефис, title — название "
         "по-русски, summary — одна строка о чём она, text — Markdown без frontmatter, links — имена заметок "
         "из каталога через запятую"),
        (wiki_append, "Дополнение заметки",
         "Дописывает text в конец заметки name, старое не трогает; links — ещё связи через запятую"),
        (wiki_add_image, "Картинка в заметку",
         "Вставляет картинку image (images/… из строки «[картинки: …]») в конец заметки name; caption — подпись"),
        (wiki_edit, "Правка заметки", "Заменяет в заметке name фрагмент old (ровно как в тексте) на new"),
    ]
    return {func.__name__: Tool(func.__name__, description, func, never, describe_signature(func), title)
            for func, title, description in described}


# ----------------------------------------
# links от модели: строка через запятую или список
# ----------------------------------------
def as_list(links: Any) -> list[str]:
    if isinstance(links, list):
        return [str(item) for item in links]
    return [item for item in str(links or "").split(",") if item.strip()]
