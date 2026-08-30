"""
Test cases for app.domain.ai_assistant Module D logic (AI Review Assistant & Guardrails).

Covers the deterministic, non-network logic so tests never require a live LLM/API:

- clean_json_response_text and truncate_long_explanation helpers
- JSON payload parsing helpers (_parse_exception_json, _parse_rule_json) via providers
- severity normalization (string/numeric/unknown) in rule parsing & rule creation
- process_ai_recommendation_decision dispatch and guardrails
- process_exception_ai_decision (Accept / Edit / Reject)
- process_rule_ai_decision (Accept / Edit / Reject)
- create_canonical_validation_rule validations and uniqueness
- generate_*_ai_recommendation high-level entry points with mocked providers
- LLMProviderRegistry provider resolution

Only the high-level generate_* functions hit the network; those are tested by
mocking the provider so the LLM call is never performed.
"""

from unittest import mock

import pytest

from app.domain.ai_assistant import (
    AIAnalysisResult,
    AIRuleResult,
    GeminiProvider,
    LLMProviderRegistry,
    OpenAIProvider,
    OpenCodeZenProvider,
    build_ai_prompt_for_exception,
    clean_json_response_text,
    create_canonical_validation_rule,
    generate_ai_rule_recommendation,
    generate_exception_ai_recommendation,
    process_ai_recommendation_decision,
    process_exception_ai_decision,
    process_rule_ai_decision,
    truncate_long_explanation,
)
from app.models import (
    AIRecommendation,
    AuditEvent,
    LoanException,
    RawLoanRecord,
    UploadBatch,
    ValidationRule,
    ValidationSeverity,
)
from tests.factory.user_factory import UserFactory


def _make_exception(
    batch,
    severity=ValidationSeverity.HIGH,
    status=LoanException.ExceptionStatus.OPEN,
    rule_code="VAL_AI_001",
    field_name="loan_id",
    raw_data=None,
    **kwargs,
):
    rule = ValidationRule.objects.create(
        rule_code=rule_code,
        strategy_key="MISSING_LOAN_ID",
        rule_name=f"Rule {rule_code}",
        field_name=field_name,
        description=f"Description {rule_code}",
    )
    record = RawLoanRecord.objects.create(
        batch=batch,
        row_number=1,
        raw_data=raw_data if raw_data is not None else {"loan_id": "LG-1"},
    )
    defaults = {
        "batch": batch,
        "raw_record": record,
        "rule": rule,
        "rule_code": rule_code,
        "field_name": field_name,
        "severity": severity,
        "description": f"Sample exception {rule_code}",
        "status": status,
    }
    defaults.update(kwargs)
    return LoanException.objects.create(**defaults), record


def _rule_recommendation(
    success=True,
    rule_code="AI_RULE_1",
    **overrides,
):
    data = {
        "success": success,
        "error_message": "" if success else "generation failed",
        "rule_code": rule_code,
        "rule_name": "AI Generated Rule",
        "description": "AI rule description",
        "field_name": "current_balance",
        "severity": 3,
        "strategy_key": "BALANCE_RANGE",
        "parameters": {"min": 0},
        "reasoning": "reasoning text",
        "confidence_score": 0.9,
    }
    data.update(overrides)
    return AIRecommendation.objects.create(
        recommendation_type=AIRecommendation.RecommendationType.RULE_GENERATION,
        suggested_rule_data=data,
        suggested_value=data["rule_code"],
        explanation=data["description"],
        reasoning=data["reasoning"],
        confidence_score=data["confidence_score"],
        status=AIRecommendation.RecommendationStatus.PENDING,
    )


@pytest.mark.django_db
class TestCleanJsonResponseText:
    """Test cases for clean_json_response_text helper."""

    def test_plain_json_unchanged(self):
        assert clean_json_response_text('{"a": 1}') == '{"a": 1}'

    def test_strips_leading_trailing_whitespace(self):
        assert clean_json_response_text('  {"a": 1}  \n') == '{"a": 1}'

    def test_removes_triple_backtick_fence(self):
        raw = '```json\n{"a": 1}\n```'
        assert clean_json_response_text(raw) == '{"a": 1}'

    def test_removes_fence_without_language_tag(self):
        raw = '```\n{"b": 2}\n```'
        assert clean_json_response_text(raw) == '{"b": 2}'

    def test_empty_string(self):
        assert clean_json_response_text("") == ""

    def test_fence_only_no_newlines_single_line_stripped(self):
        # A single-line fenced block is treated as a fence line and removed.
        assert clean_json_response_text("```json{x:1}```") == ""


