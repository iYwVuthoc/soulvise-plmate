"""总控制窗口及设置、认知、Agent、隐私页面。"""

from __future__ import annotations

import html
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from desktop_companion_agent.branding import DISPLAY_NAME
from desktop_companion_agent.config import (
    DEFAULT_BLOCKING_MODERATION_CATEGORIES,
    AgentRoutingSettings,
    AppConfig,
    ConfigManager,
)
from desktop_companion_agent.models import (
    AgentApprovalRequest,
    AgentCapability,
    AgentConnectionMode,
    AgentConnectionState,
    AgentProfile,
    AgentProviderManifest,
    CognitionDraftSchema,
    CognitionRule,
    InterestJudgment,
    ObservationEventKind,
    RuleMode,
    RuleSource,
    utc_now_iso,
)
from desktop_companion_agent.paths import AppPaths
from desktop_companion_agent.security import SecretStore
from desktop_companion_agent.services.acp_agent import (
    ACP_PROVIDER_PRESETS,
    AcpAgentProvider,
)
from desktop_companion_agent.services.agent_providers import (
    CapabilityRouter,
    ProviderRegistry,
)
from desktop_companion_agent.services.chat import OpenAIChatService
from desktop_companion_agent.services.codex_agent import (
    CODEX_DESKTOP_AUMID,
    CODEX_PROFILE_ID,
    CodexAgentService,
)
from desktop_companion_agent.services.cognition_organizer import (
    CognitionOrganizerService,
    automatic_rule_title,
    parse_cognition_terms,
)
from desktop_companion_agent.services.connectors import ConnectorRegistry
from desktop_companion_agent.services.data_export import DataExportService
from desktop_companion_agent.services.intervention import (
    InterventionService,
    is_safe_external_url,
    normalize_redirect_url,
)
from desktop_companion_agent.services.temporary_images import (
    default_vision_temporary_directory,
)
from desktop_companion_agent.storage.repository import CognitionRepository
from desktop_companion_agent.ui.cognition_dialog import CognitionOrganizerDialog
from desktop_companion_agent.ui.dashboard_widgets import ToggleSwitch, TownSceneryWidget


