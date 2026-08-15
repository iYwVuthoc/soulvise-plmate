"""跟随角色、自动翻转且不抢焦点的透明应景气泡。"""

from __future__ import annotations

import ctypes
import sys
from contextlib import suppress

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from desktop_companion_agent.models import SpeechBubbleMessage
from desktop_companion_agent.services.privacy_mask import privacy_mask_registry


class CharacterSpeechBubble(QWidget):
    """独立置顶气泡；嵌入和浮动角色都使用同一个实例。"""

    def __init__(self, character: QWidget):
        super().__init__(None)
        self.character = character
        self._bubble_on_right = True
        self._embedded_mode = False
        self._message: SpeechBubbleMessage | None = None
        self.setFixedSize(236, 92)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput
        )
        self.label = QLabel(self)
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet(
            "color: #3A1626; font: 700 15px 'Microsoft YaHei UI'; background: transparent;"
        )
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)
        self._follow_timer = QTimer(self)
        self._follow_timer.setInterval(50)
        self._follow_timer.timeout.connect(self._follow_character)

    def show_message(self, message: SpeechBubbleMessage) -> None:
        """显示新消息；同事件的Agent润色会直接替换本地模板。"""

        self._message = message
        self.label.setText(message.text)
        self._follow_character()
        self.show()
        self.raise_()
        self._follow_timer.start()
        self._hide_timer.start(round(message.duration_seconds * 1000))
        self._sync_privacy_mask()

    def _follow_character(self) -> None:
        if not self.character.isVisible():
            self.hide()
            return
        character_top_left = self.character.mapToGlobal(QPoint(0, 0))
        center = character_top_left + QPoint(
            self.character.width() // 2,
            self.character.height() // 2,
        )
        screen = QApplication.screenAt(center) or QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self._embedded_mode = bool(getattr(self.character, "is_embedded", False))
        if self._embedded_mode and self.character.parentWidget() is not None:
            host = self.character.parentWidget()
            host_top_left = host.mapToGlobal(QPoint(0, 0))
            x = host_top_left.x() + max(0, (host.width() - self.width()) // 2)
            y = max(host_top_left.y() + 18, character_top_left.y() - self.height() + 18)
            self.move(x, y)
            self.label.setGeometry(18, 10, self.width() - 36, 58)
            self._sync_privacy_mask()
            self.update()
            return
        right_x = character_top_left.x() + self.character.width() - 12
        self._bubble_on_right = right_x + self.width() <= area.right() + 1
        x = right_x if self._bubble_on_right else character_top_left.x() - self.width() + 12
        y = character_top_left.y() + 30
        x = min(max(x, area.left()), area.right() - self.width() + 1)
        y = min(max(y, area.top()), area.bottom() - self.height() + 1)
        self.move(x, y)
        body_left = 14 if self._bubble_on_right else 6
        body_right = 6 if self._bubble_on_right else 14
        self.label.setGeometry(body_left + 10, 12, self.width() - body_left - body_right - 20, 66)
        self._sync_privacy_mask()
        self.update()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._embedded_mode:
            body = QRect(6, 6, self.width() - 12, 72)
        else:
            body = QRect(14 if self._bubble_on_right else 6, 6, self.width() - 20, 80)
        path = QPainterPath()
        path.addRoundedRect(body, 16, 16)
        arrow_y = 42
        if self._embedded_mode:
            center = body.center().x()
            path.moveTo(center - 10, body.bottom())
            path.lineTo(center, self.height() - 2)
            path.lineTo(center + 10, body.bottom())
        elif self._bubble_on_right:
            path.moveTo(body.left(), arrow_y - 10)
            path.lineTo(2, arrow_y)
            path.lineTo(body.left(), arrow_y + 10)
        else:
            path.moveTo(body.right(), arrow_y - 10)
            path.lineTo(self.width() - 2, arrow_y)
            path.lineTo(body.right(), arrow_y + 10)
        path.closeSubpath()
        painter.setPen(QPen(QColor("#8F2D4A"), 2))
        painter.setBrush(QColor("#FFF4E6"))
        painter.drawPath(path)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if sys.platform == "win32":
            with suppress(Exception):
                ctypes.windll.user32.SetWindowDisplayAffinity(int(self.winId()), 0x11)

    def hideEvent(self, event) -> None:
        self._follow_timer.stop()
        privacy_mask_registry.remove("speech-bubble")
        super().hideEvent(event)

    def closeEvent(self, event) -> None:
        privacy_mask_registry.remove("speech-bubble")
        super().closeEvent(event)

    def prepare_shutdown(self) -> None:
        """停止所有显示计时器并立即隐藏，避免退出收尾期间残留气泡。"""

        self._hide_timer.stop()
        self._follow_timer.stop()
        self.hide()
        privacy_mask_registry.remove("speech-bubble")

    def _sync_privacy_mask(self) -> None:
        if not self.isVisible():
            return
        privacy_mask_registry.update(
            "speech-bubble",
            self.x(),
            self.y(),
            self.width(),
            self.height(),
        )