@pytest.mark.django_db
class TestTruncateLongExplanation:
    """Test cases for truncate_long_explanation helper."""

    def test_short_explanation_unchanged(self):
        exp = "This is a short explanation."
        out, reasoning = truncate_long_explanation(exp, "reasoning")
        assert out == exp
        assert reasoning == "reasoning"

    def test_long_explanation_truncated_and_reasoning_extended(self):
        exp = "word " * 900
        short, extended = truncate_long_explanation(exp, "step reasoning")
        assert len(short) < len(exp)
        assert short.endswith("...")
        assert "--- Full AI Explanation ---" in extended
        assert "--- Step-by-Step Reasoning ---" in extended

    def test_too_many_newlines_triggers_truncation(self):
        exp = "a\n" * 20
        short, extended = truncate_long_explanation(exp, "reasoning")
        assert short.endswith("...")
        assert "Step-by-Step Reasoning" in extended

    def test_boundary_exactly_max_chars_not_truncated(self):
        exp = "x" * 800
        short, _ = truncate_long_explanation(exp, "r")
        assert short == exp

    def test_boundary_just_over_max_chars_truncated(self):
        exp = ("x" * 799) + " y"
        short, _ = truncate_long_explanation(exp, "r")
        assert short.endswith("...")


@pytest.mark.django_db
class TestParseExceptionJsonHelpers:
    """Test cases for _parse_exception_json via a real provider instance."""

    def setup_method(self):
        self.provider = OpenCodeZenProvider()

    def test_valid_json_parse_success(self):
        raw = '{"explanation":"It failed","suggested_value":"5000","confidence_score":0.7,"reasoning":"steps"}'
        result = self.provider._parse_exception_json(raw)
        assert result.success is True
        assert result.explanation == "It failed"
        assert result.suggested_value == "5000"
        assert result.confidence_score == 0.7
        assert result.reasoning == "steps"

    def test_invalid_json_returns_failure_result(self):
        result = self.provider._parse_exception_json("not json")
        assert result.success is False
        assert "Failed to parse JSON" in result.error_message
        assert "AI generation failed" in result.explanation

    def test_missing_required_keys_returns_failure(self):
        raw = '{"explanation":"only"}'  # missing suggested_value
        result = self.provider._parse_exception_json(raw)
        assert result.success is False
        assert "missing required JSON keys" in result.error_message

    def test_missing_confidence_defaults_to_zero(self):
        raw = '{"explanation":"e","suggested_value":"v"}'
        result = self.provider._parse_exception_json(raw)
        assert result.success is True
        assert result.confidence_score == 0.0

    def test_numeric_fields_coerced_to_string(self):
        raw = '{"explanation":"e","suggested_value":1234,"confidence_score":1}'
        result = self.provider._parse_exception_json(raw)
        assert result.success is True
        assert result.suggested_value == "1234"

    def test_fenced_json_cleaned_before_parse(self):
        raw = '```json\n{"explanation":"e","suggested_value":"v"}\n```'
        result = self.provider._parse_exception_json(raw)
        assert result.success is True
        assert result.suggested_value == "v"


