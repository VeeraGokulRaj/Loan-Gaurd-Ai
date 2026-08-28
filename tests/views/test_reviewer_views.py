"""
Test cases for app.views.reviewer.ReviewerDashboardView and LoanExceptionListView.

Covers authentication gating, role-based access, HTTP method enforcement,
metrics/severity summary computation, HTMX pagination, filtering (severity,
status, batch_id, search), edge cases and invalid input fallbacks for the
Reviewer Workspace exception queue.
"""

import pytest
from django.test import Client
from django.urls import reverse

from app.models import (
    LoanException,
    RawLoanRecord,
    UploadBatch,
    ValidationRule,
    ValidationSeverity,
)
from tests.factory.user_factory import UserFactory


def _rule(rule_code="VAL_001", strategy_key="MISSING_LOAN_ID", field_name="loan_id"):
    return ValidationRule.objects.create(
        rule_code=rule_code,
        strategy_key=strategy_key,
        rule_name=f"Rule {rule_code}",
        field_name=field_name,
        description=f"Description for {rule_code}",
    )


@pytest.mark.django_db
class TestReviewerDashboardView:
    """Test cases for ReviewerDashboardView (`/reviewer/`)."""

    def setup_method(self):
        self.client = Client(enforce_csrf_checks=False)
        self.url = reverse("reviewer_dashboard")
        self.login_url = reverse("login")
        self.reviewer = UserFactory.create_reviewer(username="reviewer_dash")
        self.batch = UploadBatch.objects.create(
            file_name="loan_tape.csv",
            source_type=UploadBatch.SourceType.LOAN_TAPE,
            status=UploadBatch.BatchStatus.INGESTED,
        )

    def _auth_client(self, user=None):
        client = Client(enforce_csrf_checks=False)
        client.force_login(user or self.reviewer)
        return client

    def _make_exception(
        self,
        severity=ValidationSeverity.HIGH,
        status=LoanException.ExceptionStatus.OPEN,
        rule_code="VAL_001",
        field_name="loan_id",
        description="rule description sample",
        raw_data=None,
    ):
        rule = _rule(rule_code=rule_code)
        record = RawLoanRecord.objects.create(
            batch=self.batch,
            row_number=1,
            raw_data=raw_data or {"loan_id": "LG-1"},
        )
        return LoanException.objects.create(
            batch=self.batch,
            raw_record=record,
            rule=rule,
            rule_code=rule_code,
            field_name=field_name,
            severity=severity,
            description=description,
            status=status,
        )

    # ── Authentication & Permission Gating (Negative) ──

    def test_get_unauthenticated_redirects_to_login(self):
        response = self.client.get(self.url)
        assert response.status_code == 302
        assert self.login_url in response.url

    def test_get_superuser_denied(self):
        superuser = UserFactory.create_superuser(username="dash_admin")
        response = self._auth_client(superuser).get(self.url)
        assert response.status_code == 403

    def test_get_data_operator_denied(self):
        operator = UserFactory.create_data_operator(username="dash_op")
        response = self._auth_client(operator).get(self.url)
        assert response.status_code == 403

    def test_get_data_consumer_denied(self):
        consumer = UserFactory.create_data_consumer(username="dash_consumer")
        response = self._auth_client(consumer).get(self.url)
        assert response.status_code == 403

    # ── HTTP Method Enforcement (Negative) ──

    def test_post_returns_method_not_allowed(self):
        response = self._auth_client().post(self.url)
        assert response.status_code == 405

    # ── Positive Rendering & Context ──

    def test_get_renders_reviewer_index_template(self):
        response = self._auth_client().get(self.url)
        assert response.status_code == 200
        assert response.templates[0].name == "dashboard/reviewer/index.html"

    def test_get_context_has_expected_keys(self):
        response = self._auth_client().get(self.url)
        ctx = response.context
        for key in (
            "filter",
            "exceptions_page",
            "page_obj",
            "exceptions",
            "total_exceptions",
            "open_exceptions",
            "under_review_exceptions",
            "resolved_exceptions",
            "rejected_exceptions",
            "critical_count",
            "high_count",
            "medium_count",
            "low_count",
            "severity_choices",
            "status_choices",
            "tab_visible",
        ):
            assert key in ctx
        assert ctx["tab_visible"] is True

    def test_get_empty_database_returns_zeroed_metrics(self):
        response = self._auth_client().get(self.url)
        ctx = response.context
        assert response.status_code == 200
        assert list(ctx["exceptions"]) == []
        assert ctx["total_exceptions"] == 0
        assert ctx["open_exceptions"] == 0
        assert ctx["critical_count"] == 0
        assert ctx["high_count"] == 0
        assert ctx["medium_count"] == 0
        assert ctx["low_count"] == 0

    def test_metrics_reflect_status_breakdown(self):
        self._make_exception(status=LoanException.ExceptionStatus.OPEN)
        self._make_exception(status=LoanException.ExceptionStatus.UNDER_REVIEW, rule_code="VAL_002")
        self._make_exception(
            status=LoanException.ExceptionStatus.RESOLVED_ACCEPTED, rule_code="VAL_003"
        )
        self._make_exception(
            status=LoanException.ExceptionStatus.RESOLVED_EDITED, rule_code="VAL_004"
        )
        self._make_exception(status=LoanException.ExceptionStatus.REJECTED, rule_code="VAL_005")

        ctx = self._auth_client().get(self.url).context
        assert ctx["total_exceptions"] == 5
        assert ctx["open_exceptions"] == 1
        assert ctx["under_review_exceptions"] == 1
        assert ctx["resolved_exceptions"] == 2
        assert ctx["rejected_exceptions"] == 1

    def test_severity_breakdown_counts(self):
        self._make_exception(severity=ValidationSeverity.CRITICAL, rule_code="VAL_001")
        self._make_exception(severity=ValidationSeverity.HIGH, rule_code="VAL_002")
        self._make_exception(severity=ValidationSeverity.HIGH, rule_code="VAL_003")
        self._make_exception(severity=ValidationSeverity.MEDIUM, rule_code="VAL_004")
        self._make_exception(severity=ValidationSeverity.LOW, rule_code="VAL_005")

        ctx = self._auth_client().get(self.url).context
        assert ctx["critical_count"] == 1
        assert ctx["high_count"] == 2
        assert ctx["medium_count"] == 1
        assert ctx["low_count"] == 1

    def test_exceptions_ordered_by_severity_then_created(self):
        low = self._make_exception(severity=ValidationSeverity.LOW, rule_code="VAL_001")
        critical = self._make_exception(severity=ValidationSeverity.CRITICAL, rule_code="VAL_002")
        high = self._make_exception(severity=ValidationSeverity.HIGH, rule_code="VAL_003")
        ctx = self._auth_client().get(self.url).context
        ids = [exc.id for exc in ctx["exceptions"]]
        assert ids == [critical.id, high.id, low.id]

    # ── Pagination (Edge / Positive) ──

    def test_page_one_returns_first_ten_exceptions(self):
        for i in range(12):
            self._make_exception(rule_code=f"VAL_{i:03d}")
        response = self._auth_client().get(self.url)
        ctx = response.context
        assert len(ctx["exceptions"]) == 10
        assert ctx["exceptions_page"].has_next()
        assert not ctx["exceptions_page"].has_previous()

    def test_second_page_returns_remaining_exceptions(self):
        for i in range(12):
            self._make_exception(rule_code=f"VAL_{i:03d}")
        ctx = self._auth_client().get(self.url, {"page": 2}).context
        assert len(ctx["exceptions"]) == 2

    def test_exactly_ten_exceptions_single_page(self):
        for i in range(10):
            self._make_exception(rule_code=f"VAL_{i:03d}")
        ctx = self._auth_client().get(self.url).context
        assert len(ctx["exceptions"]) == 10
        assert not ctx["exceptions_page"].has_next()

    def test_page_beyond_last_clamps_to_final_page(self):
        for i in range(15):
            self._make_exception(rule_code=f"VAL_{i:03d}")
        response = self._auth_client().get(self.url, {"page": 99})
        assert response.status_code == 200
        assert len(response.context["exceptions"]) == 5

    def test_non_integer_page_falls_back_to_page_one(self):
        for i in range(3):
            self._make_exception(rule_code=f"VAL_{i:03d}")
        ctx = self._auth_client().get(self.url, {"page": "abc"}).context
        assert len(ctx["exceptions"]) == 3

    def test_zero_page_falls_back_to_page_one(self):
        for i in range(3):
            self._make_exception(rule_code=f"VAL_{i:03d}")
        ctx = self._auth_client().get(self.url, {"page": 0}).context
        assert len(ctx["exceptions"]) == 3

    # ── Filtering: severity / status (Positive / Invalid) ──

    def test_filter_by_severity(self):
        self._make_exception(severity=ValidationSeverity.CRITICAL, rule_code="VAL_001")
        self._make_exception(severity=ValidationSeverity.CRITICAL, rule_code="VAL_002")
        self._make_exception(severity=ValidationSeverity.LOW, rule_code="VAL_003")

        ctx = (
            self._auth_client()
            .get(self.url, {"severity": str(ValidationSeverity.CRITICAL)})
            .context
        )
        assert len(ctx["exceptions"]) == 2
        assert ctx["current_severity"] == str(ValidationSeverity.CRITICAL)

    def test_filter_by_invalid_severity_falls_back_to_all(self):
        self._make_exception(severity=ValidationSeverity.CRITICAL, rule_code="VAL_001")
        self._make_exception(severity=ValidationSeverity.LOW, rule_code="VAL_002")
        ctx = self._auth_client().get(self.url, {"severity": "999"}).context
        assert len(ctx["exceptions"]) == 2

    def test_filter_by_status(self):
        self._make_exception(status=LoanException.ExceptionStatus.OPEN, rule_code="VAL_001")
        self._make_exception(status=LoanException.ExceptionStatus.OPEN, rule_code="VAL_002")
        self._make_exception(status=LoanException.ExceptionStatus.REJECTED, rule_code="VAL_003")
        ctx = (
            self._auth_client()
            .get(self.url, {"status": str(LoanException.ExceptionStatus.OPEN)})
            .context
        )
        assert len(ctx["exceptions"]) == 2
        assert ctx["current_status"] == str(LoanException.ExceptionStatus.OPEN)

    def test_filter_by_invalid_status_falls_back_to_all(self):
        self._make_exception(rule_code="VAL_001")
        self._make_exception(rule_code="VAL_002")
        ctx = self._auth_client().get(self.url, {"status": "999"}).context
        assert len(ctx["exceptions"]) == 2

    # ── Filtering: batch_id (Positive / Invalid) ──

    def test_filter_by_batch_id(self):
        other_batch = UploadBatch.objects.create(
            file_name="other.csv",
            source_type=UploadBatch.SourceType.SERVICER_UPDATE,
            status=UploadBatch.BatchStatus.INGESTED,
        )
        rule = _rule(rule_code="VAL_099")
        self._make_exception()
        LoanException.objects.create(
            batch=other_batch,
            raw_record=RawLoanRecord.objects.create(batch=other_batch, row_number=1),
            rule=rule,
            rule_code="VAL_099",
            field_name="loan_id",
            severity=ValidationSeverity.HIGH,
            description="other batch",
            status=LoanException.ExceptionStatus.OPEN,
        )
        ctx = self._auth_client().get(self.url, {"batch_id": str(self.batch.id)}).context
        assert len(ctx["exceptions"]) == 1
        assert ctx["exceptions"][0].batch_id == self.batch.id

    def test_filter_by_non_existing_batch_falls_back_to_all(self):
        self._make_exception(rule_code="VAL_001")
        ctx = self._auth_client().get(self.url, {"batch_id": "99999"}).context
        assert len(ctx["exceptions"]) == 1

    # ── Filtering: search `q` (Positive / Edge) ──

    def test_search_by_rule_code(self):
        self._make_exception(rule_code="VAL_001", description="unique marker A")
        self._make_exception(rule_code="VAL_002", description="unique marker B")
        ctx = self._auth_client().get(self.url, {"q": "VAL_002"}).context
        assert len(ctx["exceptions"]) == 1
        assert ctx["exceptions"][0].rule_code == "VAL_002"

    def test_search_by_description_substring(self):
        self._make_exception(rule_code="VAL_001", description="loan tape anomaly detected")
        self._make_exception(rule_code="VAL_002", description="servicer file fine")
        ctx = self._auth_client().get(self.url, {"q": "anomaly"}).context
        assert len(ctx["exceptions"]) == 1

    def test_search_by_field_name(self):
        self._make_exception(rule_code="VAL_001", field_name="current_balance")
        self._make_exception(rule_code="VAL_002", field_name="loan_id")
        ctx = self._auth_client().get(self.url, {"q": "current_balance"}).context
        assert len(ctx["exceptions"]) == 1

    def test_search_by_raw_record_data(self):
        self._make_exception(rule_code="VAL_001", raw_data={"borrower_name": "priya"})
        self._make_exception(rule_code="VAL_002", raw_data={"borrower_name": "raman"})
        ctx = self._auth_client().get(self.url, {"q": "priya"}).context
        assert len(ctx["exceptions"]) == 1

    def test_search_by_hashtag_id(self):
        exc = self._make_exception(rule_code="VAL_001")
        ctx = self._auth_client().get(self.url, {"q": f"#{exc.id}"}).context
        assert [e.id for e in ctx["exceptions"]] == [exc.id]

    def test_search_no_match_returns_empty(self):
        self._make_exception(rule_code="VAL_001")
        ctx = self._auth_client().get(self.url, {"q": "definitely_not_here"}).context
        assert list(ctx["exceptions"]) == []

    def test_search_empty_string_returns_all(self):
        for i in range(3):
            self._make_exception(rule_code=f"VAL_{i:03d}")
        ctx = self._auth_client().get(self.url, {"q": ""}).context
        assert len(ctx["exceptions"]) == 3

    def test_search_with_hash_prefix_preserves_text_match(self):
        self._make_exception(rule_code="VAL_001", description="#CRITICAL loan issue")
        self._make_exception(rule_code="VAL_002", description="ordinary issue")
        ctx = self._auth_client().get(self.url, {"q": "#CRITICAL"}).context
        assert len(ctx["exceptions"]) == 1


