"""配置校验与安全地址测试。"""

from __future__ import annotations

import json
import webbrowser

import pytest
from pydantic import ValidationError
from PySide6.QtGui import QDesktopServices

from desktop_companion_agent.config import AppConfig, CaptureSettings, ConfigManager
from desktop_companion_agent.security import redact_sensitive_text
from desktop_companion_agent.services.intervention import (
    InterventionService,
    is_safe_external_url,
    normalize_redirect_url,
)


def test_capture_interval_must_not_reverse() -> None:
    with pytest.raises(ValidationError):
        CaptureSettings(normal_interval_seconds=5, active_interval_seconds=10)


def test_config_round_trip_and_no_secret(tmp_path) -> None:
    manager = ConfigManager(tmp_path / "config.json")
    config = AppConfig(observation_enabled=True, companion_enabled=True)
    manager.save(config)
    loaded = manager.load()
    payload = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert loaded.observation_enabled is True
    assert "api_key" not in json.dumps(payload).lower()
    assert not list(tmp_path.glob("*.tmp"))


def test_unversioned_config_is_migrated_in_memory(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"observation_enabled": true}', encoding="utf-8")
    loaded = ConfigManager(path).load()
    assert loaded.schema_version == 6
    assert loaded.moderation_policy.blocking_categories == [
        "sexual",
        "sexual/minors",
        "violence",
        "violence/graphic",
    ]
    assert loaded.observation_enabled is True
    assert loaded.agent_routing.primary_agent_id == "builtin.codex"
    assert loaded.agent_routing.allow_codex_temporary_images is True
    assert loaded.capture.stability_seconds == 5
    assert loaded.moderation_policy.codex_knowledge_enabled is True


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.bilibili.com/video/example", True),
        ("http://localhost:3000", True),
        ("http://127.0.0.1:8000", True),
        ("http://example.com", False),
        ("file:///C:/Windows/System32/cmd.exe", False),
        ("javascript:alert(1)", False),
        ("", False),
    ],
)
def test_safe_external_url(url: str, expected: bool) -> None:
    assert is_safe_external_url(url) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "www.bilibili.com/video/BV1xx411c7mD",
            "https://www.bilibili.com/video/BV1xx411c7mD",
        ),
        ("b23.tv/AbCd123", "https://b23.tv/AbCd123"),
        (
            "复制这条消息 https://b23.tv/AbCd123 立即观看！",
            "https://b23.tv/AbCd123",
        ),
        ("BV1xx411c7mD", "https://www.bilibili.com/video/BV1xx411c7mD"),
        ("av170001", "https://www.bilibili.com/video/av170001"),
        (
            "BV1xx411c7mD?p=2&t=30",
            "https://www.bilibili.com/video/BV1xx411c7mD?p=2&t=30",
        ),
        (
            "https://www.bilibili.com/video/BV1xx411c7mD?p=2&t=30",
            "https://www.bilibili.com/video/BV1xx411c7mD?p=2&t=30",
        ),
        ("https://video.example.test/redirect", "https://video.example.test/redirect"),
        ("example.com/video", "example.com/video"),
    ],
)
def test_normalize_bilibili_redirect(value: str, expected: str) -> None:
    assert normalize_redirect_url(value) == expected


def test_validate_redirect_does_not_open_browser(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        QDesktopServices,
        "openUrl",
        lambda url: opened.append(url.toString()) or True,
    )
    valid, normalized, _message = InterventionService.validate_redirect("av170001")
    assert valid is True
    assert normalized == "https://www.bilibili.com/video/av170001"
    assert opened == []


def test_open_redirect_uses_normalized_latest_value(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        QDesktopServices,
        "openUrl",
        lambda url: opened.append(url.toString()) or True,
    )
    success, _message = InterventionService().open_redirect(
        "分享视频：https://b23.tv/NewVideo 立即观看"
    )
    assert success is True
    assert opened == ["https://b23.tv/NewVideo"]


def test_open_redirect_retries_with_standard_browser_without_cooldown_side_effect(
    monkeypatch,
) -> None:
    retried: list[str] = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda _url: False)
    monkeypatch.setattr(
        webbrowser,
        "open_new_tab",
        lambda url: retried.append(url) or True,
    )
    success, message = InterventionService().open_redirect("BV1xx411c7mD")
    assert success is True
    assert "兼容方式" in message
    assert retried == ["https://www.bilibili.com/video/BV1xx411c7mD"]


def test_invalid_redirect_is_rejected_before_browser(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        QDesktopServices,
        "openUrl",
        lambda url: opened.append(url.toString()) or True,
    )
    success, _message = InterventionService().open_redirect("javascript:alert(1)")
    assert success is False
    assert opened == []


def test_log_redaction() -> None:
    example_key = "sk-" + "example123456"
    text = f"request failed: Bearer abcdefghijk and {example_key} api_key=secret-value"
    redacted = redact_sensitive_text(text)
    assert "abcdefghijk" not in redacted
    assert example_key not in redacted
    assert "secret-value" not in redacted
