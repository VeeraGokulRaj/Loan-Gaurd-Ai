"""
Test cases for app.views.ingestion.ingest_pipeline_view.

Covers authentication gating, HTTP method enforcement, missing-file validation
(HTMX + non-HTMX), and successful HTMX/non-HTMX ingestion flows.
"""

import pytest
from django.test import Client
from django.urls import reverse

from app.models import UploadBatch
from tests.factory.ingestion_factory import IngestionFactory
from tests.factory.user_factory import UserFactory


@pytest.mark.django_db
class TestIngestPipelineView:
    """Test cases for the ingest_pipeline_view endpoint."""

    def setup_method(self):
        self.client = Client(enforce_csrf_checks=False)
        self.url = reverse("ingest_pipeline")
        self.dashboard_url = reverse("dashboard")
        self.login_url = reverse("login")
        self.user = UserFactory.create_data_operator(username="op_view")

    def _auth_client(self):
        client = Client(enforce_csrf_checks=False)
        client.force_login(self.user)
        return client

    def _valid_files_post_data(self, files=None):
        if files is not None:
            return files
        return {
            "loan_tape_file": IngestionFactory.loan_tape_file(),
            "servicer_update_file": IngestionFactory.servicer_update_file(),
            "document_manifest_file": IngestionFactory.document_manifest_file(),
        }

    # ── Authentication Gating (Negative) ──

    def test_get_unauthenticated_redirects_to_login(self):
        """Unauthenticated GET should redirect to the login page."""
        response = self.client.get(self.url)
        assert response.status_code == 302
        assert self.login_url in response.url

    def test_post_unauthenticated_redirects_to_login(self):
        """Unauthenticated POST should redirect to the login page."""
        response = self.client.post(self.url, data=self._valid_files_post_data())
        assert response.status_code == 302
        assert self.login_url in response.url

    # ── HTTP Method Enforcement (Negative) ──

    def test_get_with_login_returns_bad_request(self):
        """Authenticated GET should return 400 Invalid request method."""
        client = self._auth_client()
        response = client.get(self.url)
        assert response.status_code == 400
        assert b"Invalid request method" in response.content

    # ── Missing Files Validation (Negative) ──

    def test_post_missing_all_files_redirects_to_dashboard(self):
        """POST without any files should redirect to dashboard with an error message."""
        client = self._auth_client()
        response = client.post(self.url, data={}, follow=True)
        assert response.status_code == 200
        texts = [str(m) for m in response.context["messages"]]
        assert any("All 3 CSV files must be selected" in t for t in texts)
        assert any(
            "Primary Loan Tape" in t and "Servicer Update" in t and "Document Manifest" in t
            for t in texts
        )

    def test_post_missing_one_file_redirects_to_dashboard(self):
        """POST missing only the loan tape should list it in the error message."""
        client = self._auth_client()
        data = self._valid_files_post_data()
        del data["loan_tape_file"]
        response = client.post(self.url, data=data, follow=True)
        texts = [str(m) for m in response.context["messages"]]
        assert any("Primary Loan Tape" in t for t in texts)
        assert not any("Servicer Update" in t and "Document Manifest" in t for t in texts)

    def test_post_missing_all_files_htmx_returns_bad_request_html(self):
        """HTMX POST with missing files should return 400 with inline HTML error."""
        client = self._auth_client()
        response = client.post(
            self.url,
            data={},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 400
        assert b"Missing Required Ingestion Files" in response.content
        assert b"All 3 CSV files must be selected" in response.content

    def test_post_missing_files_does_not_create_batches(self):
        """POST blocked by missing files should not create any UploadBatch."""
        client = self._auth_client()
        client.post(self.url, data={})
        assert UploadBatch.objects.count() == 0

    # ── Successful Ingestion (Positive) ──

    def test_post_all_files_non_htmx_redirects_to_dashboard(self):
        """POST with all files (non-HTMX) should redirect to dashboard with success message."""
        client = self._auth_client()
        response = client.post(self.url, data=self._valid_files_post_data(), follow=True)
        assert response.status_code == 200
        texts = [str(m) for m in response.context["messages"]]
        assert any("Ingestion pipeline completed successfully" in t for t in texts)

    def test_post_all_files_creates_batches(self):
        """POST with all files should create 3 UploadBatch records."""
        client = self._auth_client()
        client.post(self.url, data=self._valid_files_post_data())
        assert UploadBatch.objects.count() == 3

    def test_post_all_files_htmx_renders_partial(self):
        """HTMX POST with all files should render the session summary partial."""
        client = self._auth_client()
        response = client.post(
            self.url,
            data=self._valid_files_post_data(),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert b"dynamicSessionSummary" in response.content
        assert b"Total Rows Processed" in response.content
        assert response.templates[0].name == (
            "dashboard/operator/includes/session_summary_partial.html"
        )

    def test_post_all_files_htmx_context_has_summary(self):
        """HTMX response context should carry expected summary values."""
        client = self._auth_client()
        response = client.post(
            self.url,
            data=self._valid_files_post_data(),
            HTTP_HX_REQUEST="true",
        )
        summary = response.context["summary"]
        assert summary["total_session_rows"] == 3
        assert summary["total_session_success"] == 3
        assert len(summary["batch_results"]) == 3

    def test_post_all_files_sets_uploaded_by_to_request_user(self):
        """Batches should be attributed to the authenticated requesting user."""
        client = self._auth_client()
        client.post(self.url, data=self._valid_files_post_data())
        assert UploadBatch.objects.filter(uploaded_by=self.user).count() == 3
