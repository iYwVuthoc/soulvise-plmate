"""暮色总览角色嵌入、浮动和窗口隐藏行为测试。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QMessageBox, QWidget

from desktop_companion_agent.config import ConfigManager, ModelSettings, UiSettings
from desktop_companion_agent.models import CharacterState
from desktop_companion_agent.paths import AppPaths
from desktop_companion_agent.platforms.window_backend import NullWindowBackend
from desktop_companion_agent.security import InMemorySecretStore
from desktop_companion_agent.services.chat import OpenAIChatService
from desktop_companion_agent.services.cognition_organizer import CognitionOrganizerService
from desktop_companion_agent.services.connectors import ConnectorRegistry
from desktop_companion_agent.storage.repository import CognitionRepository
from desktop_companion_agent.ui.character import CharacterWidget
from desktop_companion_agent.ui.main_window import MainWindow


def _mouse_event(
    event_type: QMouseEvent.Type,
    local_x: float,
    local_y: float,
    global_x: float,
    global_y: float,
    button: Qt.MouseButton,
    buttons: Qt.MouseButton,
) -> QMouseEvent:
    return QMouseEvent(
        event_type,
        QPointF(local_x, local_y),
        QPointF(global_x, global_y),
        button,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )


def test_new_ui_settings_default_to_docked() -> None:
    """旧配置缺少字段时，升级后的角色默认回到风车窗。"""

    assert UiSettings().character_docked is True
    assert UiSettings.model_validate({"character_x": 12, "character_y": 34}).character_docked


def test_character_reuses_state_between_embedded_and_floating(qt_app) -> None:
    """重新设置父级不得重建角色状态或丢失眼罩信息。"""

    host = QWidget()
    host.resize(330, 450)
    host.show()
    character = CharacterWidget(NullWindowBackend())
    character.set_state(CharacterState.HAPPY)
    character.set_embedded(host)
    qt_app.processEvents()

    assert character.is_embedded
    assert character.parentWidget() is host
    assert character.state is CharacterState.HAPPY
    assert not character._follow_timer.isActive()

    character.set_floating(host.mapToGlobal(host.rect().topLeft()))
    qt_app.processEvents()
    assert not character.is_embedded
    assert character.parentWidget() is None
    assert character.state is CharacterState.HAPPY
    assert character._follow_timer.isActive()
    character.close()
    host.close()


def test_embedded_right_drag_requests_detach(qt_app) -> None:
    """右键指针越过风车窗边界时只发送一次脱离请求。"""

    host = QWidget()
    host.resize(330, 450)
    host.show()
    character = CharacterWidget(NullWindowBackend())
    character.set_embedded(host)
    qt_app.processEvents()
    spy = QSignalSpy(character.detach_requested)
    origin = character.mapToGlobal(character.rect().center())

    character.mousePressEvent(
        _mouse_event(
            QMouseEvent.Type.MouseButtonPress,
            character.width() / 2,
            character.height() / 2,
            origin.x(),
            origin.y(),
            Qt.MouseButton.RightButton,
            Qt.MouseButton.RightButton,
        )
    )
    outside = host.mapToGlobal(host.rect().bottomRight()) + character.rect().bottomRight()
    character.mouseMoveEvent(
        _mouse_event(
            QMouseEvent.Type.MouseMove,
            character.width(),
            character.height(),
            outside.x(),
            outside.y(),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.RightButton,
        )
    )
    character.mouseMoveEvent(
        _mouse_event(
            QMouseEvent.Type.MouseMove,
            character.width(),
            character.height(),
            outside.x() + 10,
            outside.y() + 10,
            Qt.MouseButton.NoButton,
            Qt.MouseButton.RightButton,
        )
    )
    assert spy.count() == 1
    character.close()
    host.close()


def test_floating_right_double_click_requests_dashboard(qt_app) -> None:
    """浮动角色右键双击应请求显示总览并归位。"""

    character = CharacterWidget(NullWindowBackend())
    character.set_floating()
    qt_app.processEvents()
    spy = QSignalSpy(character.return_to_dashboard_requested)
    character.mouseDoubleClickEvent(
        _mouse_event(
            QMouseEvent.Type.MouseButtonDblClick,
            20,
            20,
            20,
            20,
            Qt.MouseButton.RightButton,
            Qt.MouseButton.RightButton,
        )
    )
    assert spy.count() == 1
    character.close()


def test_dashboard_hide_and_page_leave_request_float(
    qt_app,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """嵌入期间隐藏总窗口或离开总览，都必须让角色返回桌面。"""

    paths = AppPaths.resolve(tmp_path / "data")
    config_manager = ConfigManager(paths.config_file)
    config_manager.load()
    repository = CognitionRepository(paths.database_file)
    secret_store = InMemorySecretStore()
    chat = OpenAIChatService(ModelSettings(), secret_store, repository, paths.context_dir)
    organizer = CognitionOrganizerService(ModelSettings(), secret_store)
    window = MainWindow(
        config_manager,
        repository,
        secret_store,
        chat,
        ConnectorRegistry(),
        paths,
        organizer,
    )
    window.show()
    qt_app.processEvents()

    page_spy = QSignalSpy(window.character_should_float)
    window.character_should_float.connect(lambda: window.set_character_docked(False))
    window.set_character_docked(True)
    window.show_page(MainWindow.PAGE_SETTINGS)
    assert page_spy.count() == 1

    window.show_page(MainWindow.PAGE_DASHBOARD)
    window.set_character_docked(True)
    window.hide()
    qt_app.processEvents()
    assert page_spy.count() == 2

    window.showNormal()
    window.show_page(MainWindow.PAGE_DASHBOARD)
    window.set_character_docked(True)
    window.showMinimized()
    qt_app.processEvents()
    assert page_spy.count() == 3

    # 右上角关闭仍表示隐藏，但必须发出明确的托盘提示事件。
    window.showNormal()
    hidden_spy = QSignalSpy(window.hidden_to_tray)
    window.close()
    qt_app.processEvents()
    assert not window.isVisible()
    assert hidden_spy.count() == 1

    # 左侧“完全退出程序”经过确认后必须发出真正的退出请求。
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    exit_spy = QSignalSpy(window.exit_requested)
    window.exit_button.click()
    assert exit_spy.count() == 1

    window.allow_close()
    window.close()
    chat.shutdown()
    organizer.shutdown()
    repository.close()
