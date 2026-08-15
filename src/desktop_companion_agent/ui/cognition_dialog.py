"""AI认知整理的输入、预览与人工确认对话框。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from desktop_companion_agent.models import CognitionDraftSchema, RuleMode
from desktop_companion_agent.services.cognition_organizer import CognitionOrganizerService


class CognitionOrganizerDialog(QDialog):
    """让用户检查AI草稿后再明确写入当前模式。"""

    drafts_confirmed = Signal(object)
    settings_requested = Signal()

    def __init__(
        self,
        mode: RuleMode,
        service: CognitionOrganizerService,
        parent=None,
    ):
        super().__init__(parent)
        self.mode = mode
        self.service = service
        self._schema: CognitionDraftSchema | None = None
        mode_name = "监督认知" if mode is RuleMode.SUPERVISION else "陪看认知"
        self.setWindowTitle(f"让AI帮我整理 · {mode_name}")
        self.setModal(True)
        self.resize(650, 570)

        layout = QVBoxLayout(self)
        title = QLabel(f"只整理当前的{mode_name}，不会读取另一类认知")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        layout.addWidget(
            QLabel("写下自然语言想法（最多2000字）。截图文字不会在这里执行任何命令。")
        )
        self.input_edit = QPlainTextEdit()
        self.input_edit.setPlaceholderText(
            "例如：我希望角色提醒成人服务引流，但性教育和医学科普不要触发。"
        )
        self.input_edit.setMaximumBlockCount(100)
        layout.addWidget(self.input_edit, 1)

        action_row = QHBoxLayout()
        self.organize_button = QPushButton("生成草稿预览")
        self.cancel_request_button = QPushButton("取消整理")
        self.cancel_request_button.setProperty("secondary", True)
        self.cancel_request_button.setEnabled(False)
        action_row.addWidget(self.organize_button)
        action_row.addWidget(self.cancel_request_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        self.status_label = QLabel("AI结果只会显示在下方，确认前不会保存。")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.settings_button = QPushButton("前往设置")
        self.settings_button.setProperty("secondary", True)
        self.settings_button.hide()
        layout.addWidget(self.settings_button, alignment=Qt.AlignmentFlag.AlignLeft)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("等待生成草稿……")
        layout.addWidget(self.preview, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.confirm_button = QPushButton("确认保存")
        self.confirm_button.setEnabled(False)
        close_button = QPushButton("关闭")
        close_button.setProperty("secondary", True)
        footer.addWidget(close_button)
        footer.addWidget(self.confirm_button)
        layout.addLayout(footer)

        self.organize_button.clicked.connect(self._organize)
        self.cancel_request_button.clicked.connect(self._cancel_request)
        self.confirm_button.clicked.connect(self._confirm)
        close_button.clicked.connect(self.close)
        self.settings_button.clicked.connect(self.settings_requested)
        service.drafts_ready.connect(self._show_drafts)
        service.error_occurred.connect(self._show_error)
        service.busy_changed.connect(self._set_busy)

    def _organize(self) -> None:
        text = self.input_edit.toPlainText()
        if len(text) > 2000:
            self._show_error("输入内容不能超过2000字")
            return
        if not text.strip():
            self._show_error("请先写下希望AI整理的内容")
            return
        self._schema = None
        self.confirm_button.setEnabled(False)
        self.preview.clear()
        self.settings_button.hide()
        if self.service.organize(text, self.mode):
            self.status_label.setText("正在整理，请稍候……")

    def _cancel_request(self) -> None:
        self.service.cancel()
        self.status_label.setText("已取消；原输入仍保留，迟到结果将被忽略。")

    def _set_busy(self, busy: bool) -> None:
        self.organize_button.setEnabled(not busy)
        self.cancel_request_button.setEnabled(busy)

    def _show_error(self, message: str) -> None:
        self.status_label.setText(message)
        self.settings_button.setVisible("API密钥" in message)

    def _show_drafts(self, schema: CognitionDraftSchema) -> None:
        self._schema = schema
        lines: list[str] = []
        for index, draft in enumerate(schema.drafts, start=1):
            lines.extend(
                [
                    f"{index}. {draft.title}",
                    f"   关键词：{'、'.join(draft.keywords)}",
                    f"   说明：{draft.description or '无'}",
                    f"   例外：{'、'.join(draft.exclusions) or '无'}",
                    "",
                ]
            )
        self.preview.setPlainText("\n".join(lines).rstrip())
        self.status_label.setText("草稿生成完成；请检查后再确认保存。")
        self.confirm_button.setEnabled(True)

    def _confirm(self) -> None:
        if self._schema is None:
            return
        self.drafts_confirmed.emit(self._schema)
        self.accept()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self.service.busy:
            self.service.cancel()
        super().closeEvent(event)
