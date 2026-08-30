"""
Test cases for the AI Copilot Reviewer views in app.views.reviewer:

- OpenAICopilotModalView        (GET: pending wrapper / initial modal)
- GenerateAIRuleView            (POST: AI rule generation)
- GenerateAIRecommendationView  (POST: AI exception recommendation)
- ProcessAIRecommendationView   (POST: reviewer decision on a recommendation)

All external LLM calls are mocked so no live API/network is required. The tests
cover authentication/role gating, HTTP method enforcement, context rendering,
model_choice parsing fallbacks, pending-recommendation detection, decision
handling (accept/edit/reject + invalid), HTMX responses, and invalid input
paths following the existing test conventions (role mixins, UserFactory, direct
model object construction).
"""

import json
from unittest import mock

import pytest
from django.test import Client
from django.urls import reverse

from app.models import (
    AIRecommendation,
    LoanException,
    RawLoanRecord,
    UploadBatch,
    ValidationRule,
    ValidationSeverity,
)
from tests.factory.user_factory import UserFactory

VIEWS = "app.views.reviewer"


def _make_rule(rule_code="VAL_AI_001", strategy_key="MISSING_LOAN_ID", field_name="loan_id"):
    return ValidationRule.objects.create(
        rule_code=rule_code,
        strategy_key=strategy_key,
        rule_name=f"Rule {rule_code}",
        field_name=field_name,
        description=f"Description {rule_code}",
    )


@pytest.mark.django_db
class AIViewsTestBase:
    """Shared fixtures/helpers for the AI copilot views tests."""

    def setup_method(self):
        self.client = Client(enforce_csrf_checks=False)
        self.login_url = reverse("login")
        self.reviewer = UserFactory.create_reviewer(username="ai_views_reviewer")
        self.batch = UploadBatch.objects.create(
            file_name="loan_tape.csv",
            source_type=UploadBatch.SourceType.LOAN_TAPE,
            status=UploadBatch.BatchStatus.INGESTED,
        )

    def _auth_client(self, user=None):
        client = Client(enforce_csrf_checks=False)
        client.force_login(user or self.reviewer)
        return client

    def _make_exception(self, raw_data=None, **kwargs):
        rule = _make_rule()
        defaults = {
            "batch": self.batch,
            "raw_record": RawLoanRecord.objects.create(
                batch=self.batch,
                row_number=1,
                raw_data=raw_data if raw_data is not None else {"loan_id": "LG-1"},
            ),
            "rule": rule,
            "rule_code": rule.rule_code,
            "field_name": "loan_id",
            "severity": ValidationSeverity.HIGH,
            "description": "sample",
            "status": LoanException.ExceptionStatus.OPEN,
        }
        defaults.update(kwargs)
        return LoanException.objects.create(**defaults)

    def _make_exception_recommendation(
        self, exception=None, status=AIRecommendation.RecommendationStatus.PENDING, **kwargs
    ):
        exc = exception or self._make_exception()
        return AIRecommendation.objects.create(
            recommendation_type=AIRecommendation.RecommendationType.EXCEPTION_REVIEW,
            exception=exc,
            rule=exc.rule,
            suggested_value="5000",
            explanation="explanation",
            reasoning="reasoning",
            confidence_score=0.8,
            created_by=self.reviewer,
            status=status,
            **kwargs,
        )

    def _make_rule_recommendation(self, success=True, **kwargs):
        data = {
            "success": success,
            "error_message": "" if success else "generation failed",
            "rule_code": "AI_RULE_1",
            "rule_name": "AI Rule",
            "description": "desc",
            "field_name": "current_balance",
            "severity": 3,
            "strategy_key": "BALANCE_RANGE",
            "parameters": {},
            "reasoning": "reasoning",
            "confidence_score": 0.9,
        }
        return AIRecommendation.objects.create(
            recommendation_type=AIRecommendation.RecommendationType.RULE_GENERATION,
            suggested_rule_data=data,
            suggested_value="AI_RULE_1",
            explanation="desc",
            reasoning="reasoning",
            confidence_score=0.9,
            created_by=self.reviewer,
            status=AIRecommendation.RecommendationStatus.PENDING,
            **kwargs,
        )


