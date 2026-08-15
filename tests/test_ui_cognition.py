"""简化认知编辑器的保存与旧规则兼容测试。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QLabel, QMessageBox

from desktop_companion_agent.config import ConfigManager, ModelSettings
from desktop_companion_agent.models import (
    CognitionDraft,
    CognitionDraftSchema,
    InterestJudgment,
    RuleMode,
    RuleSource,
)
from desktop_companion_agent.paths import AppPaths
from desktop_companion_agent.security import InMemorySecretStore
from desktop_companion_agent.services.chat import OpenAIChatService
from desktop_companion_agent.services.cognition_organizer import CognitionOrganizerService
from desktop_companion_agent.services.connectors import ConnectorRegistry
from desktop_companion_agent.storage.repository import CognitionRepository
from desktop_companion_agent.ui.main_window import MainWindow


def _window(tmp_path):
    paths = AppPaths.resolve(tmp_path / "data")
    manager = ConfigManager(paths.config_file)
    manager.load()
    repository = CognitionRepository(paths.database_file)
    secrets = InMemorySecretStore()
    chat = OpenAIChatService(ModelSettings(), secrets, repository, paths.context_dir)
    organizer = CognitionOrganizerService(ModelSettings(), secrets)
    window = MainWindow(
        manager,
        repository,
        secrets,
        chat,
        ConnectorRegistry(),
        paths,
        organizer,
    )
    return window, repository, chat, organizer


def test_simple_editor_saves_one_rule_with_terms_and_automatic_title(qt_app, tmp_path) -> None:
    window, repository, chat, organizer = _window(tmp_path)
    widgets = window._rule_widgets[RuleMode.COMPANION]
    widgets["keywords"].setPlainText("天文，宇宙; 天文\n深空摄影")
    widgets["judgment"].setCurrentIndex(1)
    window._save_rule(RuleMode.COMPANION)
    rules = repository.list_rules(RuleMode.COMPANION)
    assert len(rules) == 1
    assert rules[0].title == "不感兴趣：天文"
    assert rules[0].keywords == ["天文", "宇宙", "深空摄影"]
    assert rules[0].interest_judgment is InterestJudgment.NOT_INTERESTED
    assert repository.list_rules(RuleMode.SUPERVISION)
    window.allow_close()
    window.close()
    organizer.shutdown()
    chat.shutdown()
    repository.close()


def test_invalid_redirect_never_overwrites_previous_valid_value(
    qt_app,
    tmp_path,
    monkeypatch,
) -> None:
    window, repository, chat, organizer = _window(tmp_path)
    current = window.config_manager.config
    intervention = current.intervention.model_copy(
        update={"redirect_url": "https://b23.tv/OldVideo"}
    )
    window.config_manager.save(current.model_copy(update={"intervention": intervention}))
    window._load_config()
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(message),
    )
    window.redirect_url.setText("file:///C:/unsafe.exe")
    window._save_settings()
    assert warnings
    assert (
        window.config_manager.config.intervention.redirect_url
        == "https://b23.tv/OldVideo"
    )
    window.allow_close()
    window.close()
    organizer.shutdown()
    chat.shutdown()
    repository.close()


def test_backup_api_wording_and_existing_storage_behavior(
    qt_app,
    tmp_path,
    monkeypatch,
) -> None:
    """备用API需准确说明兼容边界，同时保持地址和密钥存储行为不变。"""

    window, repository, chat, organizer = _window(tmp_path)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    labels = {label.text() for label in window.findChildren(QLabel)}
    notice = window.findChild(QLabel, "backup_api_notice")
    assert notice is not None
    assert "默认使用OpenAI官方接口" in notice.text()
    assert "OpenAI 兼容接口" not in labels
    assert "API基础地址（高级）" in labels
    assert "第三方地址" in window.base_url.toolTip()
    assert "实际提供" in window.vision_model.toolTip()
    assert "图片审核接口" in window.moderation_model.toolTip()
    assert "实际提供" in window.chat_model.toolTip()

    custom_base_url = "https://api.example.invalid/v1"
    window.base_url.setText(custom_base_url)
    window._save_settings()
    assert window.config_manager.config.model.base_url == custom_base_url
    window._load_config()
    assert window.base_url.text() == custom_base_url

    window.api_key.setText("test-secret-value")
    window._save_api_key()
    assert window.secret_store.get("openai_api_key") == "test-secret-value"
    assert window.api_key.text() == ""
    window.allow_close()
    window.close()
    organizer.shutdown()
    chat.shutdown()
    repository.close()


def test_redirect_test_open_does_not_save_configuration(
    qt_app,
    tmp_path,
    monkeypatch,
) -> None:
    window, repository, chat, organizer = _window(tmp_path)
    opened: list[str] = []
    monkeypatch.setattr(
        QDesktopServices,
        "openUrl",
        lambda url: opened.append(url.toString()) or True,
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    window.redirect_url.setText("BV1xx411c7mD")
    window._test_redirect()
    assert opened == ["https://www.bilibili.com/video/BV1xx411c7mD"]
    assert (
        window.config_manager.config.intervention.redirect_url
        == "https://www.bilibili.com/video/BV1LtMy63EbP"
    )
    window.allow_close()
    window.close()
    organizer.shutdown()
    chat.shutdown()
    repository.close()


def test_editing_builtin_preserves_identity_source_and_created_time(qt_app, tmp_path) -> None:
    window, repository, chat, organizer = _window(tmp_path)
    listing = window._rule_widgets[RuleMode.SUPERVISION]["list"]
    item = listing.item(0)
    window._load_rule(RuleMode.SUPERVISION, item)
    selected_id = str(item.data(Qt.ItemDataRole.UserRole))
    before = window._rule_by_id(RuleMode.SUPERVISION, selected_id)
    widgets = window._rule_widgets[RuleMode.SUPERVISION]
    widgets["keywords"].setPlainText("新短句；第二短句")
    window._save_rule(RuleMode.SUPERVISION)
    after = window._rule_by_id(RuleMode.SUPERVISION, selected_id)
    assert before is not None and after is not None
    assert after.id == before.id
    assert after.source is RuleSource.BUILTIN
    assert after.created_at == before.created_at
    assert after.keywords == ["新短句", "第二短句"]
    window.allow_close()
    window.close()
    organizer.shutdown()
    chat.shutdown()
    repository.close()


def test_ai_drafts_write_only_after_explicit_confirmation(
    qt_app, tmp_path, monkeypatch
) -> None:
    window, repository, chat, organizer = _window(tmp_path)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    schema = CognitionDraftSchema(
        drafts=[CognitionDraft(title="宇宙兴趣", keywords=["星系", "望远镜"])]
    )
    assert repository.list_rules(RuleMode.COMPANION) == []
    window._save_organized_drafts(RuleMode.COMPANION, schema)
    rules = repository.list_rules(RuleMode.COMPANION)
    assert len(rules) == 1
    assert rules[0].source is RuleSource.AI_CONFIRMED
    assert rules[0].keywords == ["星系", "望远镜"]
    assert all(
        rule.mode is RuleMode.SUPERVISION
        for rule in repository.list_rules(RuleMode.SUPERVISION)
    )
    window.allow_close()
    window.close()
    organizer.shutdown()
    chat.shutdown()
    repository.close()
