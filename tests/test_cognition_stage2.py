"""阶段二认知、基础规则、AI整理与审核过滤验收。"""

from __future__ import annotations

import sqlite3
from threading import Event
from time import monotonic

import pytest
from PySide6.QtTest import QSignalSpy

from desktop_companion_agent.config import ModelSettings, ModerationPolicySettings
from desktop_companion_agent.models import (
    CognitionDraft,
    CognitionDraftSchema,
    CognitionRule,
    InterestJudgment,
    RuleMode,
    RuleSource,
    SemanticRuleMatch,
    VisionAnalysisSchema,
)
from desktop_companion_agent.security import InMemorySecretStore
from desktop_companion_agent.services.analyzer import AnalysisContext, OpenAIContentAnalyzer
from desktop_companion_agent.services.cognition_organizer import (
    CognitionOrganizerService,
    automatic_rule_title,
    parse_cognition_terms,
)
from desktop_companion_agent.storage.repository import CognitionRepository
from desktop_companion_agent.storage.starter_rules import (
    STARTER_RULES_METADATA_KEY,
    starter_supervision_rules,
)


def _wait_until(qt_app, condition, seconds: float = 2.0) -> None:
    deadline = monotonic() + seconds
    while not condition() and monotonic() < deadline:
        qt_app.processEvents()
    qt_app.processEvents()
    assert condition()


def test_starter_rules_initialize_once_and_do_not_resurrect(tmp_path) -> None:
    database = tmp_path / "cognition.sqlite3"
    repository = CognitionRepository(database)
    rules = repository.list_rules(RuleMode.SUPERVISION)
    assert {rule.id for rule in rules} == {rule.id for rule in starter_supervision_rules()}
    assert repository.get_metadata(STARTER_RULES_METADATA_KEY) == "1"

    deleted_id = rules[0].id
    repository.delete_rule(deleted_id)
    repository.close()
    reopened = CognitionRepository(database)
    assert deleted_id not in {rule.id for rule in reopened.list_rules(RuleMode.SUPERVISION)}

    reopened.clear_cognition()
    reopened.close()
    reopened = CognitionRepository(database)
    assert reopened.list_rules(RuleMode.SUPERVISION) == []
    assert reopened.get_metadata(STARTER_RULES_METADATA_KEY) == "1"
    reopened.close()


def test_existing_supervision_rules_prevent_automatic_injection(tmp_path) -> None:
    database = tmp_path / "legacy.sqlite3"
    repository = CognitionRepository(database)
    repository.clear_cognition()
    repository.add_rule(
        CognitionRule(
            id="user.existing",
            mode=RuleMode.SUPERVISION,
            title="旧版个人规则",
            keywords=["个人词条"],
        )
    )
    repository.close()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "DELETE FROM app_metadata WHERE key = ?", (STARTER_RULES_METADATA_KEY,)
        )
    reopened = CognitionRepository(database)
    assert [rule.id for rule in reopened.list_rules(RuleMode.SUPERVISION)] == [
        "user.existing"
    ]
    assert reopened.get_metadata(STARTER_RULES_METADATA_KEY) == "1"
    reopened.close()


def test_restore_only_overwrites_fixed_builtin_ids(tmp_path) -> None:
    repository = CognitionRepository(tmp_path / "restore.sqlite3")
    custom = CognitionRule(
        id="user.keep",
        mode=RuleMode.SUPERVISION,
        title="我的规则",
        keywords=["保留我"],
    )
    repository.add_rule(custom)
    repository.delete_rule("builtin.supervision.sexual-services")
    repository.restore_starter_supervision_rules()
    by_id = {rule.id: rule for rule in repository.list_rules(RuleMode.SUPERVISION)}
    assert by_id["user.keep"].keywords == ["保留我"]
    assert by_id["builtin.supervision.sexual-services"].source is RuleSource.BUILTIN
    assert len([rule for rule in by_id.values() if rule.source is RuleSource.BUILTIN]) == 5
    repository.close()


