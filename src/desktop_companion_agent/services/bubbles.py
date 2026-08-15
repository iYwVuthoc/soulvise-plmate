"""角色应景气泡的本地模板、优先级与Agent限时润色。"""

from __future__ import annotations

import re
from concurrent.futures import Future, ThreadPoolExecutor
from time import monotonic

from PySide6.QtCore import QObject, Signal, Slot

from desktop_companion_agent.config import ConfigManager
from desktop_companion_agent.models import (
    AgentCapability,
    BubbleEvent,
    BubbleEventType,
    SpeechBubbleMessage,
)
from desktop_companion_agent.services.agent_providers import AgentProvider, CapabilityRouter

_MARKUP_OR_LINK = re.compile(r"(?:https?://|www\.|[`#*<>\[\]]|\r|\n)", re.IGNORECASE)


class SpeechBubbleService(QObject):
    """先即时显示本地短句，只接受3秒内返回的同代Agent润色。"""

    message_ready = Signal(object)
    _polish_completed = Signal(object, int, float, object)

    _PRIORITIES = {
        BubbleEventType.SUPERVISION_HIT: 100,
        BubbleEventType.HAPPINESS_ZERO: 90,
        BubbleEventType.CELEBRATION: 80,
        BubbleEventType.INTERESTED: 50,
        BubbleEventType.NOT_INTERESTED: 50,
        BubbleEventType.OFFLINE: 20,
    }
    _TEMPLATES = {
        BubbleEventType.SUPERVISION_HIT: "不许看这个啦！",
        BubbleEventType.HAPPINESS_ZERO: "我要换个开心的！",
        BubbleEventType.CELEBRATION: "太棒啦，今天满分！",
        BubbleEventType.INTERESTED: "这个我也喜欢～",
        BubbleEventType.NOT_INTERESTED: "唔……换一个看看？",
        BubbleEventType.OFFLINE: "网络走丢了，我先陪着你。",
    }
    _ORDINARY_EVENTS = {
        BubbleEventType.INTERESTED,
        BubbleEventType.NOT_INTERESTED,
        BubbleEventType.OFFLINE,
    }

    def __init__(
        self,
        config_manager: ConfigManager,
        router: CapabilityRouter,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.config_manager = config_manager
        self.router = router
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bubble")
        self._generation = 0
        self._current_priority = -1
        self._visible_until = 0.0
        self._last_ordinary: dict[BubbleEventType, float] = {}
        self._active_provider: AgentProvider | None = None
        self._polish_completed.connect(self._handle_polish)

    def emit_event(self, event: BubbleEvent) -> bool:
        """按冷却和优先级显示事件；返回False表示本次被有意抑制。"""

        now = monotonic()
        priority = self._PRIORITIES[event.kind]
        if now < self._visible_until and priority < self._current_priority:
            return False
        if event.kind in self._ORDINARY_EVENTS:
            previous = self._last_ordinary.get(event.kind, float("-inf"))
            if now - previous < 10.0:
                return False
            self._last_ordinary[event.kind] = now

        self._generation += 1
        generation = self._generation
        self._current_priority = priority
        self._visible_until = now + 3.5
        local = SpeechBubbleMessage(
            text=self._TEMPLATES[event.kind],
            kind=event.kind,
            priority=priority,
        )
        self.message_ready.emit(local)

        provider = self.router.resolve(AgentCapability.BUBBLE_POLISH)
        if provider is None:
            return True
        self._active_provider = provider
        timeout = self.config_manager.config.agent_routing.bubble_polish_timeout_seconds
        deadline = now + timeout
        future = self._executor.submit(
            provider.generate_bubble,
            event.model_dump(mode="json"),
        )

        def done(value: Future[object]) -> None:
            try:
                result: object = value.result()
            except Exception as exc:
                result = exc
            self._polish_completed.emit(result, generation, deadline, local)

        future.add_done_callback(done)
        return True

    @staticmethod
    def _safe_text(value: object) -> str:
        message = SpeechBubbleMessage.model_validate(value)
        text = message.text.strip()
        if _MARKUP_OR_LINK.search(text):
            raise ValueError("Agent气泡包含链接、Markdown或多行文本")
        return text

    @Slot(object, int, float, object)
    def _handle_polish(
        self,
        value: object,
        generation: int,
        deadline: float,
        local: SpeechBubbleMessage,
    ) -> None:
        if generation != self._generation or monotonic() > deadline:
            return
        self._active_provider = None
        if isinstance(value, Exception):
            return
        try:
            text = self._safe_text(value)
        except (TypeError, ValueError):
            return
        self.message_ready.emit(local.model_copy(update={"text": text}))

    def cancel(self) -> None:
        """让迟到润色失效；本地模板已经显示，不影响观察主流程。"""

        self._generation += 1
        provider = self._active_provider
        self._active_provider = None
        if provider is not None:
            provider.cancel_runtime()

    def shutdown(self) -> None:
        self.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)
