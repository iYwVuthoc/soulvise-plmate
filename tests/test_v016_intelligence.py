"""v0.1.6通用风险知识、陪看语义与加速参数验收。"""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from desktop_companion_agent.config import (
    CompanionPolicySettings,
    ConfigManager,
    ModerationPolicySettings,
)
from desktop_companion_agent.models import (
    CognitionRule,
    InterestJudgment,
    RiskAssessment,
    RuleMode,
    SemanticRuleMatch,
)
from desktop_companion_agent.services.analyzer import (
    _agent_risk_result,
    _resolve_companion_interest,
)
from desktop_companion_agent.services.capture import (
    ANALYSIS_JPEG_QUALITY,
    MAXIMUM_ANALYSIS_EDGE,
    ActiveScreenCaptureBackend,
)


def _companion_rule(
    rule_id: str,
    judgment: InterestJudgment,
    priority: int = 50,
) -> CognitionRule:
    return CognitionRule(
        id=rule_id,
        mode=RuleMode.COMPANION,
        title=rule_id,
        keywords=[rule_id],
        interest_judgment=judgment,
        priority=priority,
    )


def test_codex_knowledge_blocks_high_confidence_without_supervision_rule() -> None:
    accepted, categories, hit, confidence = _agent_risk_result(
        [RiskAssessment(category="shock", confidence=0.93)],
        ModerationPolicySettings(),
    )
    assert accepted[0].category == "shock"
    assert categories == ["agent/shock"]
    assert hit is True
    assert confidence == 0.93


def test_low_confidence_and_reasonable_context_do_not_block() -> None:
    _accepted, categories, hit, _confidence = _agent_risk_result(
        [
            RiskAssessment(category="vulgar", confidence=0.89),
            RiskAssessment(
                category="violence/graphic",
                confidence=0.99,
                context_exempted=True,
            ),
        ],
        ModerationPolicySettings(),
    )
    assert categories == ["agent/vulgar"]
    assert hit is False


def test_codex_knowledge_can_be_disabled_without_losing_record() -> None:
    _accepted, categories, hit, _confidence = _agent_risk_result(
        [RiskAssessment(category="sexual", confidence=0.99)],
        ModerationPolicySettings(codex_knowledge_enabled=False),
    )
    assert categories == ["agent/sexual"]
    assert hit is False


def test_companion_semantics_support_same_topic_and_reject_weak_match() -> None:
    rule = _companion_rule("anime-music", InterestJudgment.INTERESTED)
    selected, judgment, confidence, conflict = _resolve_companion_interest(
        [],
        [SemanticRuleMatch(rule_id=rule.id, confidence=0.88)],
        [rule],
        CompanionPolicySettings(),
    )
    assert selected == [rule.id]
    assert judgment is InterestJudgment.INTERESTED
    assert confidence == 0.88
    assert conflict is False

    weak = _resolve_companion_interest(
        [],
        [SemanticRuleMatch(rule_id=rule.id, confidence=0.79)],
        [rule],
        CompanionPolicySettings(),
    )
    assert weak == ([], InterestJudgment.NEUTRAL, 0.0, False)


def test_companion_priority_then_confidence_and_near_tie_is_neutral() -> None:
    interested = _companion_rule("town", InterestJudgment.INTERESTED, priority=80)
    disliked = _companion_rule("noise", InterestJudgment.NOT_INTERESTED, priority=80)
    near_tie = _resolve_companion_interest(
        [],
        [
            SemanticRuleMatch(rule_id=interested.id, confidence=0.91),
            SemanticRuleMatch(rule_id=disliked.id, confidence=0.88),
        ],
        [interested, disliked],
        CompanionPolicySettings(),
    )
    assert near_tie[1] is InterestJudgment.NEUTRAL
    assert near_tie[3] is True

    clear_winner = _resolve_companion_interest(
        [],
        [
            SemanticRuleMatch(rule_id=interested.id, confidence=0.95),
            SemanticRuleMatch(rule_id=disliked.id, confidence=0.84),
        ],
        [interested, disliked],
        CompanionPolicySettings(),
    )
    assert clear_winner[:2] == ([interested.id], InterestJudgment.INTERESTED)


def test_local_exact_match_has_confidence_one_and_unknown_id_is_ignored() -> None:
    interested = _companion_rule("windmill", InterestJudgment.INTERESTED)
    result = _resolve_companion_interest(
        [interested.id],
        [SemanticRuleMatch(rule_id="invented", confidence=1.0)],
        [interested],
        CompanionPolicySettings(),
    )
    assert result == ([interested.id], InterestJudgment.INTERESTED, 1.0, False)


def test_no_companion_rules_can_never_create_interest() -> None:
    result = _resolve_companion_interest(
        [],
        [SemanticRuleMatch(rule_id="invented", confidence=1.0)],
        [],
        CompanionPolicySettings(),
    )
    assert result == ([], InterestJudgment.NEUTRAL, 0.0, False)


def test_schema_v3_migrates_old_default_stability_but_preserves_custom_value(
    tmp_path,
) -> None:
    default_path = tmp_path / "default.json"
    default_path.write_text(
        '{"schema_version":3,"capture":{"stability_seconds":15}}',
        encoding="utf-8",
    )
    assert ConfigManager(default_path).load().capture.stability_seconds == 5

    custom_path = tmp_path / "custom.json"
    custom_path.write_text(
        '{"schema_version":3,"capture":{"stability_seconds":20}}',
        encoding="utf-8",
    )
    assert ConfigManager(custom_path).load().capture.stability_seconds == 20


def test_large_analysis_image_is_downscaled_without_upscaling_small_image() -> None:
    large = Image.new("RGB", (3200, 1800), "navy")
    resized = ActiveScreenCaptureBackend._resize_for_analysis(large)
    assert resized.size == (MAXIMUM_ANALYSIS_EDGE, 900)

    small = Image.new("RGB", (640, 360), "navy")
    assert ActiveScreenCaptureBackend._resize_for_analysis(small) is small

    output = BytesIO()
    resized.save(output, format="JPEG", quality=ANALYSIS_JPEG_QUALITY)
    assert output.tell() > 0
