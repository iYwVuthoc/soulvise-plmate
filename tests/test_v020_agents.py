from __future__ import annotations

import asyncio
import sys
from concurrent.futures import Future
from pathlib import Path
from time import monotonic

from desktop_companion_agent.config import (
    DEFAULT_INTERVENTION_URL,
    AppConfig,
    ConfigManager,
)
from desktop_companion_agent.models import (
    AgentCapability,
    AgentConnectionMode,
    AgentConnectionState,
    AgentProfile,
    AgentSession,
    BubbleEventType,
    CognitionDraft,
    CognitionDraftSchema,
    SpeechBubbleMessage,
    VisionAnalysisSchema,
)
from desktop_companion_agent.paths import AppPaths
from desktop_companion_agent.security import InMemorySecretStore
from desktop_companion_agent.services.acp_agent import (
    AcpAgentProvider,
    AcpHandshake,
    AcpRuntime,
    _RestrictedAcpClient,
)
from desktop_companion_agent.services.agent_providers import CapabilityRouter, ProviderRegistry
from desktop_companion_agent.services.chat import OpenAIChatService
from desktop_companion_agent.services.cognition_organizer import CognitionOrganizerService
from desktop_companion_agent.services.connectors import ConnectorRegistry
from desktop_companion_agent.storage.repository import CognitionRepository
from desktop_companion_agent.ui.main_window import MainWindow


class FakeAcpRuntime:
    """模拟ACP握手和流式响应，不启动真实第三方Agent。"""

    image_supported = True

    def __init__(self, command: str, arguments: list[str], cwd: Path):
        self.command = command
        self.arguments = arguments
        self.cwd = cwd
        self.closed = False
        self.cancelled: list[str] = []
        self.sessions: dict[str, str] = {}
        self.restored: list[tuple[str, str]] = []
        self.workspace = ""
        self.image_prompt_count = 0
        self.approval_callback = None

    def connect(self) -> AcpHandshake:
        return AcpHandshake("test-1", self.image_supported, "模拟ACP已连接")

    def set_workspace_root(self, value: str) -> None:
        self.workspace = value

    def set_approval_callback(self, callback) -> None:
        self.approval_callback = callback

    def restore_session(self, purpose: str, session_id: str) -> None:
        self.restored.append((purpose, session_id))

    def session_id(self, purpose: str) -> str:
        return self.sessions.get(purpose, "")

    def prompt(self, purpose, blocks, timeout, delta_callback=None):
        del timeout
        self.sessions[purpose] = f"session-{purpose}"
        if len(blocks) > 1:
            self.image_prompt_count += 1
        prompt = blocks[0].text
        if "1到10条草稿" in prompt:
            return CognitionDraftSchema(
                drafts=[CognitionDraft(keywords=["风车小镇"])]
            ).model_dump_json()
        if "哥特角色短句" in prompt:
            return SpeechBubbleMessage(
                text="太棒啦，今天满分！",
                kind=BubbleEventType.CELEBRATION,
                priority=80,
            ).model_dump_json()
        if "JSON Schema" in prompt:
            return VisionAnalysisSchema(summary="无害合成测试图").model_dump_json()
        if delta_callback is not None:
            delta_callback("模拟回复")
        return "模拟回复"

    def cancel(self, purpose: str) -> None:
        self.cancelled.append(purpose)

    def close(self) -> None:
        self.closed = True


class FakeTextOnlyRuntime(FakeAcpRuntime):
    image_supported = False


class FakeInvalidStructuredRuntime(FakeAcpRuntime):
    def prompt(self, purpose, blocks, timeout, delta_callback=None):
        del purpose, blocks, timeout, delta_callback
        self.image_prompt_count += 1
        return "不是JSON"


class FakeCrashedRuntime(FakeAcpRuntime):
    def connect(self) -> AcpHandshake:
        raise RuntimeError("模拟进程崩溃")


