"""阶段三B的Codex运行路由、临时图片和无密钥认知整理验收。"""

from __future__ import annotations

import os
from pathlib import Path
from time import monotonic, time
from types import SimpleNamespace

import pytest
from PySide6.QtTest import QSignalSpy

from desktop_companion_agent.config import AppConfig, ConfigManager, ModelSettings
from desktop_companion_agent.models import (
    AgentCapability,
    AgentConnectionState,
    AgentProviderManifest,
    CognitionDraft,
    CognitionDraftSchema,
    CognitionRule,
    InterestJudgment,
    RiskAssessment,
    RuleMode,
    SemanticRuleMatch,
    VisionAnalysisSchema,
)
from desktop_companion_agent.paths import AppPaths
from desktop_companion_agent.security import InMemorySecretStore
from desktop_companion_agent.services.agent_providers import (
    AgentProvider,
    CapabilityRouter,
    ProviderRegistry,
)
from desktop_companion_agent.services.analyzer import (
    AgentRoutedContentAnalyzer,
    AnalysisContext,
    OpenAIContentAnalyzer,
)
from desktop_companion_agent.services.codex_agent import (
    CODEX_PROFILE_ID,
    CodexAgentService,
    SdkCodexBackend,
    _strict_codex_output_schema,
)
from desktop_companion_agent.services.cognition_organizer import CognitionOrganizerService
from desktop_companion_agent.services.temporary_images import VisionTemporaryImageStore
from desktop_companion_agent.storage.repository import CognitionRepository


def _wait_until(qt_app, condition, seconds: float = 2.0) -> None:
    deadline = monotonic() + seconds
    while not condition() and monotonic() < deadline:
        qt_app.processEvents()
    qt_app.processEvents()
    assert condition()


class _RuntimeProvider(AgentProvider):
    """不联网的主Agent桩，可检查临时图片是否只在调用期间存在。"""

    def __init__(self, result: VisionAnalysisSchema, ready: bool = True):
        self.result = result
        self.ready = ready
        self.image_existed_during_call = False
        self.analyze_calls = 0
        self.cancelled = False
        self.organize_calls: list[tuple[str, str]] = []

    @property
    def provider_id(self) -> str:
        return CODEX_PROFILE_ID

    def detect(self) -> AgentProviderManifest:
        return AgentProviderManifest(
            provider_id=self.provider_id,
            display_name="Codex测试桩",
            vendor="OpenAI",
            version="0.144.4-test",
            state=(
                AgentConnectionState.READY
                if self.ready
                else AgentConnectionState.OFFLINE
            ),
            capabilities=[
                AgentCapability.VISION_ANALYSIS,
                AgentCapability.SEMANTIC_SUPERVISION,
                AgentCapability.COGNITION_ORGANIZE,
            ],
        )

    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def cancel(self) -> None:
        return None

    def cancel_runtime(self) -> None:
        self.cancelled = True

    def analyze_image(self, image_path: Path, context: dict) -> VisionAnalysisSchema:
        self.analyze_calls += 1
        self.image_existed_during_call = image_path.is_file()
        assert context["window_title_untrusted"] == "普通窗口"
        return self.result

    def organize_cognition(self, mode: str, text: str) -> CognitionDraftSchema:
        self.organize_calls.append((mode, text))
        return CognitionDraftSchema(
            drafts=[CognitionDraft(keywords=["风车", "暮色小镇"], title="")]
        )


class _CountingOpenAIAnalyzer(OpenAIContentAnalyzer):
    def __init__(self):
        super().__init__(ModelSettings(), InMemorySecretStore())
        self.calls = 0

    def analyze(self, *args, **kwargs):
        self.calls += 1
        return super().analyze(*args, **kwargs)


def _routed_analyzer(tmp_path, provider: _RuntimeProvider):
    manager = ConfigManager(tmp_path / "config.json")
    manager.save(AppConfig())
    registry = ProviderRegistry()
    registry.register(provider)
    router = CapabilityRouter(manager, registry)
    openai_analyzer = _CountingOpenAIAnalyzer()
    store = VisionTemporaryImageStore(tmp_path / "vision")
    analyzer = AgentRoutedContentAnalyzer(manager, router, openai_analyzer, store)
    return analyzer, openai_analyzer, store, router


