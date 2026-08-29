"""
Test cases for app.domain.exception_handling domain logic.

Covers the unified handle_exception_action dispatcher and its helpers:
save_reviewer_comment, log_exception_audit_event, save_exception_field_edits,
apply_exception_resolution, and resolve_exception_decision with positive,
negative, edge, boundary, and invalid input scenarios.
"""

import pytest

from app.domain.exception_handling import (
    apply_exception_resolution,
    handle_exception_action,
    resolve_exception_decision,
    save_exception_field_edits,
    save_reviewer_comment,
)
from app.models import (
    AuditEvent,
    LoanException,
    RawLoanRecord,
    UploadBatch,
    ValidationRule,
    ValidationSeverity,
)
from tests.factory.user_factory import UserFactory

ALLOWED = ["loan_id", "borrower_name", "current_balance"]


def _make_exception(
    batch,
    severity=ValidationSeverity.HIGH,
    status=LoanException.ExceptionStatus.OPEN,
    rule_code="VAL_001",
    field_name="loan_id",
    raw_data=None,
    **kwargs,
):
    rule = ValidationRule.objects.create(
        rule_code=rule_code,
        strategy_key="STRATEGY",
        rule_name=f"Rule {rule_code}",
        field_name=field_name,
        description=f"Description for {rule_code}",
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
    exc = LoanException.objects.create(**defaults)
    return exc, record


@pytest.mark.django_db
class TestHandleExceptionAction:
    """Test cases for handle_exception_action dispatcher."""

    def setup_method(self):
        self.reviewer = UserFactory.create_reviewer(username="domain_handle_reviewer")
        self.batch = UploadBatch.objects.create(
            file_name="loan_tape.csv",
            source_type=UploadBatch.SourceType.LOAN_TAPE,
            status=UploadBatch.BatchStatus.INGESTED,
        )

    def test_invalid_action_type_fails(self):
        exc, _ = _make_exception(self.batch, rule_code="H_001")
        success, message, target = handle_exception_action(exc, self.reviewer, "bogus", {})
        assert success is False
        assert f"Invalid action for Exception #EXP-{exc.id}." in message
        assert target == "exception_loan_detail"

    def test_empty_action_type_fails(self):
        exc, _ = _make_exception(self.batch, rule_code="H_002")
        success, message, target = handle_exception_action(exc, self.reviewer, "", {})
        assert success is False
        assert "Invalid action" in message
        assert target == "exception_loan_detail"

    def test_save_comment_success(self):
        exc, _ = _make_exception(self.batch, rule_code="H_003")
        success, message, target = handle_exception_action(
            exc, self.reviewer, "save_comment", {"reviewer_comment": "  verified ok  "}
        )
        assert success is True
        assert f"Exception #EXP-{exc.id} updated successfully." in message
        assert target == "exception_loan_detail"
        exc.refresh_from_db()
        assert exc.reviewer_comment == "verified ok"

    def test_save_comment_blank_reports_success_but_does_not_save(self):
        exc, _ = _make_exception(self.batch, rule_code="H_004")
        success, message, target = handle_exception_action(
            exc, self.reviewer, "save_comment", {"reviewer_comment": "   "}
        )
        assert success is True
        assert target == "exception_loan_detail"
        exc.refresh_from_db()
        assert exc.reviewer_comment in (None, "")
        assert not AuditEvent.objects.filter(event_type="REVIEWER_COMMENT_ADDED").exists()

    def test_save_fields_success(self):
        exc, _ = _make_exception(
            self.batch,
            rule_code="H_005",
            field_name="borrower_name",
            raw_data={"loan_id": "LG-1", "borrower_name": "Old"},
        )
        success, message, target = handle_exception_action(
            exc,
            self.reviewer,
            "save_fields",
            {"reviewer_comment": "  fix ", "borrower_name": "New", "loan_id": "LG-1"},
        )
        assert success is True
        assert target == "exception_loan_detail"
        exc.refresh_from_db()
        assert exc.raw_record.raw_data["borrower_name"] == "New"
        assert exc.status == LoanException.ExceptionStatus.RESOLVED_EDITED

    def test_save_fields_without_changes_reports_success_without_resolution(self):
        exc, _ = _make_exception(self.batch, rule_code="H_006", raw_data={"loan_id": "LG-1"})
        success, message, target = handle_exception_action(
            exc, self.reviewer, "save_fields", {"loan_id": "LG-1", "reviewer_comment": "note"}
        )
        assert success is True
        assert target == "exception_loan_detail"
        exc.refresh_from_db()
        assert exc.status == LoanException.ExceptionStatus.OPEN
        assert exc.reviewer_comment in (None, "")

    def test_save_fields_without_raw_record_raises_related_object_error(self):
        # The raw_record FK is non-nullable, so an orphan exception cannot reach the
        # guarded "no associated raw record" message; accessing the relation raises.
        from django.core.exceptions import ObjectDoesNotExist

        orphan = LoanException(
            rule_code="H_ORPHAN",
            field_name="loan_id",
            severity=ValidationSeverity.HIGH,
            description="orphan exception",
            status=LoanException.ExceptionStatus.OPEN,
        )
        with pytest.raises(ObjectDoesNotExist):
            handle_exception_action(orphan, self.reviewer, "save_fields", {"borrower_name": "New"})

    def test_resolve_approve_returns_dashboard_target(self):
        exc, _ = _make_exception(self.batch, rule_code="H_007")
        success, message, target = handle_exception_action(
            exc,
            self.reviewer,
            "resolve_decision",
            {"decision": "approve", "override_value": "", "reviewer_comment": "ok"},
        )
        assert success is True
        assert target == "reviewer_dashboard"
        exc.refresh_from_db()
        assert exc.status == LoanException.ExceptionStatus.RESOLVED_ACCEPTED

    def test_resolve_invalid_decision_fails(self):
        exc, _ = _make_exception(self.batch, rule_code="H_008")
        success, message, target = handle_exception_action(
            exc,
            self.reviewer,
            "resolve_decision",
            {"decision": "banana", "override_value": "", "reviewer_comment": ""},
        )
        assert success is False
        assert f"Invalid decision 'banana' for Exception #EXP-{exc.id}." in message
        assert target == "exception_loan_detail"
        exc.refresh_from_db()
        assert exc.status == LoanException.ExceptionStatus.OPEN


@pytest.mark.django_db
class TestSaveReviewerComment:
    """Test cases for save_reviewer_comment."""

    def setup_method(self):
        self.reviewer = UserFactory.create_reviewer(username="domain_comment_reviewer")
        self.batch = UploadBatch.objects.create(
            file_name="loan_tape.csv",
            source_type=UploadBatch.SourceType.LOAN_TAPE,
            status=UploadBatch.BatchStatus.INGESTED,
        )

    def test_blank_comment_returns_false_and_does_not_save(self):
        exc, _ = _make_exception(self.batch, rule_code="C_001")
        assert save_reviewer_comment(exc, self.reviewer, "   ") is False
        exc.refresh_from_db()
        assert exc.reviewer_comment in (None, "")

    def test_strips_and_saves_comment(self):
        exc, _ = _make_exception(self.batch, rule_code="C_002")
        assert save_reviewer_comment(exc, self.reviewer, "  needs more info  ") is True
        exc.refresh_from_db()
        assert exc.reviewer_comment == "needs more info"

    def test_logs_reviewer_comment_audit_event(self):
        exc, _ = _make_exception(self.batch, rule_code="C_003")
        save_reviewer_comment(exc, self.reviewer, "noted")
        event = AuditEvent.objects.get(event_type="REVIEWER_COMMENT_ADDED")
        assert event.actor_id == self.reviewer.id
        assert event.actor_role == AuditEvent.ActorRole.REVIEWER
        assert event.loan_id == "LG-1"
        assert event.batch_id == self.batch.id
        assert event.payload == {"exception_id": exc.id, "comment": "noted"}


@pytest.mark.django_db
class TestSaveExceptionFieldEdits:
    """Test cases for save_exception_field_edits."""

    def setup_method(self):
        self.reviewer = UserFactory.create_reviewer(username="domain_field_reviewer")
        self.batch = UploadBatch.objects.create(
            file_name="loan_tape.csv",
            source_type=UploadBatch.SourceType.LOAN_TAPE,
            status=UploadBatch.BatchStatus.INGESTED,
        )

    def test_no_raw_record_raises_related_object_error(self):
        # The raw_record FK is non-nullable, so an orphan exception cannot reach the
        # guarded "no associated raw record" message; accessing the relation raises.
        from django.core.exceptions import ObjectDoesNotExist

        orphan = LoanException(
            rule_code="E_ORPHAN",
            field_name="loan_id",
            severity=ValidationSeverity.HIGH,
            description="orphan exception",
            status=LoanException.ExceptionStatus.OPEN,
        )
        with pytest.raises(ObjectDoesNotExist):
            save_exception_field_edits(orphan, self.reviewer, {}, ALLOWED)

    def test_no_changed_fields_returns_true_without_resolution(self):
        exc, _ = _make_exception(self.batch, rule_code="E_001", raw_data={"loan_id": "LG-1"})
        success, err = save_exception_field_edits(exc, self.reviewer, {"loan_id": "LG-1"}, ALLOWED)
        assert success is True
        assert err is None
        exc.refresh_from_db()
        assert exc.status == LoanException.ExceptionStatus.OPEN
        assert not AuditEvent.objects.filter(event_type="LOAN_RECORD_FIELD_EDITED").exists()

    def test_changed_allowed_field_updates_raw_data(self):
        exc, record = _make_exception(
            self.batch,
            rule_code="E_002",
            raw_data={"loan_id": "LG-1", "borrower_name": "Old"},
        )
        success, err = save_exception_field_edits(
            exc, self.reviewer, {"borrower_name": " New "}, ALLOWED
        )
        assert success is True
        assert err is None
        record.refresh_from_db()
        assert record.raw_data["borrower_name"] == "New"

    def test_target_field_edit_sets_override_value(self):
        exc, _ = _make_exception(
            self.batch,
            rule_code="E_003",
            field_name="current_balance",
            raw_data={"loan_id": "LG-1", "current_balance": "1000"},
        )
        save_exception_field_edits(exc, self.reviewer, {"current_balance": "999"}, ALLOWED)
        exc.refresh_from_db()
        assert exc.override_value == "999"
        assert exc.status == LoanException.ExceptionStatus.RESOLVED_EDITED

    def test_non_target_field_edit_leaves_override_untouched(self):
        exc, _ = _make_exception(
            self.batch,
            rule_code="E_004",
            field_name="current_balance",
            raw_data={"loan_id": "LG-1", "borrower_name": "Old"},
        )
        save_exception_field_edits(exc, self.reviewer, {"borrower_name": "New"}, ALLOWED)
        exc.refresh_from_db()
        assert exc.override_value in (None, "")

    def test_non_allowed_field_is_ignored(self):
        exc, _ = _make_exception(self.batch, rule_code="E_005", raw_data={"loan_id": "LG-1"})
        success, err = save_exception_field_edits(
            exc, self.reviewer, {"hacker_field": "x"}, ALLOWED
        )
        assert success is True
        exc.refresh_from_db()
        assert exc.status == LoanException.ExceptionStatus.OPEN
        assert "hacker_field" not in exc.raw_record.raw_data

    def test_applies_resolution_and_logs_audit(self):
        exc, _ = _make_exception(
            self.batch,
            rule_code="E_006",
            raw_data={"loan_id": "LG-1", "borrower_name": "Old"},
        )
        save_exception_field_edits(
            exc, self.reviewer, {"borrower_name": "New"}, ALLOWED, comment="  edited  "
        )
        exc.refresh_from_db()
        assert exc.status == LoanException.ExceptionStatus.RESOLVED_EDITED
        assert exc.resolved_by_id == self.reviewer.id
        assert exc.resolved_at is not None
        assert exc.reviewer_comment == "edited"
        event = AuditEvent.objects.get(event_type="LOAN_RECORD_FIELD_EDITED")
        assert event.payload["exception_id"] == exc.id
        assert event.payload["edits"] == {"borrower_name": {"old": "Old", "new": "New"}}
        assert event.payload["comment"] == "edited"


@pytest.mark.django_db
class TestApplyExceptionResolution:
    """Test cases for apply_exception_resolution."""

    def setup_method(self):
        self.reviewer = UserFactory.create_reviewer(username="domain_apply_reviewer")
        self.batch = UploadBatch.objects.create(
            file_name="loan_tape.csv",
            source_type=UploadBatch.SourceType.LOAN_TAPE,
            status=UploadBatch.BatchStatus.INGESTED,
        )

    def test_sets_metadata_and_comment(self):
        exc, _ = _make_exception(self.batch, rule_code="A_001")
        apply_exception_resolution(
            exc, self.reviewer, LoanException.ExceptionStatus.RESOLVED_ACCEPTED, "done"
        )
        exc.refresh_from_db()
        assert exc.status == LoanException.ExceptionStatus.RESOLVED_ACCEPTED
        assert exc.resolved_by_id == self.reviewer.id
        assert exc.resolved_at is not None
        assert exc.reviewer_comment == "done"

    def test_blank_comment_keeps_existing_comment(self):
        exc, _ = _make_exception(self.batch, rule_code="A_002")
        exc.reviewer_comment = "keep me"
        exc.save(update_fields=["reviewer_comment"])
        apply_exception_resolution(
            exc, self.reviewer, LoanException.ExceptionStatus.REJECTED, "   "
        )
        exc.refresh_from_db()
        assert exc.status == LoanException.ExceptionStatus.REJECTED
        assert exc.reviewer_comment == "keep me"


@pytest.mark.django_db
class TestResolveExceptionDecision:
    """Test cases for resolve_exception_decision."""

    def setup_method(self):
        self.reviewer = UserFactory.create_reviewer(username="domain_decision_reviewer")
        self.batch = UploadBatch.objects.create(
            file_name="loan_tape.csv",
            source_type=UploadBatch.SourceType.LOAN_TAPE,
            status=UploadBatch.BatchStatus.INGESTED,
        )

    def test_approve_accepts_exception(self):
        exc, _ = _make_exception(self.batch, rule_code="D_001")
        assert resolve_exception_decision(exc, self.reviewer, "approve", comment="  appr  ") is True
        exc.refresh_from_db()
        assert exc.status == LoanException.ExceptionStatus.RESOLVED_ACCEPTED
        assert exc.resolved_by_id == self.reviewer.id
        assert exc.resolved_at is not None
        assert exc.reviewer_comment == "appr"
        event = AuditEvent.objects.get(event_type="LOAN_APPROVED")
        assert event.payload == {"exception_id": exc.id, "decision": "APPROVED", "comment": "appr"}

    def test_approve_without_comment_keeps_comment_blank(self):
        exc, _ = _make_exception(self.batch, rule_code="D_002")
        assert resolve_exception_decision(exc, self.reviewer, "approve") is True
        exc.refresh_from_db()
        assert exc.reviewer_comment in (None, "")

    def test_reject_rejects_exception(self):
        exc, _ = _make_exception(self.batch, rule_code="D_003")
        assert resolve_exception_decision(exc, self.reviewer, "reject", comment="bad data") is True
        exc.refresh_from_db()
        assert exc.status == LoanException.ExceptionStatus.REJECTED
        assert exc.resolved_by_id == self.reviewer.id
        event = AuditEvent.objects.get(event_type="LOAN_REJECTED")
        assert event.payload["decision"] == "REJECTED"
        assert event.payload["comment"] == "bad data"

    def test_correct_updates_override_and_raw(self):
        exc, record = _make_exception(
            self.batch,
            rule_code="D_004",
            field_name="current_balance",
            raw_data={"loan_id": "LG-1", "current_balance": "1000"},
        )
        assert (
            resolve_exception_decision(exc, self.reviewer, "correct", override_val="  1200  ")
            is True
        )
        exc.refresh_from_db()
        record.refresh_from_db()
        assert exc.override_value == "1200"
        assert exc.status == LoanException.ExceptionStatus.RESOLVED_EDITED
        assert record.raw_data["current_balance"] == "1200"
        event = AuditEvent.objects.get(event_type="EXCEPTION_RESOLVED_EDITED")
        assert event.payload["field"] == "current_balance"
        assert event.payload["override_value"] == "1200"

    def test_correct_with_blank_override_sets_blank_value(self):
        exc, _ = _make_exception(self.batch, rule_code="D_005", field_name="current_balance")
        assert resolve_exception_decision(exc, self.reviewer, "correct", override_val="   ") is True
        exc.refresh_from_db()
        assert exc.override_value == ""
        assert exc.status == LoanException.ExceptionStatus.RESOLVED_EDITED

    def test_correct_with_blank_field_name_skips_raw_update(self):
        exc, _ = _make_exception(
            self.batch, rule_code="D_006", field_name="", raw_data={"loan_id": "LG-1"}
        )
        assert resolve_exception_decision(exc, self.reviewer, "correct", override_val="X") is True
        exc.refresh_from_db()
        assert exc.override_value == "X"
        assert exc.status == LoanException.ExceptionStatus.RESOLVED_EDITED
        assert exc.raw_record.raw_data == {"loan_id": "LG-1"}

    def test_invalid_decision_returns_false_and_leaves_unchanged(self):
        exc, _ = _make_exception(self.batch, rule_code="D_007")
        assert resolve_exception_decision(exc, self.reviewer, "banana") is False
        exc.refresh_from_db()
        assert exc.status == LoanException.ExceptionStatus.OPEN
        assert exc.resolved_by_id is None
        assert exc.resolved_at is None
        assert not AuditEvent.objects.filter(
            event_type__in=["LOAN_APPROVED", "LOAN_REJECTED", "EXCEPTION_RESOLVED_EDITED"]
        ).exists()