class FakeTimeoutRuntime(FakeTextOnlyRuntime):
    def prompt(self, purpose, blocks, timeout, delta_callback=None):
        del purpose, blocks, timeout, delta_callback
        raise TimeoutError("模拟超时")


def _wait(qt_app, predicate, timeout: float = 2.0) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        qt_app.processEvents()
        if predicate():
            return
    assert predicate()


def _profile(**updates) -> AgentProfile:
    values = {
        "id": "acp.test",
        "name": "测试ACP",
        "connector_type": "acp",
        "target": sys.executable,
        "vendor": "测试厂商",
        "connection_mode": AgentConnectionMode.DEEP,
        "preset_id": "custom",
        "allow_image_input": True,
    }
    values.update(updates)
    return AgentProfile(**values)


def test_schema_v6_preserves_explicit_empty_url_and_new_install_has_default(tmp_path) -> None:
    manager = ConfigManager(tmp_path / "new.json")
    assert manager.load().intervention.redirect_url == DEFAULT_INTERVENTION_URL
    assert "vd_source" not in DEFAULT_INTERVENTION_URL

    old = tmp_path / "old.json"
    old.write_text(
        '{"schema_version":5,"intervention":{"redirect_url":""}}',
        encoding="utf-8",
    )
    migrated = ConfigManager(old).load()
    assert migrated.schema_version == 6
    assert migrated.intervention.redirect_url == ""


def test_repository_migrates_and_round_trips_acp_profile(tmp_path) -> None:
    repository = CognitionRepository(tmp_path / "profiles.sqlite3")
    profile = _profile(preset_id="gemini-cli", arguments=["--model", "test"])
    repository.add_agent_profile(profile)
    restored = repository.list_agent_profiles()[0]
    assert restored.connector_type == "acp"
    assert restored.preset_id == "gemini-cli"
    assert restored.allow_image_input is True
    assert restored.arguments == ["--model", "test"]
    repository.close()


def test_acp_requires_declared_image_capability_before_sending(qt_app, tmp_path) -> None:
    repository = CognitionRepository(tmp_path / "text-only.sqlite3")
    runtimes: list[FakeTextOnlyRuntime] = []

    def factory(command, arguments, cwd):
        runtime = FakeTextOnlyRuntime(command, arguments, cwd)
        runtimes.append(runtime)
        return runtime

    provider = AcpAgentProvider(_profile(), repository, tmp_path / "runtime", factory)
    provider.connect()
    _wait(qt_app, lambda: provider.detect().state is AgentConnectionState.READY)
    manifest = provider.detect()
    assert AgentCapability.CHAT in manifest.capabilities
    assert AgentCapability.VISION_ANALYSIS not in manifest.capabilities
    assert runtimes[0].image_prompt_count == 0
    provider.shutdown()
    repository.close()


def test_acp_structured_self_test_unlocks_deep_capabilities(qt_app, tmp_path) -> None:
    repository = CognitionRepository(tmp_path / "vision.sqlite3")
    runtimes: list[FakeAcpRuntime] = []

    def factory(command, arguments, cwd):
        runtime = FakeAcpRuntime(command, arguments, cwd)
        runtimes.append(runtime)
        return runtime

    provider = AcpAgentProvider(_profile(), repository, tmp_path / "runtime", factory)
    provider.connect()
    _wait(qt_app, lambda: provider.detect().state is AgentConnectionState.READY)
    manifest = provider.detect()
    assert AgentCapability.VISION_ANALYSIS in manifest.capabilities
    assert AgentCapability.SEMANTIC_SUPERVISION in manifest.capabilities
    assert runtimes[0].image_prompt_count == 1

    image = tmp_path / "synthetic.jpg"
    image.write_bytes(b"safe-test-image")
    result = provider.analyze_image(image, {"supervision_rules": [], "companion_rules": []})
    assert result.summary == "无害合成测试图"
    assert runtimes[0].image_prompt_count == 2

    drafts = provider.organize_cognition("companion", "我喜欢风车小镇")
    assert drafts.drafts[0].keywords == ["风车小镇"]
    bubble = provider.generate_bubble(
        {
            "kind": "celebration",
            "character_state": "happy",
            "happiness_value": 100,
            "happiness_delta": 10,
        }
    )
    assert bubble.kind is BubbleEventType.CELEBRATION
    assert repository.get_agent_session(profile_id="acp.test", purpose="runtime") is not None
    provider.shutdown()
    repository.close()


