"""阶段三C的气泡、庆祝边界、奖杯历史与截图遮蔽验收。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Event
from time import monotonic
from types import SimpleNamespace

from PIL import Image
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QWidget

from desktop_companion_agent.app import ApplicationController
from desktop_companion_agent.config import ConfigManager, HappinessSettings
from desktop_companion_agent.models import (
    AgentCapability,
    AgentConnectionState,
    AgentProviderManifest,
    AnalysisResult,
    BubbleEvent,
    BubbleEventType,
    CharacterState,
    InterestJudgment,
    ObservationEvent,
    ObservationEventKind,
    RuleMode,
    SpeechBubbleMessage,
)
from desktop_companion_agent.services.agent_providers import (
    AgentProvider,
    CapabilityRouter,
    ProviderRegistry,
)
from desktop_companion_agent.services.bubbles import SpeechBubbleService
from desktop_companion_agent.services.policy import PolicyEngine
from desktop_companion_agent.services.privacy_mask import privacy_mask_registry
from desktop_companion_agent.storage.repository import CognitionRepository
from desktop_companion_agent.ui.speech_bubble import CharacterSpeechBubble


class _StateRecorder:
    """只记录角色状态，避免回归测试依赖真实GIF窗口。"""

    def __init__(self):
        self.states: list[CharacterState] = []

    def set_state(self, state: CharacterState) -> None:
        self.states.append(state)


def _wait_until(qt_app, condition, seconds: float = 2.0) -> None:
    deadline = monotonic() + seconds
    while not condition() and monotonic() < deadline:
        qt_app.processEvents()
    qt_app.processEvents()
    assert condition()


def test_happiness_reaches_100_once_then_can_celebrate_after_falling() -> None:
    policy = PolicyEngine(
        HappinessSettings(
            initial_value=50,
            interest_increment=10,
            disinterest_decrement=10,
        )
    )
    policy.happiness = 90
    interested = AnalysisResult(interest=InterestJudgment.INTERESTED)
    disinterested = AnalysisResult(interest=InterestJudgment.NOT_INTERESTED)

    first = policy.evaluate(interested, "one", False, True, 30)
    assert first.previous_happiness == 90
    assert first.happiness_value == 50
    assert first.peak_happiness_value == 100
    assert first.celebration_triggered is True

    duplicate = policy.evaluate(interested, "one", False, True, 30)
    assert duplicate.previous_happiness == 50
    assert duplicate.happiness_value == 50
    assert duplicate.celebration_triggered is False

    policy.happiness = 90
    lowered = policy.evaluate(disinterested, "three", False, True, 30)
    assert lowered.happiness_value == 80
    policy.happiness = 90
    again = policy.evaluate(interested, "four", False, True, 30)
    assert again.celebration_triggered is True
    assert again.peak_happiness_value == 100
    assert again.happiness_value == 50


def test_celebration_resets_to_custom_initial_value() -> None:
    policy = PolicyEngine(HappinessSettings(initial_value=70, interest_increment=10))
    policy.happiness = 90
    decision = policy.evaluate(
        AnalysisResult(interest=InterestJudgment.INTERESTED),
        "custom-initial",
        False,
        True,
        30,
    )
    assert decision.peak_happiness_value == 100
    assert decision.happiness_value == 70
    assert policy.happiness == 70


def test_supervision_conflict_never_creates_celebration() -> None:
    policy = PolicyEngine(HappinessSettings(initial_value=90, interest_increment=10))
    result = AnalysisResult(flagged=True, interest=InterestJudgment.INTERESTED)
    decision = policy.evaluate(result, "conflict", True, True, 30)
    assert decision.mode is RuleMode.SUPERVISION
    assert decision.happiness_value == 90
    assert decision.celebration_triggered is False


def test_pinned_celebration_is_first_never_expires_and_can_be_deleted(tmp_path) -> None:
    repository = CognitionRepository(tmp_path / "events.sqlite3")
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    repository.add_event(
        ObservationEvent(
            id="ordinary",
            mode=RuleMode.COMPANION,
            expires_at=past,
            summary="普通过期记录",
        )
    )
    repository.add_event(
        ObservationEvent(
            id="trophy",
            mode=RuleMode.COMPANION,
            expires_at=past,
            summary="记录这一值得庆祝的时刻",
            event_kind=ObservationEventKind.CELEBRATION,
            pinned=True,
            happiness_value=100,
        )
    )
    assert repository.purge_expired_events() == 1
    events = repository.list_events()
    assert [event.id for event in events] == ["trophy"]
    assert events[0].happiness_value == 100

    with repository._lock:  # noqa: SLF001
        columns = {
            row[1]
            for row in repository._connection.execute(  # noqa: SLF001
                "PRAGMA table_info(observation_events)"
            ).fetchall()
        }
    assert {"event_kind", "pinned", "happiness_value"}.issubset(columns)
    repository.delete_event("trophy")
    assert repository.list_events() == []
    repository.close()


class _BubbleProvider(AgentProvider):
    def __init__(self, first_gate: Event | None = None):
        self.first_gate = first_gate
        self.calls: list[dict] = []
        self.cancelled = False

    @property
    def provider_id(self) -> str:
        return "bubble.test"

    def detect(self) -> AgentProviderManifest:
        return AgentProviderManifest(
            provider_id=self.provider_id,
            display_name="气泡测试",
            vendor="test",
            state=AgentConnectionState.READY,
            capabilities=[AgentCapability.BUBBLE_POLISH],
        )

    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def cancel(self) -> None:
        return None

    def cancel_runtime(self) -> None:
        self.cancelled = True

    def generate_bubble(self, event: dict) -> SpeechBubbleMessage:
        self.calls.append(event)
        call_number = len(self.calls)
        if call_number == 1 and self.first_gate is not None:
            self.first_gate.wait(2)
        kind = BubbleEventType(event["kind"])
        return SpeechBubbleMessage(
            text=("迟到的兴趣润色" if call_number == 1 else "这次真的不可以哦！"),
            kind=kind,
            priority=50,
        )


def _bubble_service(tmp_path, provider: AgentProvider | None = None):
    manager = ConfigManager(tmp_path / "config.json")
    config = manager.load()
    registry = ProviderRegistry()
    if provider is not None:
        registry.register(provider)
        routing = config.agent_routing.model_copy(update={"primary_agent_id": provider.provider_id})
        manager.save(config.model_copy(update={"agent_routing": routing}))
    else:
        routing = config.agent_routing.model_copy(update={"primary_agent_id": ""})
        manager.save(config.model_copy(update={"agent_routing": routing}))
    router = CapabilityRouter(manager, registry)
    return SpeechBubbleService(manager, router)


def _event(kind: BubbleEventType, value: int = 50, delta: int = 0) -> BubbleEvent:
    return BubbleEvent(
        kind=kind,
        character_state=CharacterState.NORMAL,
        happiness_value=value,
        happiness_delta=delta,
    )


def test_offline_bubble_only_represents_connectivity_failures() -> None:
    """结构化格式等程序错误不能误报成“网络走丢了”。"""

    assert ApplicationController._is_connectivity_failure("Codex连接超时") is True
    assert ApplicationController._is_connectivity_failure("transport closed") is True
    assert ApplicationController._is_connectivity_failure("invalid_json_schema") is False


def test_angry_feedback_is_not_overwritten_by_immediate_normal_frame(
    qt_app,
    monkeypatch,
) -> None:
    """监督命中后的下一张普通画面不能让生气动画瞬间消失。"""

    del qt_app
    import desktop_companion_agent.app as app_module

    current_time = [100.0]
    monkeypatch.setattr(app_module, "monotonic", lambda: current_time[0])
    controller = ApplicationController.__new__(ApplicationController)
    controller._character_feedback_generation = 0
    controller._character_feedback_until = 0.0
    controller.character = _StateRecorder()
    controller.config_manager = SimpleNamespace(
        config=SimpleNamespace(blindfolded=False)
    )

    controller._apply_character_feedback(CharacterState.ANGRY)
    controller._apply_character_feedback(CharacterState.NORMAL)
    assert controller.character.states == [CharacterState.ANGRY]

    current_time[0] = 105.0
    controller._restore_character(controller._character_feedback_generation)
    assert controller.character.states[-1] is CharacterState.NORMAL


def test_changing_redirect_target_resets_old_cooldown() -> None:
    calls: list[str] = []
    controller = ApplicationController.__new__(ApplicationController)
    controller._last_redirect_url = "https://b23.tv/OldVideo"
    controller.policy = SimpleNamespace(
        reset_intervention_cooldown=lambda: calls.append("reset")
    )
    controller._refresh_redirect_target("https://b23.tv/NewVideo")
    assert calls == ["reset"]
    assert controller._last_redirect_url == "https://b23.tv/NewVideo"

    controller._refresh_redirect_target("https://b23.tv/NewVideo")
    assert calls == ["reset"]


def test_bubble_priority_cooldown_and_late_polish(qt_app, tmp_path) -> None:
    gate = Event()
    provider = _BubbleProvider(gate)
    service = _bubble_service(tmp_path, provider)
    messages = QSignalSpy(service.message_ready)

    assert service.emit_event(_event(BubbleEventType.INTERESTED, 60, 10)) is True
    assert messages.at(0)[0].text == "这个我也喜欢～"
    assert service.emit_event(_event(BubbleEventType.INTERESTED, 70, 10)) is False
    assert service.emit_event(_event(BubbleEventType.SUPERVISION_HIT)) is True
    assert messages.at(1)[0].text == "不许看这个啦！"
    assert service.emit_event(_event(BubbleEventType.OFFLINE)) is False
    gate.set()
    _wait_until(qt_app, lambda: messages.count() >= 3)
    assert messages.at(2)[0].text == "这次真的不可以哦！"
    assert all(
        set(call) == {
            "kind",
            "character_state",
            "happiness_value",
            "happiness_delta",
        }
        for call in provider.calls
    )
    service.shutdown()


def test_bubble_follows_character_and_flips_at_screen_edge(qt_app) -> None:
    character = QWidget()
    character.setFixedSize(176, 216)
    screen = QApplication.primaryScreen()
    assert screen is not None
    area = screen.availableGeometry()
    character.move(area.right() - character.width() + 1, area.top() + 80)
    character.show()
    message = SpeechBubbleMessage(
        text="太棒啦，今天满分！",
        kind=BubbleEventType.CELEBRATION,
        priority=80,
    )
    bubble = CharacterSpeechBubble(character)
    bubble.show_message(message)
    qt_app.processEvents()
    assert bubble._bubble_on_right is False  # noqa: SLF001
    assert area.contains(bubble.geometry())

    character.move(area.left(), area.top() + 80)
    bubble._follow_character()  # noqa: SLF001
    assert bubble._bubble_on_right is True  # noqa: SLF001
    bubble.close()
    character.close()


def test_privacy_mask_removes_character_and_bubble_pixels() -> None:
    privacy_mask_registry.update("test-mask", 10, 10, 20, 20)
    image = Image.new("RGB", (64, 64), (255, 255, 255))
    masked = privacy_mask_registry.apply(image, 0, 0)
    assert masked.getpixel((15, 15)) == (23, 19, 27)
    assert masked.getpixel((40, 40)) == (255, 255, 255)
    privacy_mask_registry.remove("test-mask")
