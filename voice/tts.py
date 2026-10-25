"""Голосовой ответ: готовые фразы из voice/phrases/ (голос по описанию, ~/my_projects/my_voice_ai), остальное — Piper."""

from __future__ import annotations

import logging
import json
import threading
import time
import wave
from pathlib import Path
from typing import Callable

import numpy as np

from .audio import LEVELS_PER_SEC, level, resample

log = logging.getLogger(__name__)

PHRASES = Path(__file__).resolve().parent / "phrases"  # готовые WAV + index.json {"phrases": {текст: файл}}


# ----------------------------------------
# Синтез и проигрывание фраз; готовые фразы кэшируются — повторный ответ без задержки
# ----------------------------------------
class Speaker:

    # ----------------------------------------
    # model_path — файл голоса .onnx (рядом .onnx.json); volume — 0..1; pitch — полутоны вверх (звонче)
    # ----------------------------------------
    def __init__(self, model_path: Path, volume: float = 0.8, pitch: float = 0.0) -> None:
        self.model_path = model_path
        self.volume = min(max(volume, 0.0), 1.0)
        self.pitch = pitch
        self._voice = None
        self._cache: dict[str, tuple[np.ndarray, int]] = {}
        self._lock = threading.Lock()

    # ----------------------------------------
    # Загружает голос и синтезирует фразы заранее, чтобы первый ответ не ждал
    # ----------------------------------------
    def prepare(self, *phrases: str) -> None:
        for phrase in phrases:
            if phrase:
                self._synthesize(phrase)

    # ----------------------------------------
    # Произносит фразу и ждёт конца; громкость голоса ~30 раз/с — в on_level (для кольца виджета)
    # ----------------------------------------
    def say(self, text: str, on_level: Callable[[float], None] | None = None) -> None:
        import sounddevice as sd

        audio, rate = self._synthesize(text)
        sd.play(audio * self.volume, rate)
        step = rate // LEVELS_PER_SEC
        started = time.monotonic()
        for index in range(0, len(audio), step):
            if on_level:
                on_level(level(audio[index:index + step]))
            delay = started + index / rate - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        sd.wait()

    # ----------------------------------------
    # Текст → звук float32 и частота; голос грузится при первой фразе
    # ----------------------------------------
    def _synthesize(self, text: str) -> tuple[np.ndarray, int]:
        with self._lock:
            if text in self._cache:
                return self._cache[text]
            ready = prerendered(text)
            if ready is not None:
                self._cache[text] = ready
                return ready
            if self._voice is None:
                from piper import PiperVoice

                if not self.model_path.is_file():
                    raise FileNotFoundError(f"нет голоса Piper: {self.model_path}")
                log.info("Загружаю голос %s", self.model_path.name)
                self._voice = PiperVoice.load(str(self.model_path))
            from piper.config import SynthesisConfig

            # Тон выше без ускорения: речь синтезируется медленнее в ratio раз и сжимается обратно
            ratio = 2 ** (self.pitch / 12)
            config = SynthesisConfig(length_scale=(self._voice.config.length_scale or 1.0) * ratio)
            chunks = list(self._voice.synthesize(text, config))
            rate = chunks[0].sample_rate
            audio = np.concatenate([chunk.audio_float_array for chunk in chunks]).astype(np.float32)
            self._cache[text] = (resample(audio, int(rate * ratio), rate), rate)
            return self._cache[text]


# ----------------------------------------
# Готовая фраза из voice/phrases/ (озвучена голосом по описанию); нет — None
# ----------------------------------------
def prerendered(text: str, folder: Path = PHRASES) -> tuple[np.ndarray, int] | None:
    try:
        name = json.loads((folder / "index.json").read_text(encoding="utf-8"))["phrases"][text]
        with wave.open(str(folder / name)) as file:
            frames = file.readframes(file.getnframes())
            rate, width, channels = file.getframerate(), file.getsampwidth(), file.getnchannels()
    except (OSError, ValueError, KeyError, wave.Error):
        return None
    if width != 2:
        log.warning("%s: ожидался 16-битный WAV", name)
        return None
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768
    return audio.reshape(-1, channels).mean(axis=1) if channels > 1 else audio, rate