@pytest.mark.django_db
class TestParseRuleJsonHelpers:
    """Test cases for _parse_rule_json severity mapping and validation."""

    def setup_method(self):
        self.provider = OpenCodeZenProvider()

    def _valid(self, **over):
        data = {
            "rule_code": "NEW_1",
            "rule_name": "Name",
            "field_name": "field",
            "severity": 3,
            "strategy_key": "KEY",
        }
        data.update(over)
        import json

        return json.dumps(data)

    def test_valid_rule_parse_success(self):
        rule = self.provider._parse_rule_json(self._valid(), "prompt")
        assert rule.success is True
        assert rule.rule_code == "NEW_1"
        assert rule.severity == 3
        assert rule.description == "prompt"

    def test_invalid_json_returns_failure(self):
        rule = self.provider._parse_rule_json("garbage", "prompt")
        assert rule.success is False
        assert "Failed to parse JSON rule payload" in rule.error_message

    def test_missing_required_field_returns_failure(self):
        rule = self.provider._parse_rule_json('{"rule_code":"X"}', "prompt")
        assert rule.success is False
        assert "missing required fields" in rule.error_message

    def test_incomplete_blank_required_field_returns_failure(self):
        rule = self.provider._parse_rule_json(
            '{"rule_code":"X","rule_name":"n","field_name":"","severity":2,"strategy_key":"k"}',
            "prompt",
        )
        assert rule.success is False
        assert "field_name" in rule.error_message

    def test_severity_string_maps_correctly(self):
        for label, num in [("LOW", 1), ("MEDIUM", 2), ("HIGH", 3), ("CRITICAL", 4)]:
            rule = self.provider._parse_rule_json(self._valid(severity=label), "prompt")
            assert rule.severity == num, label

    def test_severity_case_insensitive(self):
        rule = self.provider._parse_rule_json(self._valid(severity="high"), "prompt")
        assert rule.severity == 3

    def test_severity_unknown_string_defaults_to_medium(self):
        rule = self.provider._parse_rule_json(self._valid(severity="banana"), "prompt")
        assert rule.severity == 2

    def test_severity_missing_is_required(self):
        # severity is a required schema key; absence yields a validation failure.
        import json

        data = json.loads(self._valid())
        del data["severity"]
        parsed = self.provider._parse_rule_json(json.dumps(data), "prompt")
        assert parsed.success is False
        assert "severity" in parsed.error_message

    def test_numeric_severity_passthrough(self):
        rule = self.provider._parse_rule_json(self._valid(severity=4), "prompt")
        assert rule.severity == 4


@mock.patch.object(
    OpenCodeZenProvider,
    "generate_rule",
    return_value=AIRuleResult(
        rule_code="V_1",
        rule_name="N",
        description="d",
        field_name="f",
        severity=3,
        strategy_key="k",
    ),
)
@pytest.mark.django_db
class TestGenerateAIRuleRecommendation:
    """Test cases for generate_ai_rule_recommendation (provider mocked)."""

    def setup_method(self):
        self.reviewer = UserFactory.create_reviewer(username="ai_rule_reviewer")

    def test_creates_pending_rule_recommendation(self, mock_gen):
        rec = generate_ai_rule_recommendation(prompt_text="make a rule", user=self.reviewer)
        assert rec.recommendation_type == AIRecommendation.RecommendationType.RULE_GENERATION
        assert rec.status == AIRecommendation.RecommendationStatus.PENDING
        assert rec.suggested_value == "V_1"
        assert rec.created_by_id == self.reviewer.id
        assert mock_gen.called

    def test_creates_recommendation_when_user_anonymous(self, mock_gen):
        rec = generate_ai_rule_recommendation(prompt_text="rule", user=None)
        assert rec.created_by_id is None

    def test_logs_ai_rule_generated_event(self, mock_gen):
        rec = generate_ai_rule_recommendation(prompt_text="rule", user=self.reviewer)
        event = AuditEvent.objects.get(event_type="AI_RULE_GENERATED")
        assert event.payload["recommendation_id"] == rec.id
        assert event.payload["rule_code"] == "V_1"
        assert event.payload["success"] is True

    def test_failed_generation_still_creates_record_with_error(self, mock_gen):
        mock_gen.return_value = AIRuleResult(
            rule_code="",
            rule_name="",
            description="",
            field_name="",
            severity=0,
            strategy_key="",
            success=False,
            error_message="boom",
        )
        rec = generate_ai_rule_recommendation(prompt_text="rule", user=self.reviewer)
        assert rec.status == AIRecommendation.RecommendationStatus.PENDING
        assert "AI Rule Generation Failed" in rec.explanation
        event = AuditEvent.objects.get(event_type="AI_RULE_GENERATED")
        assert event.payload["success"] is False
        assert event.payload["error_message"] == "boom"


