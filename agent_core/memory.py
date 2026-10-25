"""Долговременная память memory/memory.md: короткие строки-факты."""

from __future__ import annotations

import re
import threading
from pathlib import Path

from .config import MEMORY_FILE


# ----------------------------------------
# Файл memory/memory.md: короткие строки-факты, общий для всех агентов
# ----------------------------------------
class Memory:

    # ----------------------------------------
    # Запоминает путь; замок нужен для фоновой записи
    # ----------------------------------------
    def __init__(self, path: Path = MEMORY_FILE) -> None:
        self.path = path
        self._lock = threading.Lock()

    # ----------------------------------------
    # Весь текст памяти
    # ----------------------------------------
    def read(self) -> str:
        return self.path.read_text(encoding="utf-8").strip() if self.path.exists() else ""

    # ----------------------------------------
    # Дописывает факт строкой «- …»; дубликаты пропускает
    # ----------------------------------------
    def add(self, fact: str) -> bool:
        fact = " ".join(fact.split()).lstrip("- ").strip()
        if not fact:
            return False
        with self._lock:
            known = {normalize_fact(line) for line in self.read().splitlines()}
            if normalize_fact(fact) in known:
                return False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as file:
                file.write(f"- {fact}\n")
        return True

    # ----------------------------------------
    # Факты списком: строки файла без маркера «- », пустые пропускаются
    # ----------------------------------------
    def facts(self) -> list[str]:
        return [line.strip().removeprefix("- ").strip() for line in self.read().splitlines() if line.strip()]

    # ----------------------------------------
    # Заменяет память целиком (редактор в интерфейсе): пустые строки и дубли выкидываются
    # ----------------------------------------
    def replace(self, facts: list[str]) -> list[str]:
        clean: list[str] = []
        seen: set[str] = set()
        for fact in facts:
            fact = " ".join(str(fact).split()).lstrip("- ").strip()
            if fact and normalize_fact(fact) not in seen:
                seen.add(normalize_fact(fact))
                clean.append(fact)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("".join(f"- {fact}\n" for fact in clean), encoding="utf-8")
        return clean


# ----------------------------------------
# Факт без регистра, маркера и знаков — для поиска дублей
# ----------------------------------------
def normalize_fact(text: str) -> str:
    return re.sub(r"[^\w ]", "", text.lower().lstrip("- ")).strip()


MEMORY_FILTER_PROMPT = """Ты фильтр долговременной памяти. Тебе дают одно сообщение пользователя.
Выпиши из него только долговременные факты, которые пользователь сам прямо сказал о себе, своей работе,
своих предпочтениях или принятых решениях. Каждый факт — короткая строка от третьего лица («Пользователь …»).
Нельзя: пересказывать задачу или просьбу, догадываться, делать выводы, сохранять разовое («сегодня», «сейчас»).
Почти всегда ответ — пустой список. Не повторяй то, что уже есть в памяти.
Верни JSON вида {"facts": [...]}."""