def test_acp_user_must_explicitly_allow_images(qt_app, tmp_path) -> None:
    repository = CognitionRepository(tmp_path / "not-authorized.sqlite3")
    runtimes: list[FakeAcpRuntime] = []

    def factory(command, arguments, cwd):
        runtime = FakeAcpRuntime(command, arguments, cwd)
        runtimes.append(runtime)
        return runtime

    provider = AcpAgentProvider(
        _profile(allow_image_input=False), repository, tmp_path / "runtime", factory
    )
    provider.connect()
    _wait(qt_app, lambda: provider.detect().state is AgentConnectionState.READY)
    assert AgentCapability.VISION_ANALYSIS not in provider.detect().capabilities
    assert runtimes[0].image_prompt_count == 0
    provider.shutdown()
    repository.close()


def test_provider_registry_can_reload_dynamic_acp_provider(tmp_path) -> None:
    repository = CognitionRepository(tmp_path / "registry.sqlite3")
    provider = AcpAgentProvider(_profile(), repository, tmp_path / "runtime", FakeAcpRuntime)
    registry = ProviderRegistry()
    registry.register(provider)
    assert registry.get("acp.test") is provider
    assert registry.unregister("acp.test") is provider
    assert registry.get("acp.test") is None
    registry.reload([provider])
    assert registry.get("acp.test") is provider
    provider.shutdown()
    repository.close()


def test_capability_router_uses_dynamic_primary_and_override(qt_app, tmp_path) -> None:
    repository = CognitionRepository(tmp_path / "router.sqlite3")
    provider = AcpAgentProvider(
        _profile(allow_image_input=False),
        repository,
        tmp_path / "runtime",
        FakeTextOnlyRuntime,
    )
    provider.connect()
    _wait(qt_app, lambda: provider.detect().state is AgentConnectionState.READY)
    registry = ProviderRegistry()
    registry.register(provider)
    manager = ConfigManager(tmp_path / "router.json")
    manager.save(
        AppConfig(
            agent_routing={
                "primary_agent_id": "acp.test",
                "capability_overrides": {"bubble_polish": "acp.test"},
            }
        )
    )
    router = CapabilityRouter(manager, registry)
    assert router.resolve(AgentCapability.CHAT) is provider
    assert router.resolve(AgentCapability.BUBBLE_POLISH) is provider
    assert router.resolve(AgentCapability.VISION_ANALYSIS) is None
    provider.shutdown()
    repository.close()


def test_acp_restores_session_pointer_and_cancel_is_separated(qt_app, tmp_path) -> None:
    repository = CognitionRepository(tmp_path / "resume.sqlite3")
    repository.save_agent_session(
        AgentSession(
            profile_id="acp.test",
            purpose="chat",
            provider_session_id="old-chat-session",
        )
    )
    runtimes: list[FakeTextOnlyRuntime] = []

    def factory(command, arguments, cwd):
        runtime = FakeTextOnlyRuntime(command, arguments, cwd)
        runtimes.append(runtime)
        return runtime

    provider = AcpAgentProvider(_profile(), repository, tmp_path / "runtime", factory)
    provider.connect()
    _wait(qt_app, lambda: provider.detect().state is AgentConnectionState.READY)
    assert ("chat", "old-chat-session") in runtimes[0].restored
    provider.cancel()
    provider.cancel_runtime()
    assert runtimes[0].cancelled == ["chat", "runtime"]
    provider.shutdown()
    repository.close()


