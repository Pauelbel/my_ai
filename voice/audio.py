"""Запись с микрофона 16 кГц, уровень громкости для виджета и короткие звуки начала и конца записи."""

from __future__ import annotations

import logging
import threading
from typing import Callable

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000  # Whisper и Vosk ждут 16 кГц моно
LEVELS_PER_SEC = 30  # столько раз в секунду уходит уровень громкости


# ----------------------------------------
# Запись с микрофона: start() — пишет и шлёт уровень в on_level, stop() — возвращает звук 16 кГц float32
# ----------------------------------------
class Recorder:

    # ----------------------------------------
    # device — номер или имя микрофона sounddevice; None — по умолчанию
    # ----------------------------------------
    def __init__(self, device: int | str | None = None) -> None:
        self.device = device
        self._stream = None
        self._chunks: list[np.ndarray] = []
        self._rate = SAMPLE_RATE
        self._on_level: Callable[[float], None] | None = None
        self._on_block: Callable[[np.ndarray, int], None] | None = None
        self._lock = threading.Lock()

    # ----------------------------------------
    # Идёт ли запись
    # ----------------------------------------
    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    # ----------------------------------------
    # Открывает микрофон; если он не умеет 16 кГц — пишет в родной частоте
    # ----------------------------------------
    def start(self, on_level: Callable[[float], None] | None = None,
              on_block: Callable[[np.ndarray, int], None] | None = None) -> None:
        import sounddevice as sd

        with self._lock:
            if self._stream is not None:
                return
            self._chunks = []
            self._on_level = on_level
            self._on_block = on_block  # сырые куски звука и их частота — для живого распознавания
            try:
                stream = self._open(sd, SAMPLE_RATE)
            except sd.PortAudioError:
                rate = int(sd.query_devices(self.device, "input")["default_samplerate"])
                log.info("Микрофон не поддерживает %d Гц, пишу в %d Гц", SAMPLE_RATE, rate)
                stream = self._open(sd, rate)
            stream.start()
            self._stream = stream

    # ----------------------------------------
    # Останавливает запись и возвращает звук 16 кГц моно float32
    # ----------------------------------------
    def stop(self) -> np.ndarray:
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is None:
            return np.zeros(0, dtype=np.float32)
        stream.stop()
        stream.close()
        chunks, self._chunks = self._chunks, []
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return resample(np.concatenate(chunks), self._rate, SAMPLE_RATE)

    # ----------------------------------------
    # Поток sounddevice с блоками по ~1/30 с — по блоку на каждое значение уровня
    # ----------------------------------------
    def _open(self, sd, rate: int):
        stream = sd.InputStream(samplerate=rate, channels=1, dtype="float32", device=self.device,
                                blocksize=rate // LEVELS_PER_SEC, callback=self._callback)
        self._rate = rate
        return stream

    # ----------------------------------------
    # Блок звука: копия в запись, громкость — подписчику
    # ----------------------------------------
    def _callback(self, indata, frames, time_info, status) -> None:
        block = indata[:, 0].copy()
        self._chunks.append(block)
        if self._on_level:
            try:
                self._on_level(level(block))
            except Exception:
                log.debug("Ошибка обработчика уровня", exc_info=True)
        if self._on_block:
            self._on_block(block, self._rate)


# ----------------------------------------
# Громкость блока 0..1: RMS с запасом, чтобы обычная речь давала ~0.3–0.7
# ----------------------------------------
def level(block: np.ndarray) -> float:
    if len(block) == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(np.square(block, dtype=np.float64))))
    return min(1.0, rms * 8)


# ----------------------------------------
# Линейная пересэмплировка до частоты Whisper
# ----------------------------------------
def resample(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate or len(audio) == 0:
        return audio.astype(np.float32, copy=False)
    duration = len(audio) / src_rate
    target = np.linspace(0, duration, int(duration * dst_rate), endpoint=False)
    source = np.arange(len(audio)) / src_rate
    return np.interp(target, source, audio).astype(np.float32)


# ----------------------------------------
# Длительность звука 16 кГц в секундах
# ----------------------------------------
def duration_s(audio: np.ndarray) -> float:
    return len(audio) / SAMPLE_RATE


# (начальная частота, конечная частота) в Гц: на старте тон взлетает, на остановке опускается
CUES = {"start": (420.0, 880.0), "stop": (760.0, 380.0)}


# ----------------------------------------
# Мягкий «пук»: синус с быстрым скольжением высоты и экспоненциальным затуханием
# ----------------------------------------
def pop(rate: int, f_from: float, f_to: float, volume: float = 0.3, length_s: float = 0.09) -> np.ndarray:
    t = np.arange(int(rate * length_s)) / rate
    freq = f_to + (f_from - f_to) * np.exp(-t / 0.018)
    phase = 2 * np.pi * np.cumsum(freq) / rate
    tone = np.sin(phase) + 0.12 * np.sin(2 * phase)  # чуть второй гармоники — звук «круглее»
    attack = np.clip(t / 0.004, 0, 1) ** 2  # 4 мс нарастания — без щелчка в начале
    release = np.clip((length_s - t) / 0.015, 0, 1)  # и в конце
    wave = tone * attack * np.exp(-t / 0.028) * release
    return (wave / np.abs(wave).max() * volume).astype(np.float32)


# ----------------------------------------
# Звуки в фоне; нет колонок или звук сломан — работа не страдает
# ----------------------------------------
class SoundCues:

    # ----------------------------------------
    # Громкость 0..1; звуки строятся при первом проигрывании под частоту колонок
    # ----------------------------------------
    def __init__(self, volume: float = 0.3, enabled: bool = True) -> None:
        self.volume = min(max(volume, 0.0), 1.0)
        self.enabled = enabled
        self._rate: int | None = None
        self._sounds: dict[str, np.ndarray] = {}
        self._lock = threading.Lock()

    # ----------------------------------------
    # Проигрывает «start» или «stop» в отдельном потоке
    # ----------------------------------------
    def play(self, name: str) -> None:
        if self.enabled:
            threading.Thread(target=self._play, args=(name,), name="sound-cue", daemon=True).start()

    # ----------------------------------------
    # Само проигрывание; ошибки только в отладочный лог
    # ----------------------------------------
    def _play(self, name: str) -> None:
        try:
            import sounddevice as sd

            with self._lock:
                if self._rate is None:
                    self._rate = int(sd.query_devices(kind="output")["default_samplerate"])
                    self._sounds = {key: pop(self._rate, *freqs, self.volume) for key, freqs in CUES.items()}
                sd.play(self._sounds[name], self._rate)
        except Exception as error:
            log.debug("Звук «%s» не проигрался: %s", name, error)
