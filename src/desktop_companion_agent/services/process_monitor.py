"""浏览器、社交和视频应用进程检测。"""

from __future__ import annotations

from collections.abc import Iterable


class ProcessMonitor:
    """使用 psutil 检测配置中的应用是否正在运行。"""

    def __init__(self, tracked_processes: Iterable[str]):
        self.set_tracked_processes(tracked_processes)

    def set_tracked_processes(self, names: Iterable[str]) -> None:
        """替换需要触发快速截图的进程列表。"""

        self._tracked = {name.strip().lower() for name in names if name.strip()}

    def running_tracked_processes(self) -> set[str]:
        """返回当前匹配到的进程名；权限错误不会中断程序。"""

        if not self._tracked:
            return set()
        try:
            import psutil
        except ImportError:
            return set()

        matched: set[str] = set()
        for process in psutil.process_iter(attrs=["name"]):
            try:
                name = (process.info.get("name") or "").lower()
                if name in self._tracked:
                    matched.add(name)
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
        return matched

    def has_tracked_process(self) -> bool:
        """是否存在任一需要快速观察的应用。"""

        return bool(self.running_tracked_processes())
