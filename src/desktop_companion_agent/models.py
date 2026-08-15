"""跨模块共享的数据模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now_iso() -> str:
    """返回带时区的 UTC 时间文本。"""

    return datetime.now(UTC).isoformat()


class RuleMode(StrEnum):
    """认知规则所属模式。"""

    SUPERVISION = "supervision"
    COMPANION = "companion"


class RuleSource(StrEnum):
    """规则来源。"""

    USER = "user"
    BUILTIN = "builtin"
    AI_PENDING = "ai_pending"
    AI_CONFIRMED = "ai_confirmed"


class InterestJudgment(StrEnum):
    """陪看兴趣判断。"""

    INTERESTED = "interested"
    NOT_INTERESTED = "not_interested"
    NEUTRAL = "neutral"


class CharacterState(StrEnum):
    """角色可视状态。"""

    NORMAL = "normal"
    HAPPY = "happy"
    ANGRY = "angry"
    BLINDFOLDED = "blindfolded"
    PATTED = "patted"
    OFFLINE = "offline"


class BubbleEventType(StrEnum):
    """触发角色短气泡的最小事件类型。"""

    SUPERVISION_HIT = "supervision_hit"
    HAPPINESS_ZERO = "happiness_zero"
    CELEBRATION = "celebration"
    INTERESTED = "interested"
    NOT_INTERESTED = "not_interested"
    OFFLINE = "offline"


class ObservationEventKind(StrEnum):
    """区分普通观察和不参与过期清理的纪念事件。"""

    OBSERVATION = "observation"
    CELEBRATION = "celebration"


class BubbleEvent(BaseModel):
    """发送给本地模板或Agent润色器的最小非敏感事件。"""

    kind: BubbleEventType
    character_state: CharacterState
    happiness_value: int = Field(ge=0, le=100)
    happiness_delta: int = Field(default=0, ge=-100, le=100)


class SpeechBubbleMessage(BaseModel):
    """角色气泡最终可展示的受约束短句。"""

    text: str = Field(min_length=1, max_length=24)
    kind: BubbleEventType
    priority: int = Field(ge=0, le=100)
    duration_seconds: float = Field(default=3.5, ge=1.0, le=10.0)


class AgentCapability(StrEnum):
    """深度 Agent 可以向 Soulvise 提供的标准能力。"""

    LAUNCH = "launch"
    CHAT = "chat"
    VISION_ANALYSIS = "vision_analysis"
    SEMANTIC_SUPERVISION = "semantic_supervision"
    COGNITION_ORGANIZE = "cognition_organize"
    BUBBLE_POLISH = "bubble_polish"
    WEB_RESEARCH = "web_research"
    PROJECT_ASSIST = "project_assist"


class AgentConnectionState(StrEnum):
    """Agent 从发现到可用期间的可展示连接状态。"""

    NOT_DETECTED = "not_detected"
    SIGNED_OUT = "signed_out"
    CONNECTING = "connecting"
    READY = "ready"
    RATE_LIMITED = "rate_limited"
    OFFLINE = "offline"
    ERROR = "error"


class AgentConnectionMode(StrEnum):
    """区分只能打开程序的入口和可承接能力的深度连接。"""

    LAUNCH_ONLY = "launch_only"
    DEEP = "deep"


class CognitionRule(BaseModel):
    """一条由用户管理的监督或兴趣规则。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid4().hex)
    mode: RuleMode
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    interest_judgment: InterestJudgment = InterestJudgment.INTERESTED
    keywords: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    priority: int = Field(default=50, ge=0, le=100)
    source: RuleSource = RuleSource.USER
    enabled: bool = True
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class RiskAssessment(BaseModel):
    """模型基于通用知识给出的单项风险判断。"""

    model_config = ConfigDict(extra="forbid")

    category: str = Field(min_length=1, max_length=80)
    confidence: float = Field(ge=0.0, le=1.0)
    context_exempted: bool = False


class SemanticRuleMatch(BaseModel):
    """模型对一条已提供认知规则的语义匹配及其独立置信度。"""

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1, max_length=160)
    confidence: float = Field(ge=0.0, le=1.0)


class VisionAnalysisSchema(BaseModel):
    """视觉模型必须返回的受约束结构。"""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(max_length=500)
    risk_assessments: list[RiskAssessment] = Field(default_factory=list, max_length=20)
    supervision_matches: list[SemanticRuleMatch] = Field(default_factory=list, max_length=50)
    companion_matches: list[SemanticRuleMatch] = Field(default_factory=list, max_length=50)
    suggested_memory: str = Field(default="", max_length=500)


