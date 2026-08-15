"""v0.1.7中文B站进程迁移、时序画面和诊断热修验收。"""

from __future__ import annotations

from io import BytesIO
from time import time

from PIL import Image

from desktop_companion_agent.config import DEFAULT_TRACKED_PROCESSES, ConfigManager
from desktop_companion_agent.services.capture import (
    MAXIMUM_ANALYSIS_EDGE,
    CapturedFrame,
    compose_temporal_frames,
)


def _frame(color: str, fingerprint: int, size: tuple[int, int] = (1600, 900)) -> CapturedFrame:
    image = Image.new("RGB", size, color)
    output = BytesIO()
    image.save(output, format="JPEG", quality=80)
    return CapturedFrame(
        jpeg_bytes=output.getvalue(),
        width=image.width,
        height=image.height,
        fingerprint=fingerprint,
        captured_at=time(),
        app_name="哔哩哔哩.exe",
        window_title="哔哩哔哩",
        window_handle=1,
    )


def test_schema_v4_known_legacy_process_list_adds_chinese_bilibili(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        '{"schema_version":4,"capture":{"tracked_processes":'
        '["chrome.exe","discord.exe","firefox.exe","msedge.exe"]}}',
        encoding="utf-8",
    )
    loaded = ConfigManager(path).load()
    assert loaded.schema_version == 6
    assert "哔哩哔哩.exe" in loaded.capture.tracked_processes
    assert set(DEFAULT_TRACKED_PROCESSES).issubset(loaded.capture.tracked_processes)


def test_schema_v4_preserves_user_custom_process_list(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        '{"schema_version":4,"capture":{"tracked_processes":["my-player.exe"]}}',
        encoding="utf-8",
    )
    loaded = ConfigManager(path).load()
    assert loaded.capture.tracked_processes == ["my-player.exe"]


def test_landscape_temporal_frames_are_memory_only_and_keep_latest_identity() -> None:
    older = _frame("navy", 11)
    latest = _frame("maroon", 22)
    temporal = compose_temporal_frames(older, latest)
    assert temporal.temporal_frame_count == 2
    assert temporal.temporal_layout == "older_top_latest_bottom"
    assert temporal.fingerprint == latest.fingerprint
    assert max(temporal.width, temporal.height) <= MAXIMUM_ANALYSIS_EDGE
    assert older.jpeg_bytes
    assert latest.jpeg_bytes
    with Image.open(BytesIO(temporal.jpeg_bytes)) as image:
        assert image.width == temporal.width
        assert image.height == temporal.height


def test_portrait_temporal_frames_use_left_to_right_layout() -> None:
    temporal = compose_temporal_frames(
        _frame("black", 1, (600, 1200)),
        _frame("white", 2, (600, 1200)),
    )
    assert temporal.temporal_layout == "older_left_latest_right"
    assert max(temporal.width, temporal.height) <= MAXIMUM_ANALYSIS_EDGE
