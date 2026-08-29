"""
Test cases for app.views.data_operator.ExecuteValidationView.

Covers authentication gating, role-based access denial, HTTP method enforcement,
the comma-separated batch_ids resolution path, the new 3-file-type requirement
(LOAN_TAPE + SERVICER_UPDATE + DOCUMENT_MANIFEST must all be selected), the
include_db_history toggle, execution metrics, severity breakdown, audit logging,
and invalid-input fallbacks (400 / 404).
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

    def _make_batch(
        self,
        source_type=UploadBatch.SourceType.LOAN_TAPE,
        status=UploadBatch.BatchStatus.INGESTED,
        n_records=0,
        raw_data=None,
    ):
        batch = UploadBatch.objects.create(
            uploaded_by=self.operator,
            file_name="file.csv",
            source_type=source_type,
            status=status,
        )
        for i in range(n_records):
            RawLoanRecord.objects.create(
                batch=batch,
                row_number=i + 2,
                raw_data=raw_data if raw_data is not None else {"loan_id": ""},
            )
        return batch

    def _make_full_set(self, n_records=0, raw_data=None):
        """Creates the 3 source batches required for validation to run."""
        tape = self._make_batch(
            source_type=UploadBatch.SourceType.LOAN_TAPE,
            n_records=n_records,
            raw_data=raw_data,
        )
        servicer = self._make_batch(source_type=UploadBatch.SourceType.SERVICER_UPDATE)
        manifest = self._make_batch(source_type=UploadBatch.SourceType.DOCUMENT_MANIFEST)
        return tape, servicer, manifest

    def _csv_batch_ids(self, *batches):
        return ",".join(str(b.id) for b in batches)

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
        response = self.client.post(self.url, {"batch_ids": "1"})
        assert response.status_code == 302
        assert self.login_url in response.url

    def test_get_unauthenticated_redirects_to_login(self):
        response = self.client.get(self.url)
        assert response.status_code == 302
        assert self.login_url in response.url

    def test_post_superuser_denied(self):
        superuser = UserFactory.create_superuser(username="exec_admin")
        response = self._auth_client(superuser).post(self.url, {"batch_ids": "1"})
        assert response.status_code == 403

    def test_post_reviewer_denied(self):
        reviewer = UserFactory.create_reviewer(username="exec_reviewer")
        response = self._auth_client(reviewer).post(self.url, {"batch_ids": "1"})
        assert response.status_code == 403

    def test_post_data_consumer_denied(self):
        consumer = UserFactory.create_data_consumer(username="exec_consumer")
        response = self._auth_client(consumer).post(self.url, {"batch_ids": "1"})
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

    def test_post_single_batch_id_parameter_ignored_returns_400(self):
        # The legacy single `batch_id` route was removed; only `batch_ids` is supported.
        self._make_full_set(n_records=1)
        response = self._auth_client().post(self.url, {"batch_id": "999999"})
        assert response.status_code == 400

    def test_post_batch_id_zero_returns_400(self):
        response = self._auth_client().post(self.url, {"batch_id": "0"})
        assert response.status_code == 400

    def test_post_batch_ids_all_non_numeric_returns_400(self):
        self._make_full_set(n_records=1)
        response = self._auth_client().post(self.url, {"batch_ids": "abc,xyz"})
        assert response.status_code == 400

    def test_post_batch_ids_all_failed_batches_returns_400(self):
        failed = self._make_batch(status=UploadBatch.BatchStatus.FAILED, n_records=1)
        self._make_rule()
        response = self._auth_client().post(self.url, {"batch_ids": str(failed.id)})
        assert response.status_code == 400

    def test_post_batch_ids_excludes_failed_batches(self):
        failed = self._make_batch(status=UploadBatch.BatchStatus.FAILED, n_records=1)
        tape, servicer, manifest = self._make_full_set(n_records=1)
        self._make_rule()
        response = self._auth_client().post(
            self.url,
            {"batch_ids": self._csv_batch_ids(failed, tape, servicer, manifest)},
        )
        assert response.status_code == 200
        assert response.context["processed_batch_ids"] == [tape.id]
        assert response.context["total_records_evaluated"] == 1

    # ── 3-File-Type Requirement (Negative / Edge) ──

    def test_post_loan_tape_only_returns_400(self):
        tape = self._make_batch(source_type=UploadBatch.SourceType.LOAN_TAPE, n_records=1)
        response = self._auth_client().post(self.url, {"batch_ids": str(tape.id)})
        assert response.status_code == 400
        assert b"all 3 CSV files" in response.content

    def test_post_two_of_three_source_types_returns_400(self):
        tape = self._make_batch(source_type=UploadBatch.SourceType.LOAN_TAPE, n_records=1)
        servicer = self._make_batch(source_type=UploadBatch.SourceType.SERVICER_UPDATE)
        response = self._auth_client().post(
            self.url, {"batch_ids": self._csv_batch_ids(tape, servicer)}
        )
        assert response.status_code == 400
        assert b"all 3 CSV files" in response.content

    # ── Positive Execution ──

    def test_post_executes_and_renders_progress_partial(self):
        tape, servicer, manifest = self._make_full_set(n_records=2)
        self._make_rule()
        response = self._auth_client().post(
            self.url,
            {"batch_ids": self._csv_batch_ids(tape, servicer, manifest)},
        )
        assert response.status_code == 200
        assert response.templates[0].name == (
            "dashboard/operator/includes/validation_progress_partial.html"
        )

    def test_post_context_reports_metrics(self):
        tape, servicer, manifest = self._make_full_set(n_records=3)
        self._make_rule()
        response = self._auth_client().post(
            self.url,
            {"batch_ids": self._csv_batch_ids(tape, servicer, manifest)},
        )
        ctx = response.context
        assert ctx["total_records_evaluated"] == 3
        assert ctx["total_exceptions_created"] == 3
        assert ctx["active_rules_count"] == 1
        assert ctx["processed_batch_ids"] == [tape.id]
        assert ctx["execution_time_ms"] >= 0

    def test_post_creates_open_exceptions(self):
        tape, servicer, manifest = self._make_full_set(n_records=2)
        rule = self._make_rule()
        self._auth_client().post(
            self.url, {"batch_ids": self._csv_batch_ids(tape, servicer, manifest)}
        )
        exceptions = LoanException.objects.filter(batch=tape)
        assert exceptions.count() == 2
        assert all(exc.status == LoanException.ExceptionStatus.OPEN for exc in exceptions)
        assert all(exc.rule_id == rule.id for exc in exceptions)
        assert all(exc.rule_code == "VAL_T1" for exc in exceptions)

    def test_post_only_loan_tape_batch_is_processed(self):
        # Even though all 3 files are selected, only the LOAN_TAPE batch is validated.
        tape, servicer, manifest = self._make_full_set(n_records=2)
        self._make_rule()
        response = self._auth_client().post(
            self.url,
            {"batch_ids": self._csv_batch_ids(tape, servicer, manifest)},
        )
        assert response.context["processed_batch_ids"] == [tape.id]
        # Only the loan tape carried raw records.
        assert RawLoanRecord.objects.filter(batch=servicer).count() == 0
        assert RawLoanRecord.objects.filter(batch=manifest).count() == 0

    def test_post_batch_ids_with_mixed_valid_and_invalid_tokens(self):
        tape, servicer, manifest = self._make_full_set(n_records=1)
        self._make_rule()
        response = self._auth_client().post(
            self.url,
            {"batch_ids": f"abc,{self._csv_batch_ids(tape, servicer, manifest)},xyz"},
        )
        assert response.status_code == 200
        assert response.context["processed_batch_ids"] == [tape.id]

    def test_post_processes_multiple_loan_tape_batches(self):
        tape_a = self._make_batch(
            source_type=UploadBatch.SourceType.LOAN_TAPE, n_records=1, raw_data={"loan_id": ""}
        )
        tape_b = self._make_batch(
            source_type=UploadBatch.SourceType.LOAN_TAPE,
            n_records=2,
            raw_data={"loan_id": "LG-1"},
        )
        servicer = self._make_batch(source_type=UploadBatch.SourceType.SERVICER_UPDATE)
        manifest = self._make_batch(source_type=UploadBatch.SourceType.DOCUMENT_MANIFEST)
        self._make_rule()
        response = self._auth_client().post(
            self.url,
            {"batch_ids": self._csv_batch_ids(tape_a, tape_b, servicer, manifest)},
        )
        ctx = response.context
        assert set(ctx["processed_batch_ids"]) == {tape_a.id, tape_b.id}
        assert ctx["total_records_evaluated"] == 3
        assert ctx["total_exceptions_created"] == 1

    def test_post_get_batch_id_parameter_supported(self):
        tape = self._make_batch(n_records=1)
        self._make_rule()
        response = self._auth_client().get(self.url, {"batch_id": str(tape.id)})
        assert response.status_code == 405

    def test_post_severity_breakdown_counts(self):
        tape, servicer, manifest = self._make_full_set(n_records=2)
        self._make_rule(severity=ValidationSeverity.CRITICAL)
        self._make_rule(
            rule_code="VAL_T2",
            strategy_key="INVALID_STATE_CODE",
            field_name="borrower_state",
            severity=ValidationSeverity.MEDIUM,
        )
        # Rows carry an invalid state code so VAL_T2 also fires.
        RawLoanRecord.objects.filter(batch=tape).update(
            raw_data={"loan_id": "", "borrower_state": "ZZ"}
        )
        response = self._auth_client().post(
            self.url, {"batch_ids": self._csv_batch_ids(tape, servicer, manifest)}
        )
        ctx = response.context
        assert ctx["total_exceptions_created"] == 4
        assert ctx["critical_count"] == 2
        assert ctx["medium_count"] == 2
        assert ctx["high_count"] == 0
        assert ctx["low_count"] == 0

    def test_post_logs_audit_event(self):
        tape, servicer, manifest = self._make_full_set(n_records=2)
        self._make_rule()
        self._auth_client().post(
            self.url, {"batch_ids": self._csv_batch_ids(tape, servicer, manifest)}
        )
        event = AuditEvent.objects.filter(event_type="VALIDATION_ENGINE_EXECUTED").get()
        assert event.actor == self.operator
        assert event.payload["batch_ids"] == [tape.id]
        assert event.payload["total_records_evaluated"] == 2
        assert event.payload["exceptions_flagged"] == 2
        assert event.payload["active_rules_evaluated"] == 1

    def test_post_auto_seeds_rules_when_none_exist(self):
        assert ValidationRule.objects.count() == 0
        tape, servicer, manifest = self._make_full_set(n_records=1)
        response = self._auth_client().post(
            self.url, {"batch_ids": self._csv_batch_ids(tape, servicer, manifest)}
        )
        assert ValidationRule.objects.count() >= 15
        assert response.context["active_rules_count"] >= 15

    def test_post_on_clean_records_creates_zero_exceptions(self):
        tape, servicer, manifest = self._make_full_set(n_records=1, raw_data={"loan_id": "LG-0001"})
        self._make_rule()
        response = self._auth_client().post(
            self.url, {"batch_ids": self._csv_batch_ids(tape, servicer, manifest)}
        )
        assert response.context["total_exceptions_created"] == 0
        assert response.context["total_records_evaluated"] == 1

    def test_post_empty_batch_creates_no_exceptions(self):
        tape, servicer, manifest = self._make_full_set(n_records=0)
        self._make_rule()
        response = self._auth_client().post(
            self.url, {"batch_ids": self._csv_batch_ids(tape, servicer, manifest)}
        )
        assert response.status_code == 200
        assert response.context["total_records_evaluated"] == 0
        assert response.context["total_exceptions_created"] == 0

    # ── include_db_history toggle ──

    def test_post_include_db_history_on_flags_historical_duplicate(self):
        # Existing historical LOAN_TAPE record for LG-1 in the database.
        historical = UploadBatch.objects.create(
            uploaded_by=self.operator,
            file_name="history.csv",
            source_type=UploadBatch.SourceType.LOAN_TAPE,
            status=UploadBatch.BatchStatus.INGESTED,
        )
        RawLoanRecord.objects.create(batch=historical, row_number=1, raw_data={"loan_id": "LG-1"})

        tape, servicer, manifest = self._make_full_set(n_records=1, raw_data={"loan_id": "LG-1"})
        self._make_rule(strategy_key="DUPLICATE_LOAN_ID", field_name="loan_id")

        response = self._auth_client().post(
            self.url,
            {
                "batch_ids": self._csv_batch_ids(tape, servicer, manifest),
                "include_db_history": "on",
            },
        )
        # Historical (1) + current (1) = 2 occurrences -> duplicate flagged.
        assert response.context["total_exceptions_created"] == 1

    def test_post_include_db_history_off_ignores_historical_duplicate(self):
        historical = UploadBatch.objects.create(
            uploaded_by=self.operator,
            file_name="history.csv",
            source_type=UploadBatch.SourceType.LOAN_TAPE,
            status=UploadBatch.BatchStatus.INGESTED,
        )
        RawLoanRecord.objects.create(batch=historical, row_number=1, raw_data={"loan_id": "LG-1"})

        tape, servicer, manifest = self._make_full_set(n_records=1, raw_data={"loan_id": "LG-1"})
        self._make_rule(strategy_key="DUPLICATE_LOAN_ID", field_name="loan_id")

        response = self._auth_client().post(
            self.url,
            {"batch_ids": self._csv_batch_ids(tape, servicer, manifest)},
        )
        # History not included -> only a single occurrence of LG-1, no duplicate.
        assert response.context["total_exceptions_created"] == 0

    def test_post_include_db_history_false_keyword_treated_as_off(self):
        tape, servicer, manifest = self._make_full_set(n_records=1, raw_data={"loan_id": "LG-1"})
        self._make_rule(strategy_key="DUPLICATE_LOAN_ID", field_name="loan_id")
        response = self._auth_client().post(
            self.url,
            {
                "batch_ids": self._csv_batch_ids(tape, servicer, manifest),
                "include_db_history": "false",
            },
        )
        assert response.context["total_exceptions_created"] == 0
