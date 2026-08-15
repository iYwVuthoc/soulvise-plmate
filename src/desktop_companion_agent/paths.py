"""应用目录管理。

所有运行时文件均写入操作系统认可的应用数据目录，避免硬编码用户路径。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QStandardPaths

from desktop_companion_agent.branding import (
    LEGACY_APPLICATION_NAME,
    LEGACY_ORGANIZATION_NAME,
)

LEGACY_MIGRATION_MARKER = ".legacy-migration-v0.1.2.json"
DATA_DIR_OVERRIDE_ENV = "SOULVISE_PLMATE_DATA_DIR"
LEGACY_DATA_DIR_OVERRIDE_ENV = "SOULVISE_PLMATE_LEGACY_DATA_DIR"


def default_legacy_data_dir() -> Path | None:
    """返回Windows旧版数据目录；其他平台无需执行此次品牌迁移。"""

    if sys.platform != "win32":
        return None
    roaming = os.environ.get("APPDATA")
    if not roaming:
        return None
    return Path(roaming) / LEGACY_ORGANIZATION_NAME / LEGACY_APPLICATION_NAME


def migrate_legacy_data(data_dir: Path, legacy_data_dir: Path | None) -> bool:
    """把旧数据完整复制到新目录，并以目录替换保证迁移原子性。

    仅当新目录完全不存在时执行。任何异常都会清理临时副本并继续保留旧目录，
    让用户修复磁盘或权限问题后可以再次启动重试。
    """

    target = Path(data_dir)
    legacy = Path(legacy_data_dir) if legacy_data_dir is not None else None
    if target.exists() or legacy is None or not legacy.is_dir():
        return False

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.migrating-{uuid4().hex}")
    try:
        shutil.copytree(legacy, temporary, copy_function=shutil.copy2)
        marker = temporary / LEGACY_MIGRATION_MARKER
        marker.write_text(
            json.dumps(
                {
                    "source": f"{LEGACY_ORGANIZATION_NAME}/{LEGACY_APPLICATION_NAME}",
                    "target": target.name,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return True


@dataclass(frozen=True, slots=True)
class AppPaths:
    """集中保存应用使用的文件路径。"""

    data_dir: Path
    config_file: Path
    database_file: Path
    log_dir: Path
    context_dir: Path

    @classmethod
    def resolve(
        cls,
        override: Path | None = None,
        legacy_override: Path | None = None,
    ) -> AppPaths:
        """解析并创建运行时目录。

        测试可通过 ``override`` 使用临时目录，避免污染真实用户数据。
        """

        if override is None:
            configured_data_dir = os.environ.get(DATA_DIR_OVERRIDE_ENV)
            configured_legacy_dir = os.environ.get(LEGACY_DATA_DIR_OVERRIDE_ENV)
            if configured_data_dir:
                # 显式覆盖仅供开发、自动化验收和便携场景使用。
                data_dir = Path(configured_data_dir).expanduser().resolve()
                legacy_data_dir = (
                    Path(configured_legacy_dir).expanduser().resolve()
                    if configured_legacy_dir
                    else default_legacy_data_dir()
                )
            else:
                location = QStandardPaths.writableLocation(
                    QStandardPaths.StandardLocation.AppDataLocation
                )
                data_dir = Path(location)
                legacy_data_dir = default_legacy_data_dir()
        else:
            data_dir = Path(override)
            legacy_data_dir = Path(legacy_override) if legacy_override is not None else None

        migrate_legacy_data(data_dir, legacy_data_dir)

        log_dir = data_dir / "logs"
        context_dir = data_dir / "contexts"
        for directory in (data_dir, log_dir, context_dir):
            directory.mkdir(parents=True, exist_ok=True)

        return cls(
            data_dir=data_dir,
            config_file=data_dir / "config.json",
            database_file=data_dir / "cognition.sqlite3",
            log_dir=log_dir,
            context_dir=context_dir,
        )
