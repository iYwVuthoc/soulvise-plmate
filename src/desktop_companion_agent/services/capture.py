"""屏幕截图后端，所有图像只在内存中流转。"""

from __future__ import annotations

import io
from abc import ABC, abstractmethod
from dataclasses import dataclass
from time import time

from PIL import Image
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPoint
from PySide6.QtGui import QCursor, QGuiApplication

from desktop_companion_agent.platforms.window_backend import WindowInfo
from desktop_companion_agent.services.fingerprint import difference_hash
from desktop_companion_agent.services.privacy_mask import privacy_mask_registry

MAXIMUM_ANALYSIS_EDGE = 1600
ANALYSIS_JPEG_QUALITY = 72
TEMPORAL_FRAME_GAP = 8


@dataclass(slots=True)
class CapturedFrame:
    """仅在内存中存在的一帧截图。"""

    jpeg_bytes: bytes
    width: int
    height: int
    fingerprint: int
    captured_at: float
    app_name: str
    window_title: str
    window_handle: int
    temporal_frame_count: int = 1
    temporal_layout: str = "single"

    def clear(self) -> None:
        """主动释放图像字节引用。"""

        self.jpeg_bytes = b""

    def memory_copy(self) -> CapturedFrame:
        """复制一份仅持有同一不可变字节对象的内存快照。"""

        return CapturedFrame(
            jpeg_bytes=self.jpeg_bytes,
            width=self.width,
            height=self.height,
            fingerprint=self.fingerprint,
            captured_at=self.captured_at,
            app_name=self.app_name,
            window_title=self.window_title,
            window_handle=self.window_handle,
            temporal_frame_count=self.temporal_frame_count,
            temporal_layout=self.temporal_layout,
        )


