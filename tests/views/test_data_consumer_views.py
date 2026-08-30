"""
Test cases for the Data Consumer Workspace views.

Covers authentication gating, role-based permission denial, HTTP method
enforcement, context computation, pagination, filters, HTMX partial rendering,
tamper detection, audit-event scoping, and CSV/JSON export behavior for the
consumer dashboard, verified loan list/detail/history/audit, and export views.
"""

import csv
import io
import json
from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.models.audit import AuditEvent
from app.models.ingestion import RawLoanRecord, UploadBatch
from app.models.verified import VerifiedLoanRecord
from tests.factory.user_factory import UserFactory
from tests.factory.verified_factory import VerifiedLoanRecordFactory


def _consumer_batch():
    return UploadBatch.objects.create(
        file_name="loan_tape.csv",
        source_type=UploadBatch.SourceType.LOAN_TAPE,
        status=UploadBatch.BatchStatus.INGESTED,
    )


def _verified(loan_id, batch=None, **kwargs):
    raw = VerifiedLoanRecordFactory.create_raw_record(raw_data={"loan_id": loan_id}, batch=batch)
    kwargs.setdefault("canonical_data", {"loan_id": loan_id})
    return VerifiedLoanRecordFactory.create_verified_record(raw_record=raw, **kwargs)


@pytest.mark.django_db
class TestDataConsumerDashboardView:
    """Test cases for DataConsumerDashboardView (`/consumer/`)."""

    def setup_method(self):
        self.client = Client(enforce_csrf_checks=False)
        self.url = reverse("consumer_dashboard")
        self.login_url = reverse("login")
        self.consumer = UserFactory.create_data_consumer(username="consumer_dash")

    def _auth_client(self, user=None):
        client = Client(enforce_csrf_checks=False)
        client.force_login(user or self.consumer)
        return client

    # ── Authentication & Permission Gating (Negative) ──

    def test_get_unauthenticated_redirects_to_login(self):
        response = self.client.get(self.url)
        assert response.status_code == 302
        assert self.login_url in response.url

    def test_get_superuser_denied(self):
        superuser = UserFactory.create_superuser(username="dash_admin")
        assert self._auth_client(superuser).get(self.url).status_code == 403

    def test_get_data_operator_denied(self):
        operator = UserFactory.create_data_operator(username="dash_op")
        assert self._auth_client(operator).get(self.url).status_code == 403

    def test_get_reviewer_denied(self):
        reviewer = UserFactory.create_reviewer(username="dash_reviewer")
        assert self._auth_client(reviewer).get(self.url).status_code == 403

    # ── HTTP Method Enforcement (Negative) ──

    def test_post_returns_method_not_allowed(self):
        assert self._auth_client().post(self.url).status_code == 405

    # ── Positive Rendering & Context ──

    def test_get_renders_consumer_index_template(self):
        response = self._auth_client().get(self.url)
        assert response.status_code == 200
        assert response.templates[0].name == "dashboard/consumer/index.html"

    def test_get_context_has_expected_keys(self):
        response = self._auth_client().get(self.url)
        ctx = response.context
        for key in (
            "title",
            "user",
            "role_name",
            "total_raw",
            "total_verified",
            "total_exceptions",
            "clean_verified_count",
            "resolved_verified_count",
            "quality_score",
            "recent_verified_records",
        ):
            assert key in ctx
        assert ctx["role_name"] == "Data Consumer"

    def test_get_empty_database_returns_zeroed_metrics(self):
        response = self._auth_client().get(self.url)
        ctx = response.context
        assert response.status_code == 200
        assert ctx["total_raw"] == 0
        assert ctx["total_verified"] == 0
        assert ctx["total_exceptions"] == 0
        assert ctx["quality_score"] == 0.0
        assert list(ctx["recent_verified_records"]) == []

    def test_quality_score_is_100_when_all_tape_verified(self):
        batch = _consumer_batch()
        _verified("LG-D1", batch=batch)
        ctx = self._auth_client().get(self.url).context
        assert ctx["total_raw"] == 1
        assert ctx["total_verified"] == 1
        assert ctx["quality_score"] == 100.0
        assert ctx["clean_verified_count"] == 1

    def test_quality_score_rounds_to_one_decimal(self):
        batch = _consumer_batch()
        _verified("LG-D2", batch=batch)
        _verified("LG-D3", batch=batch)
        RawLoanRecord.objects.create(batch=batch, row_number=5, raw_data={"loan_id": "LG-D4"})
        ctx = self._auth_client().get(self.url).context
        assert ctx["total_raw"] == 3
        assert ctx["total_verified"] == 2
        assert ctx["quality_score"] == 66.7

    def test_quality_score_falls_back_to_100_when_no_tape_but_verified_exists(self):
        servicer_batch = UploadBatch.objects.create(
            file_name="servicer.csv",
            source_type=UploadBatch.SourceType.SERVICER_UPDATE,
            status=UploadBatch.BatchStatus.INGESTED,
        )
        _verified("LG-D5", batch=servicer_batch)
        ctx = self._auth_client().get(self.url).context
        assert ctx["total_raw"] == 0
        assert ctx["quality_score"] == 100.0

    def test_total_raw_counts_only_loan_tape_batches(self):
        tape_batch = _consumer_batch()
        RawLoanRecord.objects.create(batch=tape_batch, row_number=1, raw_data={"loan_id": "LG-D6"})
        servicer_batch = UploadBatch.objects.create(
            file_name="servicer.csv",
            source_type=UploadBatch.SourceType.SERVICER_UPDATE,
            status=UploadBatch.BatchStatus.INGESTED,
        )
        RawLoanRecord.objects.create(
            batch=servicer_batch, row_number=1, raw_data={"loan_id": "LG-D7"}
        )
        ctx = self._auth_client().get(self.url).context
        assert ctx["total_raw"] == 1

    def test_clean_and_resolved_breakdown_counts(self):
        _verified("LG-D8", validation_status=VerifiedLoanRecord.ValidationStatus.PASSED_CLEAN)
        _verified("LG-D9", validation_status=VerifiedLoanRecord.ValidationStatus.RESOLVED_EXCEPTION)
        ctx = self._auth_client().get(self.url).context
        assert ctx["clean_verified_count"] == 1
        assert ctx["resolved_verified_count"] == 1

    def test_recent_verified_records_capped_at_five(self):
        for i in range(6):
            _verified(f"LG-D{i:02d}")
        ctx = self._auth_client().get(self.url).context
        assert len(ctx["recent_verified_records"]) == 5