def test_temporary_image_success_exception_and_stale_cleanup(tmp_path) -> None:
    store = VisionTemporaryImageStore(tmp_path / "vision")
    with store.materialize(b"jpeg-data") as path:
        assert path.is_file()
        assert path.read_bytes() == b"jpeg-data"
    assert not path.exists()

    with (
        pytest.raises(RuntimeError, match="模拟失败"),
        store.materialize(b"second") as failed,
    ):
        assert failed.exists()
        raise RuntimeError("模拟失败")
    assert not failed.exists()

    stale = store.root / ("vision-" + "a" * 32 + ".jpg")
    stale.write_bytes(b"old")
    os.utime(stale, (time() - 7200, time() - 7200))
    unrelated = store.root / "do-not-delete.txt"
    unrelated.write_text("保留", encoding="utf-8")
    assert store.cleanup_stale() == 1
    assert not stale.exists()
    assert unrelated.exists()
    with store.materialize(b"cancel") as active:
        store.clear_all()
        assert not active.exists()
    fresh_crash_orphan = store.root / ("vision-" + "b" * 32 + ".jpg")
    fresh_crash_orphan.write_bytes(b"fresh")
    store.clear_all()
    assert not fresh_crash_orphan.exists()


def test_codex_one_result_drives_supervision_interest_and_summary(tmp_path) -> None:
    provider = _RuntimeProvider(
        VisionAnalysisSchema(
            summary="画面展示了用户感兴趣的风车内容",
            risk_assessments=[
                RiskAssessment(category="sexual", confidence=0.92),
                RiskAssessment(category="invented-category", confidence=0.99),
            ],
            supervision_matches=[
                SemanticRuleMatch(rule_id="supervision.valid", confidence=0.92),
                SemanticRuleMatch(rule_id="invented.rule", confidence=0.99),
            ],
            companion_matches=[
                SemanticRuleMatch(rule_id="interest.valid", confidence=0.92),
                SemanticRuleMatch(rule_id="invented.interest", confidence=0.99),
            ],
            suggested_memory="用户可能喜欢风车小镇",
        )
    )
    analyzer, openai_analyzer, store, _router = _routed_analyzer(tmp_path, provider)
    supervision = CognitionRule(
        id="supervision.valid",
        mode=RuleMode.SUPERVISION,
        title="监督规则",
        keywords=["不在标题中"],
    )
    interest = CognitionRule(
        id="interest.valid",
        mode=RuleMode.COMPANION,
        title="兴趣规则",
        keywords=["也不在标题中"],
    )
    result = analyzer.analyze(
        b"synthetic-jpeg",
        AnalysisContext("测试应用", "普通窗口", True, True),
        [supervision],
        [interest],
    )
    assert result.flagged is True
    assert result.matched_supervision_rule_ids == ["supervision.valid"]
    assert result.matched_interest_rule_ids == ["interest.valid"]
    assert result.interest is InterestJudgment.INTERESTED
    assert result.summary.startswith("画面展示")
    assert result.categories == ["agent/sexual"]
    assert result.suggested_memory == "用户可能喜欢风车小镇"
    assert provider.analyze_calls == 1
    assert provider.image_existed_during_call is True
    assert openai_analyzer.calls == 0
    assert list(store.root.glob("vision-*.jpg")) == []


def test_invalid_ids_low_confidence_and_offline_never_silently_fallback(tmp_path) -> None:
    provider = _RuntimeProvider(
        VisionAnalysisSchema(
            summary="低置信度",
            supervision_matches=[SemanticRuleMatch(rule_id="valid", confidence=0.84)],
        )
    )
    analyzer, openai_analyzer, _store, _router = _routed_analyzer(tmp_path, provider)
    rule = CognitionRule(
        id="valid",
        mode=RuleMode.SUPERVISION,
        title="语义规则",
        keywords=["未命中标题"],
    )
    result = analyzer.analyze(
        b"jpeg",
        AnalysisContext("应用", "普通窗口", True, False),
        [rule],
        [],
    )
    assert result.flagged is False
    assert result.matched_supervision_rule_ids == []

    provider.ready = False
    unavailable = analyzer.analyze(
        b"jpeg",
        AnalysisContext("应用", "普通窗口", True, False),
        [rule],
        [],
    )
    assert unavailable.analysis_available is False
    assert unavailable.flagged is False
    assert openai_analyzer.calls == 0


