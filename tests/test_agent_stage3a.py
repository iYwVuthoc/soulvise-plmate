"""阶段三A的本机 Agent 配置、会话、审批和模拟 App Server 验收。"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import threading
from importlib.metadata import version
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from typing import Any

from desktop_companion_agent.config import ConfigManager
from desktop_companion_agent.models import (
    AgentCapability,
    AgentConnectionState,
    AgentProfile,
    AgentProviderManifest,
    AgentSession,
)
from desktop_companion_agent.paths import AppPaths
from desktop_companion_agent.services.agent_providers import (
    AgentProvider,
    CapabilityRouter,
    ProviderRegistry,
)
from desktop_companion_agent.services.codex_agent import (
    CODEX_PROFILE_ID,
    CodexAgentService,
    SdkCodexBackend,
    _HiddenCodexSubprocess,
    _install_hidden_codex_process_launcher,
)
from desktop_companion_agent.storage.repository import CognitionRepository


def _wait_until(qt_app, condition, seconds: float = 2.0) -> None:
    deadline = monotonic() + seconds
    while not condition() and monotonic() < deadline:
        qt_app.processEvents()
    qt_app.processEvents()
    assert condition()


class _FakeHandle:
    def __init__(self) -> None:
        self.interrupted = False

    def interrupt(self) -> None:
        self.interrupted = True


class _FakeCodexBackend:
    """不启动进程、不联网的 App Server 协议桩。"""

    def __init__(self, approval_handler) -> None:
        self.approval_handler = approval_handler
        self.signed_in = True
        self.closed = False
        self.logged_out = False
        self.chat_session_received = ""
        self.development_started = False
        self.last_handle: _FakeHandle | None = None
        self.login_cancelled = False

    def account_detail(self) -> tuple[bool, str]:
        return self.signed_in, "已登录 · test"

    def login_browser(self):
        return (
            "https://auth.example.test",
            lambda: True,
            self._cancel_login,
        )

    def login_device_code(self):
        return (
            "https://device.example.test",
            "TEST-CODE",
            lambda: True,
            self._cancel_login,
        )

    def _cancel_login(self) -> None:
        self.login_cancelled = True

    def logout(self) -> None:
        self.logged_out = True
        self.signed_in = False

    def start_or_resume_chat(self, session_id, workspace_root, skill_path):
        self.chat_session_received = session_id
        return session_id or "thread-chat-1", object()

    def start_development(self, workspace_root, skill_path):
        self.development_started = True
        return "thread-development-1", object()

    def _stream(self, text: str):
        payload = SimpleNamespace(delta=text)
        yield SimpleNamespace(method="item/agentMessage/delta", payload=payload)

    def stream_chat(self, thread, message):
        self.last_handle = _FakeHandle()
        return self.last_handle, self._stream(f"回复：{message}")

    def stream_development(self, thread, message):
        self.last_handle = _FakeHandle()
        return self.last_handle, self._stream(f"开发建议：{message}")

    def interrupt(self, handle) -> None:
        handle.interrupt()

    def close(self) -> None:
        self.closed = True


class _RouterProvider(AgentProvider):
    def __init__(
        self,
        provider_id: str,
        state: AgentConnectionState,
        recoverable: bool = False,
    ) -> None:
        self._provider_id = provider_id
        self.state = state
        self.recoverable = recoverable

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def detect(self) -> AgentProviderManifest:
        return AgentProviderManifest(
            provider_id=self.provider_id,
            display_name=self.provider_id,
            vendor="test",
            state=self.state,
            capabilities=[AgentCapability.CHAT],
        )

    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def cancel(self) -> None:
        return None

    def recover(self) -> bool:
        if self.recoverable and self.state in {
            AgentConnectionState.OFFLINE,
            AgentConnectionState.ERROR,
        }:
            self.state = AgentConnectionState.READY
            return True
        return False


def _source_tree(root: Path) -> Path:
    (root / "src" / "desktop_companion_agent").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='test'", encoding="utf-8")
    return root


def _service(tmp_path):
    paths = AppPaths.resolve(tmp_path / "data")
    repository = CognitionRepository(paths.database_file)
    backends: list[_FakeCodexBackend] = []

    def factory(handler):
        backend = _FakeCodexBackend(handler)
        backends.append(backend)
        return backend

    service = CodexAgentService(repository, paths, backend_factory=factory)
    return service, repository, backends


def test_schema_v2_upgrades_to_v4_without_losing_values(tmp_path) -> None:
    config_file = tmp_path / "config.json"
    config_file.write_text(
        '{"schema_version":2,"companion_enabled":true,"model":{"chat_model":"old"}}',
        encoding="utf-8",
    )
    loaded = ConfigManager(config_file).load()
    assert loaded.schema_version == 6
    assert loaded.companion_enabled is True
    assert loaded.model.chat_model == "old"
    assert loaded.agent_routing.primary_agent_id == CODEX_PROFILE_ID
    assert loaded.agent_routing.capability_overrides == {}
    assert loaded.capture.stability_seconds == 5
    assert loaded.companion_policy.semantic_confidence_threshold == 0.80


def test_capability_router_recovers_offline_provider_once(tmp_path) -> None:
    manager = ConfigManager(tmp_path / "config.json")
    manager.save(manager.config)
    registry = ProviderRegistry()
    provider = _RouterProvider(
        CODEX_PROFILE_ID,
        AgentConnectionState.OFFLINE,
        recoverable=True,
    )
    registry.register(provider)
    router = CapabilityRouter(manager, registry)
    assert router.resolve(AgentCapability.CHAT) is provider
    assert provider.state is AgentConnectionState.READY


def test_codex_sdk_version_and_approval_hook_are_pinned() -> None:
    def approval_handler(_method, _params):
        return {"decision": "decline"}

    backend = SdkCodexBackend(approval_handler)
    assert version("openai-codex") == "0.144.4"
    assert backend.codex._client._approval_handler is approval_handler
    backend.close()


def test_codex_windows_launcher_hides_its_console(monkeypatch) -> None:
    """Windows App Server 必须隐藏控制台，并保持 SDK 管道参数不变。"""

    captured: dict[str, Any] = {}

    def fake_popen(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return object()

    delegate = SimpleNamespace(Popen=fake_popen, PIPE=subprocess.PIPE)
    proxy = _HiddenCodexSubprocess(delegate)
    monkeypatch.setattr(os, "name", "nt")
    proxy.Popen(["codex.exe"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    assert captured["kwargs"]["creationflags"] & subprocess.CREATE_NO_WINDOW
    startupinfo = captured["kwargs"]["startupinfo"]
    assert startupinfo.dwFlags & subprocess.STARTF_USESHOWWINDOW
    assert startupinfo.wShowWindow == subprocess.SW_HIDE
    assert captured["kwargs"]["stdin"] is subprocess.PIPE


def test_codex_hidden_launcher_installation_is_idempotent(monkeypatch) -> None:
    """多个 Codex 后端共享同一个代理，不能层层重复包装。"""

    module = SimpleNamespace(subprocess=subprocess)
    monkeypatch.setattr(os, "name", "nt")
    _install_hidden_codex_process_launcher(module)
    installed = module.subprocess
    _install_hidden_codex_process_launcher(module)
    assert module.subprocess is installed


def test_legacy_agent_table_adds_deep_connection_columns(tmp_path) -> None:
    database = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE agent_profiles (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, connector_type TEXT NOT NULL,
            target TEXT NOT NULL, arguments_json TEXT NOT NULL, model TEXT NOT NULL,
            enabled INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        INSERT INTO agent_profiles VALUES
            ('legacy-web', '旧网页入口', 'web', 'https://example.com', '[]', '', 1, 'a', 'a');
        """
    )
    connection.commit()
    connection.close()

    repository = CognitionRepository(database)
    profile = repository.list_agent_profiles()[0]
    assert profile.id == "legacy-web"
    assert profile.connection_mode.value == "launch_only"
    assert profile.capabilities == []
    repository.close()