@pytest.mark.django_db
class TestLoanExceptionListView:
    """Test cases for LoanExceptionListView (`/reviewer/exceptions/`)."""

    def setup_method(self):
        self.client = Client(enforce_csrf_checks=False)
        self.url = reverse("loan_exceptions_list")
        self.login_url = reverse("login")
        self.reviewer = UserFactory.create_reviewer(username="reviewer_list")
        self.batch = UploadBatch.objects.create(
            file_name="loan_tape.csv",
            source_type=UploadBatch.SourceType.LOAN_TAPE,
            status=UploadBatch.BatchStatus.INGESTED,
        )

    def _auth_client(self, user=None):
        client = Client(enforce_csrf_checks=False)
        client.force_login(user or self.reviewer)
        return client

    def _make_exception(self, severity=ValidationSeverity.HIGH, rule_code="VAL_001", **kwargs):
        rule = _rule(rule_code=rule_code)
        record = RawLoanRecord.objects.create(
            batch=self.batch, row_number=1, raw_data={"loan_id": "LG-1"}
        )
        defaults = {
            "batch": self.batch,
            "raw_record": record,
            "rule": rule,
            "rule_code": rule_code,
            "field_name": "loan_id",
            "severity": severity,
            "description": "sample description",
            "status": LoanException.ExceptionStatus.OPEN,
        }
        defaults.update(kwargs)
        return LoanException.objects.create(**defaults)

    # ── Authentication & Permission Gating (Negative) ──

    def test_get_unauthenticated_redirects_to_login(self):
        response = self.client.get(self.url)
        assert response.status_code == 302
        assert self.login_url in response.url

    def test_get_superuser_denied(self):
        superuser = UserFactory.create_superuser(username="list_admin")
        assert self._auth_client(superuser).get(self.url).status_code == 403

    def test_get_data_operator_denied(self):
        operator = UserFactory.create_data_operator(username="list_op")
        assert self._auth_client(operator).get(self.url).status_code == 403

    def test_get_data_consumer_denied(self):
        consumer = UserFactory.create_data_consumer(username="list_consumer")
        assert self._auth_client(consumer).get(self.url).status_code == 403

    # ── HTTP Method Enforcement (Negative) ──

    def test_post_returns_method_not_allowed(self):
        assert self._auth_client().post(self.url).status_code == 405

    # ── Positive Rendering & Context ──

    def test_get_renders_exceptions_tab_template(self):
        self._make_exception()
        response = self._auth_client().get(self.url)
        assert response.status_code == 200
        assert response.templates[0].name == "dashboard/reviewer/includes/exceptions_tab.html"

    def test_get_context_has_expected_keys(self):
        response = self._auth_client().get(self.url)
        ctx = response.context
        for key in (
            "filter",
            "exceptions_page",
            "page_obj",
            "exceptions",
            "severity_choices",
            "status_choices",
            "tab_visible",
        ):
            assert key in ctx
        assert ctx["tab_visible"] is True

    def test_get_empty_database_returns_empty_list(self):
        response = self._auth_client().get(self.url)
        assert response.status_code == 200
        assert list(response.context["exceptions"]) == []

    # ── Pagination (Boundary / Edge) ──

    def test_page_one_returns_first_ten(self):
        for i in range(11):
            self._make_exception(rule_code=f"VAL_{i:03d}")
        ctx = self._auth_client().get(self.url).context
        assert len(ctx["exceptions"]) == 10
        assert ctx["exceptions_page"].has_next()

    def test_second_page_returns_remainder(self):
        for i in range(11):
            self._make_exception(rule_code=f"VAL_{i:03d}")
        ctx = self._auth_client().get(self.url, {"page": 2}).context
        assert len(ctx["exceptions"]) == 1

    def test_page_beyond_last_clamps(self):
        for i in range(4):
            self._make_exception(rule_code=f"VAL_{i:03d}")
        response = self._auth_client().get(self.url, {"page": 50})
        assert response.status_code == 200
        assert len(response.context["exceptions"]) == 4

    def test_page_non_integer_falls_back_to_page_one(self):
        for i in range(2):
            self._make_exception(rule_code=f"VAL_{i:03d}")
        ctx = self._auth_client().get(self.url, {"page": "1x"}).context
        assert len(ctx["exceptions"]) == 2

    # ── Filtering (Positive / Invalid) ──

    def test_filter_by_severity(self):
        self._make_exception(severity=ValidationSeverity.CRITICAL, rule_code="VAL_001")
        self._make_exception(severity=ValidationSeverity.HIGH, rule_code="VAL_002")
        ctx = (
            self._auth_client()
            .get(self.url, {"severity": str(ValidationSeverity.CRITICAL)})
            .context
        )
        assert len(ctx["exceptions"]) == 1

    def test_filter_by_invalid_severity_falls_back(self):
        self._make_exception(rule_code="VAL_001")
        ctx = self._auth_client().get(self.url, {"severity": "99"}).context
        assert len(ctx["exceptions"]) == 1

    def test_filter_by_status(self):
        self._make_exception(status=LoanException.ExceptionStatus.UNDER_REVIEW, rule_code="VAL_001")
        self._make_exception(status=LoanException.ExceptionStatus.OPEN, rule_code="VAL_002")
        ctx = (
            self._auth_client()
            .get(self.url, {"status": str(LoanException.ExceptionStatus.UNDER_REVIEW)})
            .context
        )
        assert len(ctx["exceptions"]) == 1

    def test_search_by_rule_code(self):
        self._make_exception(rule_code="VAL_010")
        self._make_exception(rule_code="VAL_020")
        ctx = self._auth_client().get(self.url, {"q": "VAL_020"}).context
        assert len(ctx["exceptions"]) == 1

    def test_search_no_match_empty(self):
        self._make_exception(rule_code="VAL_001")
        ctx = self._auth_client().get(self.url, {"q": "nothing"}).context
        assert list(ctx["exceptions"]) == []

    def test_search_case_insensitive(self):
        self._make_exception(rule_code="VAL_001", description="DUPLICATE LOAN FOUND")
        ctx = self._auth_client().get(self.url, {"q": "duplicate"}).context
        assert len(ctx["exceptions"]) == 1