def test_acp_development_session_is_separate_and_streams(qt_app, tmp_path) -> None:
    repository = CognitionRepository(tmp_path / "development.sqlite3")
    runtimes: list[FakeTextOnlyRuntime] = []

    def factory(command, arguments, cwd):
        runtime = FakeTextOnlyRuntime(command, arguments, cwd)
        runtimes.append(runtime)
        return runtime

    provider = AcpAgentProvider(
        _profile(allow_image_input=False, workspace_root=str(tmp_path)),
        repository,
        tmp_path / "runtime",
        factory,
    )
    completed: list[str] = []
    provider.development_completed.connect(completed.append)
    provider.connect()
    _wait(qt_app, lambda: provider.detect().state is AgentConnectionState.READY)
    assert AgentCapability.PROJECT_ASSIST in provider.detect().capabilities
    assert provider.send_development_task("只读检查测试") is True
    _wait(qt_app, lambda: bool(completed))
    assert completed == ["模拟回复"]
    assert runtimes[0].workspace == str(tmp_path)
    assert repository.get_agent_session("acp.test", "development") is not None
    provider.shutdown()
    repository.close()


def test_acp_crash_and_invalid_structure_degrade_safely(qt_app, tmp_path) -> None:
    repository = CognitionRepository(tmp_path / "errors.sqlite3")
    crashed = AcpAgentProvider(
        _profile(id="acp.crashed"), repository, tmp_path / "runtime", FakeCrashedRuntime
    )
    crashed.connect()
    _wait(qt_app, lambda: crashed.detect().state is AgentConnectionState.ERROR)
    assert not crashed.detect().capabilities

    invalid = AcpAgentProvider(
        _profile(id="acp.invalid"),
        repository,
        tmp_path / "runtime",
        FakeInvalidStructuredRuntime,
    )
    invalid.connect()
    _wait(qt_app, lambda: invalid.detect().state is AgentConnectionState.READY)
    assert AgentCapability.CHAT in invalid.detect().capabilities
    assert AgentCapability.VISION_ANALYSIS not in invalid.detect().capabilities
    crashed.shutdown()
    invalid.shutdown()
    repository.close()


def test_acp_runtime_timeout_marks_provider_offline(qt_app, tmp_path) -> None:
    repository = CognitionRepository(tmp_path / "timeout.sqlite3")
    provider = AcpAgentProvider(
        _profile(allow_image_input=False),
        repository,
        tmp_path / "runtime",
        FakeTimeoutRuntime,
    )
    provider.connect()
    _wait(qt_app, lambda: provider.detect().state is AgentConnectionState.READY)
    try:
        provider.organize_cognition("companion", "测试")
    except TimeoutError:
        pass
    else:
        raise AssertionError("超时必须向调用方报告")
    assert provider.detect().state is AgentConnectionState.OFFLINE
    assert not provider.detect().capabilities
    provider.shutdown()
    repository.close()


def test_app_config_model_keeps_default_video_only_for_new_instances() -> None:
    assert AppConfig().intervention.redirect_url == DEFAULT_INTERVENTION_URL
    assert AppConfig(intervention={"redirect_url": ""}).intervention.redirect_url == ""


def test_official_acp_sdk_stdio_handshake_and_stream(tmp_path) -> None:
    script = Path(__file__).parent / "fixtures" / "fake_acp_agent.py"
    runtime = AcpRuntime(sys.executable, [str(script)], tmp_path / "acp-runtime")
    try:
        handshake = runtime.connect(timeout=10)
        assert handshake.image_supported is True
        assert handshake.version == "1.0"
        from acp import text_block

        response = runtime.prompt("chat", [text_block("你好")], 10)
        assert response == "模拟协议回复"
    finally:
        runtime.close()


