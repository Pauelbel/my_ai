"""Распознавание речи через faster-whisper (локально, офлайн после первой загрузки модели)."""

from __future__ import annotations

import logging
import threading

import numpy as np

from .audio import SAMPLE_RATE

log = logging.getLogger(__name__)

# Образец грамотного текста: Whisper подстраивает под него пунктуацию и регистр
DEFAULT_INITIAL_PROMPT = ("Привет. Сегодня обсудили план: поправить тесты авторизации, "
                          "созвониться с командой и подготовить отчёт к пятнице.")


# ----------------------------------------
# Whisper: звук 16 кГц → текст; модель грузится лениво, на GPU без CUDA — откат на CPU
# ----------------------------------------
class Transcriber:

    # ----------------------------------------
    # Параметры модели из секции [voice] config.toml
    # ----------------------------------------
    def __init__(self, model: str = "small", language: str = "ru", device: str = "auto",
                 compute_type: str = "int8", initial_prompt: str = DEFAULT_INITIAL_PROMPT) -> None:
        self.model_name = model
        self.language = language
        self.compute_type = compute_type
        self.initial_prompt = initial_prompt
        self._device = device
        self._model = None
        self._lock = threading.Lock()

    # ----------------------------------------
    # Звук 16 кГц моно float32 → текст
    # ----------------------------------------
    def transcribe(self, audio: np.ndarray) -> str:
        return self._with_cpu_fallback(lambda: self._transcribe(audio, vad=True))

    # ----------------------------------------
    # Загружает модель и гоняет её на тишине: проблемы с GPU всплывут при старте, а не на первой фразе
    # ----------------------------------------
    def warmup(self) -> None:
        self._with_cpu_fallback(lambda: self._transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), vad=False))

    # ----------------------------------------
    # Модель из локального кэша; без кэша — скачивает один раз
    # ----------------------------------------
    def _load(self):
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                log.info("Загружаю Whisper %s (%s, %s)", self.model_name, self._device, self.compute_type)
                options = {"device": self._device, "compute_type": self.compute_type}
                try:
                    # Без local_files_only faster-whisper ходит на Hugging Face при каждом запуске
                    self._model = WhisperModel(self.model_name, local_files_only=True, **options)
                except FileNotFoundError:
                    log.info("Модели %s нет в кэше — скачиваю (только в первый раз)", self.model_name)
                    self._model = WhisperModel(self.model_name, **options)
            return self._model

    # ----------------------------------------
    # device = "auto" выбирает CUDA даже без cuBLAS/cuDNN — тогда повтор на CPU
    # ----------------------------------------
    def _with_cpu_fallback(self, run):
        try:
            return run()
        except RuntimeError as error:
            if self._device != "auto":
                raise
            log.warning("Whisper на GPU не запустился (%s), переключаюсь на CPU", error)
            with self._lock:
                self._device, self._model = "cpu", None
            return run()

    # ----------------------------------------
    # Один прогон модели; VAD режет тишину — меньше «галлюцинаций» на паузах
    # ----------------------------------------
    def _transcribe(self, audio: np.ndarray, vad: bool) -> str:
        segments, _info = self._load().transcribe(
            audio, language=self.language or None, initial_prompt=self.initial_prompt or None,
            beam_size=5, vad_filter=vad, condition_on_previous_text=False)
        return " ".join(segment.text.strip() for segment in segments).strip()

