"""本地规则与云端失败降级测试。"""

from __future__ import annotations

from desktop_companion_agent.config import ModelSettings
from desktop_companion_agent.models import CognitionRule, InterestJudgment, RuleMode
from desktop_companion_agent.security import InMemorySecretStore
from desktop_companion_agent.services.analyzer import AnalysisContext, OpenAIContentAnalyzer


def test_local_explicit_rule_works_without_api_key() -> None:
    analyzer = OpenAIContentAnalyzer(ModelSettings(), InMemorySecretStore())
    supervision = CognitionRule(
        mode=RuleMode.SUPERVISION,
        title="本地规则",
        keywords=["测试危险内容"],
    )
    interest = CognitionRule(
        mode=RuleMode.COMPANION,
        title="兴趣规则",
        keywords=["天文"],
    )
    result = analyzer.analyze(
        b"not-uploaded-without-key",
        AnalysisContext("browser.exe", "天文 - 测试危险内容", True, True),
        [supervision],
        [interest],
    )
    assert result.flagged is True
    assert result.analysis_available is False
    assert result.matched_supervision_rule_ids == [supervision.id]
    assert result.interest is InterestJudgment.INTERESTED


def test_rule_exclusion_prevents_local_match() -> None:
    analyzer = OpenAIContentAnalyzer(ModelSettings(enabled=False), InMemorySecretStore())
    rule = CognitionRule(
        mode=RuleMode.SUPERVISION,
        title="规则",
        keywords=["示例"],
        exclusions=["教学"],
    )
    result = analyzer.analyze(
        b"x",
        AnalysisContext("browser.exe", "教学示例", True, False),
        [rule],
        [],
    )
    assert result.flagged is False


def test_local_companion_rule_can_be_not_interested() -> None:
    analyzer = OpenAIContentAnalyzer(ModelSettings(enabled=False), InMemorySecretStore())
    rule = CognitionRule(
        mode=RuleMode.COMPANION,
        title="不喜欢的内容",
        interest_judgment=InterestJudgment.NOT_INTERESTED,
        keywords=["无聊节目"],
    )
    result = analyzer.analyze(
        b"x",
        AnalysisContext("video.exe", "无聊节目", False, True),
        [],
        [rule],
    )
    assert result.interest is InterestJudgment.NOT_INTERESTED
    assert result.matched_interest_rule_ids == [rule.id]
