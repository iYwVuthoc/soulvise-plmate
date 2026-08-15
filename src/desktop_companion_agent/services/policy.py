"""监督优先、陪看计分、去重与冷却策略。"""

from __future__ import annotations

from collections.abc import Callable
from time import monotonic

from desktop_companion_agent.config import HappinessSettings
from desktop_companion_agent.models import (
    AnalysisResult,
    CharacterState,
    InterestJudgment,
    PolicyDecision,
    RuleMode,
)


class PolicyEngine:
    """将一次共享分析结果转成唯一动作，防止两种模式相互争抢。"""

    def __init__(
        self,
        settings: HappinessSettings,
        clock: Callable[[], float] = monotonic,
    ):
        self.settings = settings
        self.happiness = settings.initial_value
        self._clock = clock
        self._last_intervention_at = float("-inf")
        self._scored_content: dict[str, float] = {}

    def update_settings(self, settings: HappinessSettings) -> None:
        """应用新设置，并将现有数值约束在有效范围内。"""

        self.settings = settings
        self.happiness = max(0, min(100, self.happiness))

    def _can_intervene(self, now: float) -> bool:
        return now - self._last_intervention_at >= self.settings.intervention_cooldown_seconds

    def record_intervention_success(self) -> None:
        """只有浏览器确认接收跳转请求后，才从当前时刻开始计算冷却。"""

        self._last_intervention_at = self._clock()

    def reset_intervention_cooldown(self) -> None:
        """用户主动更换拦截地址后，允许立即验证新目标。"""

        self._last_intervention_at = float("-inf")

    def _prune_scores(self, now: float, dedup_seconds: int) -> None:
        expired = [
            key for key, value in self._scored_content.items() if now - value >= dedup_seconds
        ]
        for key in expired:
            self._scored_content.pop(key, None)

    def evaluate(
        self,
        result: AnalysisResult,
        content_id: str,
        supervision_enabled: bool,
        companion_enabled: bool,
        dedup_minutes: int,
    ) -> PolicyDecision:
        """监督命中先返回；仅未命中监督时才允许改变高兴值。"""

        now = self._clock()
        previous_happiness = self.happiness
        supervision_hit = supervision_enabled and (
            result.flagged or bool(result.matched_supervision_rule_ids)
        )
        companion_hit = companion_enabled and result.interest is not InterestJudgment.NEUTRAL
        if supervision_hit:
            should_intervene = self._can_intervene(now)
            return PolicyDecision(
                mode=RuleMode.SUPERVISION,
                character_state=CharacterState.ANGRY,
                happiness_value=self.happiness,
                previous_happiness=previous_happiness,
                should_intervene=should_intervene,
                reason="命中监督规则" if should_intervene else "命中监督规则，处于拦截冷却期",
                conflict=companion_hit,
            )

        # 未进入强制拦截范围的官方类别仍属于监督事件。它们只记录和提示，
        # 不允许同时落入陪看计分，避免风险内容反过来改变角色高兴值。
        if supervision_enabled and result.categories:
            return PolicyDecision(
                mode=RuleMode.SUPERVISION,
                character_state=CharacterState.NORMAL,
                happiness_value=self.happiness,
                previous_happiness=previous_happiness,
                reason="命中仅记录的监督类别，未执行拦截",
                conflict=companion_hit,
            )

        if companion_enabled and result.interest_conflict:
            return PolicyDecision(
                mode=RuleMode.COMPANION,
                happiness_value=self.happiness,
                previous_happiness=previous_happiness,
                reason="兴趣规则语义冲突，保持中立",
                conflict=True,
            )

        if not companion_enabled or result.interest is InterestJudgment.NEUTRAL:
            return PolicyDecision(
                happiness_value=self.happiness,
                previous_happiness=previous_happiness,
                reason="无须处理",
            )

        dedup_seconds = max(60, dedup_minutes * 60)
        self._prune_scores(now, dedup_seconds)
        if content_id in self._scored_content:
            return PolicyDecision(
                mode=RuleMode.COMPANION,
                happiness_value=self.happiness,
                previous_happiness=previous_happiness,
                reason="同一内容仍在计分去重期",
            )
        self._scored_content[content_id] = now

        delta = (
            self.settings.interest_increment
            if result.interest is InterestJudgment.INTERESTED
            else -self.settings.disinterest_decrement
        )
        self.happiness = max(0, min(100, self.happiness + delta))
        celebration_triggered = previous_happiness < 100 and self.happiness >= 100
        peak_happiness_value = self.happiness if celebration_triggered else None
        should_intervene = False
        reason = "感兴趣内容" if delta > 0 else "不感兴趣内容"
        state = CharacterState.HAPPY if delta > 0 else CharacterState.NORMAL
        if self.happiness <= self.settings.trigger_threshold:
            state = CharacterState.ANGRY
            should_intervene = self._can_intervene(now)
            reason = (
                "高兴值达到阈值"
                if should_intervene
                else "高兴值达到阈值，但处于拦截冷却期"
            )
            self.happiness = self.settings.reset_value

        # 庆祝事件先记录达到的峰值，再把实时数值恢复到“启动默认值”。奖杯历史
        # 使用peak_happiness_value，因此不会被当前数值复位成50（或用户自定义值）。
        if celebration_triggered:
            self.happiness = self.settings.initial_value

        return PolicyDecision(
            mode=RuleMode.COMPANION,
            character_state=state,
            happiness_value=self.happiness,
            previous_happiness=previous_happiness,
            happiness_delta=delta,
            should_intervene=should_intervene,
            reason=reason,
            celebration_triggered=celebration_triggered,
            peak_happiness_value=peak_happiness_value,
        )

    def reset(self) -> None:
        """恢复默认高兴值并清空本次运行的内容去重状态。"""

        self.happiness = self.settings.initial_value
        self._scored_content.clear()
        self._last_intervention_at = float("-inf")