def test_agent_session_round_trip_and_profile_fields(tmp_path) -> None:
    repository = CognitionRepository(tmp_path / "agent.sqlite3")
    profile = AgentProfile(
        id=CODEX_PROFILE_ID,
        name="Codex",
        connector_type="codex",
        vendor="OpenAI",
        connection_mode="deep",
        capabilities=[AgentCapability.CHAT, AgentCapability.PROJECT_ASSIST],
    )
    repository.add_agent_profile(profile)
    repository.save_agent_session(
        AgentSession(
            profile_id=CODEX_PROFILE_ID,
            purpose="chat",
            provider_session_id="thread-123",
        )
    )
    restored = repository.list_agent_profiles()[0]
    session = repository.get_agent_session(CODEX_PROFILE_ID, "chat")
    assert restored.vendor == "OpenAI"
    assert restored.capabilities == [AgentCapability.CHAT, AgentCapability.PROJECT_ASSIST]
    assert session is not None and session.provider_session_id == "thread-123"
    repository.close()


def test_capability_router_uses_primary_and_rejects_offline_provider(tmp_path) -> None:
    manager = ConfigManager(tmp_path / "config.json")
    manager.load()
    registry = ProviderRegistry()
    ready = _RouterProvider(CODEX_PROFILE_ID, AgentConnectionState.READY)
    registry.register(ready)
    router = CapabilityRouter(manager, registry)
    assert router.resolve(AgentCapability.CHAT) is ready
    ready.state = AgentConnectionState.OFFLINE
    assert router.resolve(AgentCapability.CHAT) is None
    assert router.resolve(AgentCapability.VISION_ANALYSIS) is None


