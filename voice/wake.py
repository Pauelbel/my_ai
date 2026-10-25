"""Слово-триггер (имя агента, по умолчанию «Ася»): фоновое офлайн-прослушивание микрофона (Vosk; запасной вариант — openWakeWord)."""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

import numpy as np

from .audio import SAMPLE_RATE, level

log = logging.getLogger(__name__)

WAKE_WORDS = ["ася"]
# Похожие и частые слова: Vosk выбирает их, а не имя, — без них короткое «ася» ловится в «Вася», «я», «ясно»
DECOYS = ["вася", "я", "ясно", "сейчас", "сегодня", "наша", "ваша", "настя", "раз", "да", "нет", "так", "там",
          "хорошо", "давай", "ещё", "здравствуйте", "привет", "как", "это", "всё", "сам", "сама", "нас", "вас"]
BLOCK_S = 0.25  # кусок звука за раз: Vosk не нужно чаще
QUIET_S = 1.0  # столько тишины — и распознаватель отдыхает, пока снова не станет громко
PARTIAL_STREAK = 2  # кусков подряд со словом в промежуточном результате — срабатывание до конца фразы
PARTIAL_WINDOW = 6  # …и только в первых кусках фразы: обращение стоит в начале, ложное слово — в середине


# ----------------------------------------
# Фоновое прослушивание: свой поток, свой микрофонный поток; pause() отдаёт микрофон записи
# ----------------------------------------
class WakeListener:

    # ----------------------------------------
    # on_wake вызывается из фонового потока; gate — уровень громкости, ниже которого звук не распознаётся
    # ----------------------------------------
    def __init__(self, on_wake: Callable[[], None], confidence: float = 0.85, cooldown_s: float = 2.0,
                 gate: float = 0.04, device: int | str | None = None) -> None:
        self.on_wake = on_wake
        self.confidence = confidence
        self.cooldown_s = cooldown_s
        self.gate = gate
        self.device = device
        self._blocks: queue.Queue[np.ndarray | None] = queue.Queue()
        self._stream = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._paused = False
        self._last_wake = 0.0
        self._lock = threading.Lock()

    # ----------------------------------------
    # Слушает ли сейчас (включено и не на паузе)
    # ----------------------------------------
    @property
    def active(self) -> bool:
        return self._running and not self._paused

    # ----------------------------------------
    # Включает прослушивание: грузит модель и открывает микрофон
    # ----------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._prepare()
            self._running = True
            self._thread = threading.Thread(target=self._run, name="wake", daemon=True)
            self._thread.start()
            if not self._paused:
                self._open()

    # ----------------------------------------
    # Выключает прослушивание совсем
    # ----------------------------------------
    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._close()
            self._blocks.put(None)

    # ----------------------------------------
    # Пауза на время записи: микрофон закрывается
    # ----------------------------------------
    def pause(self) -> None:
        with self._lock:
            self._paused = True
            self._close()

    # ----------------------------------------
    # Конец паузы: микрофон снова открыт, отсчёт защиты от повторов — заново
    # ----------------------------------------
    def resume(self) -> None:
        with self._lock:
            self._paused = False
            self._last_wake = time.monotonic()
            if self._running and self._stream is None:
                self._open()

    # ----------------------------------------
    # Поток sounddevice int16 16 кГц кусками по BLOCK_S
    # ----------------------------------------
    def _open(self) -> None:
        import sounddevice as sd

        self._reset()
        self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", device=self.device,
                                      blocksize=int(SAMPLE_RATE * BLOCK_S),
                                      callback=lambda data, *_: self._blocks.put(data[:, 0].copy()))
        self._stream.start()

    # ----------------------------------------
    # Закрывает микрофонный поток, если открыт
    # ----------------------------------------
    def _close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()

    # ----------------------------------------
    # Разбор кусков: тишина распознавателю не отдаётся — так фон почти не ест CPU
    # ----------------------------------------
    def _run(self) -> None:
        preroll: deque[np.ndarray] = deque(maxlen=2)
        quiet_since = 0.0
        hearing = False
        while True:
            block = self._blocks.get()
            if block is None:
                return
            try:
                if not self.active:
                    continue
                loud = level(block.astype(np.float32) / 32768) >= self.gate
                now = time.monotonic()
                if loud:
                    quiet_since = 0.0
                    if not hearing:
                        hearing = True
                        for old in preroll:
                            self._feed(old)
                        preroll.clear()
                elif hearing:
                    quiet_since = quiet_since or now
                    if now - quiet_since > QUIET_S:
                        hearing = False
                        self._flush()
                if hearing:
                    self._feed(block)
                else:
                    preroll.append(block)
            except Exception:
                # Один сбойный кусок не должен глушить прослушивание до перезапуска
                log.exception("Ошибка распознавания слова-триггера, слушаю дальше")
                hearing = False
                preroll.clear()

    # ----------------------------------------
    # Срабатывание: не чаще раза в cooldown_s
    # ----------------------------------------
    def _fire(self, score: float) -> None:
        now = time.monotonic()
        if now - self._last_wake < self.cooldown_s or not self.active:
            return
        self._last_wake = now
        log.info("Слово-триггер (уверенность %.2f)", score)
        try:
            self.on_wake()
        except Exception:
            log.exception("Ошибка обработчика слова-триггера")

    # ----------------------------------------
    # Движок распознавания: загрузка модели
    # ----------------------------------------
    def _prepare(self) -> None:
        raise NotImplementedError

    # ----------------------------------------
    # Движок: сброс состояния перед новым прослушиванием
    # ----------------------------------------
    def _reset(self) -> None:
        raise NotImplementedError

    # ----------------------------------------
    # Движок: очередной кусок звука
    # ----------------------------------------
    def _feed(self, block: np.ndarray) -> None:
        raise NotImplementedError

    # ----------------------------------------
    # Движок: наступила тишина — дораспознать хвост
    # ----------------------------------------
    def _flush(self) -> None:
        pass