@pytest.mark.django_db
class TestVerifiedLoanListView:
    """Test cases for VerifiedLoanListView (`/consumer/verified-loans/`)."""

    def setup_method(self):
        self.client = Client(enforce_csrf_checks=False)
        self.url = reverse("verified_loan_list")
        self.login_url = reverse("login")
        self.consumer = UserFactory.create_data_consumer(username="consumer_list")

    def _auth_client(self, user=None):
        client = Client(enforce_csrf_checks=False)
        client.force_login(user or self.consumer)
        return client

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

    def test_get_reviewer_denied(self):
        reviewer = UserFactory.create_reviewer(username="list_reviewer")
        assert self._auth_client(reviewer).get(self.url).status_code == 403

    # ── HTTP Method Enforcement (Negative) ──

    def test_post_returns_method_not_allowed(self):
        assert self._auth_client().post(self.url).status_code == 405

    # ── Positive Rendering & Context ──

    def test_get_renders_list_template(self):
        _verified("LG-L1")
        response = self._auth_client().get(self.url)
        assert response.status_code == 200
        assert response.templates[0].name == "dashboard/consumer/list.html"

    def test_get_htmx_request_renders_partial_tab(self):
        _verified("LG-L2")
        response = self._auth_client().get(self.url, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert response.templates[0].name == "dashboard/consumer/includes/verified_loans_tab.html"

    def test_get_context_has_expected_keys(self):
        _verified("LG-L3")
        ctx = self._auth_client().get(self.url).context
        for key in (
            "filter",
            "title",
            "total_count",
            "validation_status_choices",
            "reviewer_decision_choices",
            "quality_score",
            "verified_records",
            "page_obj",
        ):
            assert key in ctx

    def test_get_empty_database_returns_empty_list(self):
        response = self._auth_client().get(self.url)
        assert response.status_code == 200
        assert list(response.context["verified_records"]) == []
        assert response.context["total_count"] == 0

    # ── Pagination (Positive / Boundary / Edge) ──

    def test_page_one_returns_first_ten(self):
        for i in range(12):
            _verified(f"LG-LP{i:02d}")
        ctx = self._auth_client().get(self.url).context
        assert len(ctx["verified_records"]) == 10
        assert ctx["page_obj"].has_next()
        assert not ctx["page_obj"].has_previous()

    def test_second_page_returns_remainder(self):
        for i in range(12):
            _verified(f"LG-LS{i:02d}")
        ctx = self._auth_client().get(self.url, {"page": 2}).context
        assert len(ctx["verified_records"]) == 2

    def test_page_beyond_last_returns_404(self):
        # Django 6.1 ListView raises Http404 for an out-of-range page number.
        for i in range(5):
            _verified(f"LG-LC{i:02d}")
        assert self._auth_client().get(self.url, {"page": 99}).status_code == 404

    def test_last_page_returns_final_page(self):
        for i in range(12):
            _verified(f"LG-LL{i:02d}")
        ctx = self._auth_client().get(self.url, {"page": "last"}).context
        assert len(ctx["verified_records"]) == 2

    def test_zero_page_returns_404(self):
        _verified("LG-LZ0")
        assert self._auth_client().get(self.url, {"page": 0}).status_code == 404

    def test_non_integer_page_returns_404(self):
        # Django 6.1 ListView raises Http404 when the page cannot be parsed as an int.
        for i in range(12):
            _verified(f"LG-LN{i:02d}")
        assert self._auth_client().get(self.url, {"page": "abc"}).status_code == 404

    # ── Filtering (Positive / Invalid) ──

    def test_filter_by_validation_status(self):
        _verified("LG-LF1")
        _verified("LG-LF2")
        _verified(
            "LG-LF3", validation_status=VerifiedLoanRecord.ValidationStatus.RESOLVED_EXCEPTION
        )
        ctx = self._auth_client().get(self.url, {"validation_status": "RESOLVED_EXCEPTION"}).context
        assert len(ctx["verified_records"]) == 1
        assert ctx["verified_records"][0].loan_id == "LG-LF3"
        assert ctx["total_count"] == 1

    def test_filter_by_invalid_validation_status_returns_all(self):
        _verified("LG-LF4")
        _verified("LG-LF5")
        ctx = self._auth_client().get(self.url, {"validation_status": "NOPE"}).context
        assert len(ctx["verified_records"]) == 2

    def test_filter_by_reviewer_decision(self):
        _verified("LG-LF6", reviewer_decision=VerifiedLoanRecord.ReviewerDecision.APPROVED)
        _verified("LG-LF7")
        ctx = self._auth_client().get(self.url, {"reviewer_decision": "APPROVED"}).context
        assert len(ctx["verified_records"]) == 1
        assert ctx["verified_records"][0].loan_id == "LG-LF6"

    def test_search_by_loan_id_substring(self):
        _verified("LG-UNIQUE-1")
        _verified("LG-OTHER-2")
        ctx = self._auth_client().get(self.url, {"q": "UNIQUE"}).context
        assert len(ctx["verified_records"]) == 1
        assert ctx["verified_records"][0].loan_id == "LG-UNIQUE-1"

    def test_search_by_hashtag_id(self):
        record = _verified("LG-HASH-1")
        ctx = self._auth_client().get(self.url, {"q": f"#{record.id}"}).context
        assert [r.id for r in ctx["verified_records"]] == [record.id]

    def test_search_by_record_hash(self):
        record = _verified("LG-HASH-2")
        ctx = self._auth_client().get(self.url, {"q": record.record_hash}).context
        assert len(ctx["verified_records"]) == 1

    def test_search_no_match_returns_empty(self):
        _verified("LG-NOMATCH")
        ctx = self._auth_client().get(self.url, {"q": "definitely_missing"}).context
        assert list(ctx["verified_records"]) == []

    def test_search_empty_string_returns_all(self):
        for i in range(3):
            _verified(f"LG-EMPTY{i}")
        ctx = self._auth_client().get(self.url, {"q": ""}).context
        assert len(ctx["verified_records"]) == 3


@pytest.mark.django_db
class TestVerifiedLoanDetailView:
    """Test cases for VerifiedLoanDetailView (`/consumer/verified-loans/<pk>/detail/`)."""

    def setup_method(self):
        self.client = Client(enforce_csrf_checks=False)
        self.login_url = reverse("login")
        self.consumer = UserFactory.create_data_consumer(username="consumer_detail")

    def _auth_client(self, user=None):
        client = Client(enforce_csrf_checks=False)
        client.force_login(user or self.consumer)
        return client

    def _url(self, pk):
        return reverse("verified_loan_detail", kwargs={"pk": pk})

    # ── Authentication & Permission Gating (Negative) ──

    def test_get_unauthenticated_redirects_to_login(self):
        record = _verified("LG-DTL1")
        response = self.client.get(self._url(record.pk))
        assert response.status_code == 302
        assert self.login_url in response.url

    def test_get_superuser_denied(self):
        superuser = UserFactory.create_superuser(username="detail_admin")
        record = _verified("LG-DTL2")
        assert self._auth_client(superuser).get(self._url(record.pk)).status_code == 403

    def test_get_data_operator_denied(self):
        operator = UserFactory.create_data_operator(username="detail_op")
        record = _verified("LG-DTL3")
        assert self._auth_client(operator).get(self._url(record.pk)).status_code == 403

    def test_get_reviewer_denied(self):
        reviewer = UserFactory.create_reviewer(username="detail_reviewer")
        record = _verified("LG-DTL4")
        assert self._auth_client(reviewer).get(self._url(record.pk)).status_code == 403

    # ── HTTP Method Enforcement (Negative) ──

    def test_post_returns_method_not_allowed(self):
        record = _verified("LG-DTL5")
        assert self._auth_client().post(self._url(record.pk)).status_code == 405

    # ── Positive Rendering & Context ──

    def test_get_renders_detail_template(self):
        record = _verified("LG-DTL6")
        response = self._auth_client().get(self._url(record.pk))
        assert response.status_code == 200
        assert response.templates[0].name == "dashboard/consumer/detail.html"

    def test_get_context_has_title_hash_and_integrity(self):
        record = _verified("LG-DTL7", canonical_data={"loan_id": "LG-DTL7", "amount": 1000.0})
        ctx = self._auth_client().get(self._url(record.pk)).context
        assert ctx["verified_record"].pk == record.pk
        assert "LG-DTL7" in ctx["title"]
        assert ctx["is_tampered"] is False
        assert ctx["computed_hash"] == record.compute_hash()

    def test_tampered_record_reported_in_context(self):
        record = _verified("LG-DTL8", canonical_data={"loan_id": "LG-DTL8", "amount": 1000.0})
        record.canonical_data["amount"] = 99999.0
        record.save(update_fields=["canonical_data"])
        record.refresh_from_db()
        ctx = self._auth_client().get(self._url(record.pk)).context
        assert ctx["is_tampered"] is True

    # ── 404 Handling (Invalid) ──

    def test_get_nonexistent_record_returns_404(self):
        assert self._auth_client().get(self._url(999999)).status_code == 404


@pytest.mark.django_db
class TestVerifiedLoanHistoryView:
    """Test cases for VerifiedLoanHistoryView (`/consumer/verified-loans/<pk>/history/`)."""

    def setup_method(self):
        self.client = Client(enforce_csrf_checks=False)
        self.login_url = reverse("login")
        self.consumer = UserFactory.create_data_consumer(username="consumer_history")

    def _auth_client(self, user=None):
        client = Client(enforce_csrf_checks=False)
        client.force_login(user or self.consumer)
        return client

    def _url(self, pk):
        return reverse("verified_loan_history", kwargs={"pk": pk})

    def _lineaged_record(self, loan_id="LG-HIST"):
        raw = VerifiedLoanRecordFactory.create_raw_record(raw_data={"loan_id": loan_id})
        exc = VerifiedLoanRecordFactory.create_exception(raw_record=raw)
        ai = VerifiedLoanRecordFactory.create_ai_recommendation(exception=exc)
        reviewer = UserFactory.create_reviewer()
        exc.resolved_by = reviewer
        exc.save()
        ai.reviewed_by = reviewer
        ai.save()
        return VerifiedLoanRecord.create_record(
            raw_record=raw,
            canonical_data={"loan_id": loan_id},
            exceptions=[exc],
            ai_recommendations=[ai],
            participating_reviewers=[reviewer],
            verified_by=reviewer,
        )

    def test_get_unauthenticated_redirects_to_login(self):
        record = _verified("LG-HIST0")
        assert self.client.get(self._url(record.pk)).status_code == 302

    def test_get_superuser_denied(self):
        superuser = UserFactory.create_superuser(username="history_admin")
        record = _verified("LG-HIST1")
        assert self._auth_client(superuser).get(self._url(record.pk)).status_code == 403

    def test_get_data_operator_denied(self):
        operator = UserFactory.create_data_operator(username="history_op")
        record = _verified("LG-HIST2")
        assert self._auth_client(operator).get(self._url(record.pk)).status_code == 403

    def test_get_reviewer_denied(self):
        reviewer = UserFactory.create_reviewer(username="history_rev")
        record = _verified("LG-HIST3")
        assert self._auth_client(reviewer).get(self._url(record.pk)).status_code == 403

    def test_get_renders_history_template(self):
        record = self._lineaged_record()
        response = self._auth_client().get(self._url(record.pk))
        assert response.status_code == 200
        assert response.templates[0].name == "dashboard/consumer/history.html"

    def test_get_context_exposes_lineage(self):
        record = self._lineaged_record()
        ctx = self._auth_client().get(self._url(record.pk)).context
        assert list(ctx["resolved_exceptions"]) == list(record.exceptions_resolved.all())
        assert list(ctx["ai_recommendations"]) == list(record.ai_recommendations_used.all())
        assert list(ctx["participating_reviewers"]) == list(record.participating_reviewers.all())

    def test_get_empty_lineage_record(self):
        record = _verified("LG-HIST4")
        ctx = self._auth_client().get(self._url(record.pk)).context
        assert list(ctx["resolved_exceptions"]) == []
        assert list(ctx["ai_recommendations"]) == []

    def test_get_nonexistent_record_returns_404(self):
        assert self._auth_client().get(self._url(999999)).status_code == 404


@pytest.mark.django_db
class TestVerifiedLoanAuditTrailView:
    """Test cases for VerifiedLoanAuditTrailView (`/consumer/verified-loans/<pk>/audit/`)."""

    def setup_method(self):
        self.client = Client(enforce_csrf_checks=False)
        self.login_url = reverse("login")
        self.consumer = UserFactory.create_data_consumer(username="consumer_audit")

    def _auth_client(self, user=None):
        client = Client(enforce_csrf_checks=False)
        client.force_login(user or self.consumer)
        return client

    def _url(self, pk):
        return reverse("verified_loan_audit", kwargs={"pk": pk})

    def test_get_unauthenticated_redirects_to_login(self):
        record = _verified("LG-AUD0")
        assert self.client.get(self._url(record.pk)).status_code == 302

    def test_get_superuser_denied(self):
        superuser = UserFactory.create_superuser(username="audit_admin")
        record = _verified("LG-AUD1")
        assert self._auth_client(superuser).get(self._url(record.pk)).status_code == 403

    def test_get_data_operator_denied(self):
        operator = UserFactory.create_data_operator(username="audit_op")
        record = _verified("LG-AUD2")
        assert self._auth_client(operator).get(self._url(record.pk)).status_code == 403

    def test_get_reviewer_denied(self):
        reviewer = UserFactory.create_reviewer(username="audit_rev")
        record = _verified("LG-AUD3")
        assert self._auth_client(reviewer).get(self._url(record.pk)).status_code == 403

    def test_get_renders_audit_template(self):
        record = _verified("LG-AUD4")
        response = self._auth_client().get(self._url(record.pk))
        assert response.status_code == 200
        assert response.templates[0].name == "dashboard/consumer/audit.html"

    def test_audit_events_scoped_to_loan_id(self):
        record = _verified("LG-AUD5")
        AuditEvent.log_event(event_type="VERIFIED_RECORD_CREATED", loan_id="LG-AUD5")
        AuditEvent.log_event(event_type="OTHER_EVENT", loan_id="UNRELATED-LOAN")
        AuditEvent.log_event(event_type="ORPHAN_EVENT", loan_id=None)
        ctx = self._auth_client().get(self._url(record.pk)).context
        event_types = set(ctx["audit_events"].values_list("event_type", flat=True))
        assert event_types == {"VERIFIED_RECORD_CREATED"}

    def test_audit_events_scoped_to_raw_record_id_payload(self):
        record = _verified("LG-AUD6")
        AuditEvent.log_event(
            event_type="EXCEPTION_RESOLVED",
            loan_id="DIFFERENT-LOAN",
            payload={"raw_record_id": record.raw_record_id},
        )
        ctx = self._auth_client().get(self._url(record.pk)).context
        assert set(ctx["audit_events"].values_list("event_type", flat=True)) == {
            "EXCEPTION_RESOLVED"
        }

    def test_audit_events_ordered_newest_first(self):
        record = _verified("LG-AUD7")
        earlier = AuditEvent.objects.create(
            timestamp=timezone.now() - timedelta(minutes=2),
            event_type="FIRST_EVENT",
            actor_role=AuditEvent.ActorRole.SYSTEM,
            loan_id="LG-AUD7",
        )
        later = AuditEvent.objects.create(
            timestamp=timezone.now() - timedelta(minutes=1),
            event_type="SECOND_EVENT",
            actor_role=AuditEvent.ActorRole.SYSTEM,
            loan_id="LG-AUD7",
        )
        ctx = self._auth_client().get(self._url(record.pk)).context
        events = list(ctx["audit_events"])
        assert [e.id for e in events] == [later.id, earlier.id]

    def test_get_audit_trail_with_no_events(self):
        record = _verified("LG-AUD8")
        ctx = self._auth_client().get(self._url(record.pk)).context
        assert list(ctx["audit_events"]) == []

    def test_get_nonexistent_record_returns_404(self):
        assert self._auth_client().get(self._url(999999)).status_code == 404


@pytest.mark.django_db
class TestExportVerifiedLoansView:
    """Test cases for ExportVerifiedLoansView (`/consumer/verified-loans/export/`)."""

    def setup_method(self):
        self.client = Client(enforce_csrf_checks=False)
        self.url = reverse("verified_loan_export")
        self.login_url = reverse("login")
        self.consumer = UserFactory.create_data_consumer(username="consumer_export")

    def _auth_client(self, user=None):
        client = Client(enforce_csrf_checks=False)
        client.force_login(user or self.consumer)
        return client

    # ── Authentication & Permission Gating (Negative) ──

    def test_get_unauthenticated_redirects_to_login(self):
        response = self.client.get(self.url)
        assert response.status_code == 302
        assert self.login_url in response.url

    def test_get_superuser_denied(self):
        superuser = UserFactory.create_superuser(username="export_admin")
        assert self._auth_client(superuser).get(self.url).status_code == 403

    def test_get_data_operator_denied(self):
        operator = UserFactory.create_data_operator(username="export_op")
        assert self._auth_client(operator).get(self.url).status_code == 403

    def test_get_reviewer_denied(self):
        reviewer = UserFactory.create_reviewer(username="export_reviewer")
        assert self._auth_client(reviewer).get(self.url).status_code == 403

    # ── CSV Export (Positive) ──

    def test_default_export_is_csv_attachment(self):
        _verified("LG-EX1")
        response = self._auth_client().get(self.url)
        assert response.status_code == 200
        assert response["Content-Type"] == "text/csv"
        assert response["Content-Disposition"] == 'attachment; filename="verified_loans_export.csv"'

    def test_csv_contains_header_and_record_row(self):
        record = _verified(
            "LG-EX2",
            canonical_data={
                "loan_id": "LG-EX2",
                "original_principal": 1000000.0,
                "current_balance": 900000.0,
                "interest_rate": 8.5,
            },
        )
        response = self._auth_client().get(self.url)
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8"))))
        assert rows[0] == [
            "Verified Record ID",
            "Loan ID",
            "Borrower ID",
            "Validation Status",
            "Reviewer Decision",
            "Verified At",
            "Verified By",
            "Record SHA-256 Hash",
            "Original Principal",
            "Current Balance",
            "Interest Rate",
        ]
        assert rows[1][0] == str(record.id)
        assert rows[1][1] == "LG-EX2"
        assert rows[1][3] == "Passed Validation Cleanly"
        assert rows[1][4] == "Auto Passed (System)"
        assert rows[1][6] == "System Auto-Passed"
        assert rows[1][7] == record.record_hash
        assert rows[1][8] == "1000000.0"
        assert rows[1][9] == "900000.0"
        assert rows[1][10] == "8.5"

    def test_csv_includes_verified_by_username_when_present(self):
        reviewer = UserFactory.create_reviewer()
        _verified("LG-EX3", verified_by=reviewer)
        response = self._auth_client().get(self.url)
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8"))))
        assert rows[1][6] == reviewer.username

    def test_csv_empty_database_returns_header_only(self):
        response = self._auth_client().get(self.url)
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8"))))
        assert len(rows) == 1
        assert rows[0][0] == "Verified Record ID"

    def test_csv_uppercase_format_parameter_lowered(self):
        _verified("LG-EX4")
        response = self._auth_client().get(self.url, {"format": "CSV"})
        assert response["Content-Type"] == "text/csv"

    def test_csv_unknown_format_falls_back_to_csv(self):
        _verified("LG-EX5")
        response = self._auth_client().get(self.url, {"format": "xml"})
        assert response["Content-Type"] == "text/csv"

    def test_csv_respects_validation_status_filter(self):
        _verified("LG-EX6", validation_status=VerifiedLoanRecord.ValidationStatus.PASSED_CLEAN)
        _verified(
            "LG-EX7", validation_status=VerifiedLoanRecord.ValidationStatus.RESOLVED_EXCEPTION
        )
        response = self._auth_client().get(self.url, {"validation_status": "RESOLVED_EXCEPTION"})
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8"))))
        assert len(rows) == 2
        assert rows[1][1] == "LG-EX7"

    # ── JSON Export (Positive / Edge) ──

    def test_json_export_is_json_attachment(self):
        _verified("LG-EX-J1")
        response = self._auth_client().get(self.url, {"format": "json"})
        assert response.status_code == 200
        assert response["Content-Type"] == "application/json"
        assert (
            response["Content-Disposition"] == 'attachment; filename="verified_loans_export.json"'
        )

    def test_json_contains_expected_fields(self):
        record = _verified("LG-EX-J2", canonical_data={"loan_id": "LG-EX-J2", "amount": 25.0})
        response = self._auth_client().get(self.url, {"format": "json"})
        data = json.loads(response.content)
        assert len(data) == 1
        item = data[0]
        assert item["loan_id"] == "LG-EX-J2"
        assert item["record_hash"] == record.record_hash
        assert item["canonical_data"] == {"loan_id": "LG-EX-J2", "amount": 25.0}
        assert item["verified_by"] is None
        assert item["verified_at"] is not None

    def test_json_includes_verified_by_username_when_present(self):
        reviewer = UserFactory.create_reviewer()
        _verified("LG-EX-J3", verified_by=reviewer)
        response = self._auth_client().get(self.url, {"format": "json"})
        data = json.loads(response.content)
        assert data[0]["verified_by"] == reviewer.username

    def test_json_empty_database_returns_empty_array(self):
        response = self._auth_client().get(self.url, {"format": "json"})
        assert json.loads(response.content) == []

    # ── VERIFIED_RECORD_EXPORTED audit events ──

    def test_csv_export_logs_verified_record_exported_event(self):
        """CSV export should log a VERIFIED_RECORD_EXPORTED audit event per returned record."""
        _verified("LG-EXA1")
        _verified("LG-EXA2")
        self._auth_client().get(self.url)
        events = AuditEvent.objects.filter(event_type="VERIFIED_RECORD_EXPORTED")
        assert events.count() == 2

    def test_json_export_logs_verified_record_exported_event(self):
        """JSON export should log a VERIFIED_RECORD_EXPORTED audit event per returned record."""
        _verified("LG-EXA3")
        self._auth_client().get(self.url, {"format": "json"})
        events = AuditEvent.objects.filter(event_type="VERIFIED_RECORD_EXPORTED")
        assert events.count() == 1

    def test_verified_record_exported_event_fields(self):
        """The VERIFIED_RECORD_EXPORTED event should carry correct actor, role, loan_id, batch and payload."""
        batch = _consumer_batch()
        record = _verified("LG-EXA4", batch=batch)
        self._auth_client().get(self.url)
        event = AuditEvent.objects.get(event_type="VERIFIED_RECORD_EXPORTED")

        assert event.actor == self.consumer
        assert event.actor_role == AuditEvent.ActorRole.DATA_CONSUMER
        assert event.loan_id == "LG-EXA4"
        assert event.batch_id == batch.id
        assert event.payload["verified_record_id"] == record.id
        assert event.payload["format"] == "csv"
        assert event.payload["record_hash"] == record.record_hash
        assert event.payload["validation_status"] == "Passed Validation Cleanly"

    def test_verified_record_exported_event_format_json(self):
        """A JSON export should record 'json' in the audit payload format field."""
        _verified("LG-EXA5")
        self._auth_client().get(self.url, {"format": "json"})
        event = AuditEvent.objects.get(event_type="VERIFIED_RECORD_EXPORTED")
        assert event.payload["format"] == "json"

    def test_verified_record_exported_event_batch_id_from_raw_record(self):
        """The event batch_id should derive from the record's raw record batch."""
        batch = _consumer_batch()
        record = _verified("LG-EXA6", batch=batch)
        self._auth_client().get(self.url)
        event = AuditEvent.objects.get(event_type="VERIFIED_RECORD_EXPORTED")
        assert event.batch_id == record.raw_record.batch_id == batch.id

    def test_verified_record_exported_event_validation_status_display(self):
        """The event validation_status should be the human-readable display value."""
        _verified(
            "LG-EXA7", validation_status=VerifiedLoanRecord.ValidationStatus.RESOLVED_EXCEPTION
        )
        self._auth_client().get(self.url)
        event = AuditEvent.objects.get(event_type="VERIFIED_RECORD_EXPORTED")
        assert event.payload["validation_status"] == "Resolved Exception"

    def test_export_empty_database_logs_no_events(self):
        """Exporting an empty database should create zero VERIFIED_RECORD_EXPORTED events."""
        self._auth_client().get(self.url)
        assert AuditEvent.objects.filter(event_type="VERIFIED_RECORD_EXPORTED").count() == 0

    def test_export_with_filter_logs_only_matching_events(self):
        """Export audit events should be logged only for records matched by the current filters."""
        _verified("LG-EXA8", validation_status=VerifiedLoanRecord.ValidationStatus.PASSED_CLEAN)
        _verified(
            "LG-EXA9", validation_status=VerifiedLoanRecord.ValidationStatus.RESOLVED_EXCEPTION
        )
        self._auth_client().get(self.url, {"validation_status": "RESOLVED_EXCEPTION"})
        events = AuditEvent.objects.filter(event_type="VERIFIED_RECORD_EXPORTED")
        assert events.count() == 1
        assert events.get().loan_id == "LG-EXA9"