def test_mock_codex_connect_stream_resume_and_development(qt_app, tmp_path) -> None:
    service, repository, backends = _service(tmp_path)
    source = _source_tree(tmp_path / "source")
    valid, _message = service.set_workspace_root(source)
    assert valid is True
    service.connect()
    _wait_until(qt_app, lambda: service.manifest.state is AgentConnectionState.READY)

    deltas: list[str] = []
    service.chat_delta.connect(deltas.append)
    assert service.send_chat("介绍项目") is True
    _wait_until(qt_app, lambda: not service._busy)
    assert "".join(deltas) == "回复：介绍项目"
    saved = repository.get_agent_session(CODEX_PROFILE_ID, "chat")
    assert saved is not None and saved.provider_session_id == "thread-chat-1"

    development: list[str] = []
    service.development_delta.connect(development.append)
    assert service.send_development_task("检查测试") is True
    _wait_until(qt_app, lambda: not service._busy)
    assert "".join(development) == "开发建议：检查测试"
    assert backends[0].development_started is True
    service.shutdown()

    resumed_backends: list[_FakeCodexBackend] = []

    def resumed_factory(handler):
        backend = _FakeCodexBackend(handler)
        resumed_backends.append(backend)
        return backend

    resumed = CodexAgentService(repository, service.paths, backend_factory=resumed_factory)
    resumed.connect()
    _wait_until(qt_app, lambda: resumed.manifest.state is AgentConnectionState.READY)
    assert resumed.send_chat("恢复会话") is True
    _wait_until(qt_app, lambda: not resumed._busy)
    assert resumed_backends[0].chat_session_received == "thread-chat-1"
    resumed.shutdown()
    repository.close()


