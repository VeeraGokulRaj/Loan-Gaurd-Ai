"""
Test cases for app.views.data_operator.BatchListView and FailedRowListView.

Covers authentication gating, superuser/permission denial, HTTP method enforcement,
pagination, filtering (source_type/status/q/batch_id), empty-state fallbacks,
and HTMX rendering for both the batch and failed-row list endpoints.
"""

import pytest
from django.test import Client
from django.urls import reverse

from app.models import FailedImportRow, UploadBatch
from tests.factory.user_factory import UserFactory


@pytest.mark.django_db
class TestBatchListView:
    """Test cases for the BatchListView endpoint (`/ingest/batches/`)."""

    def setup_method(self):
        self.client = Client(enforce_csrf_checks=False)
        self.url = reverse("batch_list")
        self.login_url = reverse("login")
        self.user = UserFactory.create_data_operator(username="batch_view_op")

    def _auth_client(self, user=None):
        client = Client(enforce_csrf_checks=False)
        client.force_login(user or self.user)
        return client

    def _make_batch(self, file_name="loan_tape.csv", source_type=None, status=None, **kwargs):
        return UploadBatch.objects.create(
            uploaded_by=self.user,
            file_name=file_name,
            source_type=source_type or UploadBatch.SourceType.LOAN_TAPE,
            status=status or UploadBatch.BatchStatus.INGESTED,
            **kwargs,
        )

    # ── Authentication & Permission Gating (Negative) ──

    def test_get_unauthenticated_redirects_to_login(self):
        response = self.client.get(self.url)
        assert response.status_code == 302
        assert self.login_url in response.url

    def test_get_superuser_denied(self):
        superuser = UserFactory.create_superuser(username="batch_admin")
        client = self._auth_client(superuser)
        response = client.get(self.url)
        assert response.status_code == 403

    def test_get_unpermitted_role_denied(self):
        reviewer = UserFactory.create_reviewer(username="batch_reviewer")
        client = self._auth_client(reviewer)
        response = client.get(self.url)
        assert response.status_code == 403

    # ── HTTP Method Enforcement (Negative) ──

    def test_post_returns_method_not_allowed(self):
        client = self._auth_client()
        response = client.post(self.url)
        assert response.status_code == 405

    # ── Positive Rendering & Context ──

    def test_get_renders_batches_tab_template(self):
        self._make_batch()
        client = self._auth_client()
        response = client.get(self.url)
        assert response.status_code == 200
        assert response.templates[0].name == ("dashboard/operator/includes/batches_tab.html")

    def test_get_returns_filter_and_paginator_in_context(self):
        self._make_batch()
        client = self._auth_client()
        response = client.get(self.url)
        assert response.status_code == 200
        assert "filter" in response.context
        assert "batches_page" in response.context
        assert "batches" in response.context
        assert "tab_visible" in response.context
        assert response.context["tab_visible"] is True

    def test_get_with_empty_database_returns_200(self):
        client = self._auth_client()
        response = client.get(self.url)
        assert response.status_code == 200
        assert list(response.context["batches"]) == []

    def test_get_returns_only_batches_on_that_page(self):
        for i in range(2):
            self._make_batch(file_name=f"batch_{i}.csv")
        client = self._auth_client()
        response = client.get(self.url)
        batches = list(response.context["batches"])
        assert len(batches) == 2

    # ── Pagination (Edge / Positive) ──

    def test_get_page_one_returns_first_ten_batches(self):
        for i in range(12):
            self._make_batch(file_name=f"batch_{i:03d}.csv", total_records=i + 1)
        client = self._auth_client()
        response = client.get(self.url)
        assert response.status_code == 200
        assert len(response.context["batches"]) == 10
        assert response.context["batches_page"].has_next()
        assert not response.context["batches_page"].has_previous()

    def test_get_second_page_returns_remaining_batches(self):
        for i in range(12):
            self._make_batch(file_name=f"batch_{i:03d}.csv", total_records=i + 1)
        client = self._auth_client()
        response = client.get(self.url, {"page": 2})
        assert response.status_code == 200
        assert len(response.context["batches"]) == 2

    def test_get_page_beyond_last_clamps_to_final_page(self):
        # Django's Paginator.get_page(99) clamps out-of-range pages to the last
        # available page rather than raising or returning an empty page.
        for i in range(5):
            self._make_batch(file_name=f"batch_{i}.csv")
        client = self._auth_client()
        response = client.get(self.url, {"page": 99})
        assert response.status_code == 200
        assert len(response.context["batches"]) == 5

    def test_get_page_non_integer_falls_back_to_page_one(self):
        for i in range(3):
            self._make_batch(file_name=f"batch_{i}.csv")
        client = self._auth_client()
        response = client.get(self.url, {"page": "abc"})
        assert response.status_code == 200
        assert len(response.context["batches"]) == 3

    # ── Filtering: source_type (Positive / Invalid) ──

    def test_filter_by_source_type(self):
        self._make_batch(file_name="loan_tape.csv", source_type=UploadBatch.SourceType.LOAN_TAPE)
        self._make_batch(
            file_name="servicer.csv", source_type=UploadBatch.SourceType.SERVICER_UPDATE
        )
        client = self._auth_client()
        response = client.get(
            self.url, {"source_type": str(UploadBatch.SourceType.SERVICER_UPDATE)}
        )
        batches = list(response.context["batches"])
        assert len(batches) == 1
        assert batches[0].file_name == "servicer.csv"

    def test_filter_by_invalid_source_type_gracefully_returns_all(self):
        # ChoiceFilter silently ignores invalid choice values: the queryset falls
        # back to the unfiltered full list rather than erroring.
        self._make_batch(source_type=UploadBatch.SourceType.LOAN_TAPE)
        self._make_batch(source_type=UploadBatch.SourceType.DOCUMENT_MANIFEST)
        client = self._auth_client()
        response = client.get(self.url, {"source_type": "999"})
        assert response.status_code == 200
        assert len(response.context["batches"]) == 2

    # ── Filtering: status (Positive / Invalid) ──

    def test_filter_by_status(self):
        self._make_batch(file_name="failed.csv", status=UploadBatch.BatchStatus.FAILED)
        self._make_batch(file_name="ok.csv", status=UploadBatch.BatchStatus.INGESTED)
        client = self._auth_client()
        response = client.get(self.url, {"status": str(UploadBatch.BatchStatus.FAILED)})
        batches = list(response.context["batches"])
        assert len(batches) == 1
        assert batches[0].file_name == "failed.csv"

    def test_filter_by_invalid_status_gracefully_returns_all(self):
        # Invalid ChoiceFilter status value falls back to the unfiltered list.
        self._make_batch(status=UploadBatch.BatchStatus.INGESTED)
        self._make_batch(status=UploadBatch.BatchStatus.FAILED)
        client = self._auth_client()
        response = client.get(self.url, {"status": "999"})
        assert response.status_code == 200
        assert len(response.context["batches"]) == 2

    # ── Filtering: search query `q` (Positive / Edge) ──

    def test_search_by_filename_substring(self):
        self._make_batch(file_name="daily_loan_tape.csv")
        self._make_batch(file_name="monthly_servicer.csv")
        client = self._auth_client()
        response = client.get(self.url, {"q": "loan_tape"})
        batches = list(response.context["batches"])
        assert len(batches) == 1
        assert batches[0].file_name == "daily_loan_tape.csv"

    def test_search_by_case_insensitive_filename(self):
        self._make_batch(file_name="DISBURSEMENT_REPORT.csv")
        client = self._auth_client()
        response = client.get(self.url, {"q": "disbursement"})
        assert len(response.context["batches"]) == 1

    def test_search_by_batch_id(self):
        batch = self._make_batch(file_name="target.csv")
        self._make_batch(file_name="other.csv")
        client = self._auth_client()
        response = client.get(self.url, {"q": str(batch.id)})
        assert len(response.context["batches"]) == 1

    def test_search_by_hashtag_batch_id(self):
        batch = self._make_batch(file_name="target.csv")
        client = self._auth_client()
        response = client.get(self.url, {"q": f"#{batch.id}"})
        assert len(response.context["batches"]) == 1

    def test_search_no_match_returns_empty(self):
        self._make_batch(file_name="target.csv")
        client = self._auth_client()
        response = client.get(self.url, {"q": "does_not_exist"})
        assert len(response.context["batches"]) == 0

    def test_search_empty_string_returns_all(self):
        for i in range(3):
            self._make_batch(file_name=f"b{i}.csv")
        client = self._auth_client()
        response = client.get(self.url, {"q": ""})
        assert len(response.context["batches"]) == 3