# ────────────────────────────────────────────────────────────────────────────
# OpenAICopilotModalView
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestOpenAICopilotModalView(AIViewsTestBase):
    """Test cases for OpenAICopilotModalView (`/reviewer/ai/modal/`)."""

    def get_general_url(self):
        return reverse("open_ai_copilot_modal_general")

    def get_exc_url(self, pk):
        return reverse("open_ai_copilot_modal", args=[pk])

    # Auth / permission gating (negative)
    def test_get_general_unauthenticated_redirects_to_login(self):
        response = self.client.get(self.get_general_url())
        assert response.status_code == 302
        assert self.login_url in response.url

    def test_get_general_superuser_denied(self):
        superuser = UserFactory.create_superuser(username="ai_modal_admin")
        assert self._auth_client(superuser).get(self.get_general_url()).status_code == 403

    def test_get_general_data_operator_denied(self):
        operator = UserFactory.create_data_operator(username="ai_modal_op")
        assert self._auth_client(operator).get(self.get_general_url()).status_code == 403

    def test_get_general_data_consumer_denied(self):
        consumer = UserFactory.create_data_consumer(username="ai_modal_consumer")
        assert self._auth_client(consumer).get(self.get_general_url()).status_code == 403

    # Method enforcement (negative)
    def test_post_returns_method_not_allowed(self):
        response = self._auth_client().post(self.get_general_url())
        assert response.status_code == 405

    # Positive: initial modal rendering
    @mock.patch(f"{VIEWS}.LLMProviderRegistry.list_available_providers")
    def test_get_general_renders_initial_modal(self, mock_providers):
        mock_providers.return_value = [
            {
                "provider_id": 4,
                "provider_key": "opencode_zen",
                "display_name": "OpenCode Zen",
                "is_configured": True,
            },
            {
                "provider_id": 1,
                "provider_key": "gemini",
                "display_name": "Gemini",
                "is_configured": False,
            },
        ]
        response = self._auth_client().get(self.get_general_url())
        assert response.status_code == 200
        assert response.templates[0].name == "dashboard/reviewer/ai_modal/ai_modal_initial.html"
        assert response.context["exc"] is None
        assert response.context["providers"] == mock_providers.return_value

    def test_get_general_returns_provider_list_without_exception(self):
        with mock.patch(f"{VIEWS}.LLMProviderRegistry.list_available_providers", return_value=[]):
            response = self._auth_client().get(self.get_general_url())
        assert response.status_code == 200
        assert response.context["providers"] == []

    def test_get_with_exception_and_no_pending_renders_initial(self):
        exc = self._make_exception()
        response = self._auth_client().get(self.get_exc_url(exc.id))
        assert response.status_code == 200
        assert response.templates[0].name == "dashboard/reviewer/ai_modal/ai_modal_initial.html"
        assert response.context["exc"].id == exc.id

    # Positive: pending wrapper rendering
    def test_get_with_pending_exception_renders_pending_wrapper(self):
        exc = self._make_exception()
        rec = self._make_exception_recommendation(exception=exc)
        response = self._auth_client().get(self.get_exc_url(exc.id))
        assert response.status_code == 200
        assert (
            response.templates[0].name
            == "dashboard/reviewer/ai_modal/ai_modal_pending_wrapper.html"
        )
        assert response.context["ai_rec"].id == rec.id
        assert response.context["is_pending_review_notice"] is True

    def test_get_non_pending_recommendation_renders_initial_modal(self):
        exc = self._make_exception()
        self._make_exception_recommendation(
            exception=exc, status=AIRecommendation.RecommendationStatus.ACCEPTED
        )
        response = self._auth_client().get(self.get_exc_url(exc.id))
        assert response.templates[0].name == "dashboard/reviewer/ai_modal/ai_modal_initial.html"

    def test_get_pending_rule_generation_shows_rule_data(self):
        exc = self._make_exception()
        AIRecommendation.objects.create(
            recommendation_type=AIRecommendation.RecommendationType.RULE_GENERATION,
            exception=exc,
            suggested_rule_data={"rule_code": "X_1", "field_name": "loan_id"},
            suggested_value="X_1",
            explanation="expl",
            confidence_score=0.5,
            created_by=self.reviewer,
            status=AIRecommendation.RecommendationStatus.PENDING,
        )
        response = self._auth_client().get(self.get_exc_url(exc.id))
        assert response.status_code == 200
        assert (
            response.templates[0].name
            == "dashboard/reviewer/ai_modal/ai_modal_pending_wrapper.html"
        )
        rule_data = response.context["rule_data"]
        assert rule_data["rule_code"] == "X_1"

    # Edge
    def test_get_non_existent_exception_renders_initial_modal(self):
        response = self._auth_client().get(self.get_exc_url(99999))
        assert response.status_code == 200
        assert response.context["exc"] is None

    def test_rule_recommendation_no_field_name_target_is_general(self):
        exc = self._make_exception(field_name="")
        AIRecommendation.objects.create(
            recommendation_type=AIRecommendation.RecommendationType.RULE_GENERATION,
            exception=exc,
            suggested_rule_data={},
            suggested_value="",
            explanation="expl",
            confidence_score=0.5,
            created_by=self.reviewer,
            status=AIRecommendation.RecommendationStatus.PENDING,
        )
        response = self._auth_client().get(self.get_exc_url(exc.id))
        assert response.status_code == 200
        assert response.context["target_field_name"] == "general"


