"""Агент вики: тот же луп ядра, но свой короткий промпт, только инструменты вики и хвост разговора.

Без памяти, скиллов и быстрых команд. Длинное сообщение пользователя сохраняется в raw/ как источник
до того, как модель его увидит, — заметки, созданные по нему, ссылаются на этот файл.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Generator

from agent_core.agent import Agent
from agent_core.config import Config
from agent_core.events import Event
from agent_core.journal import Journal
from agent_core.prompts import SERVICE_HELP
from agent_core.schemas import NeedSkill
from wiki.store import Wiki
from wiki.tools import wiki_tools

CHAT_DIR = ".chat"  # журналы чата вики — внутри папки вики, а не в logs/ (там /продолжить основного чата)
CONTEXT_TURNS = 3  # модель видит столько последних сообщений пользователя с ответами
RAW_MIN_CHARS = 200  # сообщение длиннее — сырой текст, сохраняется в raw/
NO_SKILLS = "В вики я умею только читать, искать, создавать, дополнять и править заметки и вставлять картинки."
COMMANDS = "В чате вики: /модель <имя> — сменить модель, /новый — новый разговор, /стоп — прервать, /помощь — это."

WIKI_PROMPT = """Ты ведёшь личную вики пользователя — заметки в Markdown. Отвечай по-русски, коротко.
Каждый ответ — ровно одно действие в JSON:
- {{"action": {{"kind": "tool", "name": "...", "args": {{...}}}}}} — вызвать инструмент;
- {{"action": {{"kind": "answer", "text": "..."}}}} — ответить пользователю или задать ему вопрос.

Как работать:
- Делай то, что просит пользователь, — формат и разделы его. Не сказал — «## Что это» и 1–3 раздела по смыслу.
- Текст пользователя причеши: опечатки, абзацы, списки, таблицы. От себя — только если без этого непонятно.
  Не выдумывай факты, числа и ссылки.
- Перед новой заметкой посмотри каталог. Есть заметка на ту же тему — спроси, дополнить её или создать новую.
  Раздел не назван — предложи подходящий из каталога (или новый) и спроси.
- links — имена заметок из каталога, которые правда по теме. Обратные ссылки вики ставит сама.
- Сырой текст уже сохранён как источник, к заметке он привяжется сам — не копируй его в ответ.
- Картинки пользователя — в строке «[картинки: images/…]»: ты их видишь. В заметку — wiki_add_image
  или ![подпись](images/имя) прямо в text. Описывай только то, что правда на картинке.
- Вопрос по вики — wiki_search и wiki_read, ответ со ссылками [[имя]]. Чего в вики нет — так и скажи.
- «Проверь вики» — по каталогу найди дубли, заметки не в своём разделе и заметки без связей;
  предложи, что поправить, сам ничего не меняй.

Сегодня {date}. Источник текущего текста: {source}

Инструменты:
{tools}

