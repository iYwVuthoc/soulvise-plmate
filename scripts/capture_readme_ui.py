"""使用隔离数据生成README总览图，不读取用户配置或个人信息。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

# Windows离屏插件不会枚举系统中文字库，因此在文档维护时使用正常平台插件
# 短暂显示隔离窗口；其他平台仍可显式传入QT_QPA_PLATFORM覆盖。
if sys.platform != "win32":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from desktop_companion_agent.config import ConfigManager, ModelSettings  # noqa: E402
from desktop_companion_agent.models import CharacterState  # noqa: E402
from desktop_companion_agent.paths import AppPaths  # noqa: E402
from desktop_companion_agent.platforms.window_backend import NullWindowBackend  # noqa: E402
from desktop_companion_agent.security import InMemorySecretStore  # noqa: E402
from desktop_companion_agent.services.chat import OpenAIChatService  # noqa: E402
from desktop_companion_agent.services.cognition_organizer import (  # noqa: E402
    CognitionOrganizerService,
)
from desktop_companion_agent.services.connectors import ConnectorRegistry  # noqa: E402
from desktop_companion_agent.storage.repository import CognitionRepository  # noqa: E402
from desktop_companion_agent.ui.character import CharacterWidget  # noqa: E402
from desktop_companion_agent.ui.main_window import MainWindow  # noqa: E402
from desktop_companion_agent.ui.styles import APP_STYLE  # noqa: E402


def _character_assets(root: Path) -> dict[CharacterState, Path]:
    """按公开状态名加载仓库内正式角色动画。"""

    directory = root / "src" / "desktop_companion_agent" / "resources" / "characters"
    return {
        state: directory / f"{state.value}.gif"
        for state in CharacterState
        if (directory / f"{state.value}.gif").is_file()
    }


def main() -> int:
    """渲染1030×700总览，并在结束时清理全部临时运行数据。"""

    root = Path(__file__).resolve().parents[1]
    output = root / "docs" / "images" / "overview-v0.2.0.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setStyleSheet(APP_STYLE)

    with TemporaryDirectory(prefix="soulvise-readme-") as data_directory:
        paths = AppPaths.resolve(Path(data_directory))
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
        character = CharacterWidget(NullWindowBackend(), _character_assets(root))
        window.resize(1030, 700)
        window.show()
        character.set_embedded(window.character_host)
        window.set_character_docked(True)
        app.processEvents()
        QTest.qWait(300)
        if not window.grab().save(str(output)):
            raise RuntimeError("README总览图保存失败")

        character.close()
        window.allow_close()
        window.close()
        organizer.shutdown()
        chat.shutdown()
        repository.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
