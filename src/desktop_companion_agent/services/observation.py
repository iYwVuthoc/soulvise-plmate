"""共享截图、分析、策略执行与文字认知保存的观察引擎。"""

from __future__ import annotations

import hashlib
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from desktop_companion_agent.config import AppConfig, ConfigManager
from desktop_companion_agent.models import (
    AnalysisResult,
    ObservationEvent,
    ObservationEventKind,
    PendingCognitionChange,
    PolicyDecision,
    RuleMode,
)
from desktop_companion_agent.platforms.window_backend import WindowBackend
from desktop_companion_agent.services.analyzer import AnalysisContext, ContentAnalyzer
from desktop_companion_agent.services.capture import (
    CaptureBackend,
    CapturedFrame,
    compose_temporal_frames,
)
from desktop_companion_agent.services.context_exporter import ContextExporter
from desktop_companion_agent.services.fingerprint import meaningfully_changed
from desktop_companion_agent.services.intervention import InterventionService
from desktop_companion_agent.services.policy import PolicyEngine
from desktop_companion_agent.services.process_monitor import ProcessMonitor
from desktop_companion_agent.storage.repository import CognitionRepository

TEMPORAL_REFERENCE_MAX_SECONDS = 15.0


@dataclass(slots=True)
class ObservationOutcome:
    """供界面展示的一次观察结果。"""

    result: AnalysisResult
    decision: PolicyDecision
    app_name: str
    content_id: str
    intervention_message: str = ""


@dataclass(slots=True)
class _AnalysisPacket:
    result: AnalysisResult
    app_name: str
    window_title: str
    content_id: str
    generation: int
    analysis_seconds: float


