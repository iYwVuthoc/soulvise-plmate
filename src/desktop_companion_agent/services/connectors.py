"""Agent 网页、桌面进程和连接器注册机制。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from PySide6.QtCore import QProcess, QUrl
from PySide6.QtGui import QDesktopServices

from desktop_companion_agent.models import AgentProfile
from desktop_companion_agent.services.intervention import is_safe_external_url


class AgentConnector(ABC):
    """Agent 连接器的最小公共接口。"""

    @abstractmethod
    def launch(self, profile: AgentProfile) -> tuple[bool, str]:
        """启动配置并返回用户可读结果。"""


class WebLauncherConnector(AgentConnector):
    """在默认浏览器打开 HTTPS 或本机控制面板。"""

    def launch(self, profile: AgentProfile) -> tuple[bool, str]:
        if not is_safe_external_url(profile.target):
            return False, "网页入口必须是 HTTPS 或本机 localhost"
        success = QDesktopServices.openUrl(QUrl(profile.target))
        return success, "已打开 Agent 网页" if success else "系统未能打开网页"


class ProcessLauncherConnector(AgentConnector):
    """用明确的路径和参数数组启动程序，不经过 Shell。"""

    def launch(self, profile: AgentProfile) -> tuple[bool, str]:
        executable = Path(profile.target).expanduser()
        if not executable.is_file():
            return False, "程序路径不存在"
        success = QProcess.startDetached(str(executable), list(profile.arguments))
        return bool(success), "已启动桌面 Agent" if success else "程序启动失败"


class ConnectorRegistry:
    """按配置类型选择连接器，便于后续增加 Codex/Claude SDK。"""

    def __init__(self):
        self._connectors: dict[str, AgentConnector] = {
            "web": WebLauncherConnector(),
            "process": ProcessLauncherConnector(),
        }

    def register(self, connector_type: str, connector: AgentConnector) -> None:
        self._connectors[connector_type] = connector

    def launch(self, profile: AgentProfile) -> tuple[bool, str]:
        if not profile.enabled:
            return False, "该 Agent 配置已停用"
        connector = self._connectors.get(profile.connector_type)
        if connector is None:
            return False, f"尚未实现 {profile.connector_type} 连接器"
        return connector.launch(profile)
