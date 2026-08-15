"""暮色小镇总览页使用的轻量主题组件。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPaintEvent, QPen, QPixmap
from PySide6.QtWidgets import QCheckBox, QWidget


class ToggleSwitch(QCheckBox):
    """使用 Qt 原生绘制的开关，保留 ``QCheckBox`` 的可访问状态。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(58, 32)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def sizeHint(self) -> QSize:
        return QSize(58, 32)

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        enabled = self.isEnabled()
        track = QColor("#8F2D4A" if self.isChecked() else "#B9AEA2")
        if not enabled:
            track.setAlpha(105)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(1, 3, 56, 26), 13, 13)
        knob_x = 31 if self.isChecked() else 5
        painter.setBrush(QColor("#FFF9F3"))
        painter.drawEllipse(QRectF(knob_x, 6, 20, 20))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#D98C5F"), 1.5))
            painter.drawRoundedRect(QRectF(0.75, 0.75, 56.5, 30.5), 15, 15)


class CharacterDockHost(QWidget):
    """承载同一个角色实例，并在窗口缩放时通知控制器重新居中。"""

    resized = Signal()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.resized.emit()


class TownSceneryWidget(QWidget):
    """用现有启动图绘制风车窗景，并提供透明角色停靠层。"""

    def __init__(self, splash_path: Path, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("townScenery")
        self.setMinimumSize(300, 440)
        self.setMaximumWidth(360)
        self._scenery = QPixmap(str(splash_path))
        self.character_host = CharacterDockHost(self)
        self.character_host.setObjectName("characterDockHost")
        self.character_host.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.character_host.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.character_host.setGeometry(self.rect())
        self.character_host.raise_()

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        outer = self.rect().adjusted(6, 6, -6, -6)

        # 圆拱轮廓保留概念图的书房窗景气质，下半部为稳定的矩形坐席。
        path = QPainterPath()
        path.moveTo(outer.left(), outer.bottom())
        path.lineTo(outer.left(), outer.top() + outer.width() * 0.42)
        path.cubicTo(
            outer.left(),
            outer.top() + 28,
            outer.center().x() - outer.width() * 0.28,
            outer.top(),
            outer.center().x(),
            outer.top(),
        )
        path.cubicTo(
            outer.center().x() + outer.width() * 0.28,
            outer.top(),
            outer.right(),
            outer.top() + 28,
            outer.right(),
            outer.top() + outer.width() * 0.42,
        )
        path.lineTo(outer.right(), outer.bottom())
        path.closeSubpath()

        painter.save()
        painter.setClipPath(path)
        painter.fillPath(path, QColor("#2A2024"))
        if not self._scenery.isNull():
            source = QRectF(
                self._scenery.width() * 0.24,
                self._scenery.height() * 0.03,
                self._scenery.width() * 0.35,
                self._scenery.height() * 0.94,
            )
            painter.drawPixmap(QRectF(outer), self._scenery, source)
            painter.fillPath(path, QColor(58, 22, 38, 24))
        painter.restore()

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#D98C5F"), 3))
        painter.drawPath(path)
        inner = path.translated(0, 0)
        painter.setPen(QPen(QColor(255, 249, 243, 125), 1))
        painter.drawPath(inner)
