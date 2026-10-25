"""Ядро агента: локальная модель через Ollama выбирает инструмент или скилл, код выполняет.

Своего интерфейса у ядра нет; интерфейсы (ui/) подключаются к контракту:
    from agent_core import Agent, Config
    agent = Agent(Config.load())
    for event in agent.send("прочитай README"):          # генератор событий Event(type, text, data, id)
        if event.type in ("confirm_request", "need_skill"):
            agent.confirm(event.id, "yes")                # "yes" | "no" | "session"; можно из другого потока
    agent.stop(); agent.status(); agent.skills(); agent.tools(); agent.history();
    agent.memory_facts(); agent.save_memory([...]); agent.close()

Модули: config — настройки и пути, schemas — схемы ответа модели, model — клиент Ollama, prompts — промпты,
tools — встроенные инструменты, browser — браузер Playwright, skills — скиллы, commands — быстрые команды,
journal — журнал JSONL, memory — долговременная память, events — Event и Status, agent — луп.
"""

from .agent import Agent
from .config import ROOT, Config
from .events import Event, Status
from .skills import SkillInfo
from .tools import TOOLS

__all__ = ["Agent", "Config", "Event", "Status", "SkillInfo", "TOOLS", "ROOT"]
