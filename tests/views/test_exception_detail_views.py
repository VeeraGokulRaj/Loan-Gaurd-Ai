"""
Test cases for app.views.reviewer.ExceptionLoanDetailView and ExceptionActionHistoryView.

Covers authentication gating, role-based access denial, HTTP method enforcement,
GET context correctness (allowed field list, target-field flag, related exceptions,
raw data fallback), 404 handling, and POST decision flows (comment, field edits,
resolve approve/reject/correct) for the unified exception detail page and the
action history page.
"""

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.models import (
    AuditEvent,
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
class TestExceptionLoanDetailViewGet:
    """Test cases for ExceptionLoanDetailView GET (`/reviewer/exceptions/<pk>/detail/`)."""

    def setup_method(self):
        self.client = Client(enforce_csrf_checks=False)
        self.login_url = reverse("login")
        self.reviewer = UserFactory.create_reviewer(username="detail_get_reviewer")
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
        raw_data=None,
        **kwargs,
    ):
        rule = _rule(rule_code=rule_code)
        record = RawLoanRecord.objects.create(
            batch=self.batch,
            row_number=1,
            raw_data=raw_data if raw_data is not None else {"loan_id": "LG-1"},
        )
        defaults = {
            "batch": self.batch,
            "raw_record": record,
            "rule": rule,
            "rule_code": rule_code,
            "field_name": field_name,
            "severity": severity,
            "description": f"sample description for {rule_code}",
            "status": status,
        }
        defaults.update(kwargs)
        return LoanException.objects.create(**defaults), record

    def _url(self, pk):
        return reverse("exception_loan_detail", kwargs={"pk": pk})

    # ── Authentication & Permission Gating (Negative) ──

    def test_get_unauthenticated_redirects_to_login(self):
        exc, _ = self._make_exception()
        response = self.client.get(self._url(exc.id))
        assert response.status_code == 302
        assert self.login_url in response.url

    def test_get_superuser_denied(self):
        superuser = UserFactory.create_superuser(username="detail_admin")
        exc, _ = self._make_exception()
        assert self._auth_client(superuser).get(self._url(exc.id)).status_code == 403

    def test_get_data_operator_denied(self):
        operator = UserFactory.create_data_operator(username="detail_op")
        exc, _ = self._make_exception()
        assert self._auth_client(operator).get(self._url(exc.id)).status_code == 403

    def test_get_data_consumer_denied(self):
        consumer = UserFactory.create_data_consumer(username="detail_consumer")
        exc, _ = self._make_exception()
        assert self._auth_client(consumer).get(self._url(exc.id)).status_code == 403

    # ── 404 Handling (Invalid) ──

    def test_get_nonexistent_exception_returns_404(self):
        response = self._auth_client().get(self._url(999999))
        assert response.status_code == 404

    # ── Positive Rendering & Context ──

    def test_get_renders_exception_detail_template(self):
        exc, _ = self._make_exception()
        response = self._auth_client().get(self._url(exc.id))
        assert response.status_code == 200
        assert response.templates[0].name == "dashboard/reviewer/detail/exception_detail.html"

    def test_get_context_has_expected_keys(self):
        exc, _ = self._make_exception()
        ctx = self._auth_client().get(self._url(exc.id)).context
        for key in ("title", "exc", "raw_data", "field_list", "related_exceptions", "user"):
            assert key in ctx
        assert ctx["exc"].id == exc.id

    def test_get_title_contains_exception_identifier(self):
        exc, _ = self._make_exception()
        ctx = self._auth_client().get(self._url(exc.id)).context
        assert ctx["title"] == f"Loan Exception - #EXP-{exc.id}"

    def test_field_list_length_matches_allowed_fields(self):
        exc, _ = self._make_exception()
        from app.views.reviewer import ALLOWED_FIELDS

        ctx = self._auth_client().get(self._url(exc.id)).context
        assert len(ctx["field_list"]) == len(ALLOWED_FIELDS)

    def test_field_list_entries_have_expected_structure(self):
        exc, _ = self._make_exception(
            rule_code="VAL_002", raw_data={"loan_id": "LG-7", "borrower_name": "Priya"}
        )
        ctx = self._auth_client().get(self._url(exc.id)).context
        entry = next(f for f in ctx["field_list"] if f["key"] == "borrower_name")
        assert entry["label"] == "Borrower Name"
        assert entry["value"] == "Priya"
        assert entry["is_target"] is False

    def test_target_field_is_marked_in_field_list(self):
        exc, _ = self._make_exception(rule_code="VAL_003", field_name="current_balance")
        ctx = self._auth_client().get(self._url(exc.id)).context
        targets = [f for f in ctx["field_list"] if f["is_target"]]
        assert len(targets) == 1
        assert targets[0]["key"] == "current_balance"

    def test_raw_data_falls_back_to_empty_dict(self):
        rule = _rule(rule_code="VAL_004")
        record = RawLoanRecord.objects.create(batch=self.batch, row_number=1, raw_data=None)
        exc = LoanException.objects.create(
            batch=self.batch,
            raw_record=record,
            rule=rule,
            rule_code="VAL_004",
            field_name="loan_id",
            severity=ValidationSeverity.HIGH,
            description="no raw payload",
            status=LoanException.ExceptionStatus.OPEN,
        )
        ctx = self._auth_client().get(self._url(exc.id)).context
        assert ctx["raw_data"] == {}

    def test_related_exceptions_exclude_self_and_order_by_severity(self):
        rule = _rule(rule_code="VAL_005")
        record = RawLoanRecord.objects.create(
            batch=self.batch, row_number=1, raw_data={"loan_id": "LG-1"}
        )
        low = LoanException.objects.create(
            batch=self.batch,
            raw_record=record,
            rule=rule,
            rule_code="VAL_005",
            field_name="loan_id",
            severity=ValidationSeverity.LOW,
            description="low severity",
            status=LoanException.ExceptionStatus.OPEN,
        )
        crit_rule = _rule(rule_code="VAL_006")
        critical = LoanException.objects.create(
            batch=self.batch,
            raw_record=record,
            rule=crit_rule,
            rule_code="VAL_006",
            field_name="loan_id",
            severity=ValidationSeverity.CRITICAL,
            description="critical severity",
            status=LoanException.ExceptionStatus.OPEN,
        )
        ctx = self._auth_client().get(self._url(low.id)).context
        related_ids = [e.id for e in ctx["related_exceptions"]]
        assert related_ids == [critical.id]
        assert low.id not in related_ids


