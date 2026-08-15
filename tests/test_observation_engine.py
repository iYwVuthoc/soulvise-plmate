"""共享观察管线的离屏集成测试。"""

from __future__ import annotations

from threading import Event
from time import monotonic

from PySide6.QtCore import QEventLoop, QTimer

from desktop_companion_agent.config import AppConfig, ConfigManager
from desktop_companion_agent.models import (
    AnalysisResult,
    CognitionRule,
    InterestJudgment,
    RuleMode,
)
from desktop_companion_agent.platforms.window_backend import NullWindowBackend
from desktop_companion_agent.services.analyzer import LocalRuleAnalyzer
from desktop_companion_agent.services.capture import SyntheticCaptureBackend
from desktop_companion_agent.services.context_exporter import ContextExporter
from desktop_companion_agent.services.intervention import InterventionService
from desktop_companion_agent.services.observation import ObservationEngine
from desktop_companion_agent.services.policy import PolicyEngine
from desktop_companion_agent.services.process_monitor import ProcessMonitor
from desktop_companion_agent.storage.repository import CognitionRepository


class BlockingAnalyzer:
    """模拟无法物理取消、稍后才返回的云端请求。"""

    def __init__(self, result: AnalysisResult | None = None):
        self.started = Event()
        self.release = Event()
        self.result = result or AnalysisResult(
            summary="迟到结果",
            interest=InterestJudgment.NOT_INTERESTED,
        )

    def analyze(self, jpeg_bytes, context, supervision_rules, companion_rules):
        del jpeg_bytes, context, supervision_rules, companion_rules
        self.started.set()
        self.release.wait(timeout=2)
        return self.result


class SwitchableProcessMonitor:
    def __init__(self):
        self.active = False

    def set_tracked_processes(self, names):
        del names

    def has_tracked_process(self) -> bool:
        return self.active


class FixedAnalyzer:
    """返回固定结构，避免验收时访问真实敏感内容或网络。"""

    def __init__(self, result: AnalysisResult):
        self.result = result

    def analyze(self, jpeg_bytes, context, supervision_rules, companion_rules):
        del jpeg_bytes, context, supervision_rules, companion_rules
        return self.result


class ContextRecordingAnalyzer(FixedAnalyzer):
    """记录观察引擎交付的时序上下文，不读取真实屏幕。"""

    def __init__(self, result: AnalysisResult):
        super().__init__(result)
        self.contexts = []

    def analyze(self, jpeg_bytes, context, supervision_rules, companion_rules):
        del supervision_rules, companion_rules
        assert jpeg_bytes.startswith(b"\xff\xd8")
        self.contexts.append(context)
        return self.result


class RecordingIntervention:
    """记录真正消费结果时使用的地址，避免测试打开真实浏览器。"""

    def __init__(self):
        self.urls: list[str] = []

    def open_redirect(self, url: str) -> tuple[bool, str]:
        self.urls.append(url)
        return True, "测试跳转成功"


def test_shared_pipeline_scores_and_never_writes_raw_screenshot(tmp_path, qt_app) -> None:
    del qt_app
    manager = ConfigManager(tmp_path / "config.json")
    config = AppConfig(
        observation_enabled=True,
        companion_enabled=True,
        capture={
            "normal_interval_seconds": 10,
            "active_interval_seconds": 5,
            "stability_seconds": 5,
            "fingerprint_distance": 1,
        },
    )
    manager.save(config)
    repository = CognitionRepository(tmp_path / "data.sqlite3")
    repository.add_rule(CognitionRule(mode=RuleMode.COMPANION, title="合成兴趣", keywords=["合成"]))
    engine = ObservationEngine(
        manager,
        repository,
        NullWindowBackend(),
        SyntheticCaptureBackend(),
        LocalRuleAnalyzer(),
        PolicyEngine(config.happiness),
        ProcessMonitor([]),
        InterventionService(),
        ContextExporter(repository, tmp_path / "contexts"),
    )
    outcomes = []
    loop = QEventLoop()
    engine.outcome_ready.connect(lambda outcome: (outcomes.append(outcome), loop.quit()))
    engine.start()
    engine._capture_tick()
    engine._stable_since = monotonic() - 6
    engine._capture_tick()
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    engine.shutdown()

    assert outcomes
    assert sum(outcome.decision.happiness_delta for outcome in outcomes) == 10
    assert len(repository.list_events(RuleMode.COMPANION)) == 1
    assert not list(tmp_path.rglob("*.jpg"))
    assert not list(tmp_path.rglob("*.png"))
    repository.close()


