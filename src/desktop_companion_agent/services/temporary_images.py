"""Codex 视觉分析专用临时图片的最小生命周期管理。"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from time import time
from uuid import uuid4

from PySide6.QtCore import QStandardPaths

from desktop_companion_agent.branding import APPLICATION_NAME, ORGANIZATION_NAME

_VALID_IMAGE_NAME = re.compile(r"^vision-[0-9a-f]{32}\.jpg$")


def default_vision_temporary_directory() -> Path:
    """返回不漫游的专用缓存目录，Windows上固定落在LOCALAPPDATA。"""

    local = os.environ.get("LOCALAPPDATA")
    if os.name == "nt" and local:
        return Path(local) / ORGANIZATION_NAME / APPLICATION_NAME / "temp" / "vision"
    cache = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
    return Path(cache) / "temp" / "vision"


class VisionTemporaryImageStore:
    """独占创建图片，并保证成功、失败、取消和退出后都可安全删除。"""

    def __init__(self, root: Path | None = None):
        self.root = (root or default_vision_temporary_directory()).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._active: set[Path] = set()
        self._lock = threading.Lock()
        self.cleanup_stale()

    @staticmethod
    def _is_managed(path: Path) -> bool:
        return bool(_VALID_IMAGE_NAME.fullmatch(path.name))

    def _delete(self, path: Path) -> None:
        """只删除专用目录内由本服务命名的普通文件。"""

        candidate = path.resolve()
        if candidate.parent != self.root or not self._is_managed(candidate):
            return
        with suppress(FileNotFoundError, PermissionError, OSError):
            if candidate.is_file() and not candidate.is_symlink():
                candidate.unlink()
        with self._lock:
            self._active.discard(candidate)

    @contextmanager
    def materialize(self, jpeg_bytes: bytes) -> Iterator[Path]:
        """把当前图片短暂写入专用目录，离开作用域时立即删除。"""

        if not jpeg_bytes:
            raise ValueError("临时图片内容为空")
        path = (self.root / f"vision-{uuid4().hex}.jpg").resolve()
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        descriptor = os.open(path, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(jpeg_bytes)
                stream.flush()
            with self._lock:
                self._active.add(path)
            yield path
        finally:
            self._delete(path)

    def cleanup_stale(self, maximum_age_seconds: int = 3600) -> int:
        """启动时只清理专用目录内超过时限且命名合法的图片。"""

        deleted = 0
        cutoff = time() - maximum_age_seconds
        for path in self.root.iterdir():
            try:
                if (
                    self._is_managed(path)
                    and path.is_file()
                    and not path.is_symlink()
                    and path.stat().st_mtime < cutoff
                ):
                    path.unlink()
                    deleted += 1
            except (FileNotFoundError, PermissionError, OSError):
                continue
        return deleted

    def clear_all(self) -> None:
        """暂停或退出时清理全部合法临时图片，包括异常遗留文件。"""

        with self._lock:
            active = tuple(self._active)
        for path in active:
            self._delete(path)
        for path in tuple(self.root.iterdir()):
            if self._is_managed(path):
                self._delete(path)