def test_term_parser_and_automatic_titles() -> None:
    assert parse_cognition_terms(" 甲，乙,甲；丙; 丁\n乙 ") == ["甲", "乙", "丙", "丁"]
    assert automatic_rule_title(RuleMode.SUPERVISION, "词条") == "监督：词条"
    assert (
        automatic_rule_title(
            RuleMode.COMPANION, "无聊节目", InterestJudgment.NOT_INTERESTED
        )
        == "不感兴趣：无聊节目"
    )
    with pytest.raises(ValueError, match="120"):
        parse_cognition_terms("字" * 121)
    with pytest.raises(ValueError, match="50"):
        parse_cognition_terms("\n".join(f"词{i}" for i in range(51)))
    assert parse_cognition_terms(" ；，\n") == []


class _ParsedResponse:
    def __init__(self, parsed):
        self.output_parsed = parsed


class _Responses:
    def __init__(self, parsed, gate: Event | None = None):
        self.parsed = parsed
        self.gate = gate
        self.calls: list[dict[str, object]] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.gate is not None:
            self.gate.wait(2)
        return _ParsedResponse(self.parsed)


class _OrganizerClient:
    def __init__(self, responses: _Responses):
        self.responses = responses


class _TimeoutResponses:
    def parse(self, **kwargs):
        del kwargs
        raise TimeoutError("模拟请求超时")


def test_ai_organizer_previews_structured_result_without_database(qt_app, tmp_path) -> None:
    secret = InMemorySecretStore()
    secret.set("openai_api_key", "test-key")
    schema = CognitionDraftSchema(
        drafts=[CognitionDraft(keywords=["天文，宇宙"], title="")]
    )
    responses = _Responses(schema)
    service = CognitionOrganizerService(
        ModelSettings(), secret, client_factory=lambda **_kwargs: _OrganizerClient(responses)
    )
    repository = CognitionRepository(tmp_path / "organizer.sqlite3")
    before = len(repository.list_rules(RuleMode.COMPANION))
    ready = QSignalSpy(service.drafts_ready)
    assert service.organize("我喜欢天文", RuleMode.COMPANION) is True
    _wait_until(qt_app, lambda: ready.count() == 1)
    result = ready.at(0)[0]
    assert result.drafts[0].keywords == ["天文", "宇宙"]
    assert result.drafts[0].title == "感兴趣：天文"
    assert len(repository.list_rules(RuleMode.COMPANION)) == before
    call = responses.calls[0]
    assert "tools" not in call
    assert "监督规则" not in str(call["input"])
    service.shutdown()
    repository.close()


def test_ai_organizer_no_key_invalid_structure_and_cancel_late_result(qt_app) -> None:
    secret = InMemorySecretStore()
    service = CognitionOrganizerService(ModelSettings(), secret)
    errors = QSignalSpy(service.error_occurred)
    assert service.organize("整理我", RuleMode.SUPERVISION) is False
    assert "尚未配置API密钥" in errors.at(0)[0]
    service.shutdown()

    secret.set("openai_api_key", "test-key")
    invalid_responses = _Responses(None)
    invalid = CognitionOrganizerService(
        ModelSettings(),
        secret,
        client_factory=lambda **_kwargs: _OrganizerClient(invalid_responses),
    )
    invalid_errors = QSignalSpy(invalid.error_occurred)
    invalid.organize("整理我", RuleMode.SUPERVISION)
    _wait_until(qt_app, lambda: invalid_errors.count() == 1)
    assert "未返回可解析" in invalid_errors.at(0)[0]
    invalid.shutdown()

    timeout = CognitionOrganizerService(
        ModelSettings(),
        secret,
        client_factory=lambda **_kwargs: _OrganizerClient(_TimeoutResponses()),
    )
    timeout_errors = QSignalSpy(timeout.error_occurred)
    timeout.organize("超时测试", RuleMode.SUPERVISION)
    _wait_until(qt_app, lambda: timeout_errors.count() == 1)
    assert "模拟请求超时" in timeout_errors.at(0)[0]
    timeout.shutdown()

    gate = Event()
    late_responses = _Responses(
        CognitionDraftSchema(drafts=[CognitionDraft(keywords=["迟到结果"])]), gate
    )
    late = CognitionOrganizerService(
        ModelSettings(),
        secret,
        client_factory=lambda **_kwargs: _OrganizerClient(late_responses),
    )
    ready = QSignalSpy(late.drafts_ready)
    late.organize("取消测试", RuleMode.SUPERVISION)
    late.cancel()
    gate.set()
    _wait_until(qt_app, lambda: not late.busy)
    qt_app.processEvents()
    assert ready.count() == 0
    late.shutdown()