def compose_temporal_frames(older: CapturedFrame, latest: CapturedFrame) -> CapturedFrame:
    """把稳定期前后两帧合成一张时序图，帮助模型识别视频的突变和扭曲。

    两帧始终只在内存中解码和重新编码。横向画面采用上下排列以尽量保留文字
    与人物细节；竖向画面采用左右排列，最终最长边仍限制为1600像素。
    """

    with Image.open(io.BytesIO(older.jpeg_bytes)) as older_source:
        older_image = older_source.convert("RGB")
    with Image.open(io.BytesIO(latest.jpeg_bytes)) as latest_source:
        latest_image = latest_source.convert("RGB")

    landscape = (
        older_image.width >= older_image.height
        and latest_image.width >= latest_image.height
    )
    if landscape:
        width = max(older_image.width, latest_image.width)
        height = older_image.height + TEMPORAL_FRAME_GAP + latest_image.height
        canvas = Image.new("RGB", (width, height), (23, 19, 27))
        canvas.paste(older_image, ((width - older_image.width) // 2, 0))
        latest_y = older_image.height + TEMPORAL_FRAME_GAP
        canvas.paste(latest_image, ((width - latest_image.width) // 2, latest_y))
        layout = "older_top_latest_bottom"
    else:
        width = older_image.width + TEMPORAL_FRAME_GAP + latest_image.width
        height = max(older_image.height, latest_image.height)
        canvas = Image.new("RGB", (width, height), (23, 19, 27))
        canvas.paste(older_image, (0, (height - older_image.height) // 2))
        latest_x = older_image.width + TEMPORAL_FRAME_GAP
        canvas.paste(latest_image, (latest_x, (height - latest_image.height) // 2))
        layout = "older_left_latest_right"

    canvas.thumbnail(
        (MAXIMUM_ANALYSIS_EDGE, MAXIMUM_ANALYSIS_EDGE),
        Image.Resampling.LANCZOS,
    )
    output = io.BytesIO()
    canvas.save(
        output,
        format="JPEG",
        quality=ANALYSIS_JPEG_QUALITY,
        optimize=True,
    )
    return CapturedFrame(
        jpeg_bytes=output.getvalue(),
        width=canvas.width,
        height=canvas.height,
        fingerprint=latest.fingerprint,
        captured_at=latest.captured_at,
        app_name=latest.app_name,
        window_title=latest.window_title,
        window_handle=latest.window_handle,
        temporal_frame_count=2,
        temporal_layout=layout,
    )


class CaptureBackend(ABC):
    """截图接口。"""

    @abstractmethod
    def capture_active_screen(self, window: WindowInfo) -> CapturedFrame:
        """截取前台窗口所在显示器。"""


class ActiveScreenCaptureBackend(CaptureBackend):
    """优先使用 MSS，失败时回退到 Qt 屏幕截图。"""

    def _screen_for_window(self, window: WindowInfo):
        point: QPoint
        if window.rect is not None:
            point = QPoint(
                window.rect.left + window.rect.width // 2,
                window.rect.top + window.rect.height // 2,
            )
        else:
            point = QCursor.pos()
        return QGuiApplication.screenAt(point) or QGuiApplication.primaryScreen()

    def capture_active_screen(self, window: WindowInfo) -> CapturedFrame:
        screen = self._screen_for_window(window)
        if screen is None:
            raise RuntimeError("未检测到可截图的显示器")
        geometry = screen.geometry()
        try:
            image = self._capture_with_mss(
                geometry.x(), geometry.y(), geometry.width(), geometry.height()
            )
        except Exception:
            image = self._capture_with_qt(screen)
        image = privacy_mask_registry.apply(image, geometry.x(), geometry.y())

        # 指纹使用遮罩后的原始画面，分析副本再等比例缩小以降低传输和推理耗时。
        fingerprint = difference_hash(image)
        image = self._resize_for_analysis(image)
        output = io.BytesIO()
        image.convert("RGB").save(
            output,
            format="JPEG",
            quality=ANALYSIS_JPEG_QUALITY,
            optimize=True,
        )
        payload = output.getvalue()
        return CapturedFrame(
            jpeg_bytes=payload,
            width=image.width,
            height=image.height,
            fingerprint=fingerprint,
            captured_at=time(),
            app_name=window.process_name or "unknown",
            window_title=window.title,
            window_handle=window.handle,
        )

    @staticmethod
    def _resize_for_analysis(image: Image.Image) -> Image.Image:
        """保持比例限制分析图最长边，小分辨率画面不做无意义放大。"""

        if max(image.size) <= MAXIMUM_ANALYSIS_EDGE:
            return image
        resized = image.copy()
        resized.thumbnail(
            (MAXIMUM_ANALYSIS_EDGE, MAXIMUM_ANALYSIS_EDGE),
            Image.Resampling.LANCZOS,
        )
        return resized

    @staticmethod
    def _capture_with_mss(left: int, top: int, width: int, height: int) -> Image.Image:
        import mss

        with mss.mss() as grabber:
            shot = grabber.grab({"left": left, "top": top, "width": width, "height": height})
            return Image.frombytes("RGB", shot.size, shot.rgb)

    @staticmethod
    def _capture_with_qt(screen) -> Image.Image:
        pixmap = screen.grabWindow(0)
        if pixmap.isNull():
            raise RuntimeError("系统拒绝了屏幕截图")
        byte_array = QByteArray()
        buffer = QBuffer(byte_array)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        try:
            if not pixmap.save(buffer, "PNG"):
                raise RuntimeError("无法编码屏幕截图")
        finally:
            buffer.close()
        return Image.open(io.BytesIO(bytes(byte_array))).convert("RGB")


class SyntheticCaptureBackend(CaptureBackend):
    """测试与演示使用的合成截图源。"""

    def __init__(self, color: tuple[int, int, int] = (42, 50, 66)):
        self.color = color
        self.sequence = 0

    def capture_active_screen(self, window: WindowInfo) -> CapturedFrame:
        self.sequence += 1
        image = Image.new("RGB", (640, 360), self.color)
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=80)
        return CapturedFrame(
            jpeg_bytes=output.getvalue(),
            width=640,
            height=360,
            fingerprint=difference_hash(image) ^ self.sequence,
            captured_at=time(),
            app_name=window.process_name or "synthetic.exe",
            window_title=window.title or "合成测试内容",
            window_handle=window.handle,
        )