@mock.patch.object(
    OpenCodeZenProvider,
    "analyze_exception",
    return_value=AIAnalysisResult(
        explanation="fixed",
        suggested_value="1000",
        confidence_score=0.8,
        reasoning="r",
    ),
)
@pytest.mark.django_db
class TestGenerateExceptionAIRecommendation:
    """Test cases for generate_exception_ai_recommendation (provider mocked)."""

    def setup_method(self):
        self.reviewer = UserFactory.create_reviewer(username="ai_exc_reviewer")
        self.batch = UploadBatch.objects.create(
            file_name="loan_tape.csv",
            source_type=UploadBatch.SourceType.LOAN_TAPE,
            status=UploadBatch.BatchStatus.INGESTED,
        )
        self.exc, _ = _make_exception(self.batch)
        self.prompt = build_ai_prompt_for_exception(self.exc, {"loan_id": "LG-1"}, None, None)

    def test_creates_exception_recommendation_pending(self, mock_ana):
        rec = generate_exception_ai_recommendation(
            loan_exception=self.exc,
            user=self.reviewer,
            model_choice=AIRecommendation.ModelProvider.OPENCODE_ZEN,
        )
        assert rec.recommendation_type == AIRecommendation.RecommendationType.EXCEPTION_REVIEW
        assert rec.exception_id == self.exc.id
        assert rec.status == AIRecommendation.RecommendationStatus.PENDING
        assert rec.suggested_value == "1000"
        assert mock_ana.called

    def test_logs_generated_event(self, mock_ana):
        rec = generate_exception_ai_recommendation(loan_exception=self.exc, user=self.reviewer)
        event = AuditEvent.objects.get(event_type="AI_RECOMMENDATION_GENERATED")
        assert event.payload["recommendation_id"] == rec.id
        assert event.payload["exception_id"] == self.exc.id
        assert event.payload["success"] is True

    def test_unauthenticated_user_sets_no_created_by(self, mock_ana):
        rec = generate_exception_ai_recommendation(loan_exception=self.exc, user=None)
        assert rec.created_by_id is None


@pytest.mark.django_db
class TestProcessExceptionAIDecision:
    """Test cases for exception review decision processing (Accept / Edit / Reject)."""

    def setup_method(self):
        self.reviewer = UserFactory.create_reviewer(username="ai_dec_reviewer")
        self.batch = UploadBatch.objects.create(
            file_name="loan_tape.csv",
            source_type=UploadBatch.SourceType.LOAN_TAPE,
            status=UploadBatch.BatchStatus.INGESTED,
        )

    def _make_rec(self, **kwargs):
        exc, _ = _make_exception(self.batch)
        return AIRecommendation.objects.create(
            recommendation_type=AIRecommendation.RecommendationType.EXCEPTION_REVIEW,
            exception=exc,
            rule=exc.rule,
            suggested_value="5000",
            explanation="explanation",
            status=AIRecommendation.RecommendationStatus.PENDING,
            **kwargs,
        ), exc

    def test_accept_sets_accepted_and_resolves_exception(self):
        rec, exc = self._make_rec()
        success, message = process_exception_ai_decision(rec, "accept", self.reviewer)
        assert success is True
        assert "accepted" in message
        rec.refresh_from_db()
        exc.refresh_from_db()
        assert rec.status == AIRecommendation.RecommendationStatus.ACCEPTED
        assert exc.status == LoanException.ExceptionStatus.RESOLVED_ACCEPTED
        assert rec.reviewed_by_id == self.reviewer.id

    def test_edit_with_value_sets_edited_and_updates_raw(self):
        rec, exc = self._make_rec()
        success, _ = process_exception_ai_decision(rec, "edit", self.reviewer, edited_value="9999")
        assert success is True
        rec.refresh_from_db()
        exc.refresh_from_db()
        assert rec.status == AIRecommendation.RecommendationStatus.EDITED
        assert rec.edited_value == "9999"
        assert exc.status == LoanException.ExceptionStatus.RESOLVED_EDITED
        assert exc.override_value == "9999"
        assert exc.raw_record.raw_data[self.exc_field(exc)] == "9999"

    @staticmethod
    def exc_field(exc):
        return exc.field_name

    def test_edit_with_empty_value_fails(self):
        rec, exc = self._make_rec()
        success, message = process_exception_ai_decision(
            rec, "edit", self.reviewer, edited_value="   "
        )
        assert success is False
        assert "cannot be empty" in message
        rec.refresh_from_db()
        assert rec.status == AIRecommendation.RecommendationStatus.PENDING

    def test_reject_sets_rejected_recommendation_but_keeps_exception_open(self):
        # Rejecting the AI recommendation does NOT resolve the underlying exception;
        # only Accept/Edit apply the resolution to the exception record.
        rec, exc = self._make_rec()
        success, message = process_exception_ai_decision(
            rec, "reject", self.reviewer, reviewer_comment="bad"
        )
        assert success is True
        assert "rejected" in message
        rec.refresh_from_db()
        exc.refresh_from_db()
        assert rec.status == AIRecommendation.RecommendationStatus.REJECTED
        assert exc.status == LoanException.ExceptionStatus.OPEN

    def test_action_trimmed_and_case_insensitive(self):
        rec, _ = self._make_rec()
        success, _ = process_exception_ai_decision(rec, "  ACCEPT  ", self.reviewer)
        assert success is True
        rec.refresh_from_db()
        assert rec.status == AIRecommendation.RecommendationStatus.ACCEPTED

    def test_invalid_action_fails(self):
        rec, _ = self._make_rec()
        success, message = process_exception_ai_decision(rec, "banana", self.reviewer)
        assert success is False
        assert "Invalid decision action" in message
        rec.refresh_from_db()
        assert rec.status == AIRecommendation.RecommendationStatus.PENDING

    def test_empty_action_fails(self):
        rec, _ = self._make_rec()
        success, _ = process_exception_ai_decision(rec, "", self.reviewer)
        assert success is False

    def test_audit_event_logged_on_accept(self):
        rec, _ = self._make_rec()
        process_exception_ai_decision(rec, "accept", self.reviewer)
        assert AuditEvent.objects.filter(event_type="AI_RECOMMENDATION_ACCEPTED").exists()


