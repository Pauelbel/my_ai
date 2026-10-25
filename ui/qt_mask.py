"""Маска окна Qt: кликабельна только видимая часть виджета (голова, открытое меню), остальное — сквозное.

Окно виджета на Qt создаётся сразу с запасом места под меню и больше не двигается при его открытии:
меню появляется сменой маски, без resize и move — поэтому ничего не дёргается.
Окно неуправляемое (X11BypassWindowManagerHint): иначе KWin прижимает большое окно к краю экрана,
и голову нельзя поставить в угол. Поверх остальных окон оно остаётся.
"""

from __future__ import annotations

from typing import Any

from PyQt6 import QtCore, QtGui


# ----------------------------------------
# Ставит маску окну из любого потока: сама смена идёт в потоке интерфейса Qt
# ----------------------------------------
class WindowMask(QtCore.QObject):
    changed = QtCore.pyqtSignal(object, object)

    # ----------------------------------------
    # Объект переезжает в поток интерфейса, иначе слот выполнялся бы в чужом потоке
    # ----------------------------------------
    def __init__(self, window: Any) -> None:
        super().__init__()
        from webview.platforms.qt import BrowserView

        self._view = BrowserView.instances[window.uid]
        self.moveToThread(self._view.thread())
        self.changed.connect(self._apply)

    # ----------------------------------------
    # Видимые области: круги (x, y, d) и прямоугольники (x, y, w, h) в координатах окна
    # ----------------------------------------
    def set(self, circles: list[tuple[int, int, int]], rects: list[tuple[int, int, int, int]] = ()) -> None:
        region = QtGui.QRegion()
        for x, y, d in circles:
            region = region.united(QtGui.QRegion(QtCore.QRect(x, y, d, d), QtGui.QRegion.RegionType.Ellipse))
        for x, y, w, h in rects:
            region = region.united(QtGui.QRegion(QtCore.QRect(x, y, w, h)))
        self.changed.emit(self._view, region)

    # ----------------------------------------
    # Сама смена маски — в потоке интерфейса
    # ----------------------------------------
    @QtCore.pyqtSlot(object, object)
    def _apply(self, view: Any, region: QtGui.QRegion) -> None:
        view.setMask(region)


# ----------------------------------------
# Окно — неуправляемым: только до первого показа (в before_show, поток интерфейса).
# Флаг на уже показанном окне Qt пересоздаёт его, и под XWayland оно перестаёт получать мышь
# ----------------------------------------
def unmanage(window: Any) -> None:
    from webview.platforms.qt import BrowserView

    view = BrowserView.instances[window.uid]
    view.setWindowFlags(view.windowFlags() | QtCore.Qt.WindowType.X11BypassWindowManagerHint)
