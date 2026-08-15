"""透明桌面角色、鼠标互动和 Windows 窗口吸附。"""

from __future__ import annotations

import ctypes
import sys
from contextlib import suppress
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QMouseEvent, QMovie, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from desktop_companion_agent.models import CharacterState
from desktop_companion_agent.platforms.window_backend import WindowBackend, WindowRect
from desktop_companion_agent.services.privacy_mask import privacy_mask_registry


class CharacterWidget(QWidget):
    """在总览窗景与置顶桌面之间复用的同一个动态角色。"""

    show_main_requested = Signal()
    show_agent_requested = Signal()
    blindfold_toggled = Signal(bool)
    patted = Signal()
    position_changed = Signal(int, int)
    detach_requested = Signal(QPoint)
    return_to_dashboard_requested = Signal()
    exit_requested = Signal()

    def __init__(
        self,
        window_backend: WindowBackend,
        assets: dict[CharacterState, Path] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.window_backend = window_backend
        self.assets = assets or {}
        self.state = CharacterState.NORMAL
        self.blindfolded = False
        self._embedded = False
        self._detach_emitted = False
        self._drag_origin: QPoint | None = None
        self._window_origin = QPoint()
        self._snap_handle = 0
        self._snap_offset_x = 0
        self._fullscreen_frozen = False
        self._movie: QMovie | None = None
        self._pixmap: QPixmap | None = None
        self._single_click_timer = QTimer(self)
        self._single_click_timer.setSingleShot(True)
        self._single_click_timer.setInterval(QApplication.doubleClickInterval())
        self._single_click_timer.timeout.connect(self._show_interaction_menu)
        self._follow_timer = QTimer(self)
        self._follow_timer.setInterval(250)
        self._follow_timer.timeout.connect(self._follow_target)
        self._follow_timer.start()
        self._privacy_timer = QTimer(self)
        self._privacy_timer.setInterval(100)
        self._privacy_timer.timeout.connect(self._sync_privacy_mask)
        self._privacy_timer.start()
        self.setFixedSize(176, 216)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowFlags(self._floating_window_flags())
        self.set_state(CharacterState.NORMAL)

    @staticmethod
    def _floating_window_flags() -> Qt.WindowType:
        """集中定义桌面浮动窗口标志，切换父级时可安全复用。"""

        return (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )

    @property
    def is_embedded(self) -> bool:
        """角色当前是否嵌入总览页的风车窗。"""

        return self._embedded

    def set_embedded(self, host: QWidget) -> None:
        """把角色嵌入总览页，不重建动画，确保状态和当前帧连续。"""

        if self._embedded and self.parentWidget() is host:
            self.reposition_in_host()
            return
        self._single_click_timer.stop()
        self._snap_handle = 0
        self._fullscreen_frozen = False
        self._follow_timer.stop()
        self.hide()
        self.setParent(host)
        self.setWindowFlags(Qt.WindowType.Widget)
        self._embedded = True
        self.reposition_in_host()
        self.show()
        self.raise_()

    def set_floating(self, global_position: QPoint | None = None) -> None:
        """将角色恢复为桌面置顶窗口，并限制在可见屏幕区域。"""

        if global_position is None:
            global_position = (
                self.mapToGlobal(QPoint(0, 0)) if self._embedded else QPoint(self.pos())
            )
        self.hide()
        self.setParent(None)
        self.setWindowFlags(self._floating_window_flags())
        self._embedded = False
        self.move(global_position)
        self.show()
        self.raise_()
        self._follow_timer.start()
        self._keep_on_screen()

    def reposition_in_host(self) -> None:
        """在风车窗中使用统一的脚底基准线居中摆放角色。"""

        host = self.parentWidget()
        if not self._embedded or host is None:
            return
        x = max(0, (host.width() - self.width()) // 2)
        y = max(0, host.height() - self.height() - 24)
        self.move(x, y)

    def showEvent(self, event) -> None:
        """尽力让 Windows 屏幕截图排除角色自身，失败时安全忽略。"""

        super().showEvent(event)
        self._sync_privacy_mask()
        if sys.platform == "win32" and not self._embedded:
            with suppress(Exception):
                # WDA_EXCLUDEFROMCAPTURE：Windows 10 2004 及以上可用。
                ctypes.windll.user32.SetWindowDisplayAffinity(int(self.winId()), 0x11)

    def hideEvent(self, event) -> None:
        privacy_mask_registry.remove("character")
        super().hideEvent(event)

    def closeEvent(self, event) -> None:
        privacy_mask_registry.remove("character")
        super().closeEvent(event)

    def prepare_shutdown(self) -> None:
        """立即停止交互、跟随与动画并隐藏角色，不等待后台服务收尾。"""

        self._single_click_timer.stop()
        self._follow_timer.stop()
        self._privacy_timer.stop()
        self._drag_origin = None
        self._snap_handle = 0
        if self._movie is not None:
            self._movie.stop()
        self.hide()
        privacy_mask_registry.remove("character")

    def _sync_privacy_mask(self) -> None:
        """同步角色全局位置，供内存截图在编码前遮蔽真实像素。"""

        if not self.isVisible():
            privacy_mask_registry.remove("character")
            return
        top_left = self.mapToGlobal(QPoint(0, 0))
        privacy_mask_registry.update(
            "character",
            top_left.x(),
            top_left.y(),
            self.width(),
            self.height(),
        )

    def set_state(self, state: CharacterState) -> None:
        """切换正式素材或占位绘制状态。"""

        self.state = CharacterState.BLINDFOLDED if self.blindfolded else state
        if self._movie is not None:
            self._movie.stop()
            self._movie = None
        self._pixmap = None
        path = self.assets.get(self.state)
        if path and path.is_file():
            if path.suffix.lower() == ".gif":
                movie = QMovie(str(path))
                if movie.isValid():
                    movie.setScaledSize(self.size())
                    movie.frameChanged.connect(self.update)
                    movie.start()
                    self._movie = movie
            else:
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    self._pixmap = pixmap
        self.update()

    def set_blindfolded(self, enabled: bool, emit_signal: bool = False) -> None:
        self.blindfolded = enabled
        self.set_state(CharacterState.BLINDFOLDED if enabled else CharacterState.NORMAL)
        if emit_signal:
            self.blindfold_toggled.emit(enabled)

    def _asset_pixmap(self) -> QPixmap | None:
        if self._movie is not None:
            frame = self._movie.currentPixmap()
            return frame if not frame.isNull() else None
        return self._pixmap

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pixmap = self._asset_pixmap()
        if pixmap is not None:
            painter.drawPixmap(self.rect(), pixmap)
            return
        self._paint_placeholder(painter)

    def _paint_placeholder(self, painter: QPainter) -> None:
        """正式角色素材缺失时绘制可验收交互的占位角色。"""

        colors = {
            CharacterState.NORMAL: QColor("#8c91f3"),
            CharacterState.HAPPY: QColor("#69c99b"),
            CharacterState.ANGRY: QColor("#ef6b73"),
            CharacterState.BLINDFOLDED: QColor("#78829a"),
            CharacterState.PATTED: QColor("#f3b76d"),
            CharacterState.OFFLINE: QColor("#9aa2b4"),
        }
        body_color = colors[self.state]
        shadow = QColor(26, 32, 47, 45)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shadow)
        painter.drawEllipse(QRect(24, 190, 128, 18))
        painter.setBrush(body_color)
        body = QPainterPath()
        body.addRoundedRect(30, 62, 116, 132, 48, 48)
        painter.drawPath(body)
        painter.setBrush(body_color.lighter(108))
        painter.drawEllipse(QRect(38, 18, 100, 100))
        painter.setPen(QPen(QColor("#30364c"), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if self.state is CharacterState.BLINDFOLDED:
            painter.drawLine(52, 62, 124, 62)
            painter.setPen(
                QPen(QColor("#202638"), 14, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            )
            painter.drawLine(58, 62, 118, 62)
        elif self.state is CharacterState.HAPPY:
            painter.drawArc(QRect(57, 51, 20, 20), 0, 180 * 16)
            painter.drawArc(QRect(99, 51, 20, 20), 0, 180 * 16)
            painter.drawArc(QRect(73, 68, 30, 23), 180 * 16, 180 * 16)
        elif self.state is CharacterState.ANGRY:
            painter.drawLine(56, 54, 76, 61)
            painter.drawLine(100, 61, 120, 54)
            painter.drawArc(QRect(73, 73, 30, 21), 0, 180 * 16)
        else:
            painter.setBrush(QColor("#30364c"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRect(60, 57, 9, 12))
            painter.drawEllipse(QRect(107, 57, 9, 12))
            painter.drawArc(QRect(76, 68, 24, 16), 180 * 16, 180 * 16)
        painter.setPen(QColor(255, 255, 255, 210))
        painter.drawText(QRect(25, 126, 126, 32), Qt.AlignmentFlag.AlignCenter, "AI 伙伴")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() is Qt.MouseButton.RightButton:
            self._drag_origin = event.globalPosition().toPoint()
            self._window_origin = (
                self.mapToGlobal(QPoint(0, 0)) if self._embedded else QPoint(self.pos())
            )
            self._detach_emitted = False
            self._snap_handle = 0
            self._fullscreen_frozen = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_origin is not None and event.buttons() & Qt.MouseButton.RightButton:
            global_position = (
                self._window_origin + event.globalPosition().toPoint() - self._drag_origin
            )
            if self._embedded:
                host = self.parentWidget()
                if host is not None:
                    host_rect = host.rect().translated(host.mapToGlobal(QPoint(0, 0)))
                    if not host_rect.contains(event.globalPosition().toPoint()):
                        if not self._detach_emitted:
                            self._detach_emitted = True
                            self.detach_requested.emit(global_position)
                        if not self._embedded:
                            self.move(global_position)
                    else:
                        local = host.mapFromGlobal(global_position)
                        x = min(max(0, local.x()), max(0, host.width() - self.width()))
                        y = min(max(0, local.y()), max(0, host.height() - self.height()))
                        self.move(x, y)
            else:
                self.move(global_position)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() is Qt.MouseButton.RightButton and self._drag_origin is not None:
            self._drag_origin = None
            self._detach_emitted = False
            if self._embedded:
                self.reposition_in_host()
            else:
                self._try_snap()
                self._keep_on_screen()
                self.position_changed.emit(self.x(), self.y())
            event.accept()
            return
        if event.button() is Qt.MouseButton.LeftButton:
            self._single_click_timer.start()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() is Qt.MouseButton.LeftButton:
            self._single_click_timer.stop()
            self.show_main_requested.emit()
            event.accept()
            return
        if event.button() is Qt.MouseButton.RightButton and not self._embedded:
            self.return_to_dashboard_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _show_interaction_menu(self) -> None:
        menu = QMenu()
        pat = menu.addAction("摸摸头")
        blindfold = menu.addAction("摘下眼罩" if self.blindfolded else "戴上眼罩")
        menu.addSeparator()
        agent = menu.addAction("打开 Agent 中心")
        main = menu.addAction("打开总窗口")
        menu.addSeparator()
        exit_action = menu.addAction("完全退出程序")
        chosen = menu.exec(self.mapToGlobal(QPoint(self.width() // 2, self.height() // 2)))
        if chosen is pat:
            self.set_state(CharacterState.PATTED)
            self.patted.emit()
            QTimer.singleShot(1800, lambda: self.set_state(CharacterState.NORMAL))
        elif chosen is blindfold:
            self.set_blindfolded(not self.blindfolded, emit_signal=True)
        elif chosen is agent:
            self.show_agent_requested.emit()
        elif chosen is main:
            self.show_main_requested.emit()
        elif chosen is exit_action:
            self.exit_requested.emit()

    def _try_snap(self) -> None:
        if self._embedded:
            return
        center_x = self.x() + self.width() // 2
        bottom_y = self.y() + self.height()
        target = self.window_backend.find_snap_target(center_x, bottom_y, tolerance=24)
        if target is None or target.rect is None:
            return
        self._snap_handle = target.handle
        self._snap_offset_x = self.x() - target.rect.left
        self.move(self.x(), target.rect.top - self.height() + 8)

    def _follow_target(self) -> None:
        if self._embedded or not self._snap_handle or self._drag_origin is not None:
            return
        info = self.window_backend.window_info(self._snap_handle)
        if (
            info is None
            or info.rect is None
            or self.window_backend.is_minimized(self._snap_handle)
            or not self._rect_on_any_screen(info.rect)
        ):
            self._snap_handle = 0
            self._fullscreen_frozen = False
            self._keep_on_screen()
            return
        if self.window_backend.is_fullscreen(self._snap_handle):
            self._fullscreen_frozen = True
            return
        self._fullscreen_frozen = False
        next_x = info.rect.left + self._snap_offset_x
        next_y = info.rect.top - self.height() + 8
        self.move(next_x, next_y)
        self._keep_on_screen()

    @staticmethod
    def _rect_on_any_screen(rect: WindowRect) -> bool:
        """目标完全移出所有屏幕后解除吸附，避免角色被持续拉到边缘。"""

        target = QRect(rect.left, rect.top, rect.width, rect.height)
        return any(screen.geometry().intersects(target) for screen in QApplication.screens())

    def _keep_on_screen(self) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = min(max(self.x(), area.left()), area.right() - self.width() + 1)
        y = min(max(self.y(), area.top()), area.bottom() - self.height() + 1)
        if QPoint(x, y) != self.pos():
            self.move(x, y)
