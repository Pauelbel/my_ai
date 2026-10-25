"""Живой черновик диктовки: Vosk распознаёт по ходу речи, пока Whisper ждёт конца записи."""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Callable

import numpy as np

from .audio import SAMPLE_RATE, resample

log = logging.getLogger(__name__)

UPDATE_S = 0.15  # черновик уходит подписчику не чаще ~7 раз в секунду


# ----------------------------------------
# Модель Vosk грузится один раз на процесс (~1 с) и переиспользуется
# ----------------------------------------
@lru_cache(maxsize=2)
def vosk_model(path: str):
    import vosk

    vosk.SetLogLevel(-1)
    return vosk.Model(path)


# ----------------------------------------
# Распознаёт куски звука в своём потоке; текст (готовые фразы + текущая) — в on_text
# ----------------------------------------
class LiveTranscript:

    # ----------------------------------------
    # model_path — папка модели Vosk; on_text вызывается из фонового потока
    # ----------------------------------------
    def __init__(self, model_path: Path, on_text: Callable[[str], None]) -> None:
        self.model_path = model_path
        self.on_text = on_text
        self._blocks: queue.Queue[tuple[np.ndarray, int] | None] = queue.Queue()
        self._done: list[str] = []
        self._sent = ""
        self._last = 0.0
        threading.Thread(target=self._run, name="live-transcript", daemon=True).start()

    # ----------------------------------------
    # Кусок звука с микрофона (float32, частота микрофона)
    # ----------------------------------------
    def feed(self, block: np.ndarray, rate: int) -> None:
        self._blocks.put((block, rate))

    # ----------------------------------------
    # Запись кончилась: поток дочитывает очередь и завершается
    # ----------------------------------------
    def stop(self) -> None:
        self._blocks.put(None)

    # ----------------------------------------
    # Поток: Vosk без грамматики, каждая законченная фраза копится, текущая — частичным результатом
    # ----------------------------------------
    def _run(self) -> None:
        try:
            import vosk

            recognizer = vosk.KaldiRecognizer(vosk_model(str(self.model_path)), SAMPLE_RATE)
        except Exception as error:
            log.warning("Живой черновик диктовки недоступен: %s", error)
            return
        while True:
            item = self._blocks.get()
            if item is None:
                return
            block, rate = item
            pcm = (np.clip(resample(block, rate, SAMPLE_RATE), -1, 1) * 32767).astype(np.int16).tobytes()
            if recognizer.AcceptWaveform(pcm):
                phrase = json.loads(recognizer.Result()).get("text", "")
                if phrase:
                    self._done.append(phrase)
                self._emit("", force=True)
            else:
                self._emit(json.loads(recognizer.PartialResult()).get("partial", ""))

    # ----------------------------------------
    # Текст подписчику, если он изменился и прошло UPDATE_S
    # ----------------------------------------
    def _emit(self, partial: str, force: bool = False) -> None:
        text = " ".join([*self._done, partial]).strip()
        if text == self._sent or (not force and time.monotonic() - self._last < UPDATE_S):
            return
        self._sent, self._last = text, time.monotonic()
        try:
            self.on_text(text)
        except Exception:
            log.debug("Ошибка подписчика черновика", exc_info=True)