def test_codex_extra_risk_category_records_until_user_enables_blocking(tmp_path) -> None:
    provider = _RuntimeProvider(
        VisionAnalysisSchema(
            summary="仅记录风险",
            risk_assessments=[RiskAssessment(category="self-harm", confidence=0.9)],
        )
    )
    analyzer, _openai, _store, _router = _routed_analyzer(tmp_path, provider)
    context = AnalysisContext("应用", "普通窗口", True, False)
    recorded = analyzer.analyze(b"jpeg", context, [], [])
    assert recorded.categories == ["agent/self-harm"]
    assert recorded.flagged is False

    current = analyzer.config_manager.config
    policy = current.moderation_policy.model_copy(
        update={
            "blocking_categories": [
                *current.moderation_policy.blocking_categories,
                "self-harm",
            ]
        }
    )
    analyzer.config_manager.save(current.model_copy(update={"moderation_policy": policy}))
    blocked = analyzer.analyze(b"jpeg", context, [], [])
    assert blocked.flagged is True


def test_local_explicit_rule_works_without_agent_or_temporary_file(tmp_path) -> None:
    provider = _RuntimeProvider(VisionAnalysisSchema(summary="不应调用"), ready=False)
    analyzer, _openai, store, _router = _routed_analyzer(tmp_path, provider)
    rule = CognitionRule(
        id="local",
        mode=RuleMode.SUPERVISION,
        title="本地规则",
        keywords=["明确命中"],
    )
    result = analyzer.analyze(
        b"jpeg",
        AnalysisContext("应用", "这里明确命中", True, False),
        [rule],
        [],
    )
    assert result.flagged is True
    assert result.analysis_available is True
    assert provider.analyze_calls == 0
    assert list(store.root.iterdir()) == []


def test_cognition_organizer_uses_ready_codex_without_api_key(qt_app, tmp_path) -> None:
    provider = _RuntimeProvider(VisionAnalysisSchema(summary="unused"))
    _analyzer, _openai, _store, router = _routed_analyzer(tmp_path, provider)
    service = CognitionOrganizerService(
        ModelSettings(),
        InMemorySecretStore(),
        capability_router=router,
    )
    ready = QSignalSpy(service.drafts_ready)
    assert service.organize("我喜欢黄昏的风车", RuleMode.COMPANION) is True
    _wait_until(qt_app, lambda: ready.count() == 1)
    result = ready.at(0)[0]
    assert result.drafts[0].keywords == ["风车", "暮色小镇"]
    assert result.drafts[0].title == "感兴趣：风车"
    assert provider.organize_calls == [("companion", "我喜欢黄昏的风车")]
    service.shutdown()


class _StructuredBackend:
    """模拟Codex App Server结构化流，并可注入越权工具事件。"""

    def __init__(self, approval_handler, forbidden: bool = False):
        self.approval_handler = approval_handler
        self.forbidden = forbidden
        self.last_handle = SimpleNamespace(interrupted=False)
        self.closed = False

    def stream_structured(self, prompt, output_schema, cwd, image_path=None):
        del prompt, output_schema, cwd, image_path

        def interrupt() -> None:
            self.last_handle.interrupted = True

        self.last_handle.interrupt = interrupt

        def stream():
            if self.forbidden:
                yield SimpleNamespace(
                    method="item/commandExecution/started",
                    payload=SimpleNamespace(item=SimpleNamespace()),
                )
                return
            text = VisionAnalysisSchema(summary="结构化成功").model_dump_json()
            yield SimpleNamespace(
                method="item/agentMessage/delta",
                payload=SimpleNamespace(delta=text),
            )

        return self.last_handle, stream()

    def interrupt(self, handle) -> None:
        handle.interrupt()

    def close(self) -> None:
        self.closed = True


def test_codex_runtime_rejects_tool_activity_and_uses_separate_backend(tmp_path) -> None:
    paths = AppPaths.resolve(tmp_path / "data")
    repository = CognitionRepository(paths.database_file)
    backends: list[_StructuredBackend] = []

    def factory(handler):
        backend = _StructuredBackend(handler, forbidden=True)
        backends.append(backend)
        return backend

    service = CodexAgentService(repository, paths, backend_factory=factory)
    service._manifest = service.manifest.model_copy(  # noqa: SLF001
        update={
            "state": AgentConnectionState.READY,
            "capabilities": [AgentCapability.VISION_ANALYSIS],
        }
    )
    image = tmp_path / "vision.jpg"
    image.write_bytes(b"jpeg")
    with pytest.raises(RuntimeError, match="禁止"):
        service.analyze_image(image, {})
    assert backends[0].last_handle.interrupted is True
    assert backends[0].approval_handler("anything", {}) == {"decision": "decline"}
    service.shutdown()
    repository.close()


