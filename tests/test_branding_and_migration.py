"""品牌常量、旧数据迁移和密钥兼容迁移测试。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from desktop_companion_agent import __version__
from desktop_companion_agent.branding import (
    APPLICATION_NAME,
    DISPLAY_NAME,
    EXECUTABLE_NAME,
    KEYRING_SERVICE_NAME,
    ORGANIZATION_NAME,
)
from desktop_companion_agent.paths import (
    DATA_DIR_OVERRIDE_ENV,
    LEGACY_DATA_DIR_OVERRIDE_ENV,
    LEGACY_MIGRATION_MARKER,
    AppPaths,
    migrate_legacy_data,
)
from desktop_companion_agent.security import KeyringSecretStore


def test_branding_constants_are_consistent() -> None:
    assert DISPLAY_NAME == "Soulvise Plmate"
    assert APPLICATION_NAME == "SoulvisePlmate"
    assert ORGANIZATION_NAME == "Soulvise"
    assert EXECUTABLE_NAME == "SoulvisePlmate.exe"
    assert KEYRING_SERVICE_NAME == "SoulvisePlmate"
    assert __version__ == "0.2.0"


def test_legacy_data_is_copied_once_and_old_data_is_preserved(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    target = tmp_path / "Soulvise" / "SoulvisePlmate"
    (legacy / "contexts").mkdir(parents=True)
    (legacy / "config.json").write_text('{"schema_version": 1}', encoding="utf-8")
    (legacy / "cognition.sqlite3").write_bytes(b"legacy-database")
    (legacy / "contexts" / "companion_context.md").write_text("旧认知", encoding="utf-8")

    paths = AppPaths.resolve(target, legacy)

    assert paths.data_dir == target
    assert legacy.is_dir()
    assert (target / "config.json").read_text(encoding="utf-8") == '{"schema_version": 1}'
    assert (target / "cognition.sqlite3").read_bytes() == b"legacy-database"
    assert (target / LEGACY_MIGRATION_MARKER).is_file()

    (legacy / "config.json").write_text("旧目录后来发生变化", encoding="utf-8")
    AppPaths.resolve(target, legacy)
    assert (target / "config.json").read_text(encoding="utf-8") == '{"schema_version": 1}'


def test_existing_new_data_is_never_overwritten(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    target = tmp_path / "new"
    legacy.mkdir()
    target.mkdir()
    (legacy / "config.json").write_text("legacy", encoding="utf-8")
    (target / "config.json").write_text("new", encoding="utf-8")

    assert migrate_legacy_data(target, legacy) is False
    assert (target / "config.json").read_text(encoding="utf-8") == "new"


def test_environment_override_keeps_runtime_migration_isolated(
    tmp_path: Path, monkeypatch
) -> None:
    legacy = tmp_path / "isolated-legacy"
    target = tmp_path / "isolated-new"
    legacy.mkdir()
    (legacy / "config.json").write_text("isolated", encoding="utf-8")
    monkeypatch.setenv(DATA_DIR_OVERRIDE_ENV, str(target))
    monkeypatch.setenv(LEGACY_DATA_DIR_OVERRIDE_ENV, str(legacy))

    paths = AppPaths.resolve()

    assert paths.data_dir == target.resolve()
    assert (target / "config.json").read_text(encoding="utf-8") == "isolated"
    assert legacy.is_dir()


def test_failed_migration_rolls_back_temporary_copy(tmp_path: Path, monkeypatch) -> None:
    legacy = tmp_path / "legacy"
    target = tmp_path / "SoulvisePlmate"
    legacy.mkdir()
    (legacy / "config.json").write_text("legacy", encoding="utf-8")

    def failing_copytree(_source, destination, **_kwargs):
        destination = Path(destination)
        destination.mkdir(parents=True)
        (destination / "partial.txt").write_text("partial", encoding="utf-8")
        raise OSError("模拟磁盘错误")

    monkeypatch.setattr("desktop_companion_agent.paths.shutil.copytree", failing_copytree)
    with pytest.raises(OSError, match="模拟磁盘错误"):
        migrate_legacy_data(target, legacy)

    assert legacy.is_dir()
    assert not target.exists()
    assert not list(tmp_path.glob(".SoulvisePlmate.migrating-*"))


def test_keyring_secret_is_copied_without_deleting_legacy(monkeypatch) -> None:
    example_secret = "test-only-legacy-secret"
    values = {("DonggeDesktopCompanionAgent", "openai_api_key"): example_secret}

    def get_password(service_name: str, name: str) -> str | None:
        return values.get((service_name, name))

    def set_password(service_name: str, name: str, value: str) -> None:
        values[(service_name, name)] = value

    def delete_password(service_name: str, name: str) -> None:
        values.pop((service_name, name), None)

    fake_keyring = SimpleNamespace(
        get_password=get_password,
        set_password=set_password,
        delete_password=delete_password,
    )
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
    store = KeyringSecretStore()

    assert store.get("openai_api_key") == example_secret
    assert values[("SoulvisePlmate", "openai_api_key")] == example_secret
    assert values[("DonggeDesktopCompanionAgent", "openai_api_key")] == example_secret

    store.delete("openai_api_key")
    assert not values