@pytest.mark.django_db
class TestProcessRuleAIDecision:
    """Test cases for rule generation decision processing."""

    def setup_method(self):
        self.reviewer = UserFactory.create_reviewer(username="ai_rule_dec_reviewer")

    def test_accept_creates_validation_rule(self):
        rec = _rule_recommendation()
        success, message = process_rule_ai_decision(rec, "accept", self.reviewer)
        assert success is True
        assert "created" in message
        rec.refresh_from_db()
        assert rec.status == AIRecommendation.RecommendationStatus.ACCEPTED
        assert rec.rule is not None
        assert ValidationRule.objects.filter(rule_code="AI_RULE_1").exists()
        assert AuditEvent.objects.filter(event_type="AI_RULE_ACCEPTED").exists()

    def test_accept_failed_generation_fails(self):
        rec = _rule_recommendation(success=False)
        success, message = process_rule_ai_decision(rec, "accept", self.reviewer)
        assert success is False
        assert "generation failed" in message
        rec.refresh_from_db()
        assert rec.status == AIRecommendation.RecommendationStatus.PENDING

    def test_edit_merges_edited_data_and_creates_rule(self):
        rec = _rule_recommendation()
        success, _ = process_rule_ai_decision(
            rec, "edit", self.reviewer, edited_rule_data={"rule_name": "Renamed"}
        )
        assert success is True
        rec.refresh_from_db()
        assert rec.status == AIRecommendation.RecommendationStatus.EDITED
        assert rec.rule is not None
        created = ValidationRule.objects.get(rule_code="AI_RULE_1")
        assert created.rule_name == "Renamed"
        assert AuditEvent.objects.filter(event_type="AI_RULE_EDITED").exists()

    def test_edit_empty_data_fails(self):
        rec = _rule_recommendation()
        success, message = process_rule_ai_decision(
            rec, "edit", self.reviewer, edited_rule_data=None
        )
        assert success is False
        assert "cannot be empty" in message
        rec.refresh_from_db()
        assert rec.status == AIRecommendation.RecommendationStatus.PENDING

    def test_edit_empty_dict_treated_as_empty(self):
        rec = _rule_recommendation()
        success, message = process_rule_ai_decision(rec, "edit", self.reviewer, edited_rule_data={})
        assert success is False
        assert "cannot be empty" in message

    def test_reject_sets_rejected_status(self):
        rec = _rule_recommendation()
        success, message = process_rule_ai_decision(rec, "reject", self.reviewer, comment="no")
        assert success is True
        assert "rejected" in message
        rec.refresh_from_db()
        assert rec.status == AIRecommendation.RecommendationStatus.REJECTED
        assert AuditEvent.objects.filter(event_type="AI_RULE_REJECTED").exists()

    def test_invalid_action_fails(self):
        rec = _rule_recommendation()
        success, message = process_rule_ai_decision(rec, "bogus", self.reviewer)
        assert success is False
        assert "Invalid decision action" in message
        rec.refresh_from_db()
        assert rec.status == AIRecommendation.RecommendationStatus.PENDING

    def test_accept_missing_required_rule_field_returns_error(self):
        rec = _rule_recommendation(rule_code="")
        success, message = process_rule_ai_decision(rec, "accept", self.reviewer)
        assert success is False
        assert "missing required fields" in message


