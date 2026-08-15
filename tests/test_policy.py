"""监督优先与高兴值状态机测试。"""

from __future__ import annotations

from desktop_companion_agent.config import HappinessSettings
from desktop_companion_agent.models import AnalysisResult, InterestJudgment, RuleMode
from desktop_companion_agent.services.policy import PolicyEngine


class FakeClock:
    def __init__(self):
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


def test_supervision_wins_conflict_without_happiness_change() -> None:
    clock = FakeClock()
    policy = PolicyEngine(HappinessSettings(), clock)
    result = AnalysisResult(
        flagged=True,
        interest=InterestJudgment.INTERESTED,
        matched_interest_rule_ids=["interest"],
    )
    decision = policy.evaluate(result, "same", True, True, 30)
    assert decision.mode is RuleMode.SUPERVISION
    assert decision.conflict is True
    assert decision.happiness_delta == 0
    assert decision.happiness_value == 50
    assert decision.should_intervene is True


def test_companion_scores_once_and_resets_at_threshold() -> None:
    clock = FakeClock()
    settings = HappinessSettings(initial_value=10, disinterest_decrement=10, reset_value=50)
    policy = PolicyEngine(settings, clock)
    result = AnalysisResult(interest=InterestJudgment.NOT_INTERESTED)
    first = policy.evaluate(result, "content-a", False, True, 30)
    assert first.happiness_delta == -10
    assert first.happiness_value == 50
    assert first.should_intervene is True

    duplicate = policy.evaluate(result, "content-a", False, True, 30)
    assert duplicate.happiness_delta == 0
    assert duplicate.happiness_value == 50


def test_supervision_cooldown_does_not_disable_later_recheck() -> None:
    clock = FakeClock()
    policy = PolicyEngine(HappinessSettings(intervention_cooldown_seconds=60), clock)
    result = AnalysisResult(flagged=True)
    assert policy.evaluate(result, "x", True, False, 30).should_intervene is True
    policy.record_intervention_success()
    clock.value += 30
    assert policy.evaluate(result, "x", True, False, 30).should_intervene is False
    clock.value += 31
    assert policy.evaluate(result, "x", True, False, 30).should_intervene is True


def test_failed_intervention_can_retry_immediately() -> None:
    clock = FakeClock()
    policy = PolicyEngine(HappinessSettings(intervention_cooldown_seconds=60), clock)
    result = AnalysisResult(flagged=True)
    assert policy.evaluate(result, "x", True, False, 30).should_intervene is True
    assert policy.evaluate(result, "x", True, False, 30).should_intervene is True


def test_record_only_category_suppresses_companion_scoring() -> None:
    policy = PolicyEngine(HappinessSettings())
    result = AnalysisResult(
        categories=["self-harm"],
        interest=InterestJudgment.INTERESTED,
        matched_interest_rule_ids=["interest"],
    )
    decision = policy.evaluate(result, "record-only", True, True, 30)
    assert decision.mode is RuleMode.SUPERVISION
    assert decision.happiness_delta == 0
    assert decision.happiness_value == 50
    assert decision.should_intervene is False
    assert decision.conflict is True


def test_companion_semantic_conflict_is_recorded_without_scoring() -> None:
    policy = PolicyEngine(HappinessSettings())
    decision = policy.evaluate(
        AnalysisResult(
            interest=InterestJudgment.NEUTRAL,
            interest_conflict=True,
            matched_interest_rule_ids=["liked", "disliked"],
        ),
        "semantic-conflict",
        False,
        True,
        30,
    )
    assert decision.mode is RuleMode.COMPANION
    assert decision.conflict is True
    assert decision.happiness_delta == 0
    assert decision.happiness_value == 50