# ----------------------------------------
# Vosk в режиме грамматики: имя, слова-приманки или «[unk]»
# ----------------------------------------
class VoskWake(WakeListener):

    # ----------------------------------------
    # model_path — папка маленькой русской модели Vosk; words — имя агента (можно несколько написаний)
    # ----------------------------------------
    def __init__(self, on_wake: Callable[[], None], model_path: Path, words: list[str] | None = None,
                 **options) -> None:
        super().__init__(on_wake, **options)
        self.model_path = model_path
        self.words = [word.lower() for word in (words or WAKE_WORDS)]
        self._model = None
        self._recognizer = None
        self._streak = 0  # сколько кусков подряд слово есть в промежуточном результате
        self._fed = 0  # кусков с начала текущей фразы

    # ----------------------------------------
    # Модель грузится один раз (~1 с)
    # ----------------------------------------
    def _prepare(self) -> None:
        if self._model is not None:
            return
        import vosk

        if not (self.model_path / "am").is_dir():
            raise FileNotFoundError(f"нет модели Vosk в {self.model_path} — скачай vosk-model-small-ru "
                                    "с alphacephei.com/vosk/models и распакуй туда")
        vosk.SetLogLevel(-1)
        self._model = vosk.Model(str(self.model_path))
        self._reset()

    # ----------------------------------------
    # Новый распознаватель с грамматикой и уверенностью по словам
    # ----------------------------------------
    def _reset(self) -> None:
        import vosk

        grammar = [*self.words, *(word for word in DECOYS if word not in self.words), "[unk]"]
        self._recognizer = vosk.KaldiRecognizer(self._model, SAMPLE_RATE, json.dumps(grammar, ensure_ascii=False))
        self._recognizer.SetWords(True)
        self._streak = self._fed = 0

    # ----------------------------------------
    # Кусок звука; слово в промежуточном результате — срабатывание сразу, не дожидаясь конца фразы
    # ----------------------------------------
    def _feed(self, block: np.ndarray) -> None:
        if self._recognizer.AcceptWaveform(block.tobytes()):
            self._check(self._recognizer.Result())
            self._fed = self._streak = 0  # конец фразы — дальше новая
            return
        self._fed += 1
        partial = json.loads(self._recognizer.PartialResult()).get("partial", "")
        heard = any(word in partial.split() for word in self.words)
        self._streak = self._streak + 1 if heard else 0
        # У промежуточного результата нет уверенности: ложное слово мелькает в одном куске, настоящее держится
        if self._streak >= PARTIAL_STREAK and self._fed <= PARTIAL_WINDOW:
            self._fire(1.0)

    # ----------------------------------------
    # Тишина: забирает недоговорённый хвост
    # ----------------------------------------
    def _flush(self) -> None:
        self._check(self._recognizer.FinalResult())
        self._fed = self._streak = 0

    # ----------------------------------------
    # Ищет в результате слово-триггер с уверенностью выше порога
    # ----------------------------------------
    def _check(self, result: str) -> None:
        for word in json.loads(fix_decimal_comma(result)).get("result", []):
            if word.get("word") in self.words and word.get("conf", 0) >= self.confidence:
                self._fire(word["conf"])
                return


# ----------------------------------------
# Запасной движок: openWakeWord со своей моделью слова (.onnx/.tflite); pip install openwakeword
# ----------------------------------------
class OpenWakeWord(WakeListener):

    # ----------------------------------------
    # model_path — файл обученной модели имени агента
    # ----------------------------------------
    def __init__(self, on_wake: Callable[[], None], model_path: Path, words: list[str] | None = None,
                 **options) -> None:
        super().__init__(on_wake, **options)
        self.model_path = model_path
        self._model = None

    # ----------------------------------------
    # Загрузка модели слова
    # ----------------------------------------
    def _prepare(self) -> None:
        if self._model is not None:
            return
        from openwakeword.model import Model

        if not self.model_path.is_file():
            raise FileNotFoundError(f"нет модели openWakeWord: {self.model_path}")
        framework = "onnx" if self.model_path.suffix == ".onnx" else "tflite"
        self._model = Model(wakeword_models=[str(self.model_path)], inference_framework=framework)

    # ----------------------------------------
    # Сброс буферов предсказаний
    # ----------------------------------------
    def _reset(self) -> None:
        if self._model is not None:
            self._model.reset()

    # ----------------------------------------
    # Предсказание по кускам 80 мс; оценка выше порога — срабатывание
    # ----------------------------------------
    def _feed(self, block: np.ndarray) -> None:
        for start in range(0, len(block) - 1279, 1280):
            scores = self._model.predict(block[start:start + 1280])
            score = max(scores.values(), default=0.0)
            if score >= self.confidence:
                self._fire(score)


# ----------------------------------------
# Vosk печатает числа по локали процесса: Qt включает русскую — и в JSON появляется «1,000000»
# ----------------------------------------
def fix_decimal_comma(text: str) -> str:
    return re.sub(r"(\d),(\d)", r"\1.\2", text)


# ----------------------------------------
# Слушатель по настройке wake_engine: "vosk" (по умолчанию) или "openwakeword"
# ----------------------------------------
def create_listener(engine: str, on_wake: Callable[[], None], model_path: Path, **options) -> WakeListener:
    if engine == "openwakeword":
        return OpenWakeWord(on_wake, model_path, **options)
    return VoskWake(on_wake, model_path, **options)
