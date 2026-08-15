"""在截图进入指纹与模型前遮蔽程序自身的角色和气泡。"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from PIL import Image, ImageDraw


@dataclass(frozen=True, slots=True)
class PrivacyMaskRect:
    """使用全局屏幕坐标保存一个程序自有窗口区域。"""

    left: int
    top: int
    width: int
    height: int


class PrivacyMaskRegistry:
    """线程安全地共享UI位置，并在内存图片中覆盖真实像素。"""

    def __init__(self) -> None:
        self._rectangles: dict[str, PrivacyMaskRect] = {}
        self._lock = threading.Lock()

    def update(self, key: str, left: int, top: int, width: int, height: int) -> None:
        with self._lock:
            self._rectangles[key] = PrivacyMaskRect(left, top, width, height)

    def remove(self, key: str) -> None:
        with self._lock:
            self._rectangles.pop(key, None)

    def apply(self, image: Image.Image, screen_left: int, screen_top: int) -> Image.Image:
        """用主题中性色覆盖相交区域，不把角色或气泡像素发送给分析器。"""

        with self._lock:
            rectangles = tuple(self._rectangles.values())
        if not rectangles:
            return image
        draw = ImageDraw.Draw(image)
        image_right = screen_left + image.width
        image_bottom = screen_top + image.height
        for rect in rectangles:
            right = rect.left + rect.width
            bottom = rect.top + rect.height
            if (
                right <= screen_left
                or rect.left >= image_right
                or bottom <= screen_top
                or rect.top >= image_bottom
            ):
                continue
            local_left = max(0, rect.left - screen_left)
            local_top = max(0, rect.top - screen_top)
            local_right = min(image.width, right - screen_left)
            local_bottom = min(image.height, bottom - screen_top)
            draw.rectangle(
                (local_left, local_top, local_right, local_bottom),
                fill=(23, 19, 27),
            )
        return image


privacy_mask_registry = PrivacyMaskRegistry()
