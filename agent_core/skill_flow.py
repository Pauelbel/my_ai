"""Как агент получает новый скилл: просит разрешения, пишет код моделью, показывает, ставит пакеты, повторяет задачу."""

from __future__ import annotations

from typing import Generator

from .config import SKILLS_DIR
from .events import Event
from .prompts import SKILL_PROMPT
from .schemas import NeedSkill, SkillCode
from .skills import load_skills, pip_install, save_skill


# ----------------------------------------
# Часть Agent: поток «нужен скилл → написать → подтвердить → установить → повторить задачу»
# ----------------------------------------
class SkillFlow:

    # ----------------------------------------
    # Одна и та же ошибка дважды подряд — модель обязана предложить скилл
    # ----------------------------------------
    def _force_need_skill(self, task: str, name: str, error: str) -> Generator[Event, None, None]:
        note = f"{name} дважды подряд дал одну и ту же ошибку: {error[:300]}. "
        if name in self._skills:
            note += f"Ошибка в коде скилла — верни need_skill с тем же именем {name}, чтобы его переписать."
        else:
            note += "Существующие инструменты не справляются — опиши скилл, который решит задачу."
        self.journal.write({"type": "note", "text": note})
        yield Event("warning", "Одна и та же ошибка дважды — прошу модель описать новый скилл.")
        try:
            action = self.model.ask(self._messages(), NeedSkill)
        except Exception as error:
            self.journal.write({"type": "end", "reason": "model_error"})
            yield Event("error", f"Модель не смогла описать скилл: {error}")
            return
        yield from self._need_skill(action, task)

    # ----------------------------------------
    # Нужен скилл: спросить, написать, показать код, поставить пакеты, перезапустить задачу
    # ----------------------------------------
    def _need_skill(self, need: NeedSkill, task: str) -> Generator[Event, None, None]:
        if not self.journal.records or self.journal.records[-1]["type"] != "need_skill":
            self.journal.write({"type": "need_skill", **need.model_dump(exclude={"kind"})})
        exists = " (заменит существующий, старый run.py → run.py.bak)" if need.name in self._skills else ""
        libs = ", ".join(need.libs) or "не нужны"
        answer = yield from self._ask_user("need_skill", f"Нужен скилл {need.name}{exists}: {need.what}\nПакеты: {libs}",
                                           need.model_dump())
        if answer == "no":
            self.journal.write({"type": "end", "reason": "skill_declined"})
            yield Event("answer", f"Без скилла {need.name} задачу не выполнить. Можно написать его позже.")
            return
        yield Event("info", f"Пишу скилл {need.name}…")
        try:
            code = self.model.ask(self._skill_messages(need, task), SkillCode, {"skill": need.name},
                                  effort=self.config.skill_reasoning_effort)
        except Exception as error:
            self.journal.write({"type": "end", "reason": "model_error"})
            yield Event("error", f"Модель не смогла написать скилл: {error}")
            return
        libs = " ".join(code.libs()) or "не нужен"
        shown = f"skills/{need.name}/SKILL.md\n{code.skill_md}\n\nskills/{need.name}/run.py\n{code.run_py}\n\npip install: {libs}"
        answer = yield from self._ask_user("confirm_request", shown,
                                           {"name": "new_skill", "skill": need.name, "libs": code.libs(),
                                            "skill_md": code.skill_md, "run_py": code.run_py})
        if answer == "no":
            self.journal.write({"type": "end", "reason": "skill_declined"})
            yield Event("answer", "Скилл не сохранён.")
            return
        yield from self._install_skill(need, code, task)

    # ----------------------------------------
    # Сохраняет скилл, ставит пакеты и запускает исходную задачу заново
    # ----------------------------------------
    def _install_skill(self, need: NeedSkill, code: SkillCode, task: str) -> Generator[Event, None, None]:
        folder = save_skill(need.name, code)
        yield Event("info", f"Скилл сохранён: {folder}")
        if code.libs():
            yield Event("info", f"pip install {' '.join(code.libs())}…")
            try:
                yield Event("info", pip_install(code.libs()))
            except Exception as error:
                self.journal.write({"type": "end", "reason": "pip_error"})
                yield Event("error", str(error))
                return
        self._skills = load_skills()
        self.journal.write({"type": "note", "text": f"Скилл {need.name} создан и есть в списке. Выполни исходную задачу."})
        self.journal.write({"type": "user", "text": task})
        yield Event("info", "Запускаю исходную задачу заново.")
        yield from self._loop(task, step=0)

    # ----------------------------------------
    # Промпт для написания скилла с образцом git_status
    # ----------------------------------------
    def _skill_messages(self, need: NeedSkill, task: str) -> list[dict[str, str]]:
        sample = SKILLS_DIR / "git_status"
        prompt = SKILL_PROMPT.format(
            sample_md=(sample / "SKILL.md").read_text(encoding="utf-8") if sample.exists() else "",
            sample_py=(sample / "run.py").read_text(encoding="utf-8") if sample.exists() else "",
            task=task, name=need.name, what=need.what, libs=", ".join(need.libs) or "нет",
            previous=self._previous_version(need.name))
        return [{"role": "user", "content": prompt}]

    # ----------------------------------------
    # Для переписывания: текущий run.py скилла и его последняя ошибка из журнала
    # ----------------------------------------
    def _previous_version(self, name: str) -> str:
        skill = self._skills.get(name)
        if skill is None:
            return ""
        code = (skill.folder / "run.py").read_text(encoding="utf-8")
        errors = [r["error"] for r in self.journal.records if r["type"] == "result" and r["name"] == name and r.get("error")]
        error = errors[-1][-1500:] if errors else "нет"
        return f"\nЭто переписывание существующего скилла. Текущий run.py:\n{code}\nПоследняя ошибка:\n{error}\n"
