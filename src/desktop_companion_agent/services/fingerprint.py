"""不写磁盘的画面指纹算法。"""

from __future__ import annotations

from PIL import Image


def difference_hash(image: Image.Image, hash_size: int = 8) -> int:
    """计算64位差异哈希，用于判断画面是否明显变化。"""

    gray = image.convert("L").resize((hash_size + 1, hash_size))
    pixels = list(gray.getdata())
    result = 0
    bit = 0
    width = hash_size + 1
    for row in range(hash_size):
        offset = row * width
        for column in range(hash_size):
            if pixels[offset + column] > pixels[offset + column + 1]:
                result |= 1 << bit
            bit += 1
    return result


def hamming_distance(first: int, second: int) -> int:
    """计算两个整数指纹之间不同位的数量。"""

    return (first ^ second).bit_count()


def meaningfully_changed(first: int | None, second: int, threshold: int) -> bool:
    """首次画面或距离达到阈值时视为明显变化。"""

    return first is None or hamming_distance(first, second) >= threshold
