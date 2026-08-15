"""认知隔离、保留策略与安全导出测试。"""

from __future__ import annotations

import zipfile
from datetime import UTC, datetime, timedelta

from desktop_companion_agent.config import ConfigManager
from desktop_companion_agent.models import CognitionRule, ObservationEvent, RuleMode
from desktop_companion_agent.services.context_exporter import ContextExporter
from desktop_companion_agent.services.data_export import DataExportService
from desktop_companion_agent.storage.repository import CognitionRepository


def test_rules_and_contexts_are_strictly_separated(tmp_path) -> None:
    repository = CognitionRepository(tmp_path / "data.sqlite3")
    repository.add_rule(
        CognitionRule(
            mode=RuleMode.SUPERVISION,
            title="监督专属规则",
            keywords=["危险词"],
        )
    )
    repository.add_rule(
        CognitionRule(
            mode=RuleMode.COMPANION,
            title="陪看专属兴趣",
            keywords=["天文"],
        )
    )
    exporter = ContextExporter(repository, tmp_path / "contexts")
    supervision, companion = exporter.export_all()
    supervision_text = supervision.read_text(encoding="utf-8")
    companion_text = companion.read_text(encoding="utf-8")
    assert "监督专属规则" in supervision_text
    assert "陪看专属兴趣" not in supervision_text
    assert "陪看专属兴趣" in companion_text
    assert "监督专属规则" not in companion_text
    repository.close()


def test_expired_events_are_deleted(tmp_path) -> None:
    repository = CognitionRepository(tmp_path / "data.sqlite3")
    repository.add_event(
        ObservationEvent(
            mode=RuleMode.SUPERVISION,
            expires_at=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
            summary="过期摘要",
        )
    )
    assert repository.purge_expired_events() == 1
    assert repository.list_events() == []
    repository.close()


def test_export_contains_no_secret_or_raw_image(tmp_path) -> None:
    manager = ConfigManager(tmp_path / "config.json")
    manager.load()
    repository = CognitionRepository(tmp_path / "data.sqlite3")
    context_dir = tmp_path / "contexts"
    ContextExporter(repository, context_dir).export_all()
    archive_path = DataExportService(manager, repository, context_dir).export(
        tmp_path / "export.zip"
    )
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        combined = "\n".join(
            archive.read(name).decode("utf-8") for name in names if not name.endswith("/")
        )
    assert "api_key" not in combined.lower()
    assert not any(name.lower().endswith((".png", ".jpg", ".jpeg")) for name in names)
    repository.close()