@pytest.mark.django_db
class TestProcessAIRecommendationDecision:
    """Test cases for the top-level decision dispatcher + re-review guardrail."""

    def setup_method(self):
        self.reviewer = UserFactory.create_reviewer(username="ai_dispatch_reviewer")
        self.batch = UploadBatch.objects.create(
            file_name="loan_tape.csv",
            source_type=UploadBatch.SourceType.LOAN_TAPE,
            status=UploadBatch.BatchStatus.INGESTED,
        )

    def test_dispatches_exception_accept(self):
        exc, _ = _make_exception(self.batch)
        rec = AIRecommendation.objects.create(
            recommendation_type=AIRecommendation.RecommendationType.EXCEPTION_REVIEW,
            exception=exc,
            rule=exc.rule,
            suggested_value="x",
            explanation="e",
            status=AIRecommendation.RecommendationStatus.PENDING,
        )
        success, _ = process_ai_recommendation_decision(rec, "accept", self.reviewer)
        assert success is True
        rec.refresh_from_db()
        assert rec.status == AIRecommendation.RecommendationStatus.ACCEPTED

    def test_dispatches_rule_accept(self):
        rec = _rule_recommendation()
        success, _ = process_ai_recommendation_decision(rec, "accept", self.reviewer)
        assert success is True
        rec.refresh_from_db()
        assert rec.status == AIRecommendation.RecommendationStatus.ACCEPTED

    def test_already_reviewed_recommendation_rejected(self):
        exc, _ = _make_exception(self.batch)
        rec = AIRecommendation.objects.create(
            recommendation_type=AIRecommendation.RecommendationType.EXCEPTION_REVIEW,
            exception=exc,
            rule=exc.rule,
            suggested_value="x",
            explanation="e",
            status=AIRecommendation.RecommendationStatus.ACCEPTED,
        )
        success, message = process_ai_recommendation_decision(rec, "reject", self.reviewer)
        assert success is False
        assert "already been reviewed" in message

    def test_invalid_action_at_top_level_not_swallowed(self):
        rec = _rule_recommendation()
        success, message = process_ai_recommendation_decision(rec, "unknown", self.reviewer)
        assert success is False
        assert "Invalid decision action" in message


@pytest.mark.django_db
class TestCreateCanonicalValidationRule:
    """Test cases for create_canonical_validation_rule."""

    def setup_method(self):
        self.reviewer = UserFactory.create_reviewer(username="ai_canon_reviewer")

    def test_creates_valid_rule(self):
        rule, err = create_canonical_validation_rule(
            {
                "rule_code": "C_1",
                "rule_name": "Name",
                "field_name": "f",
                "severity": 2,
                "strategy_key": "k",
            },
            actor=self.reviewer,
        )
        assert rule is not None
        assert err == ""
        assert rule.severity == 2
        assert rule.is_active is True

    def test_missing_required_field_returns_error(self):
        rule, err = create_canonical_validation_rule(
            {"rule_code": "C_2", "rule_name": "N", "field_name": "f"}
        )
        assert rule is None
        assert "missing required fields" in err

    def test_duplicate_rule_code_rejected(self):
        ValidationRule.objects.create(
            rule_code="DUP",
            rule_name="existing",
            field_name="f",
            description="d",
            strategy_key="k",
            severity=1,
            is_active=True,
        )
        rule, err = create_canonical_validation_rule(
            {
                "rule_code": "DUP",
                "rule_name": "N",
                "field_name": "f",
                "severity": 2,
                "strategy_key": "k",
            }
        )
        assert rule is None
        assert "already exists" in err

    def test_severity_string_mapped(self):
        rule, _ = create_canonical_validation_rule(
            {
                "rule_code": "C_3",
                "rule_name": "N",
                "field_name": "f",
                "severity": "CRITICAL",
                "strategy_key": "k",
            }
        )
        assert rule.severity == 4

    def test_severity_invalid_string_defaults_medium(self):
        rule, _ = create_canonical_validation_rule(
            {
                "rule_code": "C_4",
                "rule_name": "N",
                "field_name": "f",
                "severity": "zzz",
                "strategy_key": "k",
            }
        )
        assert rule.severity == 2

    def test_non_dict_parameters_coerced_to_empty_dict(self):
        rule, _ = create_canonical_validation_rule(
            {
                "rule_code": "C_5",
                "rule_name": "N",
                "field_name": "f",
                "severity": 1,
                "strategy_key": "k",
                "parameters": "notadict",
            }
        )
        assert rule.parameters == {}

    def test_blank_fields_stripped_then_invalid(self):
        rule, err = create_canonical_validation_rule(
            {
                "rule_code": "  ",
                "rule_name": "N",
                "field_name": "f",
                "severity": 1,
                "strategy_key": "k",
            }
        )
        assert rule is None
        assert "missing required fields" in err