def test_first_video_analysis_combines_stability_frames_in_memory(tmp_path, qt_app) -> None:
    del qt_app
    manager = ConfigManager(tmp_path / "config.json")
    config = AppConfig(
        observation_enabled=True,
        supervision_enabled=True,
        capture={"stability_seconds": 5, "fingerprint_distance": 1},
    )
    manager.save(config)
    repository = CognitionRepository(tmp_path / "data.sqlite3")
    analyzer = ContextRecordingAnalyzer(AnalysisResult(summary="安全合成画面"))
    engine = ObservationEngine(
        manager,
        repository,
        NullWindowBackend(),
        SyntheticCaptureBackend(),
        analyzer,
        PolicyEngine(config.happiness),
        ProcessMonitor([]),
        InterventionService(),
        ContextExporter(repository, tmp_path / "contexts"),
    )
    loop = QEventLoop()
    engine.outcome_ready.connect(lambda _outcome: loop.quit())
    engine.start()
    engine._capture_tick()
    engine._stable_since = monotonic() - 6
    engine._capture_tick()
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    assert analyzer.contexts
    assert analyzer.contexts[0].temporal_frame_count == 2
    assert analyzer.contexts[0].temporal_layout == "older_top_latest_bottom"
    retained = engine._previous_sample  # noqa: SLF001
    assert retained is not None
    engine._previous_sample_deadline = monotonic() - 1  # noqa: SLF001
    engine._expire_previous_sample(monotonic())  # noqa: SLF001
    assert retained.jpeg_bytes == b""
    assert engine._previous_sample is None  # noqa: SLF001
    engine.shutdown()
    assert engine._previous_sample is None  # noqa: SLF001
    assert not list(tmp_path.rglob("*.jpg"))
    repository.close()


def test_stop_invalidates_inflight_result(tmp_path, qt_app) -> None:
    del qt_app
    manager = ConfigManager(tmp_path / "config.json")
    config = AppConfig(observation_enabled=True, companion_enabled=True)
    manager.save(config)
    repository = CognitionRepository(tmp_path / "data.sqlite3")
    analyzer = BlockingAnalyzer()
    engine = ObservationEngine(
        manager,
        repository,
        NullWindowBackend(),
        SyntheticCaptureBackend(),
        analyzer,
        PolicyEngine(config.happiness),
        ProcessMonitor([]),
        InterventionService(),
        ContextExporter(repository, tmp_path / "contexts"),
    )
    outcomes = []
    engine.outcome_ready.connect(outcomes.append)
    engine.start()
    engine._capture_tick()
    engine._stable_since = monotonic() - 20
    engine._capture_tick()
    assert analyzer.started.wait(timeout=1)
    engine.stop("戴上眼罩")
    analyzer.release.set()

    loop = QEventLoop()
    QTimer.singleShot(300, loop.quit)
    loop.exec()
    engine.shutdown()
    assert outcomes == []
    assert repository.list_events() == []
    repository.close()


def test_interval_switches_between_normal_and_active(tmp_path, qt_app) -> None:
    del qt_app
    manager = ConfigManager(tmp_path / "config.json")
    config = AppConfig(observation_enabled=True, companion_enabled=True)
    manager.save(config)
    repository = CognitionRepository(tmp_path / "data.sqlite3")
    monitor = SwitchableProcessMonitor()
    engine = ObservationEngine(
        manager,
        repository,
        NullWindowBackend(),
        SyntheticCaptureBackend(),
        LocalRuleAnalyzer(),
        PolicyEngine(config.happiness),
        monitor,
        InterventionService(),
        ContextExporter(repository, tmp_path / "contexts"),
    )
    intervals = []
    engine.interval_changed.connect(lambda seconds, active: intervals.append((seconds, active)))
    engine.start()
    monitor.active = True
    engine._update_interval()
    engine.shutdown()
    assert intervals == [(10, False), (5, True)]
    repository.close()


def test_record_only_category_is_saved_as_supervision_without_scoring(
    tmp_path, qt_app
) -> None:
    del qt_app
    manager = ConfigManager(tmp_path / "config.json")
    config = AppConfig(
        observation_enabled=True,
        supervision_enabled=True,
        companion_enabled=True,
        capture={"stability_seconds": 5},
    )
    manager.save(config)
    repository = CognitionRepository(tmp_path / "data.sqlite3")
    analyzer = FixedAnalyzer(
        AnalysisResult(
            summary="仅记录类别",
            categories=["self-harm"],
            interest=InterestJudgment.INTERESTED,
            matched_interest_rule_ids=["interest"],
        )
    )
    engine = ObservationEngine(
        manager,
        repository,
        NullWindowBackend(),
        SyntheticCaptureBackend(),
        analyzer,
        PolicyEngine(config.happiness),
        ProcessMonitor([]),
        InterventionService(),
        ContextExporter(repository, tmp_path / "contexts"),
    )
    outcomes = []
    loop = QEventLoop()
    engine.outcome_ready.connect(lambda outcome: (outcomes.append(outcome), loop.quit()))
    engine.start()
    engine._capture_tick()
    engine._stable_since = monotonic() - 6
    engine._capture_tick()
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    engine.shutdown()
    events = repository.list_events()
    assert outcomes[0].decision.happiness_delta == 0
    assert outcomes[0].decision.should_intervene is False
    assert len(events) == 1
    assert events[0].mode is RuleMode.SUPERVISION
    repository.close()