# ────────────────────────────────────────────────────────────────────────────
# GenerateAIRuleView
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestGenerateAIRuleView(AIViewsTestBase):
    """Test cases for GenerateAIRuleView (`/reviewer/ai/rules/generate/`)."""

    def get_url(self):
        return reverse("generate_ai_rule")

    # Auth gating (negative)
    def test_post_unauthenticated_redirects_to_login(self):
        response = self.client.post(self.get_url(), {"prompt_text": "rule"})
        assert response.status_code == 302
        assert self.login_url in response.url

    def test_post_superuser_denied(self):
        superuser = UserFactory.create_superuser(username="ai_rule_admin")
        assert (
            self._auth_client(superuser).post(self.get_url(), {"prompt_text": "x"}).status_code
            == 403
        )

    def test_post_data_operator_denied(self):
        operator = UserFactory.create_data_operator(username="ai_rule_op")
        assert (
            self._auth_client(operator).post(self.get_url(), {"prompt_text": "x"}).status_code
            == 403
        )

    def test_post_data_consumer_denied(self):
        consumer = UserFactory.create_data_consumer(username="ai_rule_consumer")
        assert (
            self._auth_client(consumer).post(self.get_url(), {"prompt_text": "x"}).status_code
            == 403
        )

    # Method enforcement (negative)
    def test_get_returns_method_not_allowed(self):
        assert self._auth_client().get(self.get_url()).status_code == 405

    # Positive
    @mock.patch(f"{VIEWS}.generate_ai_rule_recommendation")
    def test_post_renders_response_template(self, mock_gen):
        mock_gen.return_value = self._make_rule_recommendation()
        response = self._auth_client().post(self.get_url(), {"prompt_text": "make a rule"})
        assert response.status_code == 200
        assert response.templates[0].name == "dashboard/reviewer/ai_modal/ai_modal_response.html"
        assert response.context["target_field_name"] == "current_balance"

    @mock.patch(f"{VIEWS}.generate_ai_rule_recommendation")
    def test_post_passes_prompt_and_model_choice(self, mock_gen):
        mock_gen.return_value = self._make_rule_recommendation()
        self._auth_client().post(
            self.get_url(), {"prompt_text": "  my prompt  ", "model_choice": "4"}
        )
        kwargs = mock_gen.call_args.kwargs
        assert kwargs["prompt_text"] == "my prompt"
        assert kwargs["model_choice"] == 4

    @mock.patch(f"{VIEWS}.generate_ai_rule_recommendation")
    def test_post_default_model_when_missing(self, mock_gen):
        mock_gen.return_value = self._make_rule_recommendation()
        self._auth_client().post(self.get_url(), {"prompt_text": "rule"})
        assert (
            mock_gen.call_args.kwargs["model_choice"] == AIRecommendation.ModelProvider.OPENCODE_ZEN
        )

    @mock.patch(f"{VIEWS}.generate_ai_rule_recommendation")
    def test_post_non_numeric_model_choice_falls_back_to_default(self, mock_gen):
        mock_gen.return_value = self._make_rule_recommendation()
        self._auth_client().post(self.get_url(), {"prompt_text": "rule", "model_choice": "abc"})
        assert (
            mock_gen.call_args.kwargs["model_choice"] == AIRecommendation.ModelProvider.OPENCODE_ZEN
        )

    # Negative / invalid
    @mock.patch(f"{VIEWS}.generate_ai_rule_recommendation")
    def test_post_blank_prompt_uses_default_prompt(self, mock_gen):
        mock_gen.return_value = self._make_rule_recommendation()
        self._auth_client().post(self.get_url(), {"prompt_text": "   "})
        default_prompt = "Validate loan record for missing fields, inconsistent balances, or invalid payment statuses."
        assert mock_gen.call_args.kwargs["prompt_text"] == default_prompt

    @mock.patch(f"{VIEWS}.generate_ai_rule_recommendation")
    def test_post_with_exception_id_sets_exc_context(self, mock_gen):
        exc = self._make_exception()
        mock_gen.return_value = self._make_rule_recommendation()
        response = self._auth_client().post(
            self.get_url(), {"prompt_text": "rule", "exception_id": str(exc.id)}
        )
        assert response.context["exc"].id == exc.id

    @mock.patch(f"{VIEWS}.generate_ai_rule_recommendation")
    def test_post_with_invalid_exception_id_keeps_exc_none(self, mock_gen):
        mock_gen.return_value = self._make_rule_recommendation()
        response = self._auth_client().post(
            self.get_url(), {"prompt_text": "rule", "exception_id": "notanumber"}
        )
        assert response.context["exc"] is None

    @mock.patch(f"{VIEWS}.generate_ai_rule_recommendation")
    def test_post_with_non_existent_exception_id_keeps_exc_none(self, mock_gen):
        mock_gen.return_value = self._make_rule_recommendation()
        response = self._auth_client().post(
            self.get_url(), {"prompt_text": "rule", "exception_id": "99999"}
        )
        assert response.context["exc"] is None

    @mock.patch(f"{VIEWS}.generate_ai_rule_recommendation")
    def test_rule_data_exposed_in_context(self, mock_gen):
        mock_gen.return_value = self._make_rule_recommendation()
        response = self._auth_client().post(self.get_url(), {"prompt_text": "rule"})
        assert response.context["rule_data"]["rule_code"] == "AI_RULE_1"