@pytest.mark.django_db
class TestExceptionLoanDetailViewPost:
    """Test cases for ExceptionLoanDetailView POST (`/reviewer/exceptions/<pk>/detail/`)."""

    def setup_method(self):
        self.client = Client(enforce_csrf_checks=False)
        self.login_url = reverse("login")
        self.reviewer = UserFactory.create_reviewer(username="detail_post_reviewer")
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
        raw_data=None,
        **kwargs,
    ):
        rule = _rule(rule_code=rule_code)
        record = RawLoanRecord.objects.create(
            batch=self.batch,
            row_number=1,
            raw_data=raw_data if raw_data is not None else {"loan_id": "LG-1"},
        )
        defaults = {
            "batch": self.batch,
            "raw_record": record,
            "rule": rule,
            "rule_code": rule_code,
            "field_name": field_name,
            "severity": severity,
            "description": f"sample description for {rule_code}",
            "status": status,
        }
        defaults.update(kwargs)
        return LoanException.objects.create(**defaults), record

    def _url(self, pk):
        return reverse("exception_loan_detail", kwargs={"pk": pk})

    # ── 404 & Authentication Gating (Invalid / Negative) ──

    def test_post_unauthenticated_redirects_to_login(self):
        response = self.client.post(self._url(1), {"action_type": "save_comment"})
        assert response.status_code == 302
        assert self.login_url in response.url

    def test_post_superuser_denied(self):
        superuser = UserFactory.create_superuser(username="detail_post_admin")
        exc, _ = self._make_exception()
        response = self._auth_client(superuser).post(
            self._url(exc.id), {"action_type": "save_comment"}
        )
        assert response.status_code == 403

    def test_post_nonexistent_exception_returns_404(self):
        response = self._auth_client().post(self._url(999999), {"action_type": "save_comment"})
        assert response.status_code == 404

    # ── Invalid Action Type (Negative) ──

    def test_post_invalid_action_redirects_with_error_message(self):
        exc, _ = self._make_exception(rule_code="VAL_101")
        response = self._auth_client().post(
            self._url(exc.id), {"action_type": "nonsense"}, follow=True
        )
        assert response.status_code == 200
        assert response.redirect_chain[-1][0].endswith(self._url(exc.id))
        assert any(m.level_tag == "error" for m in response.context["messages"])
        exc.refresh_from_db()
        assert exc.status == LoanException.ExceptionStatus.OPEN

    def test_post_empty_action_type_redirects_with_error_message(self):
        exc, _ = self._make_exception(rule_code="VAL_102")
        response = self._auth_client().post(self._url(exc.id), {"action_type": ""}, follow=True)
        assert response.redirect_chain[-1][0].endswith(self._url(exc.id))
        assert any(m.level_tag == "error" for m in response.context["messages"])

    # ── save_comment (Positive / Edge) ──

    def test_post_save_comment_persists_and_redirects(self):
        exc, _ = self._make_exception(rule_code="VAL_103")
        response = self._auth_client().post(
            self._url(exc.id),
            {"action_type": "save_comment", "reviewer_comment": "Looked into this thoroughly"},
            follow=True,
        )
        assert response.status_code == 200
        assert response.redirect_chain[-1][0].endswith(self._url(exc.id))
        assert any(m.level_tag == "success" for m in response.context["messages"])
        exc.refresh_from_db()
        assert exc.reviewer_comment == "Looked into this thoroughly"
        event = AuditEvent.objects.get(event_type="REVIEWER_COMMENT_ADDED")
        assert event.actor_id == self.reviewer.id
        assert event.loan_id == "LG-1"

    def test_post_save_comment_blank_reports_success_but_does_not_persist(self):
        exc, _ = self._make_exception(rule_code="VAL_104")
        response = self._auth_client().post(
            self._url(exc.id),
            {"action_type": "save_comment", "reviewer_comment": "   "},
            follow=True,
        )
        assert response.redirect_chain[-1][0].endswith(self._url(exc.id))
        assert any(m.level_tag == "success" for m in response.context["messages"])
        exc.refresh_from_db()
        assert exc.reviewer_comment in (None, "")
        assert not AuditEvent.objects.filter(event_type="REVIEWER_COMMENT_ADDED").exists()

    # ── save_fields (Positive) ──

    def test_post_save_fields_updates_raw_data_and_resolves(self):
        exc, record = self._make_exception(
            rule_code="VAL_105",
            field_name="borrower_name",
            raw_data={"loan_id": "LG-1", "borrower_name": "Old Name", "current_balance": "1000.00"},
        )
        response = self._auth_client().post(
            self._url(exc.id),
            {
                "action_type": "save_fields",
                "reviewer_comment": "  data fixed  ",
                "loan_id": "LG-1",
                "borrower_name": "New Name",
                "current_balance": "1000.00",
            },
            follow=True,
        )
        assert response.status_code == 200
        assert response.redirect_chain[-1][0].endswith(self._url(exc.id))
        assert any(m.level_tag == "success" for m in response.context["messages"])
        record.refresh_from_db()
        refreshed = LoanException.objects.get(pk=exc.id)
        assert record.raw_data["borrower_name"] == "New Name"
        assert refreshed.status == LoanException.ExceptionStatus.RESOLVED_EDITED
        assert refreshed.reviewer_comment == "data fixed"
        assert refreshed.resolved_by_id == self.reviewer.id
        assert refreshed.resolved_at is not None
        assert AuditEvent.objects.filter(event_type="LOAN_RECORD_FIELD_EDITED").exists()

    def test_post_save_fields_target_field_sets_override_value(self):
        exc, _ = self._make_exception(
            rule_code="VAL_106",
            field_name="interest_rate",
            raw_data={"loan_id": "LG-1", "interest_rate": "8.5"},
        )
        self._auth_client().post(
            self._url(exc.id),
            {"action_type": "save_fields", "loan_id": "LG-1", "interest_rate": "9.0"},
            follow=True,
        )
        refreshed = LoanException.objects.get(pk=exc.id)
        assert refreshed.override_value == "9.0"
        assert refreshed.status == LoanException.ExceptionStatus.RESOLVED_EDITED

    # ── resolve_decision (Positive / Negative) ──

    def test_post_resolve_approve_redirects_to_dashboard(self):
        exc, _ = self._make_exception(rule_code="VAL_107")
        response = self._auth_client().post(
            self._url(exc.id),
            {
                "action_type": "resolve_decision",
                "decision": "approve",
                "override_value": "",
                "reviewer_comment": "approving",
            },
            follow=True,
        )
        assert response.status_code == 200
        assert response.redirect_chain[-1][0].endswith(reverse("reviewer_dashboard"))
        assert any(m.level_tag == "success" for m in response.context["messages"])
        refreshed = LoanException.objects.get(pk=exc.id)
        assert refreshed.status == LoanException.ExceptionStatus.RESOLVED_ACCEPTED
        assert refreshed.resolved_by_id == self.reviewer.id
        assert refreshed.resolved_at is not None
        assert AuditEvent.objects.filter(event_type="LOAN_APPROVED").exists()

    def test_post_resolve_reject_redirects_to_dashboard(self):
        exc, _ = self._make_exception(rule_code="VAL_108")
        response = self._auth_client().post(
            self._url(exc.id),
            {
                "action_type": "resolve_decision",
                "decision": "reject",
                "override_value": "",
                "reviewer_comment": "rejecting",
            },
            follow=True,
        )
        assert response.redirect_chain[-1][0].endswith(reverse("reviewer_dashboard"))
        refreshed = LoanException.objects.get(pk=exc.id)
        assert refreshed.status == LoanException.ExceptionStatus.REJECTED
        assert AuditEvent.objects.filter(event_type="LOAN_REJECTED").exists()

    def test_post_resolve_correct_applies_override(self):
        exc, record = self._make_exception(
            rule_code="VAL_109",
            field_name="current_balance",
            raw_data={"loan_id": "LG-1", "current_balance": "1000.00"},
        )
        response = self._auth_client().post(
            self._url(exc.id),
            {
                "action_type": "resolve_decision",
                "decision": "correct",
                "override_value": "  1200.00  ",
                "reviewer_comment": "corrected",
            },
            follow=True,
        )
        assert response.status_code == 200
        assert response.redirect_chain[-1][0].endswith(reverse("reviewer_dashboard"))
        record.refresh_from_db()
        refreshed = LoanException.objects.get(pk=exc.id)
        assert record.raw_data["current_balance"] == "1200.00"
        assert refreshed.override_value == "1200.00"
        assert refreshed.status == LoanException.ExceptionStatus.RESOLVED_EDITED
        assert AuditEvent.objects.filter(event_type="EXCEPTION_RESOLVED_EDITED").exists()

    def test_post_resolve_invalid_decision_redirects_to_detail_with_error(self):
        exc, _ = self._make_exception(rule_code="VAL_110")
        response = self._auth_client().post(
            self._url(exc.id),
            {
                "action_type": "resolve_decision",
                "decision": "banana",
                "override_value": "",
                "reviewer_comment": "",
            },
            follow=True,
        )
        assert response.redirect_chain[-1][0].endswith(self._url(exc.id))
        assert any(m.level_tag == "error" for m in response.context["messages"])
        refreshed = LoanException.objects.get(pk=exc.id)
        assert refreshed.status == LoanException.ExceptionStatus.OPEN
        assert refreshed.resolved_by_id is None
        assert not AuditEvent.objects.filter(
            event_type__in=["LOAN_APPROVED", "LOAN_REJECTED", "EXCEPTION_RESOLVED_EDITED"]
        ).exists()


