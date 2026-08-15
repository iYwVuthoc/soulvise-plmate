"""密钥存储与日志脱敏辅助。"""

from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from contextlib import suppress

from desktop_companion_agent.branding import (
    KEYRING_SERVICE_NAME,
    LEGACY_KEYRING_SERVICE_NAME,
)

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{8,}"),
    re.compile(r"(?i)(api[_-]?key=)[^&\s]+"),
)


def redact_sensitive_text(value: str, limit: int = 500) -> str:
    """在日志或错误提示前遮盖常见密钥形式并限制长度。"""

    text = value
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(
            lambda match: (f"{match.group(1)}[已脱敏]" if match.lastindex else "[已脱敏]"),
            text,
        )
    return text[:limit]


class SecretStore(ABC):
    """密钥存储接口，便于测试替换。"""

    @abstractmethod
    def get(self, name: str) -> str | None:
        """读取密钥。"""

    @abstractmethod
    def set(self, name: str, value: str) -> None:
        """保存密钥。"""

    @abstractmethod
    def delete(self, name: str) -> None:
        """删除密钥。"""


class KeyringSecretStore(SecretStore):
    """使用系统凭据管理器保存密钥。"""

    def __init__(
        self,
        service_name: str = KEYRING_SERVICE_NAME,
        legacy_service_names: tuple[str, ...] = (LEGACY_KEYRING_SERVICE_NAME,),
    ):
        self.service_name = service_name
        self.legacy_service_names = tuple(
            name for name in legacy_service_names if name and name != service_name
        )

    def get(self, name: str) -> str | None:
        """优先读取系统凭据，开发环境可只读环境变量。"""

        try:
            import keyring

            value = keyring.get_password(self.service_name, name)
            if value:
                return value
            for legacy_service_name in self.legacy_service_names:
                legacy_value = keyring.get_password(legacy_service_name, name)
                if not legacy_value:
                    continue
                # 迁移只复制到新服务，不删除旧凭据，以便旧版程序继续回退使用。
                with suppress(Exception):
                    keyring.set_password(self.service_name, name, legacy_value)
                return legacy_value
        except Exception:
            # 密钥后端不可用时仅回退到环境变量，不落盘明文。
            pass
        if name == "openai_api_key":
            return os.environ.get("OPENAI_API_KEY")
        return None

    def set(self, name: str, value: str) -> None:
        """保存非空密钥；密钥正文不会写入配置。"""

        value = value.strip()
        if not value:
            raise ValueError("密钥不能为空")
        try:
            import keyring

            keyring.set_password(self.service_name, name, value)
        except Exception as exc:
            raise RuntimeError("系统密钥库不可用，未保存API密钥") from exc

    def delete(self, name: str) -> None:
        """删除指定系统凭据。"""

        try:
            import keyring
        except Exception:
            return
        for service_name in (self.service_name, *self.legacy_service_names):
            try:
                keyring.delete_password(service_name, name)
            except Exception:
                continue


class InMemorySecretStore(SecretStore):
    """测试专用的内存密钥存储。"""

    def __init__(self):
        self._values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self._values.get(name)

    def set(self, name: str, value: str) -> None:
        self._values[name] = value

    def delete(self, name: str) -> None:
        self._values.pop(name, None)
