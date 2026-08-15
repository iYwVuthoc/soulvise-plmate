"""外部窗口发现、吸附和跟随的跨平台接口。"""

from __future__ import annotations

import ctypes
import os
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WindowRect:
    """外部窗口的屏幕坐标。"""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)


@dataclass(frozen=True, slots=True)
class WindowInfo:
    """供观察引擎使用的最小窗口信息。"""

    handle: int = 0
    title: str = ""
    process_name: str = ""
    process_id: int = 0
    rect: WindowRect | None = None


class WindowBackend(ABC):
    """外部窗口能力接口。"""

    @abstractmethod
    def foreground_window(self) -> WindowInfo:
        """返回当前前台窗口。"""

    @abstractmethod
    def find_snap_target(self, x: int, y: int, tolerance: int = 24) -> WindowInfo | None:
        """查找适合角色坐下的窗口顶边。"""

    @abstractmethod
    def window_info(self, handle: int) -> WindowInfo | None:
        """读取指定窗口的最新状态。"""

    @abstractmethod
    def is_fullscreen(self, handle: int) -> bool:
        """判断窗口是否覆盖所属显示器。"""

    @abstractmethod
    def is_minimized(self, handle: int) -> bool:
        """判断窗口是否最小化。"""


class NullWindowBackend(WindowBackend):
    """不支持原生窗口查询的平台降级实现。"""

    def foreground_window(self) -> WindowInfo:
        return WindowInfo(process_name="unknown")

    def find_snap_target(self, x: int, y: int, tolerance: int = 24) -> WindowInfo | None:
        del x, y, tolerance
        return None

    def window_info(self, handle: int) -> WindowInfo | None:
        del handle
        return None

    def is_fullscreen(self, handle: int) -> bool:
        del handle
        return False

    def is_minimized(self, handle: int) -> bool:
        del handle
        return False


if sys.platform == "win32":
    from ctypes import wintypes

    class _MonitorInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        ]


class WindowsWindowBackend(WindowBackend):
    """基于 Win32 API 的外部窗口后端。

    仅在 Windows 构造，其他平台由工厂返回降级后端。
    """

    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("WindowsWindowBackend 只能在 Windows 使用")
        self._user32 = ctypes.windll.user32
        self._own_pid = os.getpid()

    def _title(self, handle: int) -> str:
        length = self._user32.GetWindowTextLengthW(handle)
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        self._user32.GetWindowTextW(handle, buffer, length + 1)
        return buffer.value.strip()

    def _process(self, handle: int) -> tuple[int, str]:
        process_id = ctypes.c_ulong(0)
        self._user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
        name = ""
        try:
            import psutil

            name = psutil.Process(process_id.value).name().lower()
        except Exception:
            pass
        return int(process_id.value), name

    def _rect(self, handle: int) -> WindowRect | None:
        rect = wintypes.RECT()
        if not self._user32.GetWindowRect(handle, ctypes.byref(rect)):
            return None
        value = WindowRect(rect.left, rect.top, rect.right, rect.bottom)
        if value.width <= 0 or value.height <= 0:
            return None
        return value

    def _eligible(self, handle: int) -> bool:
        if not self._user32.IsWindow(handle) or not self._user32.IsWindowVisible(handle):
            return False
        if self._user32.IsIconic(handle):
            return False
        process_id, _ = self._process(handle)
        return process_id not in {0, self._own_pid} and bool(self._title(handle))

    def foreground_window(self) -> WindowInfo:
        handle = int(self._user32.GetForegroundWindow())
        info = self.window_info(handle)
        return info or WindowInfo(handle=handle, process_name="unknown")

    def window_info(self, handle: int) -> WindowInfo | None:
        if not handle or not self._user32.IsWindow(handle):
            return None
        process_id, process_name = self._process(handle)
        return WindowInfo(
            handle=int(handle),
            title=self._title(handle),
            process_name=process_name,
            process_id=process_id,
            rect=self._rect(handle),
        )

    def find_snap_target(self, x: int, y: int, tolerance: int = 24) -> WindowInfo | None:
        candidates: list[tuple[int, WindowInfo]] = []
        enum_proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def callback(handle: int, _parameter: int) -> bool:
            if not self._eligible(handle):
                return True
            info = self.window_info(int(handle))
            if info is None or info.rect is None:
                return True
            rect = info.rect
            if rect.left <= x <= rect.right and abs(rect.top - y) <= tolerance:
                candidates.append((abs(rect.top - y), info))
            return True

        callback_pointer = enum_proc_type(callback)
        self._user32.EnumWindows(callback_pointer, 0)
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def is_fullscreen(self, handle: int) -> bool:
        info = self.window_info(handle)
        if info is None or info.rect is None:
            return False
        monitor = self._user32.MonitorFromWindow(handle, 2)
        if not monitor:
            return False
        monitor_info = _MonitorInfo()
        monitor_info.cbSize = ctypes.sizeof(_MonitorInfo)
        if not self._user32.GetMonitorInfoW(monitor, ctypes.byref(monitor_info)):
            return False
        bounds = monitor_info.rcMonitor
        rect = info.rect
        tolerance = 2
        return (
            rect.left <= bounds.left + tolerance
            and rect.top <= bounds.top + tolerance
            and rect.right >= bounds.right - tolerance
            and rect.bottom >= bounds.bottom - tolerance
        )

    def is_minimized(self, handle: int) -> bool:
        return bool(self._user32.IsIconic(handle))


def create_window_backend() -> WindowBackend:
    """为当前平台创建窗口后端。"""

    if sys.platform == "win32":
        return WindowsWindowBackend()
    return NullWindowBackend()
