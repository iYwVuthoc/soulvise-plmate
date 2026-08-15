"""应用装配、加载页、托盘和生命周期管理。"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from math import ceil
from pathlib import Path
from time import monotonic

from PySide6.QtCore import QLockFile, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QMenu,
    QMessageBox,
    QSplashScreen,
    QSystemTrayIcon,
)

from desktop_companion_agent.branding import (
    APPLICATION_NAME,
    DISPLAY_NAME,
    ORGANIZATION_NAME,
)
from desktop_companion_agent.config import AppConfig, ConfigManager
from desktop_companion_agent.models import (
    AgentConnectionMode,
    BubbleEvent,
    BubbleEventType,
    CharacterState,
    RuleMode,
)
from desktop_companion_agent.paths import AppPaths
from desktop_companion_agent.platforms.window_backend import create_window_backend
from desktop_companion_agent.security import KeyringSecretStore, redact_sensitive_text
from desktop_companion_agent.services.acp_agent import AcpAgentProvider
from desktop_companion_agent.services.agent_providers import CapabilityRouter, ProviderRegistry
from desktop_companion_agent.services.analyzer import (
    AgentRoutedContentAnalyzer,
    OpenAIContentAnalyzer,
)
from desktop_companion_agent.services.bubbles import SpeechBubbleService
from desktop_companion_agent.services.capture import ActiveScreenCaptureBackend
from desktop_companion_agent.services.chat import OpenAIChatService
from desktop_companion_agent.services.codex_agent import CodexAgentService
from desktop_companion_agent.services.cognition_organizer import CognitionOrganizerService
from desktop_companion_agent.services.connectors import ConnectorRegistry
from desktop_companion_agent.services.context_exporter import ContextExporter
from desktop_companion_agent.services.intervention import (
    InterventionService,
    normalize_redirect_url,
)
from desktop_companion_agent.services.observation import ObservationEngine, ObservationOutcome
from desktop_companion_agent.services.policy import PolicyEngine
from desktop_companion_agent.services.process_monitor import ProcessMonitor
from desktop_companion_agent.services.temporary_images import VisionTemporaryImageStore
from desktop_companion_agent.storage.repository import CognitionRepository
from desktop_companion_agent.ui.character import CharacterWidget
from desktop_companion_agent.ui.main_window import MainWindow
from desktop_companion_agent.ui.speech_bubble import CharacterSpeechBubble
from desktop_companion_agent.ui.styles import APP_STYLE

MINIMUM_SPLASH_DURATION_SECONDS = 1.2
MAXIMUM_SPLASH_WIDTH = 900
MAXIMUM_SPLASH_HEIGHT = 506


def _acquire_single_instance_lock(paths: AppPaths) -> QLockFile | None:
    """确保同一数据目录只有一个实例，避免并发截图、跳转和数据库写入。"""

    lock = QLockFile(str(paths.data_dir / "SoulvisePlmate.lock"))
    if lock.tryLock(0):
        return lock
    if lock.removeStaleLockFile() and lock.tryLock(0):
        return lock
    return None


def _application_icon(size: int = 128) -> QIcon:
    """正式图标未提供时生成不依赖文件的占位图标。"""

    resource_dir = Path(__file__).parent / "resources"
    for name in ("app_icon.ico", "app_icon.png"):
        candidate = resource_dir / name
        if candidate.is_file():
            icon = QIcon(str(candidate))
            if not icon.isNull():
                return icon
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#6d73e8"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(8, 8, size - 16, size - 16, 28, 28)
    painter.setPen(QColor("white"))
    painter.setFont(QFont("Microsoft YaHei UI", max(12, size // 5), QFont.Weight.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "AI")
    painter.end()
    return QIcon(pixmap)


def _splash(icon: QIcon) -> QSplashScreen:
    """生成可被后续正式素材替换的加载页。"""

    splash_file = Path(__file__).parent / "resources" / "splash.png"
    if splash_file.is_file():
        custom = QPixmap(str(splash_file))
        if not custom.isNull():
            value = QSplashScreen(_scaled_splash_pixmap(custom))
            value.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            return value
    pixmap = QPixmap(620, 350)
    pixmap.fill(QColor("#20283a"))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.drawPixmap(258, 60, 104, 104, icon.pixmap(104, 104))
    painter.setPen(QColor("white"))
    painter.setFont(QFont("Microsoft YaHei UI", 22, QFont.Weight.Bold))
    painter.drawText(0, 188, 620, 44, Qt.AlignmentFlag.AlignCenter, DISPLAY_NAME)
    painter.setPen(QColor("#b9c1d6"))
    painter.setFont(QFont("Microsoft YaHei UI", 11))
    painter.drawText(0, 235, 620, 30, Qt.AlignmentFlag.AlignCenter, "正在加载配置、认知与角色…")
    painter.end()
    value = QSplashScreen(pixmap)
    value.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    return value


def _scaled_splash_pixmap(pixmap: QPixmap) -> QPixmap:
    """按主屏幕可用区域缩放启动图，始终保持原始宽高比。"""

    maximum_width = MAXIMUM_SPLASH_WIDTH
    maximum_height = MAXIMUM_SPLASH_HEIGHT
    screen = QApplication.primaryScreen()
    if screen is not None:
        geometry = screen.availableGeometry()
        maximum_width = min(maximum_width, max(1, int(geometry.width() * 0.7)))
        maximum_height = min(maximum_height, max(1, int(geometry.height() * 0.7)))
    return pixmap.scaled(
        maximum_width,
        maximum_height,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _remaining_splash_delay_ms(
    started_at: float,
    current_time: float | None = None,
) -> int:
    """计算启动页还需显示的毫秒数，避免正式素材只短暂闪烁。"""

    now = monotonic() if current_time is None else current_time
    remaining_seconds = MINIMUM_SPLASH_DURATION_SECONDS - max(0.0, now - started_at)
    return max(0, ceil(remaining_seconds * 1000))


def _configure_logging(log_dir: Path) -> None:
    """仅记录运行状态；调用处禁止传入截图、完整标题和密钥。"""

    handler = RotatingFileHandler(
        log_dir / "agent.log",
        maxBytes=512_000,
        backupCount=2,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


class ApplicationController(QObject):
    """协调界面、观察引擎和持久化服务。"""

    def __init__(self, app: QApplication, paths: AppPaths, icon: QIcon):
        super().__init__()
        self.app = app
        self.paths = paths
        self.icon = icon
        self._quitting = False
        self._character_feedback_generation = 0
        self._character_feedback_until = 0.0
        self.config_manager = ConfigManager(paths.config_file)
        config = self.config_manager.load()
        self._last_redirect_url = normalize_redirect_url(config.intervention.redirect_url)
        self.repository = CognitionRepository(paths.database_file)
        self.repository.purge_expired_events()
        self.secret_store = KeyringSecretStore()
        self.context_exporter = ContextExporter(self.repository, paths.context_dir)
        self.context_exporter.export_all()
        self.window_backend = create_window_backend()
        self.openai_analyzer = OpenAIContentAnalyzer(
            config.model,
            self.secret_store,
            config.moderation_policy,
            config.companion_policy,
        )
        self.policy = PolicyEngine(config.happiness)
        self.chat_service = OpenAIChatService(
            config.model,
            self.secret_store,
            self.repository,
            paths.context_dir,
        )
        self.connectors = ConnectorRegistry()
        self.provider_registry = ProviderRegistry()
        self.codex_agent = CodexAgentService(self.repository, paths)
        self.provider_registry.register(self.codex_agent)
        self._acp_providers: dict[str, AcpAgentProvider] = {}
        self._reload_acp_providers()
        self.capability_router = CapabilityRouter(self.config_manager, self.provider_registry)
        self.bubble_service = SpeechBubbleService(
            self.config_manager,
            self.capability_router,
        )
        self.vision_temporary_store = VisionTemporaryImageStore()
        self.analyzer = AgentRoutedContentAnalyzer(
            self.config_manager,
            self.capability_router,
            self.openai_analyzer,
            self.vision_temporary_store,
        )
        self.cognition_organizer = CognitionOrganizerService(
            config.model,
            self.secret_store,
            capability_router=self.capability_router,
        )
        self.observation = ObservationEngine(
            self.config_manager,
            self.repository,
            self.window_backend,
            ActiveScreenCaptureBackend(),
            self.analyzer,
            self.policy,
            ProcessMonitor(config.capture.tracked_processes),
            InterventionService(),
            self.context_exporter,
        )
        self.main_window = MainWindow(
            self.config_manager,
            self.repository,
            self.secret_store,
            self.chat_service,
            self.connectors,
            paths,
            self.cognition_organizer,
            self.codex_agent,
            self.capability_router,
            self.provider_registry,
            self._reload_acp_providers,
        )
        self.main_window.setWindowIcon(icon)
        self.character = CharacterWidget(self.window_backend, self._character_assets())
        self.speech_bubble = CharacterSpeechBubble(self.character)
        self.character.move(config.ui.character_x, config.ui.character_y)
        self.character.set_blindfolded(config.blindfolded)
        self.tray = self._create_tray()
        self._connect()
        self._apply_config(initial=True)

    def _reload_acp_providers(self) -> None:
        """按SQLite配置重建ACP连接器，不导入或执行第三方Python插件。"""

        for provider in self._acp_providers.values():
            try:
                provider.shutdown()
            except Exception:
                logging.exception("重载时关闭ACP连接失败")
            self.provider_registry.unregister(provider.provider_id)
        self._acp_providers = {}
        runtime_root = self.paths.data_dir / "temp" / "acp-runtime"
        for profile in self.repository.list_agent_profiles():
            if (
                profile.enabled
                and profile.connector_type == "acp"
                and profile.connection_mode is AgentConnectionMode.DEEP
            ):
                provider = AcpAgentProvider(profile, self.repository, runtime_root)
                self._acp_providers[profile.id] = provider
                self.provider_registry.register(provider)

    def _character_assets(self) -> dict[CharacterState, Path]:
        """约定正式素材路径；不存在时角色组件自动使用占位绘制。"""

        directory = Path(__file__).parent / "resources" / "characters"
        result: dict[CharacterState, Path] = {}
        for state in CharacterState:
            for suffix in (".gif", ".png", ".webp"):
                candidate = directory / f"{state.value}{suffix}"
                if candidate.is_file():
                    result[state] = candidate
                    break
        return result

    def _create_tray(self) -> QSystemTrayIcon:
        tray = QSystemTrayIcon(self.icon, self)
        tray.setToolTip(DISPLAY_NAME)
        menu = QMenu()
        show = menu.addAction("显示总窗口")
        pause = menu.addAction("暂停/恢复观察")
        menu.addSeparator()
        exit_action = menu.addAction("退出程序")
        show.triggered.connect(lambda: self.main_window.show_page(MainWindow.PAGE_DASHBOARD))
        pause.triggered.connect(self._toggle_observation)
        exit_action.triggered.connect(self.shutdown)
        tray.setContextMenu(menu)
        tray.activated.connect(self._tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            tray.show()
        return tray

    def _connect(self) -> None:
        self.main_window.config_changed.connect(self._apply_config)
        self.main_window.exit_requested.connect(self.shutdown)
        self.main_window.hidden_to_tray.connect(self._notify_hidden_to_tray)
        self.character.show_main_requested.connect(
            lambda: self.main_window.show_page(MainWindow.PAGE_DASHBOARD)
        )
        self.character.show_agent_requested.connect(
            lambda: self.main_window.show_page(MainWindow.PAGE_AGENTS)
        )
        self.character.blindfold_toggled.connect(self._set_blindfolded)
        self.character.position_changed.connect(self._save_character_position)
        self.character.detach_requested.connect(self._float_character)
        self.character.return_to_dashboard_requested.connect(self._return_character_to_dashboard)
        self.character.exit_requested.connect(self.shutdown)
        self.main_window.character_should_float.connect(self._float_character)
        self.main_window.character_host.resized.connect(self._reposition_docked_character)
        self.observation.status_changed.connect(self.main_window.update_status)
        self.observation.interval_changed.connect(self.main_window.update_interval)
        self.observation.error_occurred.connect(self._show_runtime_error)
        self.observation.outcome_ready.connect(self._handle_outcome)
        self.bubble_service.message_ready.connect(self.speech_bubble.show_message)
        self.app.aboutToQuit.connect(self._finalize)

    def show(self) -> None:
        self.main_window.show()
        self.main_window.raise_()
        config = self.config_manager.config
        if config.ui.character_docked:
            QTimer.singleShot(0, self._dock_character)
        else:
            self.character.set_floating(QPoint(config.ui.character_x, config.ui.character_y))

    def _apply_config(self, initial: bool = False) -> None:
        config = self.config_manager.config
        self._refresh_redirect_target(config.intervention.redirect_url, initial)
        self.openai_analyzer.settings = config.model
        self.openai_analyzer.moderation_policy = config.moderation_policy
        self.openai_analyzer.companion_policy = config.companion_policy
        self.codex_agent.set_runtime_timeout(config.model.request_timeout_seconds)
        self.chat_service.settings = config.model
        self.cognition_organizer.settings = config.model
        self.character.set_blindfolded(config.blindfolded)
        self.main_window.update_happiness(self.policy.happiness)
        try:
            self.context_exporter.export_all()
        except OSError:
            logging.exception("认知上下文导出失败")
        self.observation.apply_config()
        if not self.observation.running:
            self.main_window.update_status(
                "角色已戴眼罩，观察停止" if config.blindfolded else "观察未开启"
            )
        if not initial:
            self.main_window.refresh_cognition()

    def _refresh_redirect_target(self, value: str, initial: bool = False) -> None:
        """同步最新跳转地址；用户真正更换目标时清除旧目标的冷却。"""

        current = normalize_redirect_url(value)
        if not initial and current != self._last_redirect_url:
            self.policy.reset_intervention_cooldown()
        self._last_redirect_url = current

    def _set_blindfolded(self, enabled: bool) -> None:
        config = self.config_manager.config.model_copy(update={"blindfolded": enabled})
        self.config_manager.save(AppConfig.model_validate(config.model_dump()))
        self.main_window.set_blindfolded(enabled)
        self._apply_config()

    def _save_character_position(self, x: int, y: int) -> None:
        if self.character.is_embedded:
            return
        current = self.config_manager.config
        ui = current.ui.model_copy(update={"character_x": x, "character_y": y})
        updated = current.model_copy(update={"ui": ui})
        self.config_manager.save(AppConfig.model_validate(updated.model_dump()))

    def _save_character_docked(self, docked: bool) -> None:
        """持久化角色所在空间，不覆盖上一次合法桌面坐标。"""

        current = self.config_manager.config
        if current.ui.character_docked == docked:
            return
        ui = current.ui.model_copy(update={"character_docked": docked})
        updated = current.model_copy(update={"ui": ui})
        self.config_manager.save(AppConfig.model_validate(updated.model_dump()))

    def _dock_character(self) -> None:
        """将角色放回总览风车窗，并保持当前表情、眼罩和动画帧。"""

        if self._quitting:
            return
        self.character.set_embedded(self.main_window.character_host)
        self.main_window.set_character_docked(True)
        self._save_character_docked(True)

    def _float_character(self, global_position: QPoint | None = None) -> None:
        """把角色放回桌面；自动脱离时使用上一次合法桌面坐标。"""

        if self._quitting:
            return
        if global_position is None:
            ui = self.config_manager.config.ui
            global_position = QPoint(ui.character_x, ui.character_y)
        self.main_window.set_character_docked(False)
        self.character.set_floating(global_position)
        self._save_character_position(self.character.x(), self.character.y())
        self._save_character_docked(False)

    def _return_character_to_dashboard(self) -> None:
        """响应浮动角色右键双击：显示总览并复位到风车窗。"""

        if self._quitting:
            return
        self.main_window.show_page(MainWindow.PAGE_DASHBOARD)
        self._dock_character()

    def _reposition_docked_character(self) -> None:
        if self.character.is_embedded:
            self.character.reposition_in_host()

    def _handle_outcome(self, outcome: ObservationOutcome) -> None:
        self.main_window.update_happiness(outcome.decision.happiness_value)
        self._apply_character_feedback(outcome.decision.character_state)
        if outcome.intervention_message:
            self.main_window.update_status(outcome.intervention_message)
        self.main_window.refresh_cognition()
        bubble_event = self._bubble_event_for(outcome)
        if bubble_event is not None:
            self.bubble_service.emit_event(bubble_event)

    def _apply_character_feedback(self, state: CharacterState) -> None:
        """让高兴或生气反馈至少保持四秒，不被紧随其后的普通帧覆盖。"""

        if state is CharacterState.NORMAL:
            if monotonic() >= self._character_feedback_until:
                self.character.set_state(CharacterState.NORMAL)
            return

        self._character_feedback_generation += 1
        generation = self._character_feedback_generation
        self._character_feedback_until = monotonic() + 4.0
        self.character.set_state(state)
        QTimer.singleShot(
            4000,
            lambda: self._restore_character(generation),
        )

    @staticmethod
    def _is_connectivity_failure(message: str) -> bool:
        """只把真实连接问题显示为离线，格式或规则错误仅保留状态提示。"""

        normalized = message.casefold()
        indicators = (
            "网络",
            "离线",
            "未连接",
            "连接失败",
            "超时",
            "offline",
            "connection",
            "timeout",
            "transport",
        )
        return any(value in normalized for value in indicators)

    @staticmethod
    def _bubble_event_for(outcome: ObservationOutcome) -> BubbleEvent | None:
        """按监督、归零、庆祝、兴趣、离线的顺序选择唯一气泡事件。"""

        decision = outcome.decision
        if decision.mode is RuleMode.SUPERVISION and (
            outcome.result.flagged or outcome.result.matched_supervision_rule_ids
        ):
            kind = BubbleEventType.SUPERVISION_HIT
        elif decision.reason.startswith("高兴值达到阈值"):
            kind = BubbleEventType.HAPPINESS_ZERO
        elif decision.celebration_triggered:
            kind = BubbleEventType.CELEBRATION
        elif decision.happiness_delta > 0:
            kind = BubbleEventType.INTERESTED
        elif decision.happiness_delta < 0:
            kind = BubbleEventType.NOT_INTERESTED
        elif (
            not outcome.result.analysis_available
            and ApplicationController._is_connectivity_failure(outcome.result.error_message)
        ):
            kind = BubbleEventType.OFFLINE
        else:
            return None
        return BubbleEvent(
            kind=kind,
            character_state=decision.character_state,
            happiness_value=(
                decision.peak_happiness_value
                if decision.celebration_triggered
                else decision.happiness_value
            ),
            happiness_delta=decision.happiness_delta,
        )

    def _restore_character(self, generation: int | None = None) -> None:
        if generation is not None and generation != self._character_feedback_generation:
            return
        remaining = self._character_feedback_until - monotonic()
        if remaining > 0:
            QTimer.singleShot(
                max(1, ceil(remaining * 1000)),
                lambda: self._restore_character(generation),
            )
            return
        if not self.config_manager.config.blindfolded:
            self.character.set_state(CharacterState.NORMAL)

    def _show_runtime_error(self, message: str) -> None:
        # 日志仅保存错误类型文本；调用方已禁止传入标题、截图或密钥。
        safe_message = redact_sensitive_text(message, 300)
        logging.warning("运行提示：%s", safe_message)
        self.main_window.update_status(safe_message)
        if self.tray.isVisible():
            self.tray.showMessage(
                DISPLAY_NAME,
                safe_message,
                QSystemTrayIcon.MessageIcon.Warning,
                4000,
            )

    def _toggle_observation(self) -> None:
        current = self.config_manager.config
        updated = current.model_copy(
            update={"observation_enabled": not current.observation_enabled}
        )
        self.config_manager.save(AppConfig.model_validate(updated.model_dump()))
        self.main_window._load_config()
        self._apply_config()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason is QSystemTrayIcon.ActivationReason.DoubleClick:
            self.main_window.show_page(MainWindow.PAGE_DASHBOARD)

    def _notify_hidden_to_tray(self) -> None:
        """说明右上角关闭只是隐藏，避免用户把仍显示的桌宠误认为退出失败。"""

        if self._quitting or not self.tray.isVisible():
            return
        self.tray.showMessage(
            DISPLAY_NAME,
            "总窗口已隐藏，桌宠仍在运行。完全退出请使用左侧、角色菜单或托盘菜单。",
            QSystemTrayIcon.MessageIcon.Information,
            5000,
        )

    def shutdown(self) -> None:
        """先同步隐藏全部桌面窗口，再尽力清理后台服务并结束事件循环。"""

        if self._quitting:
            return
        self._quitting = True

        # UI隐藏必须排在可能需要等待本机Agent响应的清理之前，确保退出操作即时可见。
        self.main_window.allow_close()
        self.speech_bubble.prepare_shutdown()
        self.character.prepare_shutdown()
        self.tray.hide()
        self.main_window.hide()

        cleanup_steps = (
            ("观察引擎", self.observation.shutdown),
            ("聊天服务", self.chat_service.shutdown),
            ("认知整理", self.cognition_organizer.shutdown),
            ("气泡服务", self.bubble_service.shutdown),
            ("Codex连接", self.codex_agent.shutdown),
            (
                "ACP连接",
                lambda: [provider.shutdown() for provider in self._acp_providers.values()],
            ),
            ("认知数据库", self.repository.close),
        )
        for name, cleanup in cleanup_steps:
            try:
                cleanup()
            except Exception:
                logging.exception("退出时清理%s失败", name)

        self.speech_bubble.close()
        self.character.close()
        self.main_window.close()
        self.app.quit()

    def _finalize(self) -> None:
        if not self._quitting:
            self.shutdown()


def main() -> int:
    """图形应用入口。"""

    app = QApplication(sys.argv)
    app.setApplicationName(APPLICATION_NAME)
    app.setApplicationDisplayName(DISPLAY_NAME)
    app.setOrganizationName(ORGANIZATION_NAME)
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(APP_STYLE)
    icon = _application_icon()
    app.setWindowIcon(icon)
    splash = _splash(icon)
    splash.show()
    splash_started_at = monotonic()
    app.processEvents()
    try:
        paths = AppPaths.resolve()
        instance_lock = _acquire_single_instance_lock(paths)
        if instance_lock is None:
            splash.close()
            QMessageBox.information(
                None,
                DISPLAY_NAME,
                "Soulvise Plmate 已在运行，请从桌宠或系统托盘打开。",
            )
            return 0
        # 只有取得单实例锁后才能清理，避免删除另一个运行实例正在分析的图片。
        VisionTemporaryImageStore().clear_all()
        _configure_logging(paths.log_dir)
        controller = ApplicationController(app, paths, icon)
    except Exception as exc:
        logging.exception("应用初始化失败")
        splash.showMessage(
            f"启动失败：{exc}",
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
            QColor("#ffb4b8"),
        )
        QTimer.singleShot(5000, app.quit)
        return app.exec()

    def finish_startup() -> None:
        """最低展示时间结束后再显示主界面并关闭启动页。"""

        controller.show()
        splash.finish(controller.main_window)

    QTimer.singleShot(_remaining_splash_delay_ms(splash_started_at), finish_startup)
    # QApplication 不持有 Python 对象引用，需将控制器挂到应用避免被回收。
    app.controller = controller  # type: ignore[attr-defined]
    app.instance_lock = instance_lock  # type: ignore[attr-defined]
    return app.exec()