@pytest.mark.django_db
class TestLLMProviderRegistry:
    """Test cases for LLMProviderRegistry provider resolution."""

    def test_get_provider_by_key(self):
        # Mock is_configured so resolution does not depend on runtime API keys.
        with mock.patch.object(
            OpenCodeZenProvider, "is_configured", new_callable=mock.PropertyMock, return_value=True
        ):
            provider = LLMProviderRegistry.get_provider("opencode_zen")
        assert isinstance(provider, OpenCodeZenProvider)

    def test_get_provider_by_id(self):
        with mock.patch.object(
            OpenCodeZenProvider, "is_configured", new_callable=mock.PropertyMock, return_value=True
        ):
            provider = LLMProviderRegistry.get_provider(AIRecommendation.ModelProvider.OPENCODE_ZEN)
        assert isinstance(provider, OpenCodeZenProvider)

    def test_get_provider_by_int_id(self):
        # get_provider only returns the requested class if it is configured
        # (has a real API key). Mock is_configured so the test is deterministic
        # regardless of whether GEMINI_API_KEY is set in the environment (local
        # vs CI differ).
        with mock.patch.object(
            GeminiProvider, "is_configured", new_callable=mock.PropertyMock, return_value=True
        ):
            provider = LLMProviderRegistry.get_provider(AIRecommendation.ModelProvider.GEMINI)
        assert isinstance(provider, GeminiProvider)

    def test_get_provider_unknown_choice_falls_back(self):
        provider = LLMProviderRegistry.get_provider(999)
        assert provider is not None

    # ruff: noqa: UP038
    def test_get_provider_none_returns_default(self):
        provider = LLMProviderRegistry.get_provider(None)
        assert isinstance(provider, (OpenCodeZenProvider, GeminiProvider, OpenAIProvider))

    def test_list_available_providers_returns_metadata(self):
        providers = LLMProviderRegistry.list_available_providers()
        assert isinstance(providers, list)
        assert len(providers) >= 3
        for p in providers:
            assert "provider_id" in p
            assert "provider_key" in p
            assert "display_name" in p
            assert "is_configured" in p


@pytest.mark.django_db
class TestBuildAIPromptForException:
    """Test cases for build_ai_prompt_for_exception."""

    def setup_method(self):
        self.batch = UploadBatch.objects.create(
            file_name="loan_tape.csv",
            source_type=UploadBatch.SourceType.LOAN_TAPE,
            status=UploadBatch.BatchStatus.INGESTED,
        )

    def test_prompt_contains_key_details(self):
        exc, _ = _make_exception(
            self.batch,
            rule_code="PROMPT_1",
            field_name="current_balance",
            raw_data={"loan_id": "LG-9", "current_balance": "5000"},
        )
        prompt = build_ai_prompt_for_exception(
            exc, {"loan_id": "LG-9", "current_balance": "5000"}, None, None
        )
        assert exc.loan_id in prompt
        assert "PROMPT_1" in prompt
        assert "current_balance" in prompt
        assert "5000" in prompt

    def test_prompt_embeds_optional_contexts(self):
        exc, _ = _make_exception(self.batch, raw_data={"loan_id": "LG-1"})
        prompt = build_ai_prompt_for_exception(
            exc, {"loan_id": "LG-1"}, {"servicer_key": "sv"}, {"doc_key": "doc"}
        )
        assert "servicer_key" in prompt
        assert "doc_key" in prompt

    def test_missing_raw_data_uses_empty_dict(self):
        exc, _ = _make_exception(self.batch, raw_data=None)
        prompt = build_ai_prompt_for_exception(exc, {}, None, None)
        assert "Loan ID:" in prompt