class CognitionDraft(BaseModel):
    """AI整理后等待用户确认的一条结构化认知草稿。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=2000)
    keywords: list[str] = Field(min_length=1, max_length=50)
    examples: list[str] = Field(default_factory=list, max_length=10)
    exclusions: list[str] = Field(default_factory=list, max_length=20)
    priority: int = Field(default=50, ge=0, le=100)
    interest_judgment: InterestJudgment = InterestJudgment.INTERESTED


class CognitionDraftSchema(BaseModel):
    """一次AI整理请求的受约束输出。"""

    model_config = ConfigDict(extra="forbid")

    drafts: list[CognitionDraft] = Field(min_length=1, max_length=10)


class AnalysisResult(BaseModel):
    """统一分析结果；``flagged``仅表示需要强制监督拦截。"""

    summary: str = "未获得有效摘要"
    categories: list[str] = Field(default_factory=list)
    risk_assessments: list[RiskAssessment] = Field(default_factory=list)
    matched_supervision_rule_ids: list[str] = Field(default_factory=list)
    matched_interest_rule_ids: list[str] = Field(default_factory=list)
    interest: InterestJudgment = InterestJudgment.NEUTRAL
    interest_conflict: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    flagged: bool = False
    analysis_available: bool = True
    error_message: str = ""
    model: str = "local"
    suggested_memory: str = ""


class ObservationEvent(BaseModel):
    """仅包含概括性文字的观察事件。"""

    id: str = Field(default_factory=lambda: uuid4().hex)
    mode: RuleMode
    created_at: str = Field(default_factory=utc_now_iso)
    expires_at: str
    app_name: str = ""
    window_title_hash: str = ""
    content_id: str = ""
    summary: str
    categories: list[str] = Field(default_factory=list)
    matched_rule_ids: list[str] = Field(default_factory=list)
    interest: InterestJudgment = InterestJudgment.NEUTRAL
    happiness_delta: int = 0
    intervened: bool = False
    false_positive: bool = False
    model: str = "local"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    event_kind: ObservationEventKind = ObservationEventKind.OBSERVATION
    pinned: bool = False
    happiness_value: int = Field(default=0, ge=0, le=100)


class PendingCognitionChange(BaseModel):
    """等待用户确认的认知修改建议。"""

    id: str = Field(default_factory=lambda: uuid4().hex)
    mode: RuleMode
    suggestion: str = Field(min_length=1, max_length=1000)
    source_event_id: str = ""
    status: str = "pending"
    created_at: str = Field(default_factory=utc_now_iso)


class AgentProfile(BaseModel):
    """可启动或可聊天的 Agent 配置。"""

    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str = Field(min_length=1, max_length=100)
    connector_type: str = Field(pattern="^(web|process|openai|sdk|codex|acp)$")
    target: str = ""
    arguments: list[str] = Field(default_factory=list)
    model: str = ""
    vendor: str = "generic"
    connection_mode: AgentConnectionMode = AgentConnectionMode.LAUNCH_ONLY
    workspace_root: str = ""
    capabilities: list[AgentCapability] = Field(default_factory=list)
    app_user_model_id: str = ""
    preset_id: str = ""
    allow_image_input: bool = False
    enabled: bool = True
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class AgentProviderManifest(BaseModel):
    """一次检测得到的 Agent 能力和认证状态，不包含任何令牌。"""

    provider_id: str
    display_name: str
    vendor: str
    version: str = ""
    state: AgentConnectionState = AgentConnectionState.NOT_DETECTED
    capabilities: list[AgentCapability] = Field(default_factory=list)
    authentication: str = "unknown"
    detail: str = ""
    desktop_available: bool = False


class AgentSession(BaseModel):
    """本地保存的 Agent 会话指针；正文仍由各自仓库管理。"""

    profile_id: str
    purpose: str = Field(pattern="^(chat|runtime|development)$")
    provider_session_id: str
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class AgentApprovalRequest(BaseModel):
    """发送给界面的单次 Codex 权限申请。"""

    id: str = Field(default_factory=lambda: uuid4().hex)
    method: str
    summary: str
    command: str = ""
    cwd: str = ""
    timeout_seconds: int = 60


class PolicyDecision(BaseModel):
    """策略引擎对一次观察作出的最终决定。"""

    mode: RuleMode | None = None
    character_state: CharacterState = CharacterState.NORMAL
    happiness_value: int = 50
    previous_happiness: int = 50
    happiness_delta: int = 0
    should_intervene: bool = False
    reason: str = ""
    conflict: bool = False
    celebration_triggered: bool = False
    peak_happiness_value: int | None = Field(default=None, ge=0, le=100)
