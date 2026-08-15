"""本机 Agent 的统一能力声明、注册与路由。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from desktop_companion_agent.config import ConfigManager
from desktop_companion_agent.models import (
    AgentCapability,
    AgentConnectionState,
    AgentProviderManifest,
)


class AgentProvider:
    """深度 Agent 连接器必须实现的最小公共接口。

    具体厂商可另外提供 Qt 信号和异步方法，但能力发现、连接和取消语义必须统一，
    这样观察、聊天和认知模块不需要知道厂商协议。
    """

    @property
    def provider_id(self) -> str:
        """返回与 AgentProfile 对应的稳定ID。"""

        raise NotImplementedError

    def detect(self) -> AgentProviderManifest:
        """执行无副作用的本机能力检测。"""

        raise NotImplementedError

    def connect(self) -> None:
        """异步建立连接。"""

        raise NotImplementedError

    def disconnect(self) -> None:
        """关闭运行时连接并取消在途任务。"""

        raise NotImplementedError

    def cancel(self) -> None:
        """取消当前可取消任务。"""

        raise NotImplementedError

    def cancel_runtime(self) -> None:
        """取消观察或认知等后台能力，不应打断用户正在进行的聊天。"""

        return None

    def recover(self) -> bool:
        """尝试恢复可重建的后台连接；默认连接器不支持自动恢复。"""

        return False

    def launch(self) -> tuple[bool, str]:
        """打开厂商桌面程序或控制面板。"""

        raise NotImplementedError

    def chat(self, message: str) -> bool:
        """提交聊天消息；异步结果由具体连接器自己的事件接口返回。"""

        raise NotImplementedError

    def analyze_image(self, image_path: Path, context: dict[str, Any]) -> Any:
        """分析一张授权图片；未声明视觉能力的连接器必须拒绝。"""

        raise NotImplementedError

    def organize_cognition(self, mode: str, text: str) -> Any:
        """生成当前认知模式的草稿；实现不得直接修改数据库。"""

        raise NotImplementedError

    def generate_bubble(self, event: dict[str, Any]) -> Any:
        """按最小事件信息生成角色短句，不接收截图或敏感摘要。"""

        raise NotImplementedError

    def supports(self, capability: AgentCapability) -> bool:
        """根据最新检测结果判断是否声明某项能力。"""

        return capability in self.detect().capabilities


class ProviderRegistry:
    """保存已实现的深度连接器，不动态执行外部未知代码。"""

    def __init__(self) -> None:
        self._providers: dict[str, AgentProvider] = {}

    def register(self, provider: AgentProvider) -> None:
        self._providers[provider.provider_id] = provider

    def unregister(self, provider_id: str) -> AgentProvider | None:
        """注销动态连接器，并把清理职责交给调用方。"""

        return self._providers.pop(provider_id, None)

    def get(self, provider_id: str) -> AgentProvider | None:
        return self._providers.get(provider_id)

    def all(self) -> tuple[AgentProvider, ...]:
        return tuple(self._providers.values())

    def reload(self, providers: list[AgentProvider], preserve_ids: set[str] | None = None) -> None:
        """原子替换一组动态Provider，不加载或执行未知Python插件。"""

        preserved = preserve_ids or set()
        self._providers = {
            provider_id: provider
            for provider_id, provider in self._providers.items()
            if provider_id in preserved
        }
        for provider in providers:
            self.register(provider)


class CapabilityRouter:
    """以主 Agent 为默认值，并允许高级配置覆盖单项能力。"""

    def __init__(self, config_manager: ConfigManager, registry: ProviderRegistry) -> None:
        self.config_manager = config_manager
        self.registry = registry

    def provider_id_for(self, capability: AgentCapability) -> str:
        settings = self.config_manager.config.agent_routing
        return settings.capability_overrides.get(capability.value, settings.primary_agent_id)

    def resolve(self, capability: AgentCapability) -> AgentProvider | None:
        provider = self.registry.get(self.provider_id_for(capability))
        if provider is None:
            return None
        manifest = provider.detect()
        if manifest.state in {
            AgentConnectionState.OFFLINE,
            AgentConnectionState.ERROR,
        } and provider.recover():
            manifest = provider.detect()
        if (
            capability not in manifest.capabilities
            or manifest.state is not AgentConnectionState.READY
        ):
            return None
        return provider