# ────────────────────────────────────────────────────────────────────────────
# GenerateAIRecommendationView
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestGenerateAIRecommendationView(AIViewsTestBase):
    """Test cases for GenerateAIRecommendationView (`/reviewer/exceptions/<pk>/ai/generate/`)."""

    def get_url(self, pk):
        return reverse("generate_ai_recommendation", args=[pk])

    # Auth gating (negative)
    def test_post_unauthenticated_redirects_to_login(self):
        exc = self._make_exception()
        response = self.client.post(self.get_url(exc.id))
        assert response.status_code == 302
        assert self.login_url in response.url

    def test_post_superuser_denied(self):
        superuser = UserFactory.create_superuser(username="ai_gen_admin")
        exc = self._make_exception()
        assert self._auth_client(superuser).post(self.get_url(exc.id)).status_code == 403

    def test_post_data_operator_denied(self):
        operator = UserFactory.create_data_operator(username="ai_gen_op")
        exc = self._make_exception()
        assert self._auth_client(operator).post(self.get_url(exc.id)).status_code == 403

    def test_post_data_consumer_denied(self):
        consumer = UserFactory.create_data_consumer(username="ai_gen_consumer")
        exc = self._make_exception()
        assert self._auth_client(consumer).post(self.get_url(exc.id)).status_code == 403

    # Method enforcement (negative)
    def test_get_returns_method_not_allowed(self):
        exc = self._make_exception()
        assert self._auth_client().get(self.get_url(exc.id)).status_code == 405

    # Positive
    @mock.patch(f"{VIEWS}.generate_exception_ai_recommendation")
    def test_post_hx_renders_response_template(self, mock_gen):
        exc = self._make_exception()
        mock_gen.return_value = self._make_exception_recommendation(exception=exc)
        response = self._auth_client().post(self.get_url(exc.id), {}, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert response.templates[0].name == "dashboard/reviewer/ai_modal/ai_modal_response.html"
        assert response.context["exc"].id == exc.id

    @mock.patch(f"{VIEWS}.generate_exception_ai_recommendation")
    def test_post_non_hx_redirects_to_exception_detail(self, mock_gen):
        exc = self._make_exception()
        mock_gen.return_value = self._make_exception_recommendation(exception=exc)
        response = self._auth_client().post(self.get_url(exc.id), {})
        assert response.status_code == 302
        assert reverse("exception_loan_detail", args=[exc.id]) in response.url

    @mock.patch(f"{VIEWS}.generate_exception_ai_recommendation")
    def test_post_passes_model_choice(self, mock_gen):
        exc = self._make_exception()
        mock_gen.return_value = self._make_exception_recommendation(exception=exc)
        self._auth_client().post(self.get_url(exc.id), {"model_choice": "1"})
        assert mock_gen.call_args.kwargs["model_choice"] == 1

    @mock.patch(f"{VIEWS}.generate_exception_ai_recommendation")
    def test_post_missing_model_choice_defaults(self, mock_gen):
        exc = self._make_exception()
        mock_gen.return_value = self._make_exception_recommendation(exception=exc)
        self._auth_client().post(self.get_url(exc.id), {})
        assert (
            mock_gen.call_args.kwargs["model_choice"] == AIRecommendation.ModelProvider.OPENCODE_ZEN
        )

    @mock.patch(f"{VIEWS}.generate_exception_ai_recommendation")
    def test_post_invalid_model_choice_falls_back(self, mock_gen):
        exc = self._make_exception()
        mock_gen.return_value = self._make_exception_recommendation(exception=exc)
        self._auth_client().post(self.get_url(exc.id), {"model_choice": "zzz"})
        assert (
            mock_gen.call_args.kwargs["model_choice"] == AIRecommendation.ModelProvider.OPENCODE_ZEN
        )

    @mock.patch(f"{VIEWS}.generate_exception_ai_recommendation")
    def test_post_target_field_value_in_context(self, mock_gen):
        exc = self._make_exception(field_name="current_balance", raw_data={"loan_id": "LG-1"})
        mock_gen.return_value = self._make_exception_recommendation(exception=exc)
        response = self._auth_client().post(self.get_url(exc.id), {}, HTTP_HX_REQUEST="true")
        assert response.context["target_field_name"] == "current_balance"

    # Invalid
    def test_post_nonexistent_exception_404(self):
        assert self._auth_client().post(self.get_url(99999)).status_code == 404


# ────────────────────────────────────────────────────────────────────────────
# ProcessAIRecommendationView
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestProcessAIRecommendationView(AIViewsTestBase):
    """Test cases for ProcessAIRecommendationView (`/reviewer/ai/<pk>/decision/`)."""

    def get_url(self, pk):
        return reverse("process_ai_recommendation", args=[pk])

    # Auth gating (negative)
    def test_post_unauthenticated_redirects_to_login(self):
        rec = self._make_exception_recommendation()
        response = self.client.post(self.get_url(rec.id), {"action": "accept"})
        assert response.status_code == 302
        assert self.login_url in response.url

    def test_post_superuser_denied(self):
        superuser = UserFactory.create_superuser(username="ai_proc_admin")
        rec = self._make_exception_recommendation()
        assert (
            self._auth_client(superuser)
            .post(self.get_url(rec.id), {"action": "accept"})
            .status_code
            == 403
        )

    def test_post_data_operator_denied(self):
        operator = UserFactory.create_data_operator(username="ai_proc_op")
        rec = self._make_exception_recommendation()
        assert (
            self._auth_client(operator).post(self.get_url(rec.id), {"action": "accept"}).status_code
            == 403
        )

    def test_post_data_consumer_denied(self):
        consumer = UserFactory.create_data_consumer(username="ai_proc_consumer")
        rec = self._make_exception_recommendation()
        assert (
            self._auth_client(consumer).post(self.get_url(rec.id), {"action": "accept"}).status_code
            == 403
        )

    # Method enforcement (negative)
    def test_get_returns_method_not_allowed(self):
        rec = self._make_exception_recommendation()
        assert self._auth_client().get(self.get_url(rec.id)).status_code == 405

    # Invalid
    def test_post_nonexistent_recommendation_404(self):
        assert (
            self._auth_client().post(self.get_url(99999), {"action": "accept"}).status_code == 404
        )

    # Positive: exception recommendation decision handling
    @mock.patch(f"{VIEWS}.process_ai_recommendation_decision", return_value=(True, "accepted"))
    def test_post_exception_accept_non_hx_redirects(self, mock_proc):
        exc = self._make_exception()
        rec = self._make_exception_recommendation(exception=exc)
        response = self._auth_client().post(self.get_url(rec.id), {"action": "accept"})
        assert response.status_code == 302
        assert f"/reviewer/exceptions/{exc.id}/detail/" in response.url
        call_args = mock_proc.call_args
        assert call_args.kwargs["action"] == "accept"

    @mock.patch(f"{VIEWS}.process_ai_recommendation_decision", return_value=(True, "accepted"))
    def test_post_hx_returns_204_with_redirect_header(self, mock_proc):
        exc = self._make_exception()
        rec = self._make_exception_recommendation(exception=exc)
        response = self._auth_client().post(
            self.get_url(rec.id), {"action": "accept"}, HTTP_HX_REQUEST="true"
        )
        assert response.status_code == 204
        assert response.headers.get("HX-Redirect") == f"/reviewer/exceptions/{exc.id}/detail/"

    @mock.patch(f"{VIEWS}.process_ai_recommendation_decision", return_value=(False, "bad action"))
    def test_post_failed_decision_still_redirects(self, mock_proc):
        exc = self._make_exception()
        rec = self._make_exception_recommendation(exception=exc)
        response = self._auth_client().post(self.get_url(rec.id), {"action": "reject"})
        assert response.status_code == 302
        assert mock_proc.called

    @mock.patch(f"{VIEWS}.process_ai_recommendation_decision", return_value=(True, "ok"))
    def test_post_passes_edited_value_and_comment(self, mock_proc):
        exc = self._make_exception()
        rec = self._make_exception_recommendation(exception=exc)
        self._auth_client().post(
            self.get_url(rec.id),
            {"action": "edit", "reviewer_comment": "  note  ", "edited_value": " 4567 "},
        )
        kwargs = mock_proc.call_args.kwargs
        assert kwargs["action"] == "edit"
        assert kwargs["comment"] == "note"
        assert kwargs["edited_value"] == "4567"

    @mock.patch(f"{VIEWS}.process_ai_recommendation_decision", return_value=(True, "ok"))
    def test_post_referer_takes_priority_over_exception_url(self, mock_proc):
        exc = self._make_exception()
        rec = self._make_exception_recommendation(exception=exc)
        response = self._auth_client().post(
            self.get_url(rec.id), {"action": "accept"}, HTTP_REFERER="/reviewer/"
        )
        assert response.status_code == 302
        assert response.url == "/reviewer/"

    # Positive: rule generation decision handling (edit path builds rule payload)
    @mock.patch(f"{VIEWS}.process_ai_recommendation_decision", return_value=(True, "ok"))
    def test_post_rule_edit_reads_form_fields(self, mock_proc):
        rec = self._make_rule_recommendation()
        self._auth_client().post(
            self.get_url(rec.id),
            {
                "action": "edit",
                "rule_code": "NEW_RULE",
                "rule_name": "Name",
                "field_name": "current_balance",
                "severity": "3",
                "strategy_key": "BALANCE_RANGE",
            },
        )
        edited = mock_proc.call_args.kwargs["edited_rule_data"]
        assert edited["rule_code"] == "NEW_RULE"
        assert edited["severity"] == 3
        assert edited["strategy_key"] == "BALANCE_RANGE"

    @mock.patch(f"{VIEWS}.process_ai_recommendation_decision", return_value=(True, "ok"))
    def test_post_rule_edit_non_numeric_severity_defaults(self, mock_proc):
        rec = self._make_rule_recommendation()
        self._auth_client().post(
            self.get_url(rec.id),
            {"action": "edit", "severity": "banana"},
        )
        edited = mock_proc.call_args.kwargs["edited_rule_data"]
        assert edited["severity"] == 2

    @mock.patch(f"{VIEWS}.process_ai_recommendation_decision", return_value=(True, "ok"))
    def test_post_rule_json_raw_parsed(self, mock_proc):
        rec = self._make_rule_recommendation()
        payload = {
            "rule_code": "J1",
            "rule_name": "Json Rule",
            "field_name": "field",
            "severity": 3,
            "strategy_key": "k",
        }
        self._auth_client().post(
            self.get_url(rec.id),
            {"action": "accept", "rule_json_raw": json.dumps(payload)},
        )
        assert mock_proc.call_args.kwargs["edited_rule_data"] == payload

    @mock.patch(f"{VIEWS}.process_ai_recommendation_decision", return_value=(True, "ok"))
    def test_post_rule_json_identical_to_original_action_unchanged(self, mock_proc):
        rec = self._make_rule_recommendation()
        original = rec.suggested_rule_data
        self._auth_client().post(
            self.get_url(rec.id),
            {"action": "accept", "rule_json_raw": json.dumps(original)},
        )
        # identical payload should not switch action to edit
        assert mock_proc.call_args.kwargs["action"] == "accept"

    @mock.patch(f"{VIEWS}.process_ai_recommendation_decision", return_value=(True, "ok"))
    def test_post_rule_json_different_from_original_switches_to_edit(self, mock_proc):
        rec = self._make_rule_recommendation()
        changed = dict(rec.suggested_rule_data)
        changed["rule_name"] = "Changed"
        self._auth_client().post(
            self.get_url(rec.id),
            {"action": "accept", "rule_json_raw": json.dumps(changed)},
        )
        assert mock_proc.call_args.kwargs["action"] == "edit"

    @mock.patch(f"{VIEWS}.process_ai_recommendation_decision")
    def test_post_invalid_rule_json_renders_error_response(self, mock_proc):
        rec = self._make_rule_recommendation()
        response = self._auth_client().post(
            self.get_url(rec.id),
            {"action": "accept", "rule_json_raw": "{not valid json"},
        )
        assert response.status_code == 200
        assert response.templates[0].name == "dashboard/reviewer/ai_modal/ai_modal_response.html"
        assert "json_error" in response.context
        mock_proc.assert_not_called()

    def test_post_rule_generation_recommendation_edit_with_no_data(self):
        rec = self._make_rule_recommendation()
        # edit with no rule_json_raw and no per-field form data -> empty rule payload dict
        with mock.patch(
            f"{VIEWS}.process_ai_recommendation_decision", return_value=(True, "ok")
        ) as mock_proc:
            self._auth_client().post(self.get_url(rec.id), {"action": "edit"})
        assert mock_proc.called
        edited = mock_proc.call_args.kwargs["edited_rule_data"]
        assert edited["rule_code"] == ""
        assert edited["severity"] == 2