def test_codex_runtime_accepts_valid_structured_result(tmp_path) -> None:
    paths = AppPaths.resolve(tmp_path / "data")
    repository = CognitionRepository(paths.database_file)

    def factory(handler):
        return _StructuredBackend(handler, forbidden=False)

    service = CodexAgentService(repository, paths, backend_factory=factory)
    service._manifest = service.manifest.model_copy(  # noqa: SLF001
        update={
            "state": AgentConnectionState.READY,
            "capabilities": [AgentCapability.VISION_ANALYSIS],
        }
    )
    image = tmp_path / "vision.jpg"
    image.write_bytes(b"jpeg")
    result = service.analyze_image(image, {"rules": []})
    assert result.summary == "结构化成功"
    service.shutdown()
    repository.close()


def test_codex_offline_runtime_recovers_without_reconnecting_chat(tmp_path) -> None:
    paths = AppPaths.resolve(tmp_path / "recovery-data")
    repository = CognitionRepository(paths.database_file)
    backends: list[_StructuredBackend] = []

    class SignedInStructuredBackend(_StructuredBackend):
        def account_detail(self):
            return True, "已登录 · recovery-test"

    def factory(handler):
        backend = SignedInStructuredBackend(handler, forbidden=False)
        backends.append(backend)
        return backend

    service = CodexAgentService(repository, paths, backend_factory=factory)
    chat_backend = factory(service._approval_handler)  # noqa: SLF001
    stale_runtime = factory(service._runtime_approval_handler)  # noqa: SLF001
    service._backend = chat_backend  # noqa: SLF001
    service._runtime_backend = stale_runtime  # noqa: SLF001
    service._manifest = service.manifest.model_copy(  # noqa: SLF001
        update={
            "state": AgentConnectionState.OFFLINE,
            "capabilities": [AgentCapability.VISION_ANALYSIS],
        }
    )

    assert service.recover() is True
    assert service.manifest.state is AgentConnectionState.READY
    assert service._backend is chat_backend  # noqa: SLF001
    assert stale_runtime.closed is True
    assert service._runtime_backend is not stale_runtime  # noqa: SLF001
    service.shutdown()
    repository.close()


def test_codex_output_schema_is_strict() -> None:
    """Codex结构化输出必须满足Responses后端的严格Schema约束。"""

    schema = _strict_codex_output_schema(VisionAnalysisSchema)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert all(
        value.get("additionalProperties") is False
        for value in schema.get("$defs", {}).values()
        if value.get("type") == "object"
    )


def test_codex_runtime_timeout_has_safe_visual_floor(tmp_path) -> None:
    """过短的通用超时不能让首次视觉初始化被提前误判为离线。"""

    paths = AppPaths.resolve(tmp_path / "timeout-data")
    repository = CognitionRepository(paths.database_file)
    service = CodexAgentService(repository, paths, backend_factory=lambda handler: None)
    service.set_runtime_timeout(5)
    assert service._runtime_timeout_seconds == 45.0
    service.shutdown()
    repository.close()


def test_sdk_observation_turn_uses_low_reasoning_effort(tmp_path) -> None:
    captured: dict[str, object] = {}

    class Handle:
        def stream(self):
            return iter(())

    class Thread:
        def turn(self, inputs, **kwargs):
            captured["inputs"] = inputs
            captured.update(kwargs)
            return Handle()

    backend = SdkCodexBackend.__new__(SdkCodexBackend)
    backend.codex = SimpleNamespace(
        thread_start=lambda **kwargs: captured.update({"thread": kwargs}) or Thread()
    )
    image = tmp_path / "vision.jpg"
    image.write_bytes(b"jpeg")
    _handle, _stream = backend.stream_structured(
        "只分类",
        {"type": "object"},
        tmp_path,
        image,
    )
    assert captured["effort"] == "low"
    assert captured["thread"]["ephemeral"] is True
