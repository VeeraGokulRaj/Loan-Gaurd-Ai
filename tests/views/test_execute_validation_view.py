"""
Test cases for app.views.data_operator.ExecuteValidationView.

Covers authentication gating, role-based access denial, HTTP method enforcement,
target batch resolution (single batch_id vs comma-separated batch_ids), auto-seeding
of validation rules, execution metrics, severity breakdown, audit logging, and
invalid-input fallbacks (400 / 404).
"""

import pytest
from django.test import Client
from django.urls import reverse

from app.models import (
    AuditEvent,
    LoanException,
    RawLoanRecord,
    UploadBatch,
    ValidationRule,
    ValidationSeverity,
)
from tests.factory.user_factory import UserFactory


@pytest.mark.django_db
class TestExecuteValidationView:
    """Test cases for ExecuteValidationView (`/validation/execute/`)."""

    def setup_method(self):
        self.client = Client(enforce_csrf_checks=False)
        self.url = reverse("execute_validation")
        self.login_url = reverse("login")
        self.operator = UserFactory.create_data_operator(username="exec_op")

    def _auth_client(self, user=None):
        client = Client(enforce_csrf_checks=False)
        client.force_login(user or self.operator)
        return client

    def _make_batch(self, status=UploadBatch.BatchStatus.INGESTED, n_records=0, raw_data=None):
        batch = UploadBatch.objects.create(
            uploaded_by=self.operator,
            file_name="loan_tape.csv",
            source_type=UploadBatch.SourceType.LOAN_TAPE,
            status=status,
        )
        for i in range(n_records):
            RawLoanRecord.objects.create(
                batch=batch,
                row_number=i + 2,
                raw_data=raw_data if raw_data is not None else {"loan_id": ""},
            )
        return batch

    def _make_rule(
        self,
        rule_code="VAL_T1",
        strategy_key="MISSING_LOAN_ID",
        field_name="loan_id",
        severity=ValidationSeverity.CRITICAL,
    ):
        return ValidationRule.objects.create(
            rule_code=rule_code,
            strategy_key=strategy_key,
            rule_name=f"Rule {rule_code}",
            field_name=field_name,
            description="Test rule",
            severity=severity,
            is_active=True,
        )

    # ── Authentication & Permission Gating (Negative) ──

    def test_post_unauthenticated_redirects_to_login(self):
        response = self.client.post(self.url, {"batch_id": "1"})
        assert response.status_code == 302
        assert self.login_url in response.url

    def test_get_unauthenticated_redirects_to_login(self):
        response = self.client.get(self.url)
        assert response.status_code == 302
        assert self.login_url in response.url

    def test_post_superuser_denied(self):
        superuser = UserFactory.create_superuser(username="exec_admin")
        response = self._auth_client(superuser).post(self.url, {"batch_id": "1"})
        assert response.status_code == 403

    def test_post_reviewer_denied(self):
        reviewer = UserFactory.create_reviewer(username="exec_reviewer")
        response = self._auth_client(reviewer).post(self.url, {"batch_id": "1"})
        assert response.status_code == 403

    def test_post_data_consumer_denied(self):
        consumer = UserFactory.create_data_consumer(username="exec_consumer")
        response = self._auth_client(consumer).post(self.url, {"batch_id": "1"})
        assert response.status_code == 403

    # ── HTTP Method Enforcement (Negative) ──

    def test_get_with_login_returns_method_not_allowed(self):
        response = self._auth_client().get(self.url)
        assert response.status_code == 405
        assert "POST" in response.headers.get("Allow", "")

    # ── Batch Resolution Failures (Invalid / Negative) ──

    def test_post_without_batch_reference_returns_400(self):
        response = self._auth_client().post(self.url, data={})
        assert response.status_code == 400
        assert b"No valid upload batches" in response.content

    def test_post_with_non_existent_single_batch_id_returns_404(self):
        response = self._auth_client().post(self.url, {"batch_id": "999999"})
        assert response.status_code == 404

    def test_post_batch_ids_all_non_numeric_returns_400(self):
        self._make_batch(n_records=1)
        response = self._auth_client().post(self.url, {"batch_ids": "abc,xyz"})
        assert response.status_code == 400

    def test_post_batch_id_zero_returns_404(self):
        response = self._auth_client().post(self.url, {"batch_id": "0"})
        assert response.status_code == 404

    def test_post_batch_ids_all_failed_batches_returns_400(self):
        failed = self._make_batch(status=UploadBatch.BatchStatus.FAILED, n_records=1)
        self._make_rule()
        response = self._auth_client().post(self.url, {"batch_ids": str(failed.id)})
        assert response.status_code == 400

    def test_batch_ids_route_excludes_failed_batches(self):
        failed = self._make_batch(status=UploadBatch.BatchStatus.FAILED, n_records=1)
        good = self._make_batch(status=UploadBatch.BatchStatus.INGESTED, n_records=1)
        self._make_rule()
        response = self._auth_client().post(self.url, {"batch_ids": f"{failed.id},{good.id}"})
        assert response.status_code == 200
        assert response.context["processed_batch_ids"] == [good.id]
        assert response.context["total_records_evaluated"] == 1

    def test_single_batch_id_route_does_not_exclude_failed_batches(self):
        # Compatibility note: the single batch_id path uses get_object_or_404 and
        # does NOT skip FAILED status batches (unlike the batch_ids route).
        failed = self._make_batch(status=UploadBatch.BatchStatus.FAILED, n_records=1)
        self._make_rule()
        response = self._auth_client().post(self.url, {"batch_id": str(failed.id)})
        assert response.status_code == 200
        assert response.context["processed_batch_ids"] == [failed.id]

    # ── Positive Execution ──

    def test_post_executes_and_renders_progress_partial(self):
        batch = self._make_batch(n_records=2)
        self._make_rule()
        response = self._auth_client().post(self.url, {"batch_id": str(batch.id)})
        assert response.status_code == 200
        assert response.templates[0].name == (
            "dashboard/operator/includes/validation_progress_partial.html"
        )

    def test_post_context_reports_metrics(self):
        batch = self._make_batch(n_records=3)
        self._make_rule()
        response = self._auth_client().post(self.url, {"batch_id": str(batch.id)})
        ctx = response.context
        assert ctx["total_records_evaluated"] == 3
        assert ctx["total_exceptions_created"] == 3
        assert ctx["active_rules_count"] == 1
        assert ctx["processed_batch_ids"] == [batch.id]
        assert ctx["execution_time_ms"] >= 0

    def test_post_creates_open_exceptions(self):
        batch = self._make_batch(n_records=2)
        rule = self._make_rule()
        self._auth_client().post(self.url, {"batch_id": str(batch.id)})
        exceptions = LoanException.objects.filter(batch=batch)
        assert exceptions.count() == 2
        assert all(exc.status == LoanException.ExceptionStatus.OPEN for exc in exceptions)
        assert all(exc.rule_id == rule.id for exc in exceptions)
        assert all(exc.rule_code == "VAL_T1" for exc in exceptions)

    def test_post_batch_ids_processes_multiple_batches(self):
        batch_a = self._make_batch(n_records=1, raw_data={"loan_id": ""})
        batch_b = self._make_batch(n_records=2, raw_data={"loan_id": "LG-1"})
        self._make_rule()
        response = self._auth_client().post(self.url, {"batch_ids": f"{batch_a.id},{batch_b.id}"})
        ctx = response.context
        assert set(ctx["processed_batch_ids"]) == {batch_a.id, batch_b.id}
        assert ctx["total_records_evaluated"] == 3
        assert ctx["total_exceptions_created"] == 1

    def test_post_batch_ids_with_mixed_valid_and_invalid_tokens(self):
        batch = self._make_batch(n_records=1)
        self._make_rule()
        response = self._auth_client().post(self.url, {"batch_ids": f"abc,{batch.id},xyz"})
        assert response.status_code == 200
        assert response.context["processed_batch_ids"] == [batch.id]

    def test_post_get_batch_id_parameter_supported(self):
        batch = self._make_batch(n_records=1)
        self._make_rule()
        response = self._auth_client().get(self.url, {"batch_id": str(batch.id)})
        assert response.status_code == 405

    def test_post_severity_breakdown_counts(self):
        batch = self._make_batch(n_records=2)
        self._make_rule(severity=ValidationSeverity.CRITICAL)
        self._make_rule(
            rule_code="VAL_T2",
            strategy_key="INVALID_STATE_CODE",
            field_name="borrower_state",
            severity=ValidationSeverity.MEDIUM,
        )
        # Rows carry an invalid state code so VAL_T2 also fires.
        RawLoanRecord.objects.filter(batch=batch).update(
            raw_data={"loan_id": "", "borrower_state": "ZZ"}
        )
        response = self._auth_client().post(self.url, {"batch_id": str(batch.id)})
        ctx = response.context
        assert ctx["total_exceptions_created"] == 4
        assert ctx["critical_count"] == 2
        assert ctx["medium_count"] == 2
        assert ctx["high_count"] == 0
        assert ctx["low_count"] == 0

    def test_post_logs_audit_event(self):
        batch = self._make_batch(n_records=2)
        self._make_rule()
        self._auth_client().post(self.url, {"batch_id": str(batch.id)})
        event = AuditEvent.objects.filter(event_type="VALIDATION_ENGINE_EXECUTED").get()
        assert event.actor == self.operator
        assert event.payload["batch_ids"] == [batch.id]
        assert event.payload["total_records_evaluated"] == 2
        assert event.payload["exceptions_flagged"] == 2
        assert event.payload["active_rules_evaluated"] == 1

    def test_post_auto_seeds_rules_when_none_exist(self):
        assert ValidationRule.objects.count() == 0
        batch = self._make_batch(n_records=1)
        response = self._auth_client().post(self.url, {"batch_id": str(batch.id)})
        assert ValidationRule.objects.count() >= 15
        assert response.context["active_rules_count"] >= 15

    def test_post_on_clean_records_creates_zero_exceptions(self):
        batch = self._make_batch(n_records=1, raw_data={"loan_id": "LG-0001"})
        self._make_rule()
        response = self._auth_client().post(self.url, {"batch_id": str(batch.id)})
        assert response.context["total_exceptions_created"] == 0
        assert response.context["total_records_evaluated"] == 1

    def test_post_empty_batch_creates_no_exceptions(self):
        batch = self._make_batch(n_records=0)
        self._make_rule()
        response = self._auth_client().post(self.url, {"batch_id": str(batch.id)})
        assert response.status_code == 200
        assert response.context["total_records_evaluated"] == 0
        assert response.context["total_exceptions_created"] == 0