@pytest.mark.django_db
class TestExceptionActionHistoryView:
    """Test cases for ExceptionActionHistoryView (`/reviewer/exceptions/<pk>/history/`)."""

    def setup_method(self):
        self.client = Client(enforce_csrf_checks=False)
        self.login_url = reverse("login")
        self.reviewer = UserFactory.create_reviewer(username="history_reviewer")
        self.batch = UploadBatch.objects.create(
            file_name="loan_tape.csv",
            source_type=UploadBatch.SourceType.LOAN_TAPE,
            status=UploadBatch.BatchStatus.INGESTED,
        )

    def _auth_client(self, user=None):
        client = Client(enforce_csrf_checks=False)
        client.force_login(user or self.reviewer)
        return client

    def _make_exception(self, rule_code="VAL_001", raw_data=None, **kwargs):
        rule = _rule(rule_code=rule_code)
        record = RawLoanRecord.objects.create(
            batch=self.batch,
            row_number=1,
            raw_data=raw_data if raw_data is not None else {"loan_id": "LG-1"},
        )
        defaults = {
            "batch": self.batch,
            "raw_record": record,
            "rule": rule,
            "rule_code": rule_code,
            "field_name": "loan_id",
            "severity": ValidationSeverity.HIGH,
            "description": f"sample description for {rule_code}",
            "status": LoanException.ExceptionStatus.OPEN,
        }
        defaults.update(kwargs)
        return LoanException.objects.create(**defaults), record

    def _url(self, pk):
        return reverse("exception_action_history", kwargs={"pk": pk})

    def _log_event(self, exc, event_type, loan_id=None, payload=None, timestamp=None):
        return AuditEvent.objects.create(
            timestamp=timestamp if timestamp is not None else timezone.now(),
            event_type=event_type,
            actor=self.reviewer,
            actor_role=AuditEvent.ActorRole.REVIEWER,
            loan_id=loan_id,
            batch_id=exc.batch_id,
            payload=payload if payload is not None else {"exception_id": exc.id},
            prev_hash="0" * 64,
            event_hash="a" * 64,
        )

    # ── Authentication & Permission Gating (Negative) ──

    def test_get_unauthenticated_redirects_to_login(self):
        exc, _ = self._make_exception()
        response = self.client.get(self._url(exc.id))
        assert response.status_code == 302
        assert self.login_url in response.url

    def test_get_superuser_denied(self):
        superuser = UserFactory.create_superuser(username="history_admin")
        exc, _ = self._make_exception()
        assert self._auth_client(superuser).get(self._url(exc.id)).status_code == 403

    def test_get_data_operator_denied(self):
        operator = UserFactory.create_data_operator(username="history_op")
        exc, _ = self._make_exception()
        assert self._auth_client(operator).get(self._url(exc.id)).status_code == 403

    def test_get_data_consumer_denied(self):
        consumer = UserFactory.create_data_consumer(username="history_consumer")
        exc, _ = self._make_exception()
        assert self._auth_client(consumer).get(self._url(exc.id)).status_code == 403

    # ── HTTP Method Enforcement (Negative) ──

    def test_post_returns_method_not_allowed(self):
        exc, _ = self._make_exception()
        assert self._auth_client().post(self._url(exc.id)).status_code == 405

    # ── 404 Handling (Invalid) ──

    def test_get_nonexistent_exception_returns_404(self):
        response = self._auth_client().get(self._url(999999))
        assert response.status_code == 404

    # ── Positive Rendering & Context ──

    def test_get_renders_exception_history_template(self):
        exc, _ = self._make_exception()
        response = self._auth_client().get(self._url(exc.id))
        assert response.status_code == 200
        assert response.templates[0].name == "dashboard/reviewer/detail/exception_history.html"

    def test_get_context_has_expected_keys(self):
        exc, _ = self._make_exception()
        ctx = self._auth_client().get(self._url(exc.id)).context
        for key in ("title", "exc", "audit_events", "user"):
            assert key in ctx
        assert ctx["title"] == f"Action History - #EXP-{exc.id}"

    def test_get_with_no_audit_events_returns_empty(self):
        exc, _ = self._make_exception()
        ctx = self._auth_client().get(self._url(exc.id)).context
        assert list(ctx["audit_events"]) == []

    # ── Filtering (Positive / Negative / Edge) ──

    def test_get_filters_events_by_loan_id(self):
        exc, _ = self._make_exception()
        self._log_event(exc, "LOAN_APPROVED", loan_id="LG-1")
        self._log_event(exc, "UNRELATED", loan_id="LG-999", payload={})
        ctx = self._auth_client().get(self._url(exc.id)).context
        types = [e.event_type for e in ctx["audit_events"]]
        assert types == ["LOAN_APPROVED"]

    def test_get_filters_events_by_payload_exception_id(self):
        exc, _ = self._make_exception()
        self._log_event(
            exc, "REVIEWER_COMMENT_ADDED", loan_id=None, payload={"exception_id": exc.id}
        )
        self._log_event(exc, "OTHER", loan_id="LG-999", payload={"request_id": "R"})
        ctx = self._auth_client().get(self._url(exc.id)).context
        types = [e.event_type for e in ctx["audit_events"]]
        assert types == ["REVIEWER_COMMENT_ADDED"]

    def test_get_combines_loan_id_and_exception_id_matches(self):
        exc, _ = self._make_exception()
        self._log_event(exc, "LOAN_APPROVED", loan_id="LG-1")
        self._log_event(exc, "COMMENTED", loan_id=None, payload={"exception_id": exc.id})
        ctx = self._auth_client().get(self._url(exc.id)).context
        types = set(e.event_type for e in ctx["audit_events"])
        assert types == {"LOAN_APPROVED", "COMMENTED"}

    def test_get_without_loan_id_filters_only_by_exception_id(self):
        exc, _ = self._make_exception(raw_data={"borrower_name": "Priya"})
        assert exc.loan_id == ""
        self._log_event(exc, "LOAN_APPROVED", loan_id=None, payload={"exception_id": exc.id})
        self._log_event(exc, "STRAY", loan_id="LG-1", payload={})
        ctx = self._auth_client().get(self._url(exc.id)).context
        types = [e.event_type for e in ctx["audit_events"]]
        assert types == ["LOAN_APPROVED"]

    # ── Ordering (Positive) ──

    def test_get_orders_events_newest_first(self):
        exc, _ = self._make_exception()
        now = timezone.now()
        self._log_event(exc, "OLD_EVENT", loan_id="LG-1", timestamp=now - timedelta(hours=2))
        self._log_event(exc, "NEW_EVENT", loan_id="LG-1", timestamp=now)
        ctx = self._auth_client().get(self._url(exc.id)).context
        types = [e.event_type for e in ctx["audit_events"]]
        assert types == ["NEW_EVENT", "OLD_EVENT"]
