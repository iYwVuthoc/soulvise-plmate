"""完全退出流程的即时隐藏和容错清理测试。"""

from __future__ import annotations

from types import SimpleNamespace

from desktop_companion_agent.app import (
    ApplicationController,
    _acquire_single_instance_lock,
)
from desktop_companion_agent.paths import AppPaths


class _Recorder:
    """记录退出调用顺序，并可模拟单个后台服务清理失败。"""

    def __init__(self, name: str, calls: list[str], failing: bool = False):
        self.name = name
        self.calls = calls
        self.failing = failing

    def _record(self, action: str) -> None:
        self.calls.append(f"{self.name}.{action}")
        if self.failing:
            raise RuntimeError("测试清理失败")

    def allow_close(self) -> None:
        self._record("allow_close")

    def prepare_shutdown(self) -> None:
        self._record("prepare_shutdown")

    def hide(self) -> None:
        self._record("hide")

    def shutdown(self) -> None:
        self._record("shutdown")

    def close(self) -> None:
        self._record("close")

    def quit(self) -> None:
        self._record("quit")


def test_shutdown_hides_character_before_background_cleanup_and_survives_error() -> None:
    """后台清理即使失败，角色也应先消失且应用仍会执行退出。"""

    calls: list[str] = []
    controller = SimpleNamespace(
        _quitting=False,
        main_window=_Recorder("main", calls),
        speech_bubble=_Recorder("bubble-ui", calls),
        character=_Recorder("character", calls),
        tray=_Recorder("tray", calls),
        observation=_Recorder("observation", calls),
        chat_service=_Recorder("chat", calls, failing=True),
        cognition_organizer=_Recorder("organizer", calls),
        bubble_service=_Recorder("bubble-service", calls),
        codex_agent=_Recorder("codex", calls),
        repository=_Recorder("repository", calls),
        app=_Recorder("app", calls),
    )

    ApplicationController.shutdown(controller)

    assert controller._quitting is True
    first_cleanup = calls.index("observation.shutdown")
    assert calls.index("character.prepare_shutdown") < first_cleanup
    assert calls.index("bubble-ui.prepare_shutdown") < first_cleanup
    assert calls.index("main.hide") < first_cleanup
    assert calls[-1] == "app.quit"


def test_shutdown_is_idempotent() -> None:
    """重复退出信号不得二次关闭数据库或后台连接。"""

    calls: list[str] = []
    controller = SimpleNamespace(_quitting=True)
    ApplicationController.shutdown(controller)
    assert calls == []


def test_single_instance_lock_can_only_be_held_once(tmp_path) -> None:
    paths = AppPaths.resolve(tmp_path / "single-instance")
    first = _acquire_single_instance_lock(paths)
    assert first is not None
    assert _acquire_single_instance_lock(paths) is None
    first.unlock()
    second = _acquire_single_instance_lock(paths)
    assert second is not None
    second.unlock()
