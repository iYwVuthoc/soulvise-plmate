"""经过地址校验的安全跳转服务。"""

from __future__ import annotations

import re
import webbrowser
from urllib.parse import urlparse

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

_BILIBILI_HTTPS_URL = re.compile(
    r"https://(?:([a-z0-9-]+)\.)?bilibili\.com/[^\s]+|https://b23\.tv/[^\s]+",
    re.IGNORECASE,
)
_BILIBILI_WITHOUT_SCHEME = re.compile(
    r"(?:(?:www|m)\.)?bilibili\.com/[^\s]+|b23\.tv/[^\s]+",
    re.IGNORECASE,
)
_BILIBILI_VIDEO_ID = re.compile(
    r"(?P<video_id>BV[0-9A-Za-z]{10}|av[0-9]+)(?P<query>\?[^\s]+)?",
    re.IGNORECASE,
)
_TRAILING_SHARE_PUNCTUATION = "，。！？；、,!?;）)]}】》\"'"


def normalize_redirect_url(value: str) -> str:
    """兼容B站分享文本、无协议地址和BV号，并统一为可校验的HTTPS地址。

    这里只补全可明确识别的B站格式；其他缺少协议的任意域名不会被猜测，
    从而避免把用户输入意外转换为未经确认的外部地址。
    """

    text = value.strip()
    if not text:
        return ""

    shared_url = _BILIBILI_HTTPS_URL.search(text)
    if shared_url:
        return shared_url.group(0).rstrip(_TRAILING_SHARE_PUNCTUATION)

    without_scheme = _BILIBILI_WITHOUT_SCHEME.search(text)
    if without_scheme:
        path = without_scheme.group(0).rstrip(_TRAILING_SHARE_PUNCTUATION)
        return f"https://{path}"

    video_id = _BILIBILI_VIDEO_ID.fullmatch(text)
    if video_id:
        identifier = video_id.group("video_id")
        query = (video_id.group("query") or "").rstrip(_TRAILING_SHARE_PUNCTUATION)
        return f"https://www.bilibili.com/video/{identifier}{query}"

    return text


def is_safe_external_url(value: str) -> bool:
    """仅允许 HTTPS，或仅供本机开发使用的 HTTP localhost。"""

    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return False
    if parsed.scheme == "https" and bool(parsed.netloc):
        return True
    return parsed.scheme == "http" and (parsed.hostname or "").lower() in {
        "localhost",
        "127.0.0.1",
        "::1",
    }


class InterventionService:
    """通过系统默认浏览器打开用户明确配置的拦截页面。"""

    def open_redirect(self, url: str) -> tuple[bool, str]:
        """验证并打开地址；空地址不会触发任何真实跳转。"""

        value = normalize_redirect_url(url)
        if not value:
            return False, "尚未配置拦截视频地址"
        if not is_safe_external_url(value):
            return False, "拦截地址必须是 HTTPS 或本机 localhost"
        if QDesktopServices.openUrl(QUrl(value)):
            return True, "已请求默认浏览器打开拦截视频"
        try:
            # 少数Qt/Windows组合会错误返回失败；使用Python标准库进行一次兼容重试。
            # 地址已经过协议与主机校验，不经过Shell拼接，也不会回显到日志。
            if webbrowser.open_new_tab(value):
                return True, "已通过兼容方式打开拦截视频"
        except (OSError, webbrowser.Error):
            pass
        return False, "系统未能打开默认浏览器；未进入冷却，可立即重试"

    @staticmethod
    def validate_redirect(url: str) -> tuple[bool, str, str]:
        """返回规范化地址和小白可理解的校验结果，不执行任何外部跳转。"""

        value = normalize_redirect_url(url)
        if not value:
            return True, "", "尚未配置；监督命中时只反馈，不会跳转"
        if not is_safe_external_url(value):
            return False, value, "地址无效：请使用HTTPS、B站分享内容、BV/AV号或localhost"
        return True, value, "地址格式有效，可点击“测试打开”进行确认"
