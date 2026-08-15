"""配置模型与安全的原子化持久化。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

DEFAULT_TRACKED_PROCESSES = [
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "opera.exe",
    "brave.exe",
    "discord.exe",
    "wechat.exe",
    "wechatappex.exe",
    "qq.exe",
    "telegram.exe",
    "slack.exe",
    "teams.exe",
    "bilibili.exe",
    "哔哩哔哩.exe",
]
LEGACY_MINIMAL_TRACKED_PROCESSES = {
    "chrome.exe",
    "discord.exe",
    "firefox.exe",
    "msedge.exe",
}
OFFICIAL_MODERATION_CATEGORIES = (
    "harassment",
    "harassment/threatening",
    "hate",
    "hate/threatening",
    "illicit",
    "illicit/violent",
    "self-harm",
    "self-harm/instructions",
    "self-harm/intent",
    "sexual",
    "sexual/minors",
    "violence",
    "violence/graphic",
)
DEFAULT_BLOCKING_MODERATION_CATEGORIES = (
    "sexual",
    "sexual/minors",
    "violence",
    "violence/graphic",
)
CURRENT_SCHEMA_VERSION = 6
DEFAULT_INTERVENTION_URL = "https://www.bilibili.com/video/BV1LtMy63EbP"


class AgentRoutingSettings(BaseModel):
    """主 Agent 与各项能力覆盖配置。"""

    model_config = ConfigDict(extra="ignore")

    primary_agent_id: str = "builtin.codex"
    capability_overrides: dict[str, str] = Field(default_factory=dict)
    allow_codex_temporary_images: bool = True
    bubble_polish_timeout_seconds: float = Field(default=3.0, ge=0.5, le=10.0)
    official_moderation_enhancement: bool = False


class CaptureSettings(BaseModel):
    """截图和内容稳定性设置。"""

    model_config = ConfigDict(extra="ignore")

    normal_interval_seconds: int = Field(default=10, ge=3, le=300)
    active_interval_seconds: int = Field(default=5, ge=2, le=300)
    stability_seconds: int = Field(default=5, ge=5, le=120)
    fingerprint_distance: int = Field(default=8, ge=1, le=64)
    content_dedup_minutes: int = Field(default=30, ge=1, le=1440)
    monitor_mode: Literal["active"] = "active"
    tracked_processes: list[str] = Field(default_factory=lambda: list(DEFAULT_TRACKED_PROCESSES))

    @field_validator("tracked_processes")
    @classmethod
    def normalize_processes(cls, values: list[str]) -> list[str]:
        """统一进程名格式并去重。"""

        cleaned = {value.strip().lower() for value in values if value.strip()}
        return sorted(cleaned)

    @model_validator(mode="after")
    def validate_intervals(self) -> CaptureSettings:
        """活动应用时的截图间隔不能比普通间隔更慢。"""

        if self.active_interval_seconds > self.normal_interval_seconds:
            raise ValueError("活动应用截图间隔不能大于普通截图间隔")
        return self


class HappinessSettings(BaseModel):
    """高兴值策略。"""

    model_config = ConfigDict(extra="ignore")

    initial_value: int = Field(default=50, ge=0, le=100)
    interest_increment: int = Field(default=10, ge=1, le=100)
    disinterest_decrement: int = Field(default=10, ge=1, le=100)
    trigger_threshold: int = Field(default=0, ge=0, le=100)
    reset_value: int = Field(default=50, ge=0, le=100)
    intervention_cooldown_seconds: int = Field(default=60, ge=5, le=3600)


class ModelSettings(BaseModel):
    """云端模型设置，不包含任何密钥。"""

    model_config = ConfigDict(extra="ignore")

    vision_model: str = Field(default="gpt-5.6-luna", min_length=1, max_length=100)
    moderation_model: str = Field(default="omni-moderation-latest", min_length=1, max_length=100)
    chat_model: str = Field(default="gpt-5.6-luna", min_length=1, max_length=100)
    base_url: str = Field(default="https://api.openai.com/v1", max_length=500)
    request_timeout_seconds: int = Field(default=25, ge=5, le=120)
    enabled: bool = True


class ModerationPolicySettings(BaseModel):
    """决定哪些官方审核类别需要强制拦截。"""

    model_config = ConfigDict(extra="ignore")

    blocking_categories: list[str] = Field(
        default_factory=lambda: list(DEFAULT_BLOCKING_MODERATION_CATEGORIES)
    )
    vision_rule_confidence_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    codex_knowledge_enabled: bool = True
    codex_knowledge_confidence_threshold: float = Field(default=0.90, ge=0.0, le=1.0)

    @field_validator("blocking_categories")
    @classmethod
    def validate_categories(cls, values: list[str]) -> list[str]:
        """去重并拒绝未知类别，防止配置拼写错误扩大拦截范围。"""

        allowed = set(OFFICIAL_MODERATION_CATEGORIES)
        result: list[str] = []
        for value in values:
            normalized = value.strip().lower()
            if normalized not in allowed:
                raise ValueError(f"未知的官方审核类别：{value}")
            if normalized not in result:
                result.append(normalized)
        return result


class CompanionPolicySettings(BaseModel):
    """陪看语义匹配、近义发散和冲突判定设置。"""

    model_config = ConfigDict(extra="ignore")

    semantic_confidence_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    conflict_confidence_margin: float = Field(default=0.05, ge=0.0, le=0.5)


class InterventionSettings(BaseModel):
    """拦截跳转设置。"""

    model_config = ConfigDict(extra="ignore")

    redirect_url: str = Field(default=DEFAULT_INTERVENTION_URL, max_length=2000)


class PrivacySettings(BaseModel):
    """隐私与数据保留设置。"""

    model_config = ConfigDict(extra="ignore")

    retain_event_days: int = Field(default=30, ge=1, le=3650)
    save_raw_screenshots: Literal[False] = False


class UiSettings(BaseModel):
    """界面位置等非敏感状态。"""

    model_config = ConfigDict(extra="ignore")

    character_x: int = 80
    character_y: int = 200
    character_scale: float = Field(default=1.0, ge=0.5, le=2.0)
    character_docked: bool = True


class AppConfig(BaseModel):
    """应用完整配置。"""

    model_config = ConfigDict(extra="ignore")

    schema_version: int = CURRENT_SCHEMA_VERSION
    observation_enabled: bool = False
    supervision_enabled: bool = False
    companion_enabled: bool = False
    blindfolded: bool = False
    capture: CaptureSettings = Field(default_factory=CaptureSettings)
    happiness: HappinessSettings = Field(default_factory=HappinessSettings)
    model: ModelSettings = Field(default_factory=ModelSettings)
    moderation_policy: ModerationPolicySettings = Field(default_factory=ModerationPolicySettings)
    companion_policy: CompanionPolicySettings = Field(default_factory=CompanionPolicySettings)
    intervention: InterventionSettings = Field(default_factory=InterventionSettings)
    privacy: PrivacySettings = Field(default_factory=PrivacySettings)
    ui: UiSettings = Field(default_factory=UiSettings)
    agent_routing: AgentRoutingSettings = Field(default_factory=AgentRoutingSettings)


class ConfigManager:
    """负责配置加载、校验和原子写入。"""

    def __init__(self, config_file: Path):
        self.config_file = Path(config_file)
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        self._config = AppConfig()

    @property
    def config(self) -> AppConfig:
        """返回当前内存配置。"""

        return self._config

    def load(self) -> AppConfig:
        """加载配置；无文件时创建安全默认配置。"""

        if not self.config_file.exists():
            self._config = AppConfig()
            self.save(self._config)
            return self._config

        try:
            payload = json.loads(self.config_file.read_text(encoding="utf-8"))
            payload = self._migrate_payload(payload)
            self._config = AppConfig.model_validate(payload)
        except (OSError, ValueError, TypeError, json.JSONDecodeError, ValidationError):
            # 无法信任的损坏配置不会直接生效，且保留原文件供用户排查。
            self._config = AppConfig()
        return self._config

    @staticmethod
    def _migrate_payload(payload: object) -> dict[str, object]:
        """把旧配置升级到当前结构；拒绝来自未来版本的未知配置。"""

        if not isinstance(payload, dict):
            raise TypeError("配置根节点必须是对象")
        migrated = dict(payload)
        version = int(migrated.get("schema_version", 0))
        if version > CURRENT_SCHEMA_VERSION:
            raise ValueError("配置版本高于当前程序支持版本")
        if version == 0:
            # 原型期无版本配置只有字段默认值差异，Pydantic 会补齐新增字段。
            migrated["schema_version"] = 1
            version = 1
        if version == 1:
            migrated.setdefault("moderation_policy", ModerationPolicySettings().model_dump())
            migrated["schema_version"] = 2
            version = 2
        if version == 2:
            migrated.setdefault("agent_routing", AgentRoutingSettings().model_dump())
            migrated["schema_version"] = 3
            version = 3
        if version == 3:
            # v0.1.5的15秒是旧默认值；只迁移这一默认值，其他用户自定义值保持不变。
            capture = dict(migrated.get("capture") or {})
            if int(capture.get("stability_seconds", 15)) == 15:
                capture["stability_seconds"] = 5
            migrated["capture"] = capture
            migrated.setdefault("companion_policy", CompanionPolicySettings().model_dump())
            migrated["schema_version"] = 4
            version = 4
        if version == 4:
            # 早期配置只保存了四个最小进程名。仅对这一已知旧默认值补全视频、
            # 社交应用和中文B站客户端，避免覆盖用户后来主动维护的自定义列表。
            capture = dict(migrated.get("capture") or {})
            tracked = {
                str(value).strip().casefold()
                for value in capture.get("tracked_processes", [])
                if str(value).strip()
            }
            if tracked == LEGACY_MINIMAL_TRACKED_PROCESSES:
                capture["tracked_processes"] = list(DEFAULT_TRACKED_PROCESSES)
            migrated["capture"] = capture
            migrated["schema_version"] = 5
            version = 5
        if version == 5:
            # ACP配置保存在SQLite中。这里仅推进配置版本，绝不补写拦截地址，
            # 从而保留升级用户原有地址以及用户主动清空后的状态。
            migrated["schema_version"] = 6
        return migrated

    def save(self, config: AppConfig | None = None) -> None:
        """使用同目录临时文件和 ``os.replace`` 原子保存配置。"""

        if config is not None:
            self._config = config
        payload = self._config.model_dump(mode="json")
        descriptor, temp_name = tempfile.mkstemp(
            prefix="config-", suffix=".tmp", dir=str(self.config_file.parent)
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self.config_file)
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)

    def update(self, **changes: object) -> AppConfig:
        """校验并保存顶层配置变更。"""

        payload = self._config.model_dump(mode="python")
        payload.update(changes)
        updated = AppConfig.model_validate(payload)
        self.save(updated)
        return updated