class _StubAnalyzer(OpenAIContentAnalyzer):
    def __init__(
        self,
        categories: list[str],
        vision: VisionAnalysisSchema | None = None,
        policy: ModerationPolicySettings | None = None,
    ):
        super().__init__(ModelSettings(), InMemorySecretStore(), policy)
        self._categories = categories
        self._vision_result = vision or VisionAnalysisSchema(summary="测试摘要")

    def _client(self):
        return object()

    def _moderate(self, client, data_url: str) -> list[str]:
        return self._categories

    def _vision(self, *args, **kwargs) -> VisionAnalysisSchema:
        return self._vision_result


class _VisionFailureAnalyzer(_StubAnalyzer):
    def _vision(self, *args, **kwargs) -> VisionAnalysisSchema:
        raise TimeoutError("视觉请求超时")


@pytest.mark.parametrize(
    "category", ["sexual", "sexual/minors", "violence", "violence/graphic"]
)
def test_default_official_categories_force_intervention(category: str) -> None:
    result = _StubAnalyzer([category]).analyze(
        b"jpeg", AnalysisContext("browser", "普通标题", True, False), [], []
    )
    assert result.flagged is True
    assert result.categories == [category]


@pytest.mark.parametrize("category", ["self-harm", "hate", "illicit", "harassment"])
def test_other_official_categories_record_only_by_default(category: str) -> None:
    result = _StubAnalyzer([category]).analyze(
        b"jpeg", AnalysisContext("browser", "普通标题", True, True), [], []
    )
    assert result.flagged is False
    assert result.categories == [category]


def test_extra_category_must_be_enabled_before_intervention() -> None:
    policy = ModerationPolicySettings(
        blocking_categories=["sexual", "self-harm"]
    )
    result = _StubAnalyzer(["self-harm"], policy=policy).analyze(
        b"jpeg", AnalysisContext("browser", "普通标题", True, False), [], []
    )
    assert result.flagged is True


def test_successful_moderation_survives_vision_failure() -> None:
    result = _VisionFailureAnalyzer(["violence"]).analyze(
        b"jpeg", AnalysisContext("browser", "普通标题", True, False), [], []
    )
    assert result.flagged is True
    assert result.categories == ["violence"]
    assert result.analysis_available is True


def test_companion_vision_failure_degrades_without_false_block() -> None:
    result = _VisionFailureAnalyzer([]).analyze(
        b"jpeg", AnalysisContext("browser", "普通标题", False, True), [], []
    )
    assert result.flagged is False
    assert result.analysis_available is False
    assert "视觉" in result.error_message


def test_vision_rule_ids_are_allowlisted_and_thresholded() -> None:
    rule = CognitionRule(
        id="valid-rule",
        mode=RuleMode.SUPERVISION,
        title="有效规则",
        keywords=["不会本地命中"],
    )
    low = VisionAnalysisSchema(
        summary="低置信度",
        supervision_matches=[SemanticRuleMatch(rule_id="valid-rule", confidence=0.84)],
    )
    low_result = _StubAnalyzer([], low).analyze(
        b"jpeg", AnalysisContext("browser", "普通标题", True, False), [rule], []
    )
    assert low_result.flagged is False
    assert low_result.matched_supervision_rule_ids == []

    invalid = VisionAnalysisSchema(
        summary="无效ID",
        supervision_matches=[
            SemanticRuleMatch(rule_id="model-created-rule", confidence=0.99)
        ],
    )
    invalid_result = _StubAnalyzer([], invalid).analyze(
        b"jpeg", AnalysisContext("browser", "普通标题", True, False), [rule], []
    )
    assert invalid_result.flagged is False
    assert invalid_result.matched_supervision_rule_ids == []

    valid = VisionAnalysisSchema(
        summary="有效ID",
        supervision_matches=[SemanticRuleMatch(rule_id="valid-rule", confidence=0.85)],
    )
    valid_result = _StubAnalyzer([], valid).analyze(
        b"jpeg", AnalysisContext("browser", "普通标题", True, False), [rule], []
    )
    assert valid_result.flagged is True
    assert valid_result.matched_supervision_rule_ids == ["valid-rule"]