@pytest.mark.django_db
class TestFailedRowListView:
    """Test cases for the FailedRowListView endpoint (`/ingest/failed-rows/`)."""

    def setup_method(self):
        self.client = Client(enforce_csrf_checks=False)
        self.url = reverse("failed_row_list")
        self.login_url = reverse("login")
        self.user = UserFactory.create_data_operator(username="failed_view_op")

    def _auth_client(self, user=None):
        client = Client(enforce_csrf_checks=False)
        client.force_login(user or self.user)
        return client

    def _make_batch(self, file_name="loan_tape.csv"):
        return UploadBatch.objects.create(
            uploaded_by=self.user,
            file_name=file_name,
            source_type=UploadBatch.SourceType.LOAN_TAPE,
            status=UploadBatch.BatchStatus.PARTIAL_SUCCESS,
        )

    def _make_failed_row(
        self, batch, reason="Invalid column count", raw_line="a,b,c", row_number=2
    ):
        return FailedImportRow.objects.create(
            batch=batch,
            failure_reason=reason,
            raw_line=raw_line,
            row_number=row_number,
        )

    # ── Authentication & Permission Gating (Negative) ──

    def test_get_unauthenticated_redirects_to_login(self):
        response = self.client.get(self.url)
        assert response.status_code == 302
        assert self.login_url in response.url

    def test_get_superuser_denied(self):
        superuser = UserFactory.create_superuser(username="failed_admin")
        client = self._auth_client(superuser)
        response = client.get(self.url)
        assert response.status_code == 403

    def test_get_unpermitted_role_denied(self):
        consumer = UserFactory.create_data_consumer(username="failed_consumer")
        client = self._auth_client(consumer)
        response = client.get(self.url)
        assert response.status_code == 403

    # ── HTTP Method Enforcement (Negative) ──

    def test_post_returns_method_not_allowed(self):
        client = self._auth_client()
        response = client.post(self.url)
        assert response.status_code == 405

    # ── Positive Rendering & Context ──

    def test_get_renders_failed_tab_template(self):
        batch = self._make_batch()
        self._make_failed_row(batch)
        client = self._auth_client()
        response = client.get(self.url)
        assert response.status_code == 200
        assert response.templates[0].name == ("dashboard/operator/includes/failed_tab.html")

    def test_get_returns_expected_context_keys(self):
        batch = self._make_batch()
        self._make_failed_row(batch)
        client = self._auth_client()
        response = client.get(self.url)
        ctx = response.context
        assert "filter" in ctx
        assert "failed_rows_page" in ctx
        assert "failed_rows" in ctx
        assert "failed_batches" in ctx
        assert "selected_batch_id" in ctx
        assert "total_failed_rows" in ctx
        assert "tab_visible" in ctx

    def test_get_empty_database_returns_200_and_fallback_batches(self):
        client = self._auth_client()
        response = client.get(self.url)
        assert response.status_code == 200
        assert list(response.context["failed_rows"]) == []
        assert response.context["total_failed_rows"] == 0

    def test_get_returns_all_failed_rows(self):
        batch = self._make_batch()
        for i in range(3):
            self._make_failed_row(batch, row_number=i + 1)
        client = self._auth_client()
        response = client.get(self.url)
        assert len(response.context["failed_rows"]) == 3
        assert response.context["total_failed_rows"] == 3

    # ── Pagination (Edge / Positive) ──

    def test_get_page_one_returns_first_ten_rows(self):
        batch = self._make_batch()
        for i in range(12):
            self._make_failed_row(batch, row_number=i + 1)
        client = self._auth_client()
        response = client.get(self.url)
        assert len(response.context["failed_rows"]) == 10

    def test_get_second_page_returns_remaining_rows(self):
        batch = self._make_batch()
        for i in range(12):
            self._make_failed_row(batch, row_number=i + 1)
        client = self._auth_client()
        response = client.get(self.url, {"page": 2})
        assert len(response.context["failed_rows"]) == 2

    def test_get_page_beyond_last_clamps_to_final_page(self):
        # Paginator.get_page clamps out-of-range pages to the last available page.
        batch = self._make_batch()
        for i in range(5):
            self._make_failed_row(batch, row_number=i + 1)
        client = self._auth_client()
        response = client.get(self.url, {"page": 99})
        assert response.status_code == 200
        assert len(response.context["failed_rows"]) == 5

    def test_get_non_integer_page_falls_back_to_page_one(self):
        batch = self._make_batch()
        for i in range(3):
            self._make_failed_row(batch, row_number=i + 1)
        client = self._auth_client()
        response = client.get(self.url, {"page": "-1x"})
        assert len(response.context["failed_rows"]) == 3

    # ── Filtering: search query `q` (Positive / Edge) ──

    def test_search_by_failure_reason_substring(self):
        batch = self._make_batch()
        self._make_failed_row(batch, reason="Header mismatch detected", raw_line="x")
        self._make_failed_row(batch, reason="Encoding not utf-8", raw_line="y")
        client = self._auth_client()
        response = client.get(self.url, {"q": "encoding"})
        assert len(response.context["failed_rows"]) == 1

    def test_search_by_raw_line_substring(self):
        batch = self._make_batch()
        self._make_failed_row(batch, raw_line="malformed,row", reason="parse")
        self._make_failed_row(batch, raw_line="good,row", reason="other")
        client = self._auth_client()
        response = client.get(self.url, {"q": "malformed"})
        assert len(response.context["failed_rows"]) == 1

    def test_search_by_batch_id(self):
        batch = self._make_batch(file_name="loan_tape.csv")
        other_batch = self._make_batch(file_name="servicer.csv")
        self._make_failed_row(batch)
        self._make_failed_row(other_batch)
        client = self._auth_client()
        response = client.get(self.url, {"q": str(batch.id)})
        assert len(response.context["failed_rows"]) == 1

    def test_search_by_hashtag_batch_id(self):
        batch = self._make_batch(file_name="loan_tape.csv")
        other = self._make_batch(file_name="other.csv")
        self._make_failed_row(batch)
        self._make_failed_row(other)
        client = self._auth_client()
        response = client.get(self.url, {"q": f"#{batch.id}"})
        assert len(response.context["failed_rows"]) == 1

    def test_search_case_insensitive(self):
        batch = self._make_batch()
        self._make_failed_row(batch, reason="MULTI-BAIT FAILURE")
        client = self._auth_client()
        response = client.get(self.url, {"q": "multi-bait"})
        assert len(response.context["failed_rows"]) == 1

    def test_search_no_match_returns_empty(self):
        batch = self._make_batch()
        self._make_failed_row(batch)
        client = self._auth_client()
        response = client.get(self.url, {"q": "nothing_matches"})
        assert len(response.context["failed_rows"]) == 0

    # ── Filtering: batch_id (Positive / Invalid) ──

    def test_filter_by_batch_id(self):
        batch_a = self._make_batch(file_name="a.csv")
        batch_b = self._make_batch(file_name="b.csv")
        self._make_failed_row(batch_a)
        self._make_failed_row(batch_a)
        self._make_failed_row(batch_b)
        client = self._auth_client()
        response = client.get(self.url, {"batch_id": str(batch_a.id)})
        assert len(response.context["failed_rows"]) == 2
        assert response.context["selected_batch_id"] == str(batch_a.id)

    def test_filter_by_non_existent_batch_id_gracefully_returns_all(self):
        # ModelChoiceFilter silently ignores a non-existent pk, falling back to
        # the unfiltered list rather than erroring.
        batch = self._make_batch()
        self._make_failed_row(batch)
        client = self._auth_client()
        response = client.get(self.url, {"batch_id": "99999"})
        assert response.status_code == 200
        assert len(response.context["failed_rows"]) == 1

    # ── Failed batches fallback (Edge) ──

    def test_failed_batches_lists_only_batches_with_failures(self):
        batch_with_failure = self._make_batch(file_name="with_failure.csv")
        self._make_batch(file_name="no_failure.csv")
        self._make_failed_row(batch_with_failure)
        client = self._auth_client()
        response = client.get(self.url)
        failed_batches = list(response.context["failed_batches"])
        assert len(failed_batches) == 1
        assert failed_batches[0].file_name == "with_failure.csv"

    def test_failed_batches_falls_back_to_recent_when_no_failures(self):
        for i in range(3):
            self._make_batch(file_name=f"recent_{i}.csv")
        client = self._auth_client()
        response = client.get(self.url)
        failed_batches = list(response.context["failed_batches"])
        assert len(failed_batches) == 3