class ObservationEngine(QObject):
    """Qt 定时器驱动的单通道观察引擎。

    云端请求最多并发一个；请求进行时新画面只覆盖旧待分析画面，不形成队列。
    ``stop`` 会递增代次，因此已无法物理取消的网络请求也不能产生迟到拦截。
    """

    status_changed = Signal(str)
    interval_changed = Signal(int, bool)
    frame_captured = Signal()
    analysis_busy_changed = Signal(bool)
    outcome_ready = Signal(object)
    error_occurred = Signal(str)
    _worker_completed = Signal(object)

    def __init__(
        self,
        config_manager: ConfigManager,
        repository: CognitionRepository,
        window_backend: WindowBackend,
        capture_backend: CaptureBackend,
        analyzer: ContentAnalyzer,
        policy: PolicyEngine,
        process_monitor: ProcessMonitor,
        intervention: InterventionService,
        context_exporter: ContextExporter,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.config_manager = config_manager
        self.repository = repository
        self.window_backend = window_backend
        self.capture_backend = capture_backend
        self.analyzer = analyzer
        self.policy = policy
        self.process_monitor = process_monitor
        self.intervention = intervention
        self.context_exporter = context_exporter
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._capture_tick)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="observer")
        self._worker_completed.connect(self._handle_worker_completed)
        self._running = False
        self._generation = 0
        self._busy = False
        self._pending_frame: CapturedFrame | None = None
        self._previous_sample: CapturedFrame | None = None
        self._previous_sample_deadline = 0.0
        self._stable_scope = ""
        self._stable_since = 0.0
        self._last_analyzed_fingerprint: int | None = None
        self._current_interval = 0

    @property
    def running(self) -> bool:
        return self._running

    @staticmethod
    def _should_run(config: AppConfig) -> bool:
        return (
            config.observation_enabled
            and (config.supervision_enabled or config.companion_enabled)
            and not config.blindfolded
        )

    def apply_config(self) -> None:
        """依据最新配置启动、停止或更新观察参数。"""

        config = self.config_manager.config
        self.process_monitor.set_tracked_processes(config.capture.tracked_processes)
        self.policy.update_settings(config.happiness)
        if self._should_run(config):
            self.start()
        else:
            self.stop("观察已暂停")

    def start(self) -> None:
        """启用观察，并立即执行首次本地截图。"""

        if not self._should_run(self.config_manager.config):
            self.stop("观察开关未同时满足")
            return
        if not self._running:
            self._running = True
            self._generation += 1
            self.status_changed.emit("观察运行中")
            QTimer.singleShot(0, self._capture_tick)
        self._update_interval()

    def _clear_previous_sample(self) -> None:
        """释放时序参考帧，并同时清除其内存期限。"""

        if self._previous_sample is not None:
            self._previous_sample.clear()
            self._previous_sample = None
        self._previous_sample_deadline = 0.0

    def _expire_previous_sample(self, now: float) -> None:
        """参考帧超过15秒后主动释放，静止画面也不会长期留在内存。"""

        if self._previous_sample is not None and now >= self._previous_sample_deadline:
            self._clear_previous_sample()

    def stop(self, reason: str = "观察已暂停") -> None:
        """立即停止截图并让全部在途分析结果失效。"""

        was_active = self._running or self._busy or self._pending_frame is not None
        self._running = False
        self._generation += 1
        self._timer.stop()
        # 戴眼罩、关闭开关或退出时不仅忽略迟到结果，还要中断Agent并删除临时图片。
        cancel_analysis = getattr(self.analyzer, "cancel", None)
        if callable(cancel_analysis):
            cancel_analysis()
        if self._pending_frame is not None:
            self._pending_frame.clear()
            self._pending_frame = None
        self._clear_previous_sample()
        self._stable_scope = ""
        self._stable_since = 0.0
        self._last_analyzed_fingerprint = None
        if was_active:
            self.status_changed.emit(reason)

    def _update_interval(self) -> None:
        config = self.config_manager.config.capture
        active = self.process_monitor.has_tracked_process()
        seconds = config.active_interval_seconds if active else config.normal_interval_seconds
        if seconds != self._current_interval:
            self._current_interval = seconds
            self._timer.setInterval(seconds * 1000)
            self.interval_changed.emit(seconds, active)
        if self._running and not self._timer.isActive():
            self._timer.start()

    @staticmethod
    def _scope_for(frame: CapturedFrame) -> str:
        normalized_title = " ".join(frame.window_title.casefold().split())[:300]
        return f"{frame.app_name.casefold()}|{normalized_title}"

    @staticmethod
    def _content_id(frame: CapturedFrame) -> str:
        """优先用应用和标题标识内容，避免视频逐帧变化导致重复计分。"""

        scope = ObservationEngine._scope_for(frame)
        if not frame.window_title.strip():
            scope = f"{scope}|{frame.fingerprint:016x}"
        return hashlib.sha256(scope.encode("utf-8", errors="replace")).hexdigest()

    @Slot()
    def _capture_tick(self) -> None:
        if not self._running or not self._should_run(self.config_manager.config):
            self.stop("观察已暂停")
            return
        self._update_interval()
        try:
            window = self.window_backend.foreground_window()
            frame = self.capture_backend.capture_active_screen(window)
            self.frame_captured.emit()
        except Exception as exc:
            self.error_occurred.emit(f"截图失败：{str(exc)[:200]}")
            return

        now = monotonic()
        self._expire_previous_sample(now)
        scope = self._scope_for(frame)
        if scope != self._stable_scope:
            if self._previous_sample is not None:
                self._previous_sample.clear()
            self._stable_scope = scope
            self._stable_since = now
            self._last_analyzed_fingerprint = None
            self._previous_sample = frame.memory_copy()
            self._previous_sample_deadline = now + TEMPORAL_REFERENCE_MAX_SECONDS
            required = self.config_manager.config.capture.stability_seconds
            self.status_changed.emit(f"等待画面稳定（约{required}秒）")

        stable_for = now - self._stable_since
        required = self.config_manager.config.capture.stability_seconds
        changed = meaningfully_changed(
            self._last_analyzed_fingerprint,
            frame.fingerprint,
            self.config_manager.config.capture.fingerprint_distance,
        )
        if stable_for < required or not changed:
            frame.clear()
            return

        # 视频的异常感通常来自连续画面的扭曲、突变或重复。稳定期开始帧与
        # 当前帧差异明显时合成一张内存时序图；原始两帧随后立即释放。
        previous = self._previous_sample
        latest_sample = frame.memory_copy()
        if previous is not None and meaningfully_changed(
            previous.fingerprint,
            frame.fingerprint,
            self.config_manager.config.capture.fingerprint_distance,
        ):
            try:
                temporal = compose_temporal_frames(previous, frame)
            except Exception as exc:
                # 合成失败不应阻断监督，退回当前单帧并只给出脱敏诊断。
                self.error_occurred.emit(f"时序画面合成失败，已改用单帧：{str(exc)[:120]}")
            else:
                frame.clear()
                frame = temporal
        if previous is not None:
            previous.clear()
        self._previous_sample = latest_sample
        self._previous_sample_deadline = now + TEMPORAL_REFERENCE_MAX_SECONDS

        if self._busy:
            if self._pending_frame is not None:
                self._pending_frame.clear()
            self._pending_frame = frame
            return
        self._submit(frame)

    def _submit(self, frame: CapturedFrame) -> None:
        self._busy = True
        self.analysis_busy_changed.emit(True)
        self.status_changed.emit("正在分析最新画面…")
        self._last_analyzed_fingerprint = frame.fingerprint
        generation = self._generation
        config = self.config_manager.config
        context = AnalysisContext(
            app_name=frame.app_name,
            window_title=frame.window_title,
            supervision_enabled=config.supervision_enabled,
            companion_enabled=config.companion_enabled,
            temporal_frame_count=frame.temporal_frame_count,
            temporal_layout=frame.temporal_layout,
        )
        supervision_rules = self.repository.list_rules(RuleMode.SUPERVISION, True)
        companion_rules = self.repository.list_rules(RuleMode.COMPANION, True)
        content_id = self._content_id(frame)

        def work() -> _AnalysisPacket:
            started_at = monotonic()
            try:
                result = self.analyzer.analyze(
                    frame.jpeg_bytes, context, supervision_rules, companion_rules
                )
                return _AnalysisPacket(
                    result=result,
                    app_name=frame.app_name,
                    window_title=frame.window_title,
                    content_id=content_id,
                    generation=generation,
                    analysis_seconds=max(0.0, monotonic() - started_at),
                )
            finally:
                frame.clear()

        future = self._executor.submit(work)
        future.add_done_callback(self._future_done)

    def _future_done(self, future: Future[_AnalysisPacket]) -> None:
        try:
            packet: _AnalysisPacket | Exception = future.result()
        except Exception as exc:
            packet = exc
        self._worker_completed.emit(packet)

    @Slot(object)
    def _handle_worker_completed(self, packet: _AnalysisPacket | Exception) -> None:
        self._busy = False
        self.analysis_busy_changed.emit(False)
        if isinstance(packet, Exception):
            self.error_occurred.emit(f"识别失败：{str(packet)[:200]}")
        elif self._running and packet.generation == self._generation:
            self._consume(packet)

        pending = self._pending_frame
        self._pending_frame = None
        if pending is not None:
            if self._running:
                self._submit(pending)
            else:
                pending.clear()

    def _consume(self, packet: _AnalysisPacket) -> None:
        config = self.config_manager.config
        decision = self.policy.evaluate(
            packet.result,
            packet.content_id,
            config.supervision_enabled,
            config.companion_enabled,
            config.capture.content_dedup_minutes,
        )
        intervention_message = ""
        actually_intervened = False
        if decision.should_intervene:
            actually_intervened, intervention_message = self.intervention.open_redirect(
                config.intervention.redirect_url
            )
            if actually_intervened:
                self.policy.record_intervention_success()

        meaningful = (
            bool(packet.result.matched_supervision_rule_ids)
            or packet.result.interest_conflict
            or packet.result.flagged
            or bool(packet.result.categories)
            or decision.happiness_delta != 0
            or bool(packet.result.suggested_memory)
        )
        if meaningful:
            self._save_event(packet, decision, actually_intervened)
            if packet.result.suggested_memory.strip() and decision.mode is not None:
                self.repository.add_pending_change(
                    PendingCognitionChange(
                        mode=decision.mode,
                        suggestion=packet.result.suggested_memory.strip(),
                    )
                )
            try:
                self.context_exporter.export_all()
            except OSError as exc:
                self.error_occurred.emit(f"认知上下文导出失败：{str(exc)[:160]}")

        if not packet.result.analysis_available:
            self.status_changed.emit(
                "云端识别不可用，本地规则仍有效："
                f"{packet.result.error_message}（用时{packet.analysis_seconds:.1f}秒）"
            )
        elif packet.result.categories and not packet.result.flagged:
            categories = "、".join(packet.result.categories[:4])
            confidence = f"，最高置信度{packet.result.confidence:.0%}"
            self.status_changed.emit(
                f"检测到仅记录的审核类别：{categories}{confidence}；未执行拦截"
                f"（用时{packet.analysis_seconds:.1f}秒）"
            )
        elif packet.result.flagged:
            self.status_changed.emit(
                f"命中监督风险，最高置信度{packet.result.confidence:.0%}"
                f"（用时{packet.analysis_seconds:.1f}秒）"
            )
        else:
            self.status_changed.emit(f"分析完成，用时{packet.analysis_seconds:.1f}秒")
        self.outcome_ready.emit(
            ObservationOutcome(
                result=packet.result,
                decision=decision,
                app_name=packet.app_name,
                content_id=packet.content_id,
                intervention_message=intervention_message,
            )
        )

    def _save_event(
        self,
        packet: _AnalysisPacket,
        decision: PolicyDecision,
        intervened: bool,
    ) -> None:
        mode = decision.mode
        if mode is None:
            mode = (
                RuleMode.SUPERVISION
                if (
                    packet.result.flagged
                    or packet.result.matched_supervision_rule_ids
                    or packet.result.categories
                )
                else RuleMode.COMPANION
            )
        matched = (
            packet.result.matched_supervision_rule_ids
            if mode is RuleMode.SUPERVISION
            else packet.result.matched_interest_rule_ids
        )
        expires = datetime.now(UTC) + timedelta(
            days=self.config_manager.config.privacy.retain_event_days
        )
        self.repository.add_event(
            ObservationEvent(
                mode=mode,
                expires_at=expires.isoformat(),
                app_name=packet.app_name,
                window_title_hash=self.repository.hash_window_title(packet.window_title),
                content_id=packet.content_id,
                summary=(
                    f"记录这一值得庆祝的时刻：{packet.result.summary}"
                    if decision.celebration_triggered
                    else packet.result.summary
                ),
                categories=packet.result.categories,
                matched_rule_ids=matched,
                interest=packet.result.interest,
                happiness_delta=decision.happiness_delta,
                intervened=intervened,
                model=packet.result.model,
                confidence=packet.result.confidence,
                event_kind=(
                    ObservationEventKind.CELEBRATION
                    if decision.celebration_triggered
                    else ObservationEventKind.OBSERVATION
                ),
                pinned=decision.celebration_triggered,
                happiness_value=(
                    decision.peak_happiness_value
                    if decision.celebration_triggered
                    else decision.happiness_value
                ),
            )
        )

    def shutdown(self) -> None:
        """退出应用时停止计时器并释放线程池。"""

        self.stop("程序正在退出")
        self._executor.shutdown(wait=False, cancel_futures=True)