{catalog}"""


# ----------------------------------------
# Агент чата вики
# ----------------------------------------
class WikiAgent(Agent):

    # ----------------------------------------
    # Папка проекта — папка вики; модель — своя, если задана в [wiki]
    # ----------------------------------------
    def __init__(self, config: Config, wiki: Wiki, model: str = "") -> None:
        super().__init__(replace(config, project_dir=wiki.root, auto_memory=False, model=model or config.model))
        self.wiki = wiki
        self.own_model = bool(model)  # своя модель из [wiki]: смена модели в настройках её не трогает
        self.source = ""  # raw/… последнего сырого текста: к нему привязываются новые заметки
        self._skills = {}
        self.commands = []
        self.toolset = wiki_tools(wiki, lambda: self.source)
        self.journal = self._new_journal()

    # ----------------------------------------
    # «Очистить»: новый разговор — свежий журнал, без источника и разрешений; заметки и прежние журналы остаются
    # ----------------------------------------
    def clear(self) -> None:
        self.journal = self._new_journal()
        self.source = ""
        self.approved.clear()
        self.started = time.monotonic()
        self.state, self.step = "idle", 0
        self._context_used = self._prompt_tokens = self._completion_tokens = 0
        self._speed = 0.0

    # ----------------------------------------
    # Журнал чата вики: <папка вики>/.chat/<дата>_<время><id>.jsonl (файл появится при первой записи)
    # ----------------------------------------
    def _new_journal(self) -> Journal:
        return Journal(self.wiki.root / CHAT_DIR / f"{datetime.now():%Y-%m-%d_%H%M%S}{secrets.token_hex(2)}.jsonl")

    # ----------------------------------------
    # Новые настройки подключения; своя модель вики остаётся
    # ----------------------------------------
    def configure(self, **changes: Any) -> None:
        if self.own_model:
            changes.pop("model", None)
        super().configure(**changes)

    # ----------------------------------------
    # Длинный текст — сначала в raw/; картинки — копией в images/ вики, их имена — строкой в сообщении; потом луп
    # ----------------------------------------
    def send(self, text: str, images: list[str] | None = None) -> Generator[Event, None, None]:
        if len(text.strip()) >= RAW_MIN_CHARS:
            self.source = self.wiki.save_raw(text)
            yield Event("info", f"Сырой текст сохранён: {self.source}")
        if images:
            copies = [self.wiki.save_image(Path(path)) for path in images]
            text = f"{text}\n[картинки: {', '.join(copies)}]"
            images = [str(self.wiki.root / copy) for copy in copies]
            yield Event("info", f"Картинки сохранены в вики: {', '.join(copies)}")
        yield from super().send(text, images)

    # ----------------------------------------
    # Служебные команды — свои (поле ввода общее с чатом); быстрые команды скиллов не работают; остальное — модели
    # ----------------------------------------
    def _dispatch(self, text: str, images: list[str]) -> Generator[Event, None, None]:
        command, _, arg = text.partition(" ")
        if not images and command.startswith("/") and command in {"/модель", "/новый", "/помощь"} | set(SERVICE_HELP):
            if command == "/модель" and arg.strip():
                self.config.model = arg.strip()
            elif command == "/новый":
                self.clear()
            yield Event("info", f"Модель: {self.config.model}" if command == "/модель"
                        else "Новый разговор." if command == "/новый" else COMMANDS)
            return
        self.journal.write({"type": "user", "text": text, **({"images": images} if images else {})})
        yield from self._loop(text, step=0)

    # ----------------------------------------
    # Свой промпт: инструменты вики, каталог; история — с CONTEXT_TURNS-го с конца сообщения пользователя
    # ----------------------------------------
    def _messages(self) -> list[dict[str, Any]]:
        tools = "\n".join(f"- {t.signature} — {t.description}" for t in self.toolset.values())
        catalog = self.wiki.catalog()
        if len(catalog) > self.config.output_limit:
            catalog = catalog[:self.config.output_limit] + "\n…[каталог обрезан — ищи через wiki_search]"
        system = WIKI_PROMPT.format(date=f"{datetime.now():%Y-%m-%d}", source=self.source or "нет",
                                    tools=tools, catalog=catalog)
        records = self.journal.records
        users = [index for index, record in enumerate(records) if record["type"] == "user"]
        start = users[-CONTEXT_TURNS] if len(users) > CONTEXT_TURNS else 0
        return [{"role": "system", "content": system}, *self.journal.messages(len(records) - start)]

    # ----------------------------------------
    # Модель попросила скилл — вернуть к инструментам вики (подсказка ядра про list_dir и run здесь не к месту)
    # ----------------------------------------
    def _try_tools_first(self, need: NeedSkill, forbidden: bool) -> Generator[Event, None, None]:
        self.journal.write({"type": "note", "text": "Новых скиллов в вики нет: обойдись инструментами wiki_* "
                                                    "или ответь пользователю (answer)."})
        yield Event("info", f"Модель хотела скилл {need.name} — прошу обойтись инструментами вики.")

    # ----------------------------------------
    # Новые скиллы в вики не пишутся: вместо этого — короткий ответ
    # ----------------------------------------
    def _need_skill(self, need: NeedSkill, task: str) -> Generator[Event, None, None]:
        yield from self._refuse()

    # ----------------------------------------
    # Инструмент дважды упал с той же ошибкой — тоже без нового скилла
    # ----------------------------------------
    def _force_need_skill(self, task: str, name: str, error: str) -> Generator[Event, None, None]:
        yield from self._refuse(f"{name} не получился: {error}")

    # ----------------------------------------
    # Ответ вместо скилла
    # ----------------------------------------
    def _refuse(self, reason: str = "") -> Generator[Event, None, None]:
        text = f"{reason}\n\n{NO_SKILLS}".strip()
        self.journal.write({"type": "answer", "text": text})
        self.journal.write({"type": "end"})
        yield Event("answer", text)