def test_record_only_agent_risk_status_displays_confidence(tmp_path, qt_app) -> None:
    del qt_app
    manager = ConfigManager(tmp_path / "config.json")
    config = AppConfig(
        observation_enabled=True,
        supervision_enabled=True,
        capture={"stability_seconds": 5},
        intervention={"redirect_url": ""},
    )
    manager.save(config)
    repository = CognitionRepository(tmp_path / "data.sqlite3")
    engine = ObservationEngine(
        manager,
        repository,
        NullWindowBackend(),
        SyntheticCaptureBackend(),
        FixedAnalyzer(
            AnalysisResult(
                summary="边界风险",
                categories=["agent/vulgar"],
                confidence=0.81,
            )
        ),
        PolicyEngine(config.happiness),
        ProcessMonitor([]),
        InterventionService(),
        ContextExporter(repository, tmp_path / "contexts"),
    )
    statuses: list[str] = []
    loop = QEventLoop()
    engine.status_changed.connect(statuses.append)
    engine.outcome_ready.connect(lambda _outcome: loop.quit())
    engine.start()
    engine._capture_tick()
    engine._stable_since = monotonic() - 6
    engine._capture_tick()
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    engine.shutdown()
    assert any("agent/vulgar" in item and "81%" in item for item in statuses)
    repository.close()


def test_empty_redirect_never_opens_browser_but_keeps_angry_feedback(
    tmp_path, qt_app
) -> None:
    del qt_app
    manager = ConfigManager(tmp_path / "config.json")
    config = AppConfig(
        observation_enabled=True,
        supervision_enabled=True,
        capture={"stability_seconds": 5},
        intervention={"redirect_url": ""},
    )
    manager.save(config)
    repository = CognitionRepository(tmp_path / "data.sqlite3")
    engine = ObservationEngine(
        manager,
        repository,
        NullWindowBackend(),
        SyntheticCaptureBackend(),
        FixedAnalyzer(AnalysisResult(summary="命中", flagged=True)),
        PolicyEngine(config.happiness),
        ProcessMonitor([]),
        InterventionService(),
        ContextExporter(repository, tmp_path / "contexts"),
    )
    outcomes = []
    loop = QEventLoop()
    engine.outcome_ready.connect(lambda outcome: (outcomes.append(outcome), loop.quit()))
    engine.start()
    engine._capture_tick()
    engine._stable_since = monotonic() - 6
    engine._capture_tick()
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    engine.shutdown()
    assert outcomes[0].decision.should_intervene is True
    assert outcomes[0].decision.character_state.value == "angry"
    assert outcomes[0].intervention_message == "尚未配置拦截视频地址"
    assert repository.list_events(RuleMode.SUPERVISION)[0].intervened is False
    repository.close()


def test_inflight_analysis_uses_latest_redirect_url(tmp_path, qt_app) -> None:
    del qt_app
    manager = ConfigManager(tmp_path / "config.json")
    config = AppConfig(
        observation_enabled=True,
        supervision_enabled=True,
        capture={"stability_seconds": 5},
        intervention={"redirect_url": "https://b23.tv/OldVideo"},
    )
    manager.save(config)
    repository = CognitionRepository(tmp_path / "data.sqlite3")
    analyzer = BlockingAnalyzer()
    intervention = RecordingIntervention()
    engine = ObservationEngine(
        manager,
        repository,
        NullWindowBackend(),
        SyntheticCaptureBackend(),
        analyzer,
        PolicyEngine(config.happiness),
        ProcessMonitor([]),
        intervention,
        ContextExporter(repository, tmp_path / "contexts"),
    )
    outcomes = []
    engine.outcome_ready.connect(outcomes.append)
    engine.start()
    engine._capture_tick()
    engine._stable_since = monotonic() - 6
    engine._capture_tick()
    assert analyzer.started.wait(timeout=1)

    latest = manager.config.intervention.model_copy(
        update={"redirect_url": "https://b23.tv/NewVideo"}
    )
    manager.save(manager.config.model_copy(update={"intervention": latest}))
    analyzer.result = AnalysisResult(summary="命中", flagged=True)
    analyzer.release.set()

    loop = QEventLoop()
    QTimer.singleShot(1000, loop.quit)
    engine.outcome_ready.connect(lambda _outcome: loop.quit())
    loop.exec()
    engine.shutdown()
    assert outcomes
    assert intervention.urls == ["https://b23.tv/NewVideo"]
    repository.close()
