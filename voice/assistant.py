"""Голосовой помощник: запись → Whisper → чистка речи → буфер обмена (или поле ввода чата), имя-триггер, голосовой ответ.

Без UI и без ядра агента: состояния уходят в on_state, адрес Ollama и модель для чистки передаёт тот, кто создаёт.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import clipboard
from .audio import Recorder, SoundCues, duration_s
from .cleanup import clean_text
from .live import LiveTranscript
from .stt import DEFAULT_INITIAL_PROMPT, Transcriber
from .tts import Speaker
from .wake import create_listener

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent  # папка проекта: пути к моделям в config.toml — от неё
MIN_AUDIO_S = 0.3


@dataclass(frozen=True)
class _Recording:
    """Контекст одной записи, который не меняется при старте следующей."""

    token: int
    mode: str
    callback: Callable[[str], None] | None


# ----------------------------------------
# Голос: запись → Whisper → чистка → буфер обмена (или поле ввода чата); состояния — в виджет
# ----------------------------------------
class VoiceAssistant:

    # ----------------------------------------
    # settings — секция [voice] config.toml; base_url и model — Ollama для чистки речи (из конфига ядра)
    # ----------------------------------------
    def __init__(self, settings: dict[str, Any], base_url: str, model: str) -> None:
        self.settings = settings
        self.base_url = base_url
        self.model = model
        device = settings.get("mic_device")
        self.recorder = Recorder(device)
        self.cues = SoundCues(settings.get("volume", 0.3), settings.get("sounds", True))
        self.transcriber = Transcriber(settings.get("whisper_model", "small"), settings.get("language", "ru"),
                                       settings.get("device", "auto"), settings.get("compute_type", "int8"),
                                       settings.get("initial_prompt", DEFAULT_INITIAL_PROMPT))
        self.reply = str(settings.get("reply", "Да, слушаю."))
        voice = ROOT / Path(settings.get("tts_voice", "voice/models/piper/ru_RU-irina-medium.onnx")).expanduser()
        self.speaker = Speaker(voice, float(settings.get("tts_volume", 0.8)), float(settings.get("tts_pitch", 3)))
        self.on_state: Callable[..., None] | None = None  # состояние для виджета: state, level, text, wake
        self.last_text = ""
        self.wake = None
        self._recording: _Recording | None = None
        self._generation = 0
        self._last_result_generation = 0
        self._speech = False
        self._quiet_since = 0.0
        self._started = 0.0
        self._stopping = False
        self._live: LiveTranscript | None = None
        self._lock = threading.Lock()
        self._chat = self._cleanup_client(base_url, model)

    # ----------------------------------------
    # Новое подключение ядра: клиент чистки хранит адрес и модель внутри, поэтому его тоже нужно пересоздать
    # ----------------------------------------
    def configure(self, base_url: str, model: str) -> None:
        chat = self._cleanup_client(base_url, model)
        with self._lock:
            self.base_url, self.model, self._chat = base_url, model, chat

    # ----------------------------------------
    # Идёт ли запись
    # ----------------------------------------
    @property
    def recording(self) -> bool:
        return self.recorder.is_recording

    # ----------------------------------------
    # Клик по виджету или имя агента: старт записи в буфер; повтор — стоп
    # ----------------------------------------
    def toggle(self) -> None:
        if self.recording:
            self.stop()
        else:
            self.start("clipboard")

    # ----------------------------------------
    # Кнопка микрофона в чате: блокирует до готового текста; повторный вызов останавливает запись
    # ----------------------------------------
    def dictate(self, on_partial: Callable[[str], None] | None = None) -> str | None:
        if self.recording:
            self.stop()
            return None
        done = threading.Event()
        box: dict[str, str] = {}
        self.start("dictate", lambda text: (box.update(text=text), done.set()), live=on_partial)
        done.wait(timeout=float(self.settings.get("max_record_s", 120)) + 60)
        return box.get("text")

    # ----------------------------------------
    # Старт записи: триггер на паузу, голосовой ответ (greet), микрофон, звук «start»
    # ----------------------------------------
    def start(self, mode: str, on_result: Callable[[str], None] | None = None, greet: bool = False,
              live: Callable[[str], None] | None = None) -> None:
        error: Exception | None = None
        failed_live: LiveTranscript | None = None
        with self._lock:
            if self.recording:
                return
            if self.wake is not None:
                self.wake.pause()
            if greet and self.reply:
                self._greet()
            self._generation += 1
            recording = _Recording(self._generation, mode, on_result)
            self._recording = recording
            self._speech, self._quiet_since, self._started = False, 0.0, time.monotonic()
            self._stopping = False
            self._live = LiveTranscript(self._vosk_path(), live) if live else None  # черновик по ходу речи
            try:
                self.recorder.start(self._on_level, self._live.feed if self._live else None)
            except Exception as caught:
                error = caught
                failed_live, self._live = self._live, None
                self._recording = None
        if error is not None:
            if failed_live:
                failed_live.stop()
            if self.wake is not None:
                self.wake.resume()
            log.error("Микрофон не открылся", exc_info=(type(error), error, error.__traceback__))
            self._finish("", f"Микрофон недоступен: {error}", recording=recording)
            return
        self.cues.play("start")
        self._show_for(recording, state="recording", level=0.0, text="")

    # ----------------------------------------
    # Стоп записи: звук «stop», обработка в фоне
    # ----------------------------------------
    def stop(self) -> None:
        error: Exception | None = None
        with self._lock:
            if not self.recording:
                return
            recording = self._recording
            try:
                audio = self.recorder.stop()
            except Exception as caught:
                error = caught
                audio = None
            live, self._live = self._live, None
            self._recording = None
        if live:
            live.stop()
        self.cues.play("stop")  # микрофон уже закрыт — звук в запись не попадёт
        if self.wake is not None:
            self.wake.resume()
        if recording is None:
            return
        if error is not None:
            log.error("Микрофон не остановился", exc_info=(type(error), error, error.__traceback__))
            self._finish("", f"Ошибка микрофона: {error}", recording=recording)
            return
        self._show_for(recording, state="processing", level=0.0)
        threading.Thread(target=self._process, args=(audio, recording), name="voice", daemon=True).start()

    # ----------------------------------------
    # Включает или выключает фоновое прослушивание имени агента
    # ----------------------------------------
    def set_wake(self, on: bool) -> dict[str, Any]:
        try:
            if on:
                if self.wake is None:
                    self.wake = self._wake_listener()
                self.wake.start()
                if self.recording:
                    self.wake.pause()
            elif self.wake is not None:
                self.wake.stop()
        except Exception as error:
            log.exception("Слово-триггер не запустился")
            self._show(state="error", text=f"Триггер не запустился: {error}", wake=False)
            return {"ok": False, "error": str(error)}
        self._show(wake=on)
        return {"ok": True, "wake": on}

    # ----------------------------------------
    # Загружает Whisper заранее, чтобы первая фраза не ждала
    # ----------------------------------------
    def warmup(self) -> None:
        try:
            self.transcriber.warmup()
        except Exception as error:
            log.exception("Whisper не загрузился")
            self._show(state="error", text=f"Whisper не загрузился: {error}")
        try:
            self.speaker.prepare(self.reply)
        except Exception as error:
            log.warning("Голосовой ответ недоступен: %s", error)

    # ----------------------------------------
    # «Да, слушаю» голосом до записи — чтобы ответ не попал в микрофон; кольцо пульсирует в такт голосу
    # ----------------------------------------
    def _greet(self) -> None:
        self._show(state="speaking", level=0.0, text="")
        try:
            self.speaker.say(self.reply, lambda value: self._show(level=value))
        except Exception as error:
            log.warning("Голосовой ответ не прозвучал: %s", error)

    # ----------------------------------------
    # Уровень громкости ~30 раз/с: в виджет и для остановки по тишине
    # ----------------------------------------
    def _on_level(self, value: float) -> None:
        self._show(level=value)
        now = time.monotonic()
        if value >= float(self.settings.get("silence_level", 0.06)):
            self._speech, self._quiet_since = True, 0.0
            return
        self._quiet_since = self._quiet_since or now
        silence = float(self.settings.get("silence_s", 1.5))
        too_long = now - self._started > float(self.settings.get("max_record_s", 120))
        nothing_said = not self._speech and now - self._started > silence + 3.5
        if not self._stopping and ((self._speech and now - self._quiet_since >= silence) or nothing_said or too_long):
            self._stopping = True
            threading.Thread(target=self.stop, daemon=True).start()

    # ----------------------------------------
    # Конвейер после записи: Whisper → чистка (без Ollama — сырой текст) → буфер или поле ввода
    # ----------------------------------------
    def _process(self, audio, recording: _Recording) -> None:
        if duration_s(audio) < MIN_AUDIO_S:
            self._finish("", "Слишком коротко — ничего не записал", recording=recording)
            return
        try:
            raw = self.transcriber.transcribe(audio)
        except Exception as error:
            log.exception("Ошибка распознавания")
            self._finish("", f"Ошибка распознавания: {error}", recording=recording)
            return
        if not raw:
            self._finish("", "Речь не распознана", recording=recording)
            return
        note = ""
        text = raw
        # Диктовка в чат — ваши слова как есть: агент их и так поймёт, а чистка моделью может подменить их ответом
        dictating = recording.mode == "dictate"
        if self.settings.get("cleanup", True) and not (dictating and not self.settings.get("dictate_cleanup", False)):
            try:
                with self._lock:
                    chat = self._chat
                text = clean_text(raw, chat)
            except Exception as error:
                log.warning("Чистка не удалась, оставляю сырой текст: %s", error)
                note = "Ollama недоступна — текст без чистки"
        log.info("Whisper: %s | чистый: %s", raw, text)
        with self._lock:
            if recording.token >= self._last_result_generation:
                self.last_text = text
                self._last_result_generation = recording.token
        if recording.mode == "dictate":
            self._finish(text, "", "в поле", recording=recording)
            return
        try:
            clipboard.copy(text)
        except Exception as error:
            self._finish("", str(error), recording=recording)
            return
        self._finish(text, "", "скопировано", note, recording)

    # ----------------------------------------
    # Итог: «готово» или «ошибка» в виджет, текст — ждущему dictate()
    # ----------------------------------------
    def _finish(self, text: str, error: str, label: str = "", note: str = "",
                recording: _Recording | None = None) -> None:
        if error:
            self._show_for(recording, state="error", text=error, level=0.0)
        else:
            self._show_for(recording, state="done", text=label, note=note, level=0.0)
        if recording and recording.callback:
            try:
                recording.callback(text)
            except Exception:
                log.exception("Ошибка обработчика результата диктовки")

    # ----------------------------------------
    # Старый фоновый результат не должен затирать состояние уже начавшейся записи
    # ----------------------------------------
    def _show_for(self, recording: _Recording | None, **values: Any) -> None:
        with self._lock:
            current = recording is None or recording.token == self._generation
        if current:
            self._show(**values)

    # ----------------------------------------
    # Состояние подписчику (виджету), если он есть
    # ----------------------------------------
    def _show(self, **values: Any) -> None:
        if self.on_state is not None:
            try:
                self.on_state(**values)
            except Exception:
                log.debug("Ошибка подписчика состояния голоса", exc_info=True)

    # ----------------------------------------
    # Клиент Ollama для чистки: OpenAI-совместимый адрес ядра, без размышлений модели
    # ----------------------------------------
    def _cleanup_client(self, base_url: str, model: str) -> Callable[[list[dict[str, str]], int], str]:
        from openai import OpenAI

        client = OpenAI(base_url=base_url, api_key="ollama",
                        timeout=float(self.settings.get("cleanup_timeout", 30)), max_retries=0)
        cleanup_model = self.settings.get("cleanup_model") or model

        # ----------------------------------------
        # Один ответ модели без стриминга
        # ----------------------------------------
        def chat(messages: list[dict[str, str]], max_tokens: int) -> str:
            reply = client.chat.completions.create(model=cleanup_model, messages=messages, temperature=0,
                                                   max_tokens=max_tokens,
                                                   extra_body={"reasoning_effort": "none"})
            return reply.choices[0].message.content or ""

        return chat

    # ----------------------------------------
    # Папка модели Vosk — та же, что у имени-триггера (для живого черновика диктовки)
    # ----------------------------------------
    def _vosk_path(self) -> Path:
        default = "voice/models/vosk-model-small-ru-0.22"
        return ROOT / Path(self.settings.get("live_model", default)).expanduser()

    # ----------------------------------------
    # Слушатель имени агента по настройкам [voice]
    # ----------------------------------------
    def _wake_listener(self):
        engine = self.settings.get("wake_engine", "vosk")
        default = "voice/models/vosk-model-small-ru-0.22" if engine == "vosk" else "voice/models/wake.onnx"
        model = ROOT / Path(self.settings.get("wake_model", default)).expanduser()
        return create_listener(engine, self._on_wake, model, words=list(self.settings.get("wake_words", ["ася"])),
                               confidence=float(self.settings.get("wake_confidence", 0.85)),
                               cooldown_s=float(self.settings.get("wake_cooldown_s", 2.0)),
                               gate=float(self.settings.get("wake_gate", 0.04)),
                               device=self.settings.get("mic_device"))

    # ----------------------------------------
    # Имя агента услышано — голосовой ответ и запись, если ещё не пишем
    # ----------------------------------------
    def _on_wake(self) -> None:
        if not self.recording:
            self.start("clipboard", greet=True)
