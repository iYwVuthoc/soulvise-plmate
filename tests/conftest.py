"""测试公共夹具。"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def qt_app() -> QApplication:
    """复用一个离屏 Qt 应用，避免测试创建多个 QApplication。"""

    app = QApplication.instance() or QApplication([])
    app.setApplicationName("SoulvisePlmateTests")
    return app