def test_codex_browser_device_login_logout_and_cancel(qt_app, tmp_path) -> None:
    service, repository, backends = _service(tmp_path)
    service.connect()
    _wait_until(qt_app, lambda: service.manifest.state is AgentConnectionState.READY)
    browser_urls: list[str] = []
    device_codes: list[tuple[str, str]] = []
    service.browser_login_ready.connect(browser_urls.append)
    service.device_login_ready.connect(lambda url, code: device_codes.append((url, code)))

    service.login_browser()
    _wait_until(qt_app, lambda: bool(browser_urls) and not service._busy)
    service.login_device_code()
    _wait_until(qt_app, lambda: bool(device_codes) and not service._busy)
    assert browser_urls == ["https://auth.example.test"]
    assert device_codes == [("https://device.example.test", "TEST-CODE")]

    handle = _FakeHandle()
    service._active_handle = handle
    login_cancelled: list[bool] = []
    service._active_cancel = lambda: login_cancelled.append(True)
    service.cancel()
    assert handle.interrupted is True
    assert login_cancelled == [True]
    service.logout()
    _wait_until(qt_app, lambda: service.manifest.state is AgentConnectionState.SIGNED_OUT)
    assert backends[0].logged_out is True
    service.shutdown()
    repository.close()


def test_codex_error_state_mapping_is_explicit() -> None:
    assert (
        CodexAgentService._connection_state_for_error(RuntimeError("rate limit"))
        is AgentConnectionState.RATE_LIMITED
    )


def test_failed_codex_connection_can_be_retried(qt_app, tmp_path) -> None:
    """App Server 管道被关闭后应释放旧后端，下一次连接重新创建进程。"""

    paths = AppPaths.resolve(tmp_path / "retry-data")
    repository = CognitionRepository(paths.database_file)
    backends: list[_FakeCodexBackend] = []

    class FailingBackend(_FakeCodexBackend):
        def account_detail(self) -> tuple[bool, str]:
            raise OSError(22, "Invalid argument")

    def factory(handler):
        backend = (
            FailingBackend(handler)
            if not backends
            else _FakeCodexBackend(handler)
        )
        backends.append(backend)
        return backend

    service = CodexAgentService(repository, paths, backend_factory=factory)
    service.connect()
    _wait_until(qt_app, lambda: service.manifest.state is AgentConnectionState.ERROR)
    assert service._backend is None
    assert backends[0].closed is True
    assert service.manifest.detail == "Codex App Server 已断开，请重新检测连接"

    service.connect()
    _wait_until(qt_app, lambda: service.manifest.state is AgentConnectionState.READY)
    assert len(backends) == 3
    assert service._runtime_backend is backends[2]
    service.shutdown()
    repository.close()
    assert (
        CodexAgentService._connection_state_for_error(ConnectionError("offline"))
        is AgentConnectionState.OFFLINE
    )
    assert (
        CodexAgentService._connection_state_for_error(RuntimeError("bad response"))
        is AgentConnectionState.ERROR
    )


def test_development_approval_allows_once_and_rejects_outside_workspace(
    qt_app,
    tmp_path,
) -> None:
    service, repository, _backends = _service(tmp_path)
    source = _source_tree(tmp_path / "source")
    assert service.set_workspace_root(source)[0] is True
    result: dict[str, Any] = {}

    def request_approval() -> None:
        result.update(
            service._approval_handler(
                "item/fileChange/requestApproval",
                {"cwd": str(source), "changes": [{"path": "README.md"}]},
            )
        )

    thread = threading.Thread(target=request_approval)
    thread.start()
    _wait_until(qt_app, lambda: bool(service._pending_approvals))
    request_id = next(iter(service._pending_approvals))
    service.resolve_approval(request_id, True)
    thread.join(timeout=1)
    assert result == {"decision": "accept"}

    rejected = service._approval_handler(
        "item/fileChange/requestApproval",
        {"cwd": str(tmp_path), "changes": [{"path": "outside.txt"}]},
    )
    assert rejected == {"decision": "decline"}
    service.shutdown()
    repository.close()
