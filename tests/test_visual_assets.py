"""正式视觉素材、启动页缩放与展示时长测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtGui import QIcon, QMovie, QPixmap

from desktop_companion_agent import __version__
from desktop_companion_agent.app import (
    MAXIMUM_SPLASH_HEIGHT,
    MAXIMUM_SPLASH_WIDTH,
    _remaining_splash_delay_ms,
    _scaled_splash_pixmap,
)

RESOURCE_ROOT = (
    Path(__file__).parents[1] / "src" / "desktop_companion_agent" / "resources"
)
CHARACTER_FRAMES = {
    "normal.gif": 24,
    "happy.gif": 18,
    "angry.gif": 24,
    "blindfolded.gif": 28,
    "patted.gif": 20,
    "offline.gif": 30,
}


@pytest.mark.parametrize(("filename", "expected_frames"), CHARACTER_FRAMES.items())
def test_character_gif_is_animated_and_transparent(
    qt_app, filename: str, expected_frames: int
) -> None:
    """六种角色动画必须保留原始帧数，并具有透明背景。"""

    del qt_app
    path = RESOURCE_ROOT / "characters" / filename
    with Image.open(path) as image:
        assert image.size == (528, 648)
        assert image.n_frames == expected_frames
        assert image.info.get("transparency") is not None
        for frame_index in (0, image.n_frames // 2, image.n_frames - 1):
            image.seek(frame_index)
            rgba = image.convert("RGBA")
            assert rgba.getpixel((0, 0))[3] == 0
            assert rgba.getpixel((rgba.width - 1, rgba.height - 1))[3] == 0

    movie = QMovie(str(path))
    assert movie.isValid()
    assert movie.frameCount() == expected_frames


def test_application_icon_contains_windows_sizes(qt_app) -> None:
    """ICO 必须包含常用的小图标和高分辨率图标。"""

    del qt_app
    icon = QIcon(str(RESOURCE_ROOT / "app_icon.ico"))
    sizes = {(size.width(), size.height()) for size in icon.availableSizes()}
    assert not icon.isNull()
    assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= sizes


def test_splash_is_scaled_without_cropping(qt_app) -> None:
    """启动图需等比例缩放，且不得超过约定的最大显示区域。"""

    del qt_app
    original = QPixmap(str(RESOURCE_ROOT / "splash.png"))
    scaled = _scaled_splash_pixmap(original)
    assert not original.isNull()
    assert scaled.width() <= MAXIMUM_SPLASH_WIDTH
    assert scaled.height() <= MAXIMUM_SPLASH_HEIGHT
    assert scaled.width() < original.width()
    assert abs(scaled.width() / scaled.height() - original.width() / original.height()) < 0.01


def test_splash_minimum_duration_and_version() -> None:
    """初始化较快时补足1.2秒，初始化较慢时不再额外等待。"""

    assert _remaining_splash_delay_ms(10.0, 10.0) == 1200
    assert _remaining_splash_delay_ms(10.0, 10.5) == 700
    assert _remaining_splash_delay_ms(10.0, 11.5) == 0
    assert __version__ == "0.2.0"


def test_packaging_includes_pinned_codex_runtime() -> None:
    """正式EXE必须同时包含Python SDK和与其匹配的Codex本机运行时。"""

    root = Path(__file__).parents[1]
    spec = (root / "packaging" / "pysidedeploy.spec").read_text(encoding="utf-8")
    assert "--include-package=openai_codex" in spec
    assert "--include-package=codex_cli_bin" in spec
    assert "--include-package=acp" in spec
    assert "--include-raw-dir=.venv/Lib/site-packages/codex_cli_bin/bin" in spec
    assert "--include-raw-dir=.venv/Lib/site-packages/codex_cli_bin/codex-resources" in spec
    assert "--user-plugin=packaging/nuitka_codex_bytecode.py" in spec
    assert "--file-version=0.2.0.0" in spec


def test_installer_uses_stable_overridable_app_id() -> None:
    """正式安装器维持稳定AppId，同时允许隔离的升级测试覆盖它。"""

    root = Path(__file__).parents[1]
    script = (root / "installer" / "SoulvisePlmate.iss").read_text(encoding="utf-8")
    assert '#define MyAppId "{{A1A7519E-D09C-49C2-AEDC-13463264051C}"' in script
    assert "AppId={#MyAppId}" in script


def test_packaging_generates_current_release_hash_manifest() -> None:
    """便携版或安装器构建完成后都必须刷新当前版本SHA-256清单。"""

    root = Path(__file__).parents[1]
    script = (root / "scripts" / "package.ps1").read_text(encoding="utf-8")
    assert "function Write-ReleaseManifest" in script
    assert script.count("Write-ReleaseManifest -Version $AppVersion") == 2
    assert "SHA256SUMS.txt" in script