class MainWindow(QMainWindow):
    """面向普通用户的总控制窗口。"""

    config_changed = Signal()
    exit_requested = Signal()
    hidden_to_tray = Signal()
    character_should_float = Signal()

    PAGE_DASHBOARD = 0
    PAGE_SETTINGS = 1
    PAGE_COGNITION = 2
    PAGE_AGENTS = 3
    PAGE_PRIVACY = 4

    def __init__(
        self,
        config_manager: ConfigManager,
        repository: CognitionRepository,
        secret_store: SecretStore,
        chat_service: OpenAIChatService,
        connector_registry: ConnectorRegistry,
        paths: AppPaths,
        organizer_service: CognitionOrganizerService,
        codex_service: CodexAgentService | None = None,
        capability_router: CapabilityRouter | None = None,
        provider_registry: ProviderRegistry | None = None,
        agent_profiles_changed: Callable[[], None] | None = None,
    ):
        super().__init__()
        self.config_manager = config_manager
        self.repository = repository
        self.secret_store = secret_store
        self.chat_service = chat_service
        self.connector_registry = connector_registry
        self.paths = paths
        self.organizer_service = organizer_service
        self.codex_service = codex_service
        self.capability_router = capability_router
        self.provider_registry = provider_registry
        self.agent_profiles_changed = agent_profiles_changed
        self._allow_close = False
        self._character_docked = False
        self._page_animation: QPropertyAnimation | None = None
        self._rule_widgets: dict[RuleMode, dict[str, object]] = {}
        self._organizer_dialog: CognitionOrganizerDialog | None = None
        self._codex_streaming = False
        self._development_streaming = False
        self._acp_streaming = False
        self._routed_chat_provider_id = ""
        self._routed_chat_streaming = False
        self._wired_acp_objects: set[int] = set()
        self.setWindowTitle(DISPLAY_NAME)
        self.resize(1030, 700)
        self.setMinimumSize(960, 680)
        self._build_ui()
        self._load_config()
        self.refresh_cognition()
        self.refresh_agents()
        self._wire_chat()
        self._wire_codex()

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("appShell")
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        navigation = QWidget()
        navigation.setObjectName("navigation")
        navigation.setFixedWidth(202)
        navigation_layout = QVBoxLayout(navigation)
        navigation_layout.setContentsMargins(10, 22, 10, 12)
        navigation_layout.setSpacing(6)
        brand = QLabel(DISPLAY_NAME)
        brand.setObjectName("brandTitle")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle = QLabel("暮色小镇 · 桌面陪伴")
        subtitle.setObjectName("brandSubtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        navigation_layout.addWidget(brand)
        navigation_layout.addWidget(subtitle)
        navigation_layout.addSpacing(12)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        navigation_items = (
            ("总览", QStyle.StandardPixmap.SP_DesktopIcon),
            ("参数设置", QStyle.StandardPixmap.SP_FileDialogDetailedView),
            ("认知积累", QStyle.StandardPixmap.SP_DirIcon),
            ("Agent 中心", QStyle.StandardPixmap.SP_ComputerIcon),
            ("隐私数据", QStyle.StandardPixmap.SP_DriveHDIcon),
        )
        for title, icon in navigation_items:
            self.sidebar.addItem(QListWidgetItem(self._tinted_icon(icon, "#D7BFAF"), title))
        self.sidebar.currentRowChanged.connect(self.show_page)
        navigation_layout.addWidget(self.sidebar, 1)

        town_preview = QLabel()
        town_preview.setObjectName("townPreview")
        town_preview.setFixedHeight(145)
        town_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        splash = QPixmap(str(Path(__file__).parents[1] / "resources" / "splash.png"))
        if not splash.isNull():
            crop = splash.copy(
                0,
                int(splash.height() * 0.32),
                int(splash.width() * 0.62),
                int(splash.height() * 0.68),
            )
            town_preview.setPixmap(
                crop.scaled(
                    178,
                    145,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        navigation_layout.addWidget(town_preview)
        self.exit_button = QPushButton("完全退出程序")
        self.exit_button.setProperty("exitButton", True)
        self.exit_button.setToolTip("停止观察、关闭桌宠和Agent会话并结束程序")
        self.exit_button.clicked.connect(self._confirm_full_exit)
        navigation_layout.addWidget(self.exit_button)
        root.addWidget(navigation)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_dashboard())
        self.pages.addWidget(self._build_settings())
        self.pages.addWidget(self._build_cognition())
        self.pages.addWidget(self._build_agents())
        self.pages.addWidget(self._build_privacy())
        root.addWidget(self.pages, 1)
        self.setCentralWidget(central)
        self.sidebar.setCurrentRow(0)

    @staticmethod
    def _content_page() -> tuple[QScrollArea, QVBoxLayout]:
        page = QScrollArea()
        page.setObjectName("pageScroll")
        page.setWidgetResizable(True)
        page.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content.setObjectName("pageContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 24, 28, 24)
        page.setWidget(content)
        return page, layout

    @staticmethod
    def _page(title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        page, layout = MainWindow._content_page()
        heading = QLabel(title)
        heading.setObjectName("pageHeading")
        description = QLabel(subtitle)
        description.setObjectName("pageSubtitle")
        description.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(description)
        layout.addSpacing(12)
        return page, layout

    @staticmethod
    def _card() -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        return card, layout

    def _build_dashboard(self) -> QWidget:
        page, layout = self._content_page()
        layout.setContentsMargins(22, 20, 24, 16)
        body = QHBoxLayout()
        body.setSpacing(20)

        scenery_column = QVBoxLayout()
        scenery_column.setSpacing(8)
        scenery_title = QLabel("风车书房")
        scenery_title.setObjectName("sectionHeading")
        scenery_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scenery_column.addWidget(scenery_title)
        self.town_scenery = TownSceneryWidget(
            Path(__file__).parents[1] / "resources" / "splash.png"
        )
        self.character_host = self.town_scenery.character_host
        scenery_column.addWidget(self.town_scenery, 1)
        scenery_hint = QLabel("按住角色右键拖出窗外，即可切换为桌面浮动角色")
        scenery_hint.setObjectName("sceneryHint")
        scenery_hint.setWordWrap(True)
        scenery_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scenery_column.addWidget(scenery_hint)
        body.addLayout(scenery_column, 4)

        controls = QFrame()
        controls.setObjectName("dashboardPanel")
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(22, 18, 22, 16)
        controls_layout.setSpacing(0)
        heading = QLabel("观察状态")
        heading.setObjectName("sectionHeading")
        controls_layout.addWidget(heading)
        controls_layout.addSpacing(8)

        self.observation_switch = ToggleSwitch()
        self.supervision_switch = ToggleSwitch()
        self.companion_switch = ToggleSwitch()
        for widget in (self.observation_switch, self.supervision_switch, self.companion_switch):
            widget.stateChanged.connect(self._save_switches)
        controls_layout.addWidget(
            self._dashboard_switch_row(
                "观察总开关",
                "关闭后停止全部截图",
                self.observation_switch,
                QStyle.StandardPixmap.SP_DialogApplyButton,
            )
        )
        controls_layout.addWidget(
            self._dashboard_switch_row(
                "监督模式",
                "命中规则时提醒并拦截",
                self.supervision_switch,
                QStyle.StandardPixmap.SP_MessageBoxWarning,
            )
        )
        controls_layout.addWidget(
            self._dashboard_switch_row(
                "陪看模式",
                "根据兴趣认知调整高兴值",
                self.companion_switch,
                QStyle.StandardPixmap.SP_DialogYesButton,
            )
        )

        happiness = QFrame()
        happiness.setObjectName("dashboardMetric")
        happiness_layout = QVBoxLayout(happiness)
        happiness_layout.setContentsMargins(4, 18, 4, 18)
        happiness_heading = QHBoxLayout()
        happiness_heading.addWidget(self._metric_icon(QStyle.StandardPixmap.SP_DialogSaveButton))
        happiness_title = QLabel("高兴值")
        happiness_title.setObjectName("metricTitle")
        happiness_heading.addWidget(happiness_title)
        happiness_heading.addStretch(1)
        self.happiness_value_label = QLabel("50")
        self.happiness_value_label.setObjectName("metricTitle")
        happiness_heading.addWidget(self.happiness_value_label)
        happiness_layout.addLayout(happiness_heading)
        self.happiness_bar = QProgressBar()
        self.happiness_bar.setRange(0, 100)
        self.happiness_bar.setTextVisible(False)
        happiness_layout.addWidget(self.happiness_bar)
        controls_layout.addWidget(happiness)

        self.interval_label = QLabel("截图间隔：--")
        self.status_label = QLabel("状态：正在初始化")
        self.status_label.setWordWrap(True)
        self.model_label = QLabel("模型：--")
        controls_layout.addWidget(
            self._dashboard_information_row(
                "截图间隔", self.interval_label, QStyle.StandardPixmap.SP_ComputerIcon
            )
        )
        controls_layout.addWidget(
            self._dashboard_information_row(
                "模型 / 网络状态",
                self.model_label,
                QStyle.StandardPixmap.SP_DriveNetIcon,
                self.status_label,
            )
        )
        controls_layout.addStretch(1)
        body.addWidget(controls, 6)
        layout.addLayout(body, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        for title, page_index in (
            ("认知积累", self.PAGE_COGNITION),
            ("打开 Agent", self.PAGE_AGENTS),
            ("参数设置", self.PAGE_SETTINGS),
        ):
            button = QPushButton(title)
            button.clicked.connect(lambda _checked=False, index=page_index: self.show_page(index))
            buttons.addWidget(button)
        layout.addLayout(buttons)
        return page

    def _metric_icon(self, standard_icon: QStyle.StandardPixmap) -> QLabel:
        icon = QLabel()
        icon.setFixedSize(34, 34)
        icon.setPixmap(self._tinted_icon(standard_icon, "#8F2D4A").pixmap(24, 24))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return icon

    def _tinted_icon(self, standard_icon: QStyle.StandardPixmap, color: str) -> QIcon:
        """统一 Qt 标准图标的颜色，使导航与酒红主题保持一致。"""

        source = self.style().standardIcon(standard_icon).pixmap(24, 24)
        result = QPixmap(source.size())
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        painter.drawPixmap(0, 0, source)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(result.rect(), QColor(color))
        painter.end()
        return QIcon(result)

    def _dashboard_switch_row(
        self,
        title: str,
        detail: str,
        switch: ToggleSwitch,
        standard_icon: QStyle.StandardPixmap,
    ) -> QFrame:
        row = QFrame()
        row.setObjectName("dashboardMetric")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(4, 15, 4, 15)
        row_layout.addWidget(self._metric_icon(standard_icon))
        text_layout = QVBoxLayout()
        text_layout.setSpacing(1)
        title_label = QLabel(title)
        title_label.setObjectName("metricTitle")
        detail_label = QLabel(detail)
        detail_label.setObjectName("metricDetail")
        text_layout.addWidget(title_label)
        text_layout.addWidget(detail_label)
        row_layout.addLayout(text_layout, 1)
        row_layout.addWidget(switch)
        return row

    def _dashboard_information_row(
        self,
        title: str,
        value_label: QLabel,
        standard_icon: QStyle.StandardPixmap,
        secondary_label: QLabel | None = None,
    ) -> QFrame:
        row = QFrame()
        row.setObjectName("dashboardMetric")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(4, 14, 4, 14)
        row_layout.addWidget(self._metric_icon(standard_icon))
        text_layout = QVBoxLayout()
        text_layout.setSpacing(1)
        title_label = QLabel(title)
        title_label.setObjectName("metricTitle")
        value_label.setObjectName("metricDetail")
        text_layout.addWidget(title_label)
        text_layout.addWidget(value_label)
        if secondary_label is not None:
            secondary_label.setObjectName("metricDetail")
            text_layout.addWidget(secondary_label)
        row_layout.addLayout(text_layout, 1)
        return row

    def _spin(self, minimum: int, maximum: int, suffix: str = "") -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSuffix(suffix)
        return spin

    @staticmethod
    def _toggle_advanced(button: QToolButton, panel: QWidget, checked: bool) -> None:
        """展开或收起高级选项，并同步三角方向。"""

        panel.setVisible(checked)
        button.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)

    def _build_settings(self) -> QWidget:
        page, layout = self._page(
            "参数设置", "设置会写入带版本号的 config.json；API 密钥只进入系统凭据管理器。"
        )
        tabs = QTabWidget()
        self.settings_tabs = tabs

        capture = QWidget()
        capture_form = QFormLayout(capture)
        self.normal_interval = self._spin(3, 300, " 秒")
        self.active_interval = self._spin(2, 300, " 秒")
        self.stability_seconds = self._spin(5, 120, " 秒")
        self.dedup_minutes = self._spin(1, 1440, " 分钟")
        capture_form.addRow("普通环境截图间隔", self.normal_interval)
        capture_form.addRow("浏览/社交/视频应用间隔", self.active_interval)
        capture_form.addRow("内容稳定时间", self.stability_seconds)
        capture_form.addRow("同一内容计分去重", self.dedup_minutes)
        tabs.addTab(capture, "截图")

        happiness = QWidget()
        happiness_form = QFormLayout(happiness)
        self.initial_happiness = self._spin(0, 100)
        self.interest_increment = self._spin(1, 100)
        self.disinterest_decrement = self._spin(1, 100)
        self.trigger_threshold = self._spin(0, 100)
        self.reset_happiness = self._spin(0, 100)
        self.cooldown_seconds = self._spin(5, 3600, " 秒")
        happiness_form.addRow("启动默认值", self.initial_happiness)
        happiness_form.addRow("感兴趣增加", self.interest_increment)
        happiness_form.addRow("不感兴趣减少", self.disinterest_decrement)
        happiness_form.addRow("触发阈值", self.trigger_threshold)
        happiness_form.addRow("触发后复位", self.reset_happiness)
        happiness_form.addRow("拦截冷却", self.cooldown_seconds)
        tabs.addTab(happiness, "高兴值")

        cloud = QWidget()
        cloud_form = QFormLayout(cloud)
        backup_api_notice = QLabel(
            "此处是可选的备用云端API。默认使用OpenAI官方接口；第三方服务即使采用"
            "相似格式，也不一定支持图片理解、结构化输出或图片审核。"
        )
        backup_api_notice.setObjectName("backup_api_notice")
        backup_api_notice.setWordWrap(True)
        self.vision_model = QLineEdit()
        self.moderation_model = QLineEdit()
        self.chat_model = QLineEdit()
        self.base_url = QLineEdit()
        self.vision_model.setToolTip("模型名称必须由当前连接的API服务实际提供。")
        self.moderation_model.setToolTip(
            "审核模型必须由当前连接的API服务实际提供，并支持程序所需的图片审核接口。"
        )
        self.chat_model.setToolTip("模型名称必须由当前连接的API服务实际提供。")
        self.base_url.setToolTip(
            "默认：https://api.openai.com/v1。修改为第三方地址前，请确认服务支持本程序所需接口。"
        )
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("留空表示不修改")
        key_buttons = QWidget()
        key_layout = QHBoxLayout(key_buttons)
        key_layout.setContentsMargins(0, 0, 0, 0)
        save_key = QPushButton("保存密钥")
        delete_key = QPushButton("删除密钥")
        delete_key.setProperty("secondary", True)
        save_key.clicked.connect(self._save_api_key)
        delete_key.clicked.connect(self._delete_api_key)
        key_layout.addWidget(save_key)
        key_layout.addWidget(delete_key)
        cloud_form.addRow(backup_api_notice)
        cloud_form.addRow("视觉模型", self.vision_model)
        cloud_form.addRow("审核模型", self.moderation_model)
        cloud_form.addRow("聊天模型", self.chat_model)
        cloud_form.addRow("API基础地址（高级）", self.base_url)
        cloud_form.addRow("API 密钥", self.api_key)
        cloud_form.addRow("", key_buttons)

        moderation_toggle = QToolButton()
        moderation_toggle.setText("高级审核类别（默认仅记录）")
        moderation_toggle.setCheckable(True)
        moderation_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        moderation_toggle.setArrowType(Qt.ArrowType.RightArrow)
        moderation_advanced = QWidget()
        self.moderation_toggle = moderation_toggle
        self.moderation_advanced = moderation_advanced
        moderation_layout = QVBoxLayout(moderation_advanced)
        moderation_layout.setContentsMargins(18, 0, 0, 0)
        notice = QLabel(
            "色情、未成年人色情、暴力、血腥暴力默认强制拦截。以下类别默认只记录；"
            "勾选后才允许触发强制拦截。"
        )
        notice.setWordWrap(True)
        moderation_layout.addWidget(notice)
        self.codex_knowledge_enabled = QCheckBox(
            "允许Codex使用通用知识识别高置信度色情、暴力、低俗和精神污染"
        )
        self.codex_knowledge_enabled.setToolTip(
            "不依赖关键词；政治内容仍必须使用你自己填写的监督规则。"
        )
        moderation_layout.addWidget(self.codex_knowledge_enabled)
        category_labels = {
            "harassment": "骚扰",
            "harassment/threatening": "骚扰威胁",
            "hate": "仇恨",
            "hate/threatening": "仇恨威胁",
            "illicit": "违法指导",
            "illicit/violent": "暴力违法指导",
            "self-harm": "自伤",
            "self-harm/instructions": "自伤指导",
            "self-harm/intent": "自伤意图",
        }
        self.extra_moderation_checks: dict[str, QCheckBox] = {}
        for category, label in category_labels.items():
            checkbox = QCheckBox(f"{label}（{category}）")
            self.extra_moderation_checks[category] = checkbox
            moderation_layout.addWidget(checkbox)
        moderation_advanced.hide()
        moderation_toggle.toggled.connect(
            lambda checked: self._toggle_advanced(moderation_toggle, moderation_advanced, checked)
        )
        cloud_form.addRow(moderation_toggle)
        cloud_form.addRow(moderation_advanced)
        tabs.addTab(cloud, "模型与密钥")

        intervention = QWidget()
        intervention_form = QFormLayout(intervention)
        self.redirect_url = QLineEdit()
        self.redirect_url.setPlaceholderText("尚未配置，禁止真实跳转")
        intervention_form.addRow("拦截视频地址", self.redirect_url)
        redirect_buttons = QWidget()
        redirect_buttons_layout = QHBoxLayout(redirect_buttons)
        redirect_buttons_layout.setContentsMargins(0, 0, 0, 0)
        validate_redirect = QPushButton("验证地址")
        test_redirect = QPushButton("测试打开")
        test_redirect.setProperty("secondary", True)
        validate_redirect.clicked.connect(self._validate_redirect)
        test_redirect.clicked.connect(self._test_redirect)
        redirect_buttons_layout.addWidget(validate_redirect)
        redirect_buttons_layout.addWidget(test_redirect)
        redirect_buttons_layout.addStretch(1)
        self.redirect_status = QLabel("支持HTTPS、B站分享文本、短链、BV号和AV号")
        self.redirect_status.setWordWrap(True)
        intervention_form.addRow("", redirect_buttons)
        intervention_form.addRow("状态", self.redirect_status)
        tabs.addTab(intervention, "拦截")

        layout.addWidget(tabs)
        save = QPushButton("保存全部设置")
        save.clicked.connect(self._save_settings)
        layout.addWidget(save, alignment=Qt.AlignmentFlag.AlignRight)
        return page

    def _build_cognition(self) -> QWidget:
        page, layout = self._page(
            "认知积累",
            "监督与陪看严格隔离；Codex会把陪看词条作为主题种子理解近义和同主题内容，"
            "AI建议仍须人工确认。",
        )
        self.cognition_tabs = QTabWidget()
        self.cognition_tabs.addTab(self._build_rule_panel(RuleMode.SUPERVISION), "监督认知")
        self.cognition_tabs.addTab(self._build_rule_panel(RuleMode.COMPANION), "陪看认知")
        self.cognition_tabs.addTab(self._build_history_panel(), "观察历史")
        self.cognition_tabs.addTab(self._build_pending_panel(), "待审核")
        layout.addWidget(self.cognition_tabs)
        return page

    def _build_rule_panel(self, mode: RuleMode) -> QWidget:
        panel = QWidget()
        root = QHBoxLayout(panel)
        listing = QListWidget()
        listing.setObjectName("cognitionRuleList")
        listing.setMinimumWidth(245)
        form_widget = QWidget()
        form_root = QVBoxLayout(form_widget)
        form_root.setContentsMargins(6, 0, 0, 0)

        quick_label = QLabel("关键词或短句")
        quick_label.setObjectName("sectionTitle")
        form_root.addWidget(quick_label)
        helper = QLabel("每行一个，也支持中文/英文逗号和分号；自动去重。")
        helper.setWordWrap(True)
        form_root.addWidget(helper)
        keywords = QPlainTextEdit()
        keywords.setPlaceholderText("例如：擦边直播\n成人资源群\n事故现场无打码")
        keywords.setMaximumHeight(150)
        form_root.addWidget(keywords)

        judgment = None
        if mode is RuleMode.COMPANION:
            judgment_row = QHBoxLayout()
            judgment_row.addWidget(QLabel("这类内容属于："))
            judgment = QComboBox()
            judgment.addItem("感兴趣（增加高兴值）", InterestJudgment.INTERESTED.value)
            judgment.addItem("不感兴趣（减少高兴值）", InterestJudgment.NOT_INTERESTED.value)
            judgment_row.addWidget(judgment, 1)
            form_root.addLayout(judgment_row)

        buttons = QHBoxLayout()
        save = QPushButton("保存认知")
        clear = QPushButton("新建")
        delete = QPushButton("删除")
        ai_organize = QPushButton("让AI帮我整理")
        clear.setProperty("secondary", True)
        delete.setProperty("secondary", True)
        ai_organize.setProperty("secondary", True)
        buttons.addWidget(save)
        buttons.addWidget(clear)
        buttons.addWidget(delete)
        buttons.addWidget(ai_organize)
        if mode is RuleMode.SUPERVISION:
            restore = QPushButton("恢复基础规则")
            restore.setProperty("secondary", True)
            buttons.addWidget(restore)
            restore.clicked.connect(self._restore_starter_rules)
        buttons.addStretch(1)
        form_root.addLayout(buttons)

        advanced_toggle = QToolButton()
        advanced_toggle.setText("高级设置")
        advanced_toggle.setCheckable(True)
        advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
        form_root.addWidget(advanced_toggle, alignment=Qt.AlignmentFlag.AlignLeft)

        advanced = QWidget()
        form = QFormLayout(advanced)
        title = QLineEdit()
        description = QPlainTextEdit()
        description.setMaximumHeight(100)
        examples = QPlainTextEdit()
        examples.setMaximumHeight(70)
        exclusions = QPlainTextEdit()
        exclusions.setMaximumHeight(70)
        priority = self._spin(0, 100)
        enabled = QCheckBox("启用")
        enabled.setChecked(True)
        form.addRow("名称（可留空自动生成）", title)
        form.addRow("说明", description)
        form.addRow("例子（每行一个）", examples)
        form.addRow("例外（每行一个）", exclusions)
        form.addRow("优先级", priority)
        form.addRow("", enabled)
        advanced.hide()
        advanced_toggle.toggled.connect(
            lambda checked, button=advanced_toggle, value=advanced: self._toggle_advanced(
                button, value, checked
            )
        )
        form_root.addWidget(advanced)
        form_root.addStretch(1)
        root.addWidget(listing, 1)
        root.addWidget(form_widget, 2)
        widgets: dict[str, object] = {
            "list": listing,
            "title": title,
            "description": description,
            "keywords": keywords,
            "examples": examples,
            "exclusions": exclusions,
            "judgment": judgment,
            "priority": priority,
            "enabled": enabled,
            "advanced": advanced,
            "advanced_toggle": advanced_toggle,
            "selected_id": "",
        }
        self._rule_widgets[mode] = widgets
        listing.currentItemChanged.connect(
            lambda current, _old, value=mode: self._load_rule(value, current)
        )
        clear.clicked.connect(lambda _checked=False, value=mode: self._clear_rule_form(value))
        save.clicked.connect(lambda _checked=False, value=mode: self._save_rule(value))
        delete.clicked.connect(lambda _checked=False, value=mode: self._delete_rule(value))
        ai_organize.clicked.connect(lambda _checked=False, value=mode: self._open_organizer(value))
        return panel

    def _build_history_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.history_table = QTableWidget(0, 6)
        self.history_table.setHorizontalHeaderLabels(
            ["事件", "时间", "应用", "摘要", "高兴值", "已跳转"]
        )
        self.history_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.history_table)
        buttons = QHBoxLayout()
        refresh = QPushButton("刷新")
        false_positive = QPushButton("标记误判")
        delete_event = QPushButton("删除所选记录")
        false_positive.setProperty("secondary", True)
        delete_event.setProperty("secondary", True)
        refresh.clicked.connect(self.refresh_cognition)
        false_positive.clicked.connect(self._mark_false_positive)
        delete_event.clicked.connect(self._delete_history_event)
        buttons.addWidget(refresh)
        buttons.addWidget(false_positive)
        buttons.addWidget(delete_event)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return panel

    def _build_pending_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.pending_list = QListWidget()
        layout.addWidget(self.pending_list)
        buttons = QHBoxLayout()
        approve = QPushButton("确认并加入认知")
        reject = QPushButton("拒绝")
        reject.setProperty("secondary", True)
        approve.clicked.connect(lambda: self._resolve_pending(True))
        reject.clicked.connect(lambda: self._resolve_pending(False))
        buttons.addWidget(approve)
        buttons.addWidget(reject)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return panel

    def _build_agents(self) -> QWidget:
        page, layout = self._page(
            "本机 Agent 中心",
            "连接电脑中的 Agent，并由一个主 Agent 提供聊天、视觉、监督、认知和项目辅助能力。",
        )
        tabs = QTabWidget()

        codex = QWidget()
        codex_layout = QVBoxLayout(codex)
        status_card, status_layout = self._card()
        title_row = QHBoxLayout()
        codex_title = QLabel("Codex 深度连接")
        codex_title.setObjectName("sectionHeading")
        self.codex_state_badge = QLabel("等待检测")
        self.codex_state_badge.setObjectName("statusPill")
        title_row.addWidget(codex_title)
        title_row.addStretch(1)
        title_row.addWidget(self.codex_state_badge)
        status_layout.addLayout(title_row)
        self.codex_detail_label = QLabel("使用官方本机 SDK；登录令牌不进入 Soulvise 数据库。")
        self.codex_detail_label.setWordWrap(True)
        status_layout.addWidget(self.codex_detail_label)
        self.codex_capabilities_label = QLabel("当前能力：等待连接")
        self.codex_capabilities_label.setWordWrap(True)
        status_layout.addWidget(self.codex_capabilities_label)
        connect_row = QHBoxLayout()
        self.codex_connect = QPushButton("检测并连接")
        self.codex_browser_login = QPushButton("浏览器登录")
        self.codex_device_login = QPushButton("设备码登录")
        self.codex_logout = QPushButton("退出登录")
        self.codex_disconnect = QPushButton("断开连接")
        self.codex_open_desktop = QPushButton("打开桌面 Codex")
        self.codex_browser_login.setProperty("secondary", True)
        self.codex_device_login.setProperty("secondary", True)
        self.codex_logout.setProperty("secondary", True)
        self.codex_disconnect.setProperty("secondary", True)
        self.codex_open_desktop.setProperty("secondary", True)
        for button in (
            self.codex_connect,
            self.codex_browser_login,
            self.codex_device_login,
            self.codex_logout,
            self.codex_disconnect,
            self.codex_open_desktop,
        ):
            connect_row.addWidget(button)
        connect_row.addStretch(1)
        status_layout.addLayout(connect_row)
        codex_layout.addWidget(status_card)

        source_card, source_layout = self._card()
        source_title = QLabel("Soulvise 源码目录")
        source_title.setObjectName("sectionTitle")
        source_layout.addWidget(source_title)
        source_hint = QLabel(
            "配置后 Codex 可快速读取项目 skill 与架构；普通聊天仍只读，开发修改单独审批。"
        )
        source_hint.setWordWrap(True)
        source_layout.addWidget(source_hint)
        source_row = QHBoxLayout()
        self.codex_workspace = QLineEdit()
        self.codex_workspace.setPlaceholderText("未配置时只能辅助程序运行，不能修改源码")
        browse_source = QPushButton("选择目录")
        save_source = QPushButton("保存并验证")
        browse_source.setProperty("secondary", True)
        source_row.addWidget(self.codex_workspace, 1)
        source_row.addWidget(browse_source)
        source_row.addWidget(save_source)
        source_layout.addLayout(source_row)
        codex_layout.addWidget(source_card)

        self.codex_chat_view = QTextBrowser()
        self.codex_chat_view.setPlaceholderText("连接并登录 Codex 后可在这里开始只读项目聊天")
        self.codex_chat_input = QPlainTextEdit()
        self.codex_chat_input.setMaximumHeight(80)
        self.codex_chat_input.setPlaceholderText("例如：请快速说明这个程序的构造和当前功能")
        codex_chat_row = QHBoxLayout()
        self.codex_chat_send = QPushButton("发送给 Codex")
        self.codex_chat_cancel = QPushButton("取消")
        self.codex_chat_new = QPushButton("新会话")
        self.codex_chat_cancel.setProperty("secondary", True)
        self.codex_chat_new.setProperty("secondary", True)
        codex_chat_row.addWidget(self.codex_chat_send)
        codex_chat_row.addWidget(self.codex_chat_cancel)
        codex_chat_row.addWidget(self.codex_chat_new)
        codex_chat_row.addStretch(1)
        codex_layout.addWidget(self.codex_chat_view, 1)
        codex_layout.addWidget(self.codex_chat_input)
        codex_layout.addLayout(codex_chat_row)
        tabs.addTab(codex, "Codex")

        acp = QWidget()
        acp_layout = QVBoxLayout(acp)
        acp_notice = QLabel(
            "ACP可连接Claude、Gemini及其他可信本机Agent。协议兼容不等于系统沙箱；"
            "Soulvise不会自动下载Agent，认证令牌仍由各Agent自行管理。"
        )
        acp_notice.setWordWrap(True)
        acp_layout.addWidget(acp_notice)
        acp_editor = QHBoxLayout()
        self.acp_list = QListWidget()
        self.acp_list.setMinimumWidth(220)
        acp_editor.addWidget(self.acp_list, 1)
        acp_form_widget = QWidget()
        acp_form = QFormLayout(acp_form_widget)
        self.acp_name = QLineEdit()
        self.acp_preset = QComboBox()
        for preset in ACP_PROVIDER_PRESETS.values():
            self.acp_preset.addItem(preset.display_name, preset.id)
        self.acp_target = QLineEdit()
        self.acp_target.setPlaceholderText("预设可留空；自定义ACP必须选择可信程序")
        self.acp_arguments = QLineEdit()
        self.acp_arguments.setPlaceholderText("每个参数用 | 分隔，不经过Shell")
        self.acp_workspace = QLineEdit()
        self.acp_workspace.setPlaceholderText("可选；项目辅助只读取此目录")
        self.acp_allow_images = QCheckBox("允许该Agent接收单次视觉临时图片")
        acp_form.addRow("名称", self.acp_name)
        acp_form.addRow("预设", self.acp_preset)
        acp_form.addRow("程序路径", self.acp_target)
        acp_form.addRow("附加参数", self.acp_arguments)
        acp_form.addRow("源码目录", self.acp_workspace)
        acp_form.addRow("图片授权", self.acp_allow_images)
        acp_buttons = QHBoxLayout()
        self.acp_save = QPushButton("保存")
        self.acp_connect = QPushButton("连接并自检")
        self.acp_disconnect = QPushButton("断开")
        self.acp_delete = QPushButton("删除")
        self.acp_disconnect.setProperty("secondary", True)
        self.acp_delete.setProperty("secondary", True)
        for button in (
            self.acp_save,
            self.acp_connect,
            self.acp_disconnect,
            self.acp_delete,
        ):
            acp_buttons.addWidget(button)
        acp_form.addRow("", acp_buttons)
        self.acp_status = QLabel("选择配置后可检测连接")
        self.acp_status.setWordWrap(True)
        acp_form.addRow("状态", self.acp_status)
        acp_editor.addWidget(acp_form_widget, 2)
        acp_layout.addLayout(acp_editor)
        self.acp_chat_view = QTextBrowser()
        self.acp_chat_view.setPlaceholderText("连接所选ACP Agent后可开始只读聊天")
        self.acp_chat_input = QPlainTextEdit()
        self.acp_chat_input.setMaximumHeight(75)
        self.acp_chat_input.setPlaceholderText("输入消息；文件、终端与权限请求默认拒绝")
        acp_chat_buttons = QHBoxLayout()
        self.acp_chat_send = QPushButton("发送给所选Agent")
        self.acp_chat_cancel = QPushButton("取消")
        self.acp_chat_cancel.setProperty("secondary", True)
        acp_chat_buttons.addWidget(self.acp_chat_send)
        acp_chat_buttons.addWidget(self.acp_chat_cancel)
        acp_chat_buttons.addStretch(1)
        acp_layout.addWidget(self.acp_chat_view, 1)
        acp_layout.addWidget(self.acp_chat_input)
        acp_layout.addLayout(acp_chat_buttons)
        tabs.addTab(acp, "ACP Agent")

        routing = QWidget()
        routing_layout = QVBoxLayout(routing)
        routing_card, routing_card_layout = self._card()
        routing_card_layout.addWidget(QLabel("主 Agent"))
        self.primary_agent_combo = QComboBox()
        routing_card_layout.addWidget(self.primary_agent_combo)
        route_hint = QLabel("普通用户只需选择一个主 Agent；没有明确授权时不会静默调用其他厂商。")
        route_hint.setWordWrap(True)
        routing_card_layout.addWidget(route_hint)
        routing_card_layout.addWidget(QLabel("单项能力覆盖（可选）"))
        self.capability_override_combos: dict[AgentCapability, QComboBox] = {}
        capability_labels = {
            AgentCapability.CHAT: "聊天",
            AgentCapability.VISION_ANALYSIS: "视觉分析",
            AgentCapability.SEMANTIC_SUPERVISION: "语义监督",
            AgentCapability.COGNITION_ORGANIZE: "认知整理",
            AgentCapability.BUBBLE_POLISH: "气泡润色",
            AgentCapability.WEB_RESEARCH: "联网研究",
            AgentCapability.PROJECT_ASSIST: "项目辅助",
        }
        for capability, label in capability_labels.items():
            combo = QComboBox()
            combo.setProperty("capabilityLabel", label)
            self.capability_override_combos[capability] = combo
            routing_card_layout.addWidget(QLabel(label))
            routing_card_layout.addWidget(combo)
        self.codex_temporary_images = QCheckBox("允许Codex视觉分析时短暂创建图片临时文件")
        self.official_moderation_enhancement = QCheckBox(
            "并行启用OpenAI官方图片审核增强（需要API密钥）"
        )
        routing_card_layout.addWidget(self.codex_temporary_images)
        routing_card_layout.addWidget(self.official_moderation_enhancement)
        privacy_hint = QLabel(
            "临时图片仅位于本机专用目录，分析、取消、戴眼罩或暂停后立即删除；官方审核增强默认关闭。"
        )
        privacy_hint.setWordWrap(True)
        routing_card_layout.addWidget(privacy_hint)
        save_primary = QPushButton("保存主 Agent")
        routing_card_layout.addWidget(save_primary, alignment=Qt.AlignmentFlag.AlignLeft)
        routing_layout.addWidget(routing_card)
        self.routing_capability_label = QLabel("能力路由将在连接后显示")
        self.routing_capability_label.setWordWrap(True)
        routing_layout.addWidget(self.routing_capability_label)
        routing_layout.addStretch(1)
        tabs.addTab(routing, "能力路由")

        chat = QWidget()
        chat_layout = QVBoxLayout(chat)
        self.chat_view = QTextBrowser()
        self.chat_input = QPlainTextEdit()
        self.chat_input.setMaximumHeight(90)
        self.chat_input.setPlaceholderText("输入消息；认知上下文只读注入")
        chat_buttons = QHBoxLayout()
        self.chat_send = QPushButton("发送")
        self.chat_cancel = QPushButton("取消")
        new_chat = QPushButton("新会话")
        self.chat_cancel.setProperty("secondary", True)
        new_chat.setProperty("secondary", True)
        self.chat_send.clicked.connect(self._send_chat)
        self.chat_cancel.clicked.connect(self._cancel_routed_chat)
        new_chat.clicked.connect(self._new_chat)
        chat_buttons.addWidget(self.chat_send)
        chat_buttons.addWidget(self.chat_cancel)
        chat_buttons.addWidget(new_chat)
        chat_buttons.addStretch(1)
        chat_layout.addWidget(self.chat_view)
        chat_layout.addWidget(self.chat_input)
        chat_layout.addLayout(chat_buttons)
        tabs.addTab(chat, "主 Agent / API备用聊天")

        launchers = QWidget()
        launcher_layout = QHBoxLayout(launchers)
        self.agent_list = QListWidget()
        launcher_layout.addWidget(self.agent_list, 1)
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        self.agent_name = QLineEdit()
        self.agent_type = QComboBox()
        self.agent_type.addItem("网页入口", "web")
        self.agent_type.addItem("桌面程序", "process")
        self.agent_target = QLineEdit()
        self.agent_arguments = QLineEdit()
        self.agent_arguments.setPlaceholderText("每个参数用 | 分隔，不经过 Shell")
        form.addRow("名称", self.agent_name)
        form.addRow("类型", self.agent_type)
        form.addRow("URL 或程序完整路径", self.agent_target)
        form.addRow("启动参数", self.agent_arguments)
        launcher_buttons = QWidget()
        row = QHBoxLayout(launcher_buttons)
        row.setContentsMargins(0, 0, 0, 0)
        save = QPushButton("保存")
        launch = QPushButton("启动")
        delete = QPushButton("删除")
        delete.setProperty("secondary", True)
        save.clicked.connect(self._save_agent)
        launch.clicked.connect(self._launch_agent)
        delete.clicked.connect(self._delete_agent)
        row.addWidget(save)
        row.addWidget(launch)
        row.addWidget(delete)
        form.addRow("", launcher_buttons)
        launcher_layout.addWidget(form_widget, 2)
        self.agent_list.currentItemChanged.connect(self._load_agent)
        tabs.addTab(launchers, "启动型 Agent")

        development = QWidget()
        development_layout = QVBoxLayout(development)
        dev_notice = QLabel(
            "开发模式默认关闭且不跨重启保留。只读检查可直接执行；任何文件修改、"
            "写入型命令或联网提权都必须由你逐次批准。"
        )
        dev_notice.setWordWrap(True)
        development_layout.addWidget(dev_notice)
        self.development_view = QTextBrowser()
        self.development_input = QPlainTextEdit()
        self.development_input.setMaximumHeight(90)
        self.development_input.setPlaceholderText("配置源码目录后，例如：检查当前测试并提出改进")
        dev_row = QHBoxLayout()
        self.development_send = QPushButton("开始只读开发会话")
        self.development_cancel = QPushButton("取消")
        self.development_cancel.setProperty("secondary", True)
        dev_row.addWidget(self.development_send)
        dev_row.addWidget(self.development_cancel)
        dev_row.addStretch(1)
        development_layout.addWidget(self.development_view, 1)
        development_layout.addWidget(self.development_input)
        development_layout.addLayout(dev_row)
        tabs.addTab(development, "开发模式")

        self.codex_connect.clicked.connect(self._connect_codex)
        self.codex_browser_login.clicked.connect(self._login_codex_browser)
        self.codex_device_login.clicked.connect(self._login_codex_device)
        self.codex_logout.clicked.connect(self._logout_codex)
        self.codex_disconnect.clicked.connect(self._disconnect_codex)
        self.codex_open_desktop.clicked.connect(self._open_codex_desktop)
        browse_source.clicked.connect(self._browse_codex_workspace)
        save_source.clicked.connect(self._save_codex_workspace)
        save_primary.clicked.connect(self._save_primary_agent)
        self.codex_chat_send.clicked.connect(self._send_codex_chat)
        self.codex_chat_cancel.clicked.connect(self._cancel_codex)
        self.codex_chat_new.clicked.connect(self._new_codex_chat)
        self.development_send.clicked.connect(self._send_development_task)
        self.development_cancel.clicked.connect(self._cancel_development)
        self.acp_list.currentItemChanged.connect(self._load_acp)
        self.acp_save.clicked.connect(self._save_acp)
        self.acp_connect.clicked.connect(self._connect_acp)
        self.acp_disconnect.clicked.connect(self._disconnect_acp)
        self.acp_delete.clicked.connect(self._delete_acp)
        self.acp_chat_send.clicked.connect(self._send_acp_chat)
        self.acp_chat_cancel.clicked.connect(self._cancel_acp)
        layout.addWidget(tabs)
        return page

    def _build_privacy(self) -> QWidget:
        page, layout = self._page(
            "隐私数据",
            "默认不长期保存原始截图；主Agent视觉获授权时只创建随请求删除的临时图片。",
        )
        card, card_layout = self._card()
        card_layout.addWidget(QLabel(f"数据目录：{self.paths.data_dir}"))
        card_layout.addWidget(QLabel(f"认知上下文：{self.paths.context_dir}"))
        card_layout.addWidget(QLabel("API 密钥：系统凭据管理器（不进入配置、日志或导出）"))
        temporary_path = default_vision_temporary_directory()
        temporary_label = QLabel(f"视觉临时目录：{temporary_path}（分析、暂停或退出后立即删除）")
        temporary_label.setWordWrap(True)
        card_layout.addWidget(temporary_label)
        layout.addWidget(card)
        export_data = QPushButton("导出非敏感数据")
        clear_history = QPushButton("清空观察历史")
        clear_chat = QPushButton("清空聊天记录")
        clear_cognition = QPushButton("立即删除全部认知")
        export_data.clicked.connect(self._export_data)
        clear_history.clicked.connect(self._clear_history)
        clear_chat.clicked.connect(self._clear_chat)
        clear_cognition.clicked.connect(self._clear_cognition)
        layout.addWidget(export_data)
        layout.addWidget(clear_history)
        layout.addWidget(clear_chat)
        layout.addWidget(clear_cognition)
        layout.addStretch(1)
        return page

    def _load_config(self) -> None:
        config = self.config_manager.config
        for switch, value in (
            (self.observation_switch, config.observation_enabled),
            (self.supervision_switch, config.supervision_enabled),
            (self.companion_switch, config.companion_enabled),
        ):
            switch.blockSignals(True)
            switch.setChecked(value)
            switch.blockSignals(False)
        capture = config.capture
        self.normal_interval.setValue(capture.normal_interval_seconds)
        self.active_interval.setValue(capture.active_interval_seconds)
        self.stability_seconds.setValue(capture.stability_seconds)
        self.dedup_minutes.setValue(capture.content_dedup_minutes)
        happiness = config.happiness
        self.initial_happiness.setValue(happiness.initial_value)
        self.interest_increment.setValue(happiness.interest_increment)
        self.disinterest_decrement.setValue(happiness.disinterest_decrement)
        self.trigger_threshold.setValue(happiness.trigger_threshold)
        self.reset_happiness.setValue(happiness.reset_value)
        self.cooldown_seconds.setValue(happiness.intervention_cooldown_seconds)
        self.vision_model.setText(config.model.vision_model)
        self.moderation_model.setText(config.model.moderation_model)
        self.chat_model.setText(config.model.chat_model)
        self.base_url.setText(config.model.base_url)
        self.redirect_url.setText(config.intervention.redirect_url)
        valid, _normalized, message = InterventionService.validate_redirect(
            config.intervention.redirect_url
        )
        self.redirect_status.setText(message if valid else "现有地址需要重新验证")
        blocking = set(config.moderation_policy.blocking_categories)
        self.codex_knowledge_enabled.setChecked(config.moderation_policy.codex_knowledge_enabled)
        for category, checkbox in self.extra_moderation_checks.items():
            checkbox.setChecked(category in blocking)
        self.happiness_bar.setValue(config.happiness.initial_value)
        api_status = "已配置" if self.secret_store.get("openai_api_key") else "未配置"
        self.model_label.setText(f"模型：{config.model.vision_model} · API {api_status}")

    def _save_switches(self) -> None:
        current = self.config_manager.config
        updated = current.model_copy(
            update={
                "observation_enabled": self.observation_switch.isChecked(),
                "supervision_enabled": self.supervision_switch.isChecked(),
                "companion_enabled": self.companion_switch.isChecked(),
            }
        )
        self.config_manager.save(AppConfig.model_validate(updated.model_dump()))
        self.config_changed.emit()

    def _save_settings(self) -> None:
        if self.active_interval.value() > self.normal_interval.value():
            QMessageBox.warning(self, "设置无效", "活动应用截图间隔不能大于普通间隔。")
            return
        redirect_url = normalize_redirect_url(self.redirect_url.text())
        if redirect_url and not is_safe_external_url(redirect_url):
            QMessageBox.warning(
                self,
                "拦截地址无效",
                "请填写完整的 HTTPS 地址、B站分享链接或BV号。",
            )
            return
        self.redirect_url.setText(redirect_url)
        current = self.config_manager.config
        capture = current.capture.model_copy(
            update={
                "normal_interval_seconds": self.normal_interval.value(),
                "active_interval_seconds": self.active_interval.value(),
                "stability_seconds": self.stability_seconds.value(),
                "content_dedup_minutes": self.dedup_minutes.value(),
            }
        )
        happiness = current.happiness.model_copy(
            update={
                "initial_value": self.initial_happiness.value(),
                "interest_increment": self.interest_increment.value(),
                "disinterest_decrement": self.disinterest_decrement.value(),
                "trigger_threshold": self.trigger_threshold.value(),
                "reset_value": self.reset_happiness.value(),
                "intervention_cooldown_seconds": self.cooldown_seconds.value(),
            }
        )
        model = current.model.model_copy(
            update={
                "vision_model": self.vision_model.text().strip(),
                "moderation_model": self.moderation_model.text().strip(),
                "chat_model": self.chat_model.text().strip(),
                "base_url": self.base_url.text().strip(),
            }
        )
        intervention = current.intervention.model_copy(update={"redirect_url": redirect_url})
        moderation_policy = current.moderation_policy.model_copy(
            update={
                "codex_knowledge_enabled": self.codex_knowledge_enabled.isChecked(),
                "blocking_categories": [
                    *DEFAULT_BLOCKING_MODERATION_CATEGORIES,
                    *[
                        category
                        for category, checkbox in self.extra_moderation_checks.items()
                        if checkbox.isChecked()
                    ],
                ],
            }
        )
        try:
            updated = AppConfig.model_validate(
                current.model_copy(
                    update={
                        "capture": capture,
                        "happiness": happiness,
                        "model": model,
                        "moderation_policy": moderation_policy,
                        "intervention": intervention,
                    }
                ).model_dump()
            )
        except ValueError as exc:
            QMessageBox.warning(self, "设置无效", str(exc))
            return
        self.config_manager.save(updated)
        self.config_changed.emit()
        QMessageBox.information(self, "已保存", "设置已保存并立即生效。")

    def _validate_redirect(self) -> None:
        """只验证并规范化输入，不打开浏览器，也不改变观察冷却。"""

        valid, normalized, message = InterventionService.validate_redirect(self.redirect_url.text())
        self.redirect_status.setText(message)
        if not valid:
            QMessageBox.warning(self, "拦截地址无效", message)
            return
        self.redirect_url.setText(normalized)
        QMessageBox.information(self, "验证完成", message)

    def _test_redirect(self) -> None:
        """由用户明确点击后测试浏览器；该操作不写历史、不进入冷却。"""

        valid, normalized, message = InterventionService.validate_redirect(self.redirect_url.text())
        self.redirect_status.setText(message)
        if not valid or not normalized:
            QMessageBox.warning(self, "无法测试", message)
            return
        self.redirect_url.setText(normalized)
        opened, result = InterventionService().open_redirect(normalized)
        self.redirect_status.setText(result)
        if opened:
            QMessageBox.information(self, "测试完成", result)
        else:
            QMessageBox.warning(self, "测试失败", result)

    def _save_api_key(self) -> None:
        try:
            self.secret_store.set("openai_api_key", self.api_key.text())
        except (ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "密钥未保存", str(exc))
            return
        self.api_key.clear()
        self._load_config()
        QMessageBox.information(self, "已保存", "API 密钥已写入系统凭据管理器。")

    def _delete_api_key(self) -> None:
        self.secret_store.delete("openai_api_key")
        self.api_key.clear()
        self._load_config()

    def refresh_cognition(self) -> None:
        for mode, widgets in self._rule_widgets.items():
            listing = widgets["list"]
            assert isinstance(listing, QListWidget)
            listing.clear()
            for rule in self.repository.list_rules(mode):
                source = {
                    RuleSource.BUILTIN: "内置",
                    RuleSource.AI_CONFIRMED: "AI确认",
                    RuleSource.AI_PENDING: "待审",
                    RuleSource.USER: "我的",
                }[rule.source]
                item = QListWidgetItem(f"{'✓' if rule.enabled else '○'} [{source}] {rule.title}")
                item.setData(Qt.ItemDataRole.UserRole, rule.id)
                listing.addItem(item)
        events = self.repository.list_events(limit=200)
        self.history_table.setRowCount(len(events))
        for row, event in enumerate(events):
            values = [
                (
                    "🏆 庆祝时刻"
                    if event.event_kind is ObservationEventKind.CELEBRATION
                    else ("监督" if event.mode is RuleMode.SUPERVISION else "陪看")
                ),
                event.created_at[:19].replace("T", " "),
                event.app_name,
                event.summary,
                str(event.happiness_value),
                "是" if event.intervened else "否",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, event.id)
                    item.setData(Qt.ItemDataRole.UserRole + 1, event.event_kind.value)
                if event.pinned:
                    item.setBackground(QColor("#F6D98B"))
                    item.setForeground(QColor("#3A1626"))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.history_table.setItem(row, column, item)
        self.pending_list.clear()
        for change in self.repository.list_pending_changes():
            item = QListWidgetItem(
                f"[{'监督' if change.mode is RuleMode.SUPERVISION else '陪看'}] {change.suggestion}"
            )
            item.setData(Qt.ItemDataRole.UserRole, change.id)
            item.setData(Qt.ItemDataRole.UserRole + 1, change.mode.value)
            item.setData(Qt.ItemDataRole.UserRole + 2, change.suggestion)
            self.pending_list.addItem(item)

    def _rule_by_id(self, mode: RuleMode, rule_id: str) -> CognitionRule | None:
        return next((item for item in self.repository.list_rules(mode) if item.id == rule_id), None)

    def _load_rule(self, mode: RuleMode, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        widgets = self._rule_widgets[mode]
        rule = self._rule_by_id(mode, str(item.data(Qt.ItemDataRole.UserRole)))
        if rule is None:
            return
        widgets["selected_id"] = rule.id
        widgets["title"].setText(rule.title)
        widgets["description"].setPlainText(rule.description)
        widgets["keywords"].setPlainText("\n".join(rule.keywords))
        widgets["examples"].setPlainText("\n".join(rule.examples))
        widgets["exclusions"].setPlainText("\n".join(rule.exclusions))
        judgment = widgets["judgment"]
        if isinstance(judgment, QComboBox):
            index = judgment.findData(rule.interest_judgment.value)
            judgment.setCurrentIndex(max(0, index))
        widgets["priority"].setValue(rule.priority)
        widgets["enabled"].setChecked(rule.enabled)

    def _clear_rule_form(self, mode: RuleMode) -> None:
        widgets = self._rule_widgets[mode]
        widgets["selected_id"] = ""
        for key in ("title", "keywords", "exclusions", "description", "examples"):
            widgets[key].clear()
        judgment = widgets["judgment"]
        if isinstance(judgment, QComboBox):
            judgment.setCurrentIndex(0)
        widgets["priority"].setValue(50)
        widgets["enabled"].setChecked(True)
        widgets["list"].clearSelection()

    def _save_rule(self, mode: RuleMode) -> None:
        widgets = self._rule_widgets[mode]
        try:
            keywords = parse_cognition_terms(widgets["keywords"].toPlainText())
            exclusions = parse_cognition_terms(widgets["exclusions"].toPlainText())
        except ValueError as exc:
            QMessageBox.warning(self, "词条无效", str(exc))
            return
        if not keywords:
            QMessageBox.warning(self, "缺少词条", "请至少填写一个关键词或短句。")
            return
        selected_id = str(widgets["selected_id"])
        existing = self._rule_by_id(mode, selected_id) if selected_id else None
        judgment_widget = widgets["judgment"]
        interest_judgment = (
            InterestJudgment(str(judgment_widget.currentData()))
            if isinstance(judgment_widget, QComboBox)
            else InterestJudgment.INTERESTED
        )
        title = widgets["title"].text().strip() or automatic_rule_title(
            mode, keywords[0], interest_judgment
        )
        values = {
            "mode": mode,
            "title": title,
            "description": widgets["description"].toPlainText().strip(),
            "interest_judgment": interest_judgment,
            "keywords": keywords,
            "examples": [
                line.strip()
                for line in widgets["examples"].toPlainText().splitlines()
                if line.strip()
            ],
            "exclusions": exclusions,
            "priority": widgets["priority"].value(),
            "enabled": widgets["enabled"].isChecked(),
            "updated_at": utc_now_iso(),
        }
        rule = (
            existing.model_copy(update=values) if existing is not None else CognitionRule(**values)
        )
        self.repository.add_rule(rule)
        self.refresh_cognition()
        self.config_changed.emit()

    def _restore_starter_rules(self) -> None:
        answer = QMessageBox.question(
            self,
            "恢复基础规则",
            "只会恢复五条固定内置规则，并覆盖这些固定ID的内容；不会修改你的个人规则。继续吗？",
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return
        restored = self.repository.restore_starter_supervision_rules()
        self.refresh_cognition()
        self.config_changed.emit()
        QMessageBox.information(self, "恢复完成", f"已恢复{len(restored)}条基础规则。")

    def _open_organizer(self, mode: RuleMode) -> None:
        if self._organizer_dialog is not None:
            self._organizer_dialog.raise_()
            self._organizer_dialog.activateWindow()
            return
        dialog = CognitionOrganizerDialog(mode, self.organizer_service, self)
        self._organizer_dialog = dialog
        dialog.drafts_confirmed.connect(
            lambda schema, value=mode: self._save_organized_drafts(value, schema)
        )
        dialog.settings_requested.connect(lambda: self._open_settings_from_organizer(dialog))
        dialog.finished.connect(lambda _result: self._forget_organizer_dialog(dialog))
        dialog.show()

    def _forget_organizer_dialog(self, dialog: CognitionOrganizerDialog) -> None:
        if self._organizer_dialog is dialog:
            self._organizer_dialog = None

    def _open_settings_from_organizer(self, dialog: CognitionOrganizerDialog) -> None:
        """无密钥时先关闭模态窗口，再让设置页可正常操作。"""

        dialog.close()
        self.show_page(self.PAGE_SETTINGS)
        self.settings_tabs.setCurrentIndex(2)

    def _save_organized_drafts(
        self,
        mode: RuleMode,
        schema: CognitionDraftSchema,
    ) -> None:
        for draft in schema.drafts:
            self.repository.add_rule(
                CognitionRule(
                    mode=mode,
                    title=draft.title,
                    description=draft.description,
                    interest_judgment=(
                        draft.interest_judgment
                        if mode is RuleMode.COMPANION
                        else InterestJudgment.INTERESTED
                    ),
                    keywords=draft.keywords,
                    examples=draft.examples,
                    exclusions=draft.exclusions,
                    priority=draft.priority,
                    source=RuleSource.AI_CONFIRMED,
                )
            )
        self.refresh_cognition()
        self.config_changed.emit()
        QMessageBox.information(
            self,
            "认知已保存",
            f"已人工确认并保存{len(schema.drafts)}条AI整理认知。",
        )

    def _delete_rule(self, mode: RuleMode) -> None:
        rule_id = str(self._rule_widgets[mode]["selected_id"])
        if not rule_id:
            return
        self.repository.delete_rule(rule_id)
        self._clear_rule_form(mode)
        self.refresh_cognition()
        self.config_changed.emit()

    def _mark_false_positive(self) -> None:
        row = self.history_table.currentRow()
        if row < 0:
            return
        item = self.history_table.item(row, 0)
        if item is not None:
            if item.data(Qt.ItemDataRole.UserRole + 1) == ObservationEventKind.CELEBRATION.value:
                QMessageBox.information(self, "庆祝时刻", "奖杯记录不是监督误判，可直接删除。")
                return
            self.repository.mark_false_positive(str(item.data(Qt.ItemDataRole.UserRole)))
            self.refresh_cognition()

    def _delete_history_event(self) -> None:
        """允许用户单独删除普通历史或永久置顶的庆祝纪念。"""

        row = self.history_table.currentRow()
        item = self.history_table.item(row, 0) if row >= 0 else None
        if item is None:
            return
        answer = QMessageBox.question(self, "删除记录", "确定删除所选观察记录吗？")
        if answer is QMessageBox.StandardButton.Yes:
            self.repository.delete_event(str(item.data(Qt.ItemDataRole.UserRole)))
            self.refresh_cognition()
            self.config_changed.emit()

    def _resolve_pending(self, approved: bool) -> None:
        item = self.pending_list.currentItem()
        if item is None:
            return
        change_id = str(item.data(Qt.ItemDataRole.UserRole))
        if approved:
            mode = RuleMode(str(item.data(Qt.ItemDataRole.UserRole + 1)))
            suggestion = str(item.data(Qt.ItemDataRole.UserRole + 2))
            self.repository.add_rule(
                CognitionRule(
                    mode=mode,
                    title="已确认的 AI 认知",
                    description=suggestion,
                    source=RuleSource.AI_CONFIRMED,
                )
            )
        self.repository.resolve_pending_change(change_id, approved)
        self.refresh_cognition()
        self.config_changed.emit()

    def refresh_agents(self) -> None:
        self.agent_list.clear()
        self.acp_list.clear()
        profiles = self.repository.list_agent_profiles()
        codex_profile = next((item for item in profiles if item.id == CODEX_PROFILE_ID), None)
        codex_values = {
            "name": "Codex",
            "connector_type": "codex",
            "vendor": "OpenAI",
            "connection_mode": AgentConnectionMode.DEEP,
            "capabilities": [
                AgentCapability.LAUNCH,
                AgentCapability.CHAT,
                AgentCapability.WEB_RESEARCH,
                AgentCapability.PROJECT_ASSIST,
            ],
            "app_user_model_id": CODEX_DESKTOP_AUMID,
            "enabled": True,
        }
        if codex_profile is None:
            codex_profile = AgentProfile(id=CODEX_PROFILE_ID, **codex_values)
            self.repository.add_agent_profile(codex_profile)
        else:
            updated_codex = codex_profile.model_copy(update=codex_values)
            if updated_codex != codex_profile:
                codex_profile = updated_codex
                self.repository.add_agent_profile(codex_profile)

        launch_profiles = [
            item
            for item in profiles
            if item.id != CODEX_PROFILE_ID and item.connector_type in {"web", "process"}
        ]
        if not launch_profiles:
            defaults = [
                AgentProfile(name="ChatGPT", connector_type="web", target="https://chatgpt.com/"),
                AgentProfile(name="Claude", connector_type="web", target="https://claude.ai/"),
            ]
            for profile in defaults:
                self.repository.add_agent_profile(profile)
        profiles = self.repository.list_agent_profiles()
        launch_profiles = [
            item
            for item in profiles
            if item.id != CODEX_PROFILE_ID
            and item.connection_mode is AgentConnectionMode.LAUNCH_ONLY
        ]
        for profile in launch_profiles:
            item = QListWidgetItem(f"{profile.name} · {profile.connector_type}")
            item.setData(Qt.ItemDataRole.UserRole, profile.id)
            self.agent_list.addItem(item)

        deep_profiles = [
            profile
            for profile in profiles
            if profile.enabled
            and profile.connection_mode is AgentConnectionMode.DEEP
            and profile.connector_type in {"codex", "acp"}
        ]
        for profile in deep_profiles:
            if profile.connector_type == "acp":
                preset = ACP_PROVIDER_PRESETS.get(profile.preset_id or "custom")
                preset_name = preset.display_name if preset is not None else "未知预设"
                item = QListWidgetItem(f"{profile.name} · {preset_name}")
                item.setData(Qt.ItemDataRole.UserRole, profile.id)
                self.acp_list.addItem(item)
                self._wire_acp_provider(profile.id)

        self.primary_agent_combo.blockSignals(True)
        self.primary_agent_combo.clear()
        self.primary_agent_combo.addItem("不使用主 Agent", "")
        for profile in deep_profiles:
            self.primary_agent_combo.addItem(profile.name, profile.id)
        current_primary = self.config_manager.config.agent_routing.primary_agent_id
        index = self.primary_agent_combo.findData(current_primary)
        self.primary_agent_combo.setCurrentIndex(max(0, index))
        self.primary_agent_combo.blockSignals(False)
        routing = self.config_manager.config.agent_routing
        for capability, combo in self.capability_override_combos.items():
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("跟随主 Agent", "")
            for profile in deep_profiles:
                combo.addItem(profile.name, profile.id)
            configured = routing.capability_overrides.get(capability.value, "")
            override_index = combo.findData(configured)
            combo.setCurrentIndex(max(0, override_index))
            combo.blockSignals(False)
        self.codex_temporary_images.setChecked(routing.allow_codex_temporary_images)
        self.official_moderation_enhancement.setChecked(routing.official_moderation_enhancement)

        self.codex_workspace.setText(codex_profile.workspace_root)
        if self.codex_service is not None:
            desired = (
                Path(codex_profile.workspace_root).resolve()
                if codex_profile.workspace_root
                else None
            )
            if desired != self.codex_service.workspace_root:
                valid, _message = self.codex_service.set_workspace_root(
                    codex_profile.workspace_root
                )
                if not valid:
                    self.codex_detail_label.setText("已保存的源码目录失效，请重新选择。")
        self._update_routing_label()
        self._update_development_availability()

    def _selected_acp(self) -> AgentProfile | None:
        item = self.acp_list.currentItem()
        if item is None:
            return None
        profile_id = str(item.data(Qt.ItemDataRole.UserRole))
        return next(
            (
                profile
                for profile in self.repository.list_agent_profiles()
                if profile.id == profile_id and profile.connector_type == "acp"
            ),
            None,
        )

    def _load_acp(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        profile = self._selected_acp()
        if profile is None:
            return
        self.acp_name.setText(profile.name)
        preset_index = self.acp_preset.findData(profile.preset_id or "custom")
        self.acp_preset.setCurrentIndex(max(0, preset_index))
        self.acp_target.setText(profile.target)
        self.acp_arguments.setText("|".join(profile.arguments))
        self.acp_workspace.setText(profile.workspace_root)
        self.acp_allow_images.setChecked(profile.allow_image_input)
        self._wire_acp_provider(profile.id)
        provider = self.provider_registry.get(profile.id) if self.provider_registry else None
        if provider is not None:
            self._on_acp_manifest(profile.id, provider.detect())

    def _save_acp(self) -> None:
        name = self.acp_name.text().strip()
        preset_id = str(self.acp_preset.currentData() or "custom")
        target = self.acp_target.text().strip()
        workspace = self.acp_workspace.text().strip()
        if not name:
            QMessageBox.warning(self, "配置不完整", "请填写Agent名称。")
            return
        if preset_id == "custom" and not target:
            QMessageBox.warning(self, "配置不完整", "自定义ACP Agent必须选择程序路径。")
            return
        if workspace and not Path(workspace).expanduser().is_dir():
            QMessageBox.warning(self, "源码目录无效", "源码目录不存在或不是文件夹。")
            return
        existing = self._selected_acp()
        preset = ACP_PROVIDER_PRESETS[preset_id]
        payload = {
            "name": name,
            "connector_type": "acp",
            "target": target,
            "arguments": [
                value.strip() for value in self.acp_arguments.text().split("|") if value.strip()
            ],
            "vendor": preset.vendor,
            "connection_mode": AgentConnectionMode.DEEP,
            "workspace_root": workspace,
            "capabilities": [],
            "preset_id": preset_id,
            "allow_image_input": self.acp_allow_images.isChecked(),
            "enabled": True,
        }
        profile = existing.model_copy(update=payload) if existing else AgentProfile(**payload)
        self.repository.add_agent_profile(profile)
        if self.agent_profiles_changed is not None:
            self.agent_profiles_changed()
        self.refresh_agents()
        self._select_list_profile(self.acp_list, profile.id)
        QMessageBox.information(self, "ACP Agent", "配置已保存。请点击“连接并自检”。")

    @staticmethod
    def _select_list_profile(widget: QListWidget, profile_id: str) -> None:
        for index in range(widget.count()):
            item = widget.item(index)
            if str(item.data(Qt.ItemDataRole.UserRole)) == profile_id:
                widget.setCurrentItem(item)
                return

    def _delete_acp(self) -> None:
        profile = self._selected_acp()
        if profile is None:
            return
        answer = QMessageBox.question(
            self,
            "删除ACP Agent",
            f"确定删除“{profile.name}”吗？Agent自身账号和令牌不会被删除。",
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return
        current = self.config_manager.config
        cleaned_overrides = {
            capability: provider_id
            for capability, provider_id in current.agent_routing.capability_overrides.items()
            if provider_id != profile.id
        }
        routing_updates: dict[str, object] = {
            "capability_overrides": cleaned_overrides,
        }
        if current.agent_routing.primary_agent_id == profile.id:
            fallback = ""
            if self.provider_registry is not None:
                codex = self.provider_registry.get(CODEX_PROFILE_ID)
                if (
                    codex is not None
                    and codex.detect().state is not AgentConnectionState.NOT_DETECTED
                ):
                    fallback = CODEX_PROFILE_ID
            routing_updates["primary_agent_id"] = fallback
            QMessageBox.information(
                self,
                "主 Agent 已回退",
                "已回退到Codex。" if fallback else "Codex不可用，已改为不使用主Agent。",
            )
        routing = current.agent_routing.model_copy(update=routing_updates)
        self.config_manager.save(
            AppConfig.model_validate(
                current.model_copy(update={"agent_routing": routing}).model_dump()
            )
        )
        self.repository.delete_agent_profile(profile.id)
        if self.agent_profiles_changed is not None:
            self.agent_profiles_changed()
        self.refresh_agents()
        self.config_changed.emit()

    def _selected_acp_provider(self) -> AcpAgentProvider | None:
        profile = self._selected_acp()
        if profile is None or self.provider_registry is None:
            return None
        provider = self.provider_registry.get(profile.id)
        return provider if isinstance(provider, AcpAgentProvider) else None

    def _wire_acp_provider(self, profile_id: str) -> None:
        if self.provider_registry is None:
            return
        provider = self.provider_registry.get(profile_id)
        if not isinstance(provider, AcpAgentProvider) or id(provider) in self._wired_acp_objects:
            return
        self._wired_acp_objects.add(id(provider))
        provider.manifest_changed.connect(
            lambda manifest, current_id=profile_id: self._on_acp_manifest(current_id, manifest)
        )
        provider.chat_delta.connect(
            lambda value, current_id=profile_id: self._on_acp_chat_delta(current_id, value)
        )
        provider.chat_delta.connect(
            lambda value, current_id=profile_id: self._on_routed_chat_delta(current_id, value)
        )
        provider.chat_completed.connect(
            lambda value, current_id=profile_id: self._on_acp_chat_completed(current_id, value)
        )
        provider.development_delta.connect(
            lambda value, current_id=profile_id: self._on_acp_development_delta(current_id, value)
        )
        provider.development_completed.connect(
            lambda value, current_id=profile_id: self._on_acp_development_completed(
                current_id, value
            )
        )
        provider.approval_requested.connect(
            lambda request, current_id=profile_id: self._on_acp_approval(current_id, request)
        )
        provider.chat_completed.connect(
            lambda value, current_id=profile_id: self._on_routed_chat_completed(current_id, value)
        )
        provider.error_occurred.connect(
            lambda value, current_id=profile_id: self._on_acp_error(current_id, value)
        )

    def _connect_acp(self) -> None:
        provider = self._selected_acp_provider()
        if provider is None:
            QMessageBox.information(self, "请选择Agent", "请先保存并选择一个ACP Agent。")
            return
        provider.connect()

    def _disconnect_acp(self) -> None:
        provider = self._selected_acp_provider()
        if provider is not None:
            provider.disconnect()

    def _send_acp_chat(self) -> None:
        provider = self._selected_acp_provider()
        message = self.acp_chat_input.toPlainText().strip()
        if provider is None or not provider.chat(message):
            QMessageBox.warning(self, "无法发送", "所选ACP Agent尚未连接或正在忙。")
            return
        self.acp_chat_view.append(f"<p><b>你：</b>{html.escape(message)}</p>")
        self.acp_chat_view.append("<p><b>Agent：</b></p>")
        self._acp_streaming = False
        self.acp_chat_input.clear()

    def _cancel_acp(self) -> None:
        provider = self._selected_acp_provider()
        if provider is not None:
            provider.cancel()

    def _on_acp_manifest(self, profile_id: str, manifest: AgentProviderManifest) -> None:
        selected = self._selected_acp()
        if selected is not None and selected.id == profile_id:
            state_labels = {
                AgentConnectionState.NOT_DETECTED: "未检测",
                AgentConnectionState.SIGNED_OUT: "已检测，未连接",
                AgentConnectionState.CONNECTING: "连接与自检中",
                AgentConnectionState.READY: "可用",
                AgentConnectionState.RATE_LIMITED: "限流",
                AgentConnectionState.OFFLINE: "离线",
                AgentConnectionState.ERROR: "错误",
            }
            capability_text = "、".join(value.value for value in manifest.capabilities)
            self.acp_status.setText(
                f"{state_labels[manifest.state]}：{manifest.detail}"
                + (f"；能力：{capability_text}" if capability_text else "")
            )
            self.acp_chat_send.setEnabled(manifest.state is AgentConnectionState.READY)
        self._update_routing_label()
        self._update_development_availability()

    def _on_acp_chat_delta(self, profile_id: str, value: str) -> None:
        selected = self._selected_acp()
        if selected is None or selected.id != profile_id:
            return
        self._acp_streaming = True
        self.acp_chat_view.moveCursor(QTextCursor.MoveOperation.End)
        self.acp_chat_view.insertPlainText(value)

    def _on_acp_chat_completed(self, profile_id: str, value: str) -> None:
        selected = self._selected_acp()
        if selected is None or selected.id != profile_id:
            return
        if not self._acp_streaming and value:
            self.acp_chat_view.append(f"<p>{html.escape(value)}</p>")
        self.acp_chat_view.append("<br>")
        self._acp_streaming = False

    def _on_acp_error(self, profile_id: str, message: str) -> None:
        selected = self._selected_acp()
        if selected is not None and selected.id == profile_id:
            self.acp_status.setText(message)

    def _on_acp_development_delta(self, profile_id: str, value: str) -> None:
        provider_id = (
            self.capability_router.provider_id_for(AgentCapability.PROJECT_ASSIST)
            if self.capability_router is not None
            else ""
        )
        if provider_id != profile_id:
            return
        self._on_development_delta(value)

    def _on_acp_development_completed(self, profile_id: str, value: str) -> None:
        provider_id = (
            self.capability_router.provider_id_for(AgentCapability.PROJECT_ASSIST)
            if self.capability_router is not None
            else ""
        )
        if provider_id != profile_id:
            return
        self._on_development_completed(value)

    def _on_acp_approval(self, profile_id: str, request: AgentApprovalRequest) -> None:
        provider = self.provider_registry.get(profile_id) if self.provider_registry else None
        if not isinstance(provider, AcpAgentProvider):
            return
        details = request.summary
        if request.command:
            details += f"\n\n操作类别：{request.command}"
        if request.cwd:
            details += f"\n\n工作目录：{request.cwd}"
        details += (
            "\n\nACP不是操作系统沙箱，只应连接可信Agent。是否仅允许本次操作？审批超时将自动拒绝。"
        )
        answer = QMessageBox.question(
            self,
            "ACP开发权限审批",
            details,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        provider.resolve_approval(
            request.id,
            answer is QMessageBox.StandardButton.Yes,
        )

    def _update_routing_label(self) -> None:
        if self.provider_registry is None:
            return
        provider_id = str(self.primary_agent_combo.currentData() or "")
        provider = self.provider_registry.get(provider_id) if provider_id else None
        if provider is None:
            self.routing_capability_label.setText("主 Agent：未使用")
            return
        manifest = provider.detect()
        capabilities = "、".join(value.value for value in manifest.capabilities) or "等待连接"
        self.routing_capability_label.setText(f"主 Agent：{manifest.display_name} · {capabilities}")

    def _update_development_availability(self) -> None:
        provider = (
            self.capability_router.resolve(AgentCapability.PROJECT_ASSIST)
            if self.capability_router is not None
            else None
        )
        self.development_send.setEnabled(provider is not None)

    def _selected_agent(self) -> AgentProfile | None:
        item = self.agent_list.currentItem()
        if item is None:
            return None
        profile_id = str(item.data(Qt.ItemDataRole.UserRole))
        return next(
            (value for value in self.repository.list_agent_profiles() if value.id == profile_id),
            None,
        )

    def _load_agent(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        profile = self._selected_agent()
        if profile is None:
            return
        self.agent_name.setText(profile.name)
        index = self.agent_type.findData(profile.connector_type)
        self.agent_type.setCurrentIndex(max(0, index))
        self.agent_target.setText(profile.target)
        self.agent_arguments.setText("|".join(profile.arguments))

    def _save_agent(self) -> None:
        name = self.agent_name.text().strip()
        target = self.agent_target.text().strip()
        if not name or not target:
            QMessageBox.warning(self, "配置不完整", "名称和 URL/程序路径不能为空。")
            return
        existing = self._selected_agent()
        payload = {
            "name": name,
            "connector_type": str(self.agent_type.currentData()),
            "target": target,
            "arguments": [item for item in self.agent_arguments.text().split("|") if item],
        }
        profile = existing.model_copy(update=payload) if existing else AgentProfile(**payload)
        self.repository.add_agent_profile(profile)
        self.refresh_agents()

    def _launch_agent(self) -> None:
        profile = self._selected_agent()
        if profile is None:
            QMessageBox.information(self, "请选择 Agent", "请先选择一个 Agent 入口。")
            return
        success, message = self.connector_registry.launch(profile)
        if success:
            self.status_label.setText(f"状态：{message}")
        else:
            QMessageBox.warning(self, "启动失败", message)

    def _delete_agent(self) -> None:
        profile = self._selected_agent()
        if profile is not None:
            self.repository.delete_agent_profile(profile.id)
            self.refresh_agents()

    def _wire_codex(self) -> None:
        """连接 Codex 服务信号；未提供服务时明确禁用深度入口。"""

        service = self.codex_service
        if service is None:
            self.codex_state_badge.setText("服务不可用")
            for button in (
                self.codex_connect,
                self.codex_browser_login,
                self.codex_device_login,
                self.codex_logout,
                self.codex_disconnect,
                self.codex_chat_send,
            ):
                button.setDisabled(True)
            self._update_development_availability()
            return
        service.manifest_changed.connect(self._on_codex_manifest)
        service.browser_login_ready.connect(self._on_codex_browser_login)
        service.device_login_ready.connect(self._on_codex_device_login)
        service.chat_delta.connect(self._on_codex_chat_delta)
        service.chat_completed.connect(self._on_codex_chat_completed)
        service.chat_delta.connect(
            lambda value: self._on_routed_chat_delta(CODEX_PROFILE_ID, value)
        )
        service.chat_completed.connect(
            lambda value: self._on_routed_chat_completed(CODEX_PROFILE_ID, value)
        )
        service.development_delta.connect(self._on_development_delta)
        service.development_completed.connect(self._on_development_completed)
        service.error_occurred.connect(self._on_codex_error)
        service.busy_changed.connect(self._on_codex_busy)
        service.approval_requested.connect(self._on_codex_approval)
        self._on_codex_manifest(service.detect())

    def _on_codex_manifest(self, manifest: AgentProviderManifest) -> None:
        labels = {
            AgentConnectionState.NOT_DETECTED: "未检测",
            AgentConnectionState.SIGNED_OUT: "未登录",
            AgentConnectionState.CONNECTING: "连接中",
            AgentConnectionState.READY: "可用",
            AgentConnectionState.RATE_LIMITED: "限流",
            AgentConnectionState.OFFLINE: "离线",
            AgentConnectionState.ERROR: "错误",
        }
        capability_names = {
            AgentCapability.LAUNCH: "启动",
            AgentCapability.CHAT: "聊天",
            AgentCapability.WEB_RESEARCH: "联网研究",
            AgentCapability.PROJECT_ASSIST: "项目辅助",
            AgentCapability.VISION_ANALYSIS: "视觉分析",
            AgentCapability.SEMANTIC_SUPERVISION: "语义监督",
            AgentCapability.COGNITION_ORGANIZE: "认知整理",
            AgentCapability.BUBBLE_POLISH: "气泡润色",
        }
        self.codex_state_badge.setText(labels[manifest.state])
        version = f" · SDK {manifest.version}" if manifest.version else ""
        self.codex_detail_label.setText(f"{manifest.detail}{version}")
        names = [capability_names[item] for item in manifest.capabilities]
        self.codex_capabilities_label.setText(
            "当前能力：" + ("、".join(names) if names else "尚不可用")
        )
        self._update_routing_label()
        ready = manifest.state is AgentConnectionState.READY
        signed_out = manifest.state is AgentConnectionState.SIGNED_OUT
        detected = manifest.state is not AgentConnectionState.NOT_DETECTED
        self.codex_connect.setEnabled(
            detected and manifest.state is not AgentConnectionState.CONNECTING
        )
        self.codex_browser_login.setEnabled(signed_out)
        self.codex_device_login.setEnabled(signed_out)
        self.codex_logout.setEnabled(ready)
        self.codex_disconnect.setEnabled(
            manifest.state
            in {
                AgentConnectionState.SIGNED_OUT,
                AgentConnectionState.READY,
                AgentConnectionState.RATE_LIMITED,
                AgentConnectionState.OFFLINE,
                AgentConnectionState.ERROR,
            }
        )
        self.codex_chat_send.setEnabled(ready)
        self._update_development_availability()
        self.codex_open_desktop.setEnabled(manifest.desktop_available)

    def _connect_codex(self) -> None:
        if self.codex_service is not None:
            self.codex_service.connect()

    def _login_codex_browser(self) -> None:
        if self.codex_service is not None:
            self.codex_service.login_browser()

    def _login_codex_device(self) -> None:
        if self.codex_service is not None:
            self.codex_service.login_device_code()

    def _logout_codex(self) -> None:
        if self.codex_service is not None:
            self.codex_service.logout()

    def _disconnect_codex(self) -> None:
        if self.codex_service is not None:
            self.codex_service.disconnect()

    def _on_codex_browser_login(self, url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))
        QMessageBox.information(self, "Codex 登录", "浏览器已打开，完成登录后请返回本程序。")

    def _on_codex_device_login(self, url: str, code: str) -> None:
        QDesktopServices.openUrl(QUrl(url))
        QMessageBox.information(
            self,
            "Codex 设备码登录",
            f"浏览器已打开。请输入设备码：\n\n{code}\n\nSoulvise 不会保存该设备码。",
        )

    def _open_codex_desktop(self) -> None:
        if self.codex_service is None:
            return
        success, message = self.codex_service.open_desktop()
        if success:
            self.update_status(message)
        else:
            QMessageBox.warning(self, "启动失败", message)

    def _browse_codex_workspace(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "选择 Soulvise Plmate 源码目录",
            self.codex_workspace.text().strip() or str(Path.cwd()),
        )
        if directory:
            self.codex_workspace.setText(directory)

    def _save_codex_workspace(self) -> None:
        if self.codex_service is None:
            return
        value = self.codex_workspace.text().strip()
        valid, message = self.codex_service.set_workspace_root(value)
        if not valid:
            QMessageBox.warning(self, "源码目录无效", message)
            return
        profile = next(
            (item for item in self.repository.list_agent_profiles() if item.id == CODEX_PROFILE_ID),
            None,
        )
        if profile is not None:
            self.repository.add_agent_profile(profile.model_copy(update={"workspace_root": value}))
        self._on_codex_manifest(self.codex_service.manifest)
        QMessageBox.information(self, "源码目录", message)

    def _save_primary_agent(self) -> None:
        selected = str(self.primary_agent_combo.currentData() or "")
        overrides = {
            capability.value: str(combo.currentData())
            for capability, combo in self.capability_override_combos.items()
            if str(combo.currentData() or "")
        }
        current = self.config_manager.config
        routing = AgentRoutingSettings.model_validate(
            current.agent_routing.model_copy(
                update={
                    "primary_agent_id": selected,
                    "capability_overrides": overrides,
                    "allow_codex_temporary_images": self.codex_temporary_images.isChecked(),
                    "official_moderation_enhancement": (
                        self.official_moderation_enhancement.isChecked()
                    ),
                }
            ).model_dump()
        )
        self.config_manager.save(
            AppConfig.model_validate(
                current.model_copy(update={"agent_routing": routing}).model_dump()
            )
        )
        self.config_changed.emit()
        self._update_routing_label()
        self._update_development_availability()
        QMessageBox.information(self, "主 Agent", "主 Agent 设置已保存。")

    def _send_codex_chat(self) -> None:
        if self.codex_service is None:
            return
        message = self.codex_chat_input.toPlainText().strip()
        if self.codex_service.send_chat(message):
            self.codex_chat_view.append(f"<p><b>你：</b>{html.escape(message)}</p>")
            self.codex_chat_view.append("<p><b>Codex：</b></p>")
            self._codex_streaming = False
            self.codex_chat_input.clear()

    def _on_codex_chat_delta(self, delta: str) -> None:
        self._codex_streaming = True
        self.codex_chat_view.moveCursor(QTextCursor.MoveOperation.End)
        self.codex_chat_view.insertPlainText(delta)

    def _on_codex_chat_completed(self, message: str) -> None:
        if not self._codex_streaming:
            self.codex_chat_view.append(f"<p>{html.escape(message)}</p>")
        self.codex_chat_view.append("<br>")
        self._codex_streaming = False

    def _new_codex_chat(self) -> None:
        if self.codex_service is not None:
            self.codex_service.new_chat()
        self.codex_chat_view.clear()

    def _send_development_task(self) -> None:
        message = self.development_input.toPlainText().strip()
        if not message:
            return
        provider = (
            self.capability_router.resolve(AgentCapability.PROJECT_ASSIST)
            if self.capability_router is not None
            else None
        )
        accepted = False
        provider_name = "主 Agent"
        if isinstance(provider, AcpAgentProvider):
            accepted = provider.send_development_task(message)
            provider_name = provider.profile.name
        elif provider is self.codex_service and self.codex_service is not None:
            accepted = self.codex_service.send_development_task(message)
            provider_name = "Codex"
        if accepted:
            self.development_view.append(f"<p><b>你：</b>{html.escape(message)}</p>")
            self.development_view.append(
                f"<p><b>{html.escape(provider_name)}（临时开发会话）：</b></p>"
            )
            self._development_streaming = False
            self.development_input.clear()
        else:
            QMessageBox.information(
                self,
                "开发会话不可用",
                "请先连接支持项目辅助的主Agent，并为它保存有效的Soulvise源码目录。",
            )

    def _on_development_delta(self, delta: str) -> None:
        self._development_streaming = True
        self.development_view.moveCursor(QTextCursor.MoveOperation.End)
        self.development_view.insertPlainText(delta)

    def _on_development_completed(self, message: str) -> None:
        if not self._development_streaming:
            self.development_view.append(f"<p>{html.escape(message)}</p>")
        self.development_view.append("<br>")
        self._development_streaming = False

    def _cancel_codex(self) -> None:
        if self.codex_service is not None:
            self.codex_service.cancel()

    def _cancel_development(self) -> None:
        provider = (
            self.capability_router.resolve(AgentCapability.PROJECT_ASSIST)
            if self.capability_router is not None
            else None
        )
        if isinstance(provider, AcpAgentProvider):
            provider.cancel_development()
        elif provider is self.codex_service and self.codex_service is not None:
            self.codex_service.cancel()

    def _on_codex_busy(self, busy: bool) -> None:
        self.codex_chat_cancel.setEnabled(busy)
        codex_handles_development = (
            self.capability_router is not None
            and self.capability_router.provider_id_for(AgentCapability.PROJECT_ASSIST)
            == CODEX_PROFILE_ID
        )
        if codex_handles_development:
            self.development_cancel.setEnabled(busy)
        if busy:
            self.codex_chat_send.setDisabled(True)
            if codex_handles_development:
                self.development_send.setDisabled(True)
        elif self.codex_service is not None:
            self._on_codex_manifest(self.codex_service.manifest)

    def _on_codex_error(self, message: str) -> None:
        QMessageBox.warning(self, "Codex 连接失败", message)

    def _on_codex_approval(self, request: AgentApprovalRequest) -> None:
        """每次审批只提供本次允许或拒绝，不保存永久授权。"""

        details = request.summary
        if request.command:
            details += f"\n\n请求内容：{request.command}"
        if request.cwd:
            details += f"\n\n工作目录：{request.cwd}"
        details += "\n\n是否仅允许本次操作？审批超时将自动拒绝。"
        answer = QMessageBox.question(
            self,
            "Codex 开发权限审批",
            details,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if self.codex_service is not None:
            self.codex_service.resolve_approval(
                request.id,
                answer is QMessageBox.StandardButton.Yes,
            )

    def _wire_chat(self) -> None:
        self.chat_service.response_ready.connect(self._chat_response)
        self.chat_service.error_occurred.connect(
            lambda message: QMessageBox.warning(self, "聊天失败", message)
        )
        self.chat_service.busy_changed.connect(lambda busy: self.chat_send.setDisabled(busy))

    def _send_chat(self) -> None:
        message = self.chat_input.toPlainText().strip()
        if not message:
            return
        provider_id = (
            self.capability_router.provider_id_for(AgentCapability.CHAT)
            if self.capability_router is not None
            else ""
        )
        if provider_id:
            provider = self.capability_router.resolve(AgentCapability.CHAT)
            if provider is None:
                QMessageBox.warning(
                    self,
                    "主 Agent 不可用",
                    "聊天所选Provider尚未连接或不支持聊天；不会静默切换到其他云端API。",
                )
                return
            self._routed_chat_provider_id = provider.provider_id
            self._routed_chat_streaming = False
            if not provider.chat(message):
                QMessageBox.warning(self, "无法发送", "主 Agent正忙或当前不可用。")
                return
            self.chat_view.append(f"<p><b>你：</b>{html.escape(message)}</p>")
            self.chat_view.append("<p><b>主 Agent：</b></p>")
            self.chat_input.clear()
            return
        if self.chat_service.send(message):
            self._routed_chat_provider_id = ""
            self.chat_view.append(f"<p><b>你：</b>{html.escape(message)}</p>")
            self.chat_input.clear()

    def _on_routed_chat_delta(self, provider_id: str, value: str) -> None:
        if self._routed_chat_provider_id != provider_id:
            return
        self._routed_chat_streaming = True
        self.chat_view.moveCursor(QTextCursor.MoveOperation.End)
        self.chat_view.insertPlainText(value)

    def _on_routed_chat_completed(self, provider_id: str, value: str) -> None:
        if self._routed_chat_provider_id != provider_id:
            return
        if not self._routed_chat_streaming and value:
            self.chat_view.append(f"<p>{html.escape(value)}</p>")
        self.chat_view.append("<br>")
        self._routed_chat_streaming = False

    def _cancel_routed_chat(self) -> None:
        if self._routed_chat_provider_id and self.provider_registry is not None:
            provider = self.provider_registry.get(self._routed_chat_provider_id)
            if provider is not None:
                provider.cancel()
                return
        self.chat_service.cancel()

    def _chat_response(self, message: str) -> None:
        safe = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.chat_view.append(f"<p><b>AI：</b>{safe.replace(chr(10), '<br>')}</p>")

    def _new_chat(self) -> None:
        if self._routed_chat_provider_id == CODEX_PROFILE_ID and self.codex_service is not None:
            self.codex_service.new_chat()
        elif self._routed_chat_provider_id and self.provider_registry is not None:
            provider = self.provider_registry.get(self._routed_chat_provider_id)
            if provider is not None:
                provider.cancel()
        self._routed_chat_provider_id = ""
        self._routed_chat_streaming = False
        self.chat_service.new_session()
        self.chat_view.clear()

    def _clear_history(self) -> None:
        answer = QMessageBox.question(
            self,
            "确认清空",
            "将删除全部观察摘要、奖杯纪念和相关待审核建议，是否继续？",
        )
        if answer is QMessageBox.StandardButton.Yes:
            self.repository.clear_history()
            self.refresh_cognition()
            self.config_changed.emit()

    def _clear_chat(self) -> None:
        answer = QMessageBox.question(self, "确认清空", "将删除全部本地聊天记录，是否继续？")
        if answer is QMessageBox.StandardButton.Yes:
            self.chat_service.cancel()
            self.repository.clear_chat_history()
            self.chat_view.clear()

    def _export_data(self) -> None:
        filename, _filter = QFileDialog.getSaveFileName(
            self,
            "导出非敏感数据",
            str(self.paths.data_dir / "desktop-agent-export.zip"),
            "ZIP 压缩包 (*.zip)",
        )
        if not filename:
            return
        try:
            output = DataExportService(
                self.config_manager, self.repository, self.paths.context_dir
            ).export(Path(filename))
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        QMessageBox.information(self, "导出完成", f"已保存到：\n{output}")

    def _clear_cognition(self) -> None:
        answer = QMessageBox.warning(
            self,
            "确认删除全部认知",
            "此操作将删除监督规则、陪看兴趣、观察摘要和待审核建议，无法撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer is QMessageBox.StandardButton.Yes:
            self.repository.clear_cognition()
            self.refresh_cognition()
            self.config_changed.emit()

    def show_page(self, index: int) -> None:
        if not 0 <= index < self.pages.count():
            return
        previous = self.pages.currentIndex()
        if self._character_docked and previous == self.PAGE_DASHBOARD and index != previous:
            self.character_should_float.emit()
        self.pages.setCurrentIndex(index)
        if self.sidebar.currentRow() != index:
            self.sidebar.blockSignals(True)
            self.sidebar.setCurrentRow(index)
            self.sidebar.blockSignals(False)
        if index == self.PAGE_COGNITION:
            self.refresh_cognition()
        elif index == self.PAGE_AGENTS:
            self.refresh_agents()
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if previous != index:
            self._fade_current_page()

    def _fade_current_page(self) -> None:
        """对当前页面执行约150毫秒淡入，不阻塞交互。"""

        page = self.pages.currentWidget()
        if page is None:
            return
        if self._page_animation is not None:
            self._page_animation.stop()
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(150)
        animation.setStartValue(0.18)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.finished.connect(lambda: page.setGraphicsEffect(None))
        self._page_animation = animation
        animation.start()

    def set_character_docked(self, docked: bool) -> None:
        """同步角色状态机，防止隐藏与页面切换重复发送脱离请求。"""

        self._character_docked = docked

    def update_status(self, message: str) -> None:
        self.status_label.setText(f"状态：{message}")

    def update_interval(self, seconds: int, active: bool) -> None:
        label = "活动应用" if active else "普通环境"
        self.interval_label.setText(f"截图间隔：{seconds} 秒（{label}）")

    def update_happiness(self, value: int) -> None:
        self.happiness_bar.setValue(value)
        self.happiness_value_label.setText(str(value))

    def set_blindfolded(self, enabled: bool) -> None:
        status = "角色已戴眼罩，观察停止" if enabled else "眼罩已摘下"
        self.update_status(status)

    def allow_close(self) -> None:
        self._allow_close = True

    def _confirm_full_exit(self) -> None:
        """明确区分完全退出和右上角关闭按钮的隐藏行为。"""

        answer = QMessageBox.question(
            self,
            "完全退出 Soulvise Plmate",
            "完全退出会停止观察、关闭桌宠和Agent会话。确定退出吗？",
        )
        if answer is QMessageBox.StandardButton.Yes:
            self.exit_requested.emit()

    def hideEvent(self, event) -> None:
        if self._character_docked and not self._allow_close:
            self.character_should_float.emit()
        super().hideEvent(event)

    def changeEvent(self, event) -> None:
        if (
            event.type() is QEvent.Type.WindowStateChange
            and self.isMinimized()
            and self._character_docked
            and not self._allow_close
        ):
            self.character_should_float.emit()
        super().changeEvent(event)

    def closeEvent(self, event) -> None:
        if self._allow_close:
            event.accept()
        else:
            self.hide()
            self.hidden_to_tray.emit()
            event.ignore()