def test_npm_cmd_preset_is_converted_to_node_argument_array(tmp_path) -> None:
    shim = tmp_path / "gemini.cmd"
    node = tmp_path / "node.exe"
    script = tmp_path / "node_modules" / "agent" / "index.js"
    script.parent.mkdir(parents=True)
    node.write_bytes(b"")
    script.write_text("", encoding="utf-8")
    shim.write_text(
        '@ECHO off\n"%dp0%\\node.exe" "%dp0%\\node_modules\\agent\\index.js" %*',
        encoding="utf-8",
    )
    repository = CognitionRepository(tmp_path / "shim.sqlite3")
    provider = AcpAgentProvider(
        _profile(target=str(shim), preset_id="gemini-cli", arguments=["--model", "test"]),
        repository,
        tmp_path / "runtime",
        FakeAcpRuntime,
    )
    command, arguments, _detail = provider._resolve_command()
    assert command == str(node.resolve())
    assert arguments == [str(script.resolve()), "--acp", "--model", "test"]
    repository.close()


def test_agent_center_lists_acp_as_dynamic_primary(qt_app, tmp_path) -> None:
    del qt_app
    paths = AppPaths.resolve(tmp_path / "ui-data")
    manager = ConfigManager(paths.config_file)
    manager.save(AppConfig(agent_routing={"primary_agent_id": "acp.test"}))
    repository = CognitionRepository(paths.database_file)
    profile = _profile(allow_image_input=False)
    repository.add_agent_profile(profile)
    provider = AcpAgentProvider(profile, repository, tmp_path / "runtime", FakeTextOnlyRuntime)
    registry = ProviderRegistry()
    registry.register(provider)
    secrets = InMemorySecretStore()
    chat = OpenAIChatService(manager.config.model, secrets, repository, paths.context_dir)
    organizer = CognitionOrganizerService(manager.config.model, secrets)
    window = MainWindow(
        manager,
        repository,
        secrets,
        chat,
        ConnectorRegistry(),
        paths,
        organizer,
        capability_router=CapabilityRouter(manager, registry),
        provider_registry=registry,
    )
    assert window.acp_list.count() == 1
    assert window.primary_agent_combo.currentData() == "acp.test"
    assert window.capability_override_combos[AgentCapability.CHAT].findData("acp.test") >= 0
    window.allow_close()
    window.close()
    provider.shutdown()
    organizer.shutdown()
    chat.shutdown()
    repository.close()


def test_restricted_acp_client_denies_permissions_and_file_writes() -> None:
    client = _RestrictedAcpClient()
    response = asyncio.run(client.request_permission("runtime"))
    assert response.outcome.outcome == "cancelled"
    try:
        asyncio.run(client.write_text_file(session_id="runtime", path="x", content="y"))
    except PermissionError:
        pass
    else:
        raise AssertionError("只读ACP客户端不得允许文件写入")


def test_development_permission_only_accepts_explicit_allow_once() -> None:
    from acp.schema import PermissionOption, ToolCallStart, ToolCallUpdate

    client = _RestrictedAcpClient()
    client.set_purpose("development-session", "development")

    def approve_once(_session_id, _tool_call, _options):
        result: Future[str | None] = Future()
        result.set_result("allow-this-once")
        return result

    client.set_approval_callback(approve_once)
    response = asyncio.run(
        client.request_permission(
            "development-session",
            ToolCallUpdate(tool_call_id="tool-1", kind="edit", title="修改测试文件"),
            [
                PermissionOption(
                    option_id="allow-this-once",
                    name="仅本次允许",
                    kind="allow_once",
                )
            ],
        )
    )
    assert response.outcome.outcome == "selected"
    client.begin("development-session")
    asyncio.run(
        client.session_update(
            "development-session",
            ToolCallStart(
                tool_call_id="tool-1",
                title="修改测试文件",
                kind="edit",
                session_update="tool_call",
            ),
        )
    )
    _text, violation = client.finish("development-session")
    assert violation is False
