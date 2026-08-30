"""
Test cases for app.domain.verified_service domain logic.

Covers the two domain entry points `process_clean_records_for_batch` and
`sync_verified_record_for_loan` plus their reusable helpers with positive,
negative, edge, boundary and invalid input scenarios.
"""

import itertools

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError

from app.domain.verified_service import (
    build_canonical_data,
    collect_participating_reviewers,
    determine_verification_outcomes,
    get_primary_record,
    process_clean_records_for_batch,
    sync_verified_record_for_loan,
    validate_loan_eligibility_for_verification,
)
from app.models.ai import AIRecommendation
from app.models.audit import AuditEvent
from app.models.ingestion import RawLoanRecord, UploadBatch
from app.models.validation import LoanException
from app.models.verified import VerifiedLoanRecord
from tests.factory.user_factory import UserFactory
from tests.factory.verified_factory import VerifiedLoanRecordFactory


def _batch(source_type=UploadBatch.SourceType.LOAN_TAPE):
    return UploadBatch.objects.create(
        file_name="loan_tape.csv",
        source_type=source_type,
        status=UploadBatch.BatchStatus.INGESTED,
    )


def _raw(loan_id="LG-1", batch=None, row_number=1, **extra):
    raw_data = {"loan_id": loan_id}
    raw_data.update(extra)
    return RawLoanRecord.objects.create(
        batch=batch or _batch(), row_number=row_number, raw_data=raw_data
    )


_RULE_SEQ = itertools.count(1)


def _resolved_exception(
    raw_record,
    status=LoanException.ExceptionStatus.RESOLVED_ACCEPTED,
    field_name="current_balance",
    override_value=None,
    resolved_by=None,
):
    rule = VerifiedLoanRecordFactory.create_rule(
        rule_code=f"VAL_EXT_{next(_RULE_SEQ)}", field_name=field_name
    )
    return LoanException.objects.create(
        batch=raw_record.batch,
        raw_record=raw_record,
        rule=rule,
        rule_code=rule.rule_code,
        field_name=rule.field_name,
        severity=3,
        description="Sample exception for verified service tests.",
        status=status,
        override_value=override_value,
        resolved_by=resolved_by,
    )


@pytest.mark.django_db
class TestGetPrimaryRecord:
    """Tests for the get_primary_record helper."""

    def test_none_returns_none(self):
        assert get_primary_record(None) is None

    def test_loan_tape_record_returns_itself(self):
        batch = _batch()
        raw = _raw(loan_id="LG-1", batch=batch)
        assert get_primary_record(raw) == raw

    def test_record_without_batch_returns_itself(self):
        raw = RawLoanRecord.objects.create(row_number=1, raw_data={"loan_id": "LG-1"})
        assert get_primary_record(raw) == raw

    def test_servicer_record_resolves_primary_loan_tape(self):
        tape = _batch()
        primary = _raw(loan_id="LG-X", batch=tape)
        serv_batch = _batch(UploadBatch.SourceType.SERVICER_UPDATE)
        serv_raw = _raw(loan_id="LG-X", batch=serv_batch)
        assert get_primary_record(serv_raw) == primary

    def test_servicer_record_picks_latest_primary_when_multiple(self):
        tape = _batch()
        _raw(loan_id="LG-Y", batch=tape, row_number=1)
        newer = _raw(loan_id="LG-Y", batch=tape, row_number=2)
        serv_batch = _batch(UploadBatch.SourceType.SERVICER_UPDATE)
        serv_raw = _raw(loan_id="LG-Y", batch=serv_batch)
        assert get_primary_record(serv_raw) == newer

    def test_servicer_record_with_no_matching_primary_returns_none(self):
        serv_batch = _batch(UploadBatch.SourceType.SERVICER_UPDATE)
        serv_raw = _raw(loan_id="LG-NOPE", batch=serv_batch)
        assert get_primary_record(serv_raw) is None

    def test_servicer_record_without_loan_id_returns_none(self):
        serv_batch = _batch(UploadBatch.SourceType.SERVICER_UPDATE)
        serv_raw = RawLoanRecord.objects.create(
            batch=serv_batch, row_number=1, raw_data={"borrower_name": "No loan key"}
        )
        assert get_primary_record(serv_raw) is None


@pytest.mark.django_db
class TestValidateLoanEligibilityForVerification:
    """Tests for the validate_loan_eligibility_for_verification helper."""

    def test_none_not_eligible(self):
        ok, msg = validate_loan_eligibility_for_verification(None)
        assert ok is False
        assert "None" in msg

    def test_record_without_loan_id_not_eligible(self):
        raw = RawLoanRecord.objects.create(row_number=1, raw_data={"borrower_name": "X"})
        ok, msg = validate_loan_eligibility_for_verification(raw)
        assert ok is False
        assert "loan_id" in msg

    def test_already_verified_loan_not_eligible(self):
        raw = _raw(loan_id="LG-V1")
        VerifiedLoanRecordFactory.create_verified_record(
            raw_record=raw, canonical_data={"loan_id": "LG-V1"}
        )
        ok, msg = validate_loan_eligibility_for_verification(raw)
        assert ok is False
        assert "already verified" in msg

    def test_clean_record_is_eligible(self):
        raw = _raw(loan_id="LG-C1")
        ok, msg = validate_loan_eligibility_for_verification(raw)
        assert ok is True
        assert "Eligible" in msg

    def test_open_exception_not_eligible(self):
        raw = _raw(loan_id="LG-O1")
        _resolved_exception(raw, status=LoanException.ExceptionStatus.OPEN)
        ok, msg = validate_loan_eligibility_for_verification(raw)
        assert ok is False
        assert "unresolved" in msg

    def test_under_review_exception_not_eligible(self):
        raw = _raw(loan_id="LG-U1")
        _resolved_exception(raw, status=LoanException.ExceptionStatus.UNDER_REVIEW)
        ok, _ = validate_loan_eligibility_for_verification(raw)
        assert ok is False

    def test_rejected_exception_not_eligible(self):
        raw = _raw(loan_id="LG-RJ1")
        _resolved_exception(raw, status=LoanException.ExceptionStatus.REJECTED)
        ok, msg = validate_loan_eligibility_for_verification(raw)
        assert ok is False
        assert "rejected" in msg

    def test_all_resolved_exceptions_eligible(self):
        raw = _raw(loan_id="LG-R1")
        _resolved_exception(raw)
        _resolved_exception(raw, field_name="interest_rate")
        ok, _ = validate_loan_eligibility_for_verification(raw)
        assert ok is True


@pytest.mark.django_db
class TestCollectParticipatingReviewers:
    """Tests for the collect_participating_reviewers helper."""

    def test_empty_inputs_returns_empty(self):
        assert collect_participating_reviewers() == []

    def test_actor_with_pk_included(self):
        reviewer = UserFactory.create_reviewer()
        assert collect_participating_reviewers(actor=reviewer) == [reviewer]

    def test_actor_without_pk_excluded(self):
        assert collect_participating_reviewers(actor=AnonymousUser()) == []

    def test_exception_resolved_by_included(self):
        reviewer = UserFactory.create_reviewer()
        raw = _raw(loan_id="LG-P1")
        exc = _resolved_exception(raw, resolved_by=reviewer)
        assert collect_participating_reviewers(resolved_exceptions=[exc]) == [reviewer]

    def test_ai_recommendation_reviewed_by_included(self):
        reviewer = UserFactory.create_reviewer()
        raw = _raw(loan_id="LG-P2")
        exc = _resolved_exception(raw)
        ai = VerifiedLoanRecordFactory.create_ai_recommendation(exception=exc)
        ai.reviewed_by = reviewer
        ai.save()
        assert collect_participating_reviewers(ai_recommendations=[ai]) == [reviewer]

    def test_reviewers_deduplicated_across_sources(self):
        reviewer = UserFactory.create_reviewer()
        raw = _raw(loan_id="LG-P3")
        exc = _resolved_exception(raw, resolved_by=reviewer)
        ai = VerifiedLoanRecordFactory.create_ai_recommendation(exception=exc)
        ai.reviewed_by = reviewer
        ai.save()
        reviewers = collect_participating_reviewers(
            actor=reviewer, resolved_exceptions=[exc], ai_recommendations=[ai]
        )
        assert len(reviewers) == 1


@pytest.mark.django_db
class TestDetermineVerificationOutcomes:
    """Tests for the determine_verification_outcomes helper."""

    def test_no_exceptions_auto_passed(self):
        status, decision = determine_verification_outcomes([], [], [])
        assert status == VerifiedLoanRecord.ValidationStatus.PASSED_CLEAN
        assert decision == VerifiedLoanRecord.ReviewerDecision.AUTO_PASSED

    def test_resolved_accepted_exception_approved_by_default(self):
        raw = _raw(loan_id="LG-D1")
        exc = _resolved_exception(raw)
        status, decision = determine_verification_outcomes([exc], [exc], [])
        assert status == VerifiedLoanRecord.ValidationStatus.RESOLVED_EXCEPTION
        assert decision == VerifiedLoanRecord.ReviewerDecision.APPROVED

    def test_ai_accepted_recommendation_wins(self):
        raw = _raw(loan_id="LG-D2")
        exc = _resolved_exception(raw)
        ai = VerifiedLoanRecordFactory.create_ai_recommendation(exception=exc)
        ai.status = AIRecommendation.RecommendationStatus.ACCEPTED
        ai.save()
        _, decision = determine_verification_outcomes([exc], [exc], [ai])
        assert decision == VerifiedLoanRecord.ReviewerDecision.AI_ACCEPTED

    def test_ai_edited_recommendation_wins(self):
        raw = _raw(loan_id="LG-D3")
        exc = _resolved_exception(raw)
        ai = VerifiedLoanRecordFactory.create_ai_recommendation(exception=exc)
        ai.status = AIRecommendation.RecommendationStatus.EDITED
        ai.save()
        _, decision = determine_verification_outcomes([exc], [exc], [ai])
        assert decision == VerifiedLoanRecord.ReviewerDecision.AI_EDITED

    def test_manually_edited_exception_decision(self):
        raw = _raw(loan_id="LG-D4")
        exc = _resolved_exception(raw, status=LoanException.ExceptionStatus.RESOLVED_EDITED)
        _, decision = determine_verification_outcomes([exc], [exc], [])
        assert decision == VerifiedLoanRecord.ReviewerDecision.EDITED

    def test_decision_type_overrides_ai_derivation(self):
        raw = _raw(loan_id="LG-D5")
        exc = _resolved_exception(raw)
        ai = VerifiedLoanRecordFactory.create_ai_recommendation(exception=exc)
        _, decision = determine_verification_outcomes(
            [exc], [exc], [ai], decision_type=VerifiedLoanRecord.ReviewerDecision.EDITED
        )
        assert decision == VerifiedLoanRecord.ReviewerDecision.EDITED

    def test_all_exceptions_ignored_when_list_empty(self):
        status, decision = determine_verification_outcomes([], [], [])
        assert (status, decision) == (
            VerifiedLoanRecord.ValidationStatus.PASSED_CLEAN,
            VerifiedLoanRecord.ReviewerDecision.AUTO_PASSED,
        )


@pytest.mark.django_db
class TestBuildCanonicalData:
    """Tests for the build_canonical_data helper."""

    def test_copies_raw_data_payload(self):
        raw = _raw(loan_id="LG-CA1", borrower_id="BR-1", loan_amount=100)
        assert build_canonical_data(raw) == {
            "loan_id": "LG-CA1",
            "borrower_id": "BR-1",
            "loan_amount": 100,
        }

    def test_applies_resolved_exception_overrides(self):
        raw = _raw(loan_id="LG-CA2", current_balance=100.0)
        exc = _resolved_exception(
            raw,
            status=LoanException.ExceptionStatus.RESOLVED_EDITED,
            field_name="current_balance",
            override_value="500.00",
        )
        canon = build_canonical_data(raw, [exc])
        assert canon["current_balance"] == 500.0

    def test_ignores_exception_without_override_value(self):
        raw = _raw(loan_id="LG-CA3", current_balance=100.0)
        exc = _resolved_exception(raw, override_value=None)
        canon = build_canonical_data(raw, [exc])
        assert canon["current_balance"] == 100.0

    def test_formats_currency_and_percent_strings_to_float(self):
        raw = _raw(loan_id="LG-CA4", original_principal="$2,500,000.00", interest_rate="8.5%")
        canon = build_canonical_data(raw)
        assert canon["original_principal"] == 2500000.0
        assert canon["interest_rate"] == 8.5

    def test_coerces_integer_amounts_to_float(self):
        raw = _raw(loan_id="LG-CA5", current_balance=2500000)
        assert build_canonical_data(raw)["current_balance"] == 2500000.0

    def test_leaves_unparseable_amount_as_is(self):
        raw = _raw(loan_id="LG-CA6", interest_rate="not-a-number")
        assert build_canonical_data(raw)["interest_rate"] == "not-a-number"

    def test_strips_and_coerces_identifiers(self):
        raw = RawLoanRecord.objects.create(
            row_number=1, raw_data={"loan_id": "  LG-CA7  ", "borrower_id": 777}
        )
        canon = build_canonical_data(raw)
        assert canon["loan_id"] == "LG-CA7"
        assert canon["borrower_id"] == "777"

    def test_empty_raw_data_returns_defaults(self):
        raw = RawLoanRecord.objects.create(row_number=1, raw_data=None)
        canon = build_canonical_data(raw)
        assert canon == {"loan_id": "", "borrower_id": ""}

    def test_does_not_mutate_source_raw_data(self):
        raw = _raw(loan_id="LG-CA8", interest_rate=" $ 10 % ")
        original = dict(raw.raw_data)
        build_canonical_data(raw)
        assert raw.raw_data == original


@pytest.mark.django_db
class TestSyncVerifiedRecordForLoan:
    """Tests for the sync_verified_record_for_loan domain entry point."""

    def test_none_record_returns_none(self):
        assert sync_verified_record_for_loan(None) is None

    def test_creates_clean_auto_passed_record(self):
        raw = _raw(loan_id="LG-SYNC1")
        record = sync_verified_record_for_loan(raw)
        assert record is not None
        assert record.raw_record == raw
        assert record.loan_id == "LG-SYNC1"
        assert record.validation_status == VerifiedLoanRecord.ValidationStatus.PASSED_CLEAN
        assert record.reviewer_decision == VerifiedLoanRecord.ReviewerDecision.AUTO_PASSED
        assert record.verify_integrity() is True

    def test_resolved_accepted_exception_yields_approved_record(self):
        raw = _raw(loan_id="LG-SYNC2")
        exc = _resolved_exception(raw)
        record = sync_verified_record_for_loan(raw)
        assert record.validation_status == VerifiedLoanRecord.ValidationStatus.RESOLVED_EXCEPTION
        assert record.reviewer_decision == VerifiedLoanRecord.ReviewerDecision.APPROVED
        assert list(record.exceptions_resolved.all()) == [exc]

    def test_edited_exception_yields_edited_decision(self):
        raw = _raw(loan_id="LG-SYNC3")
        exc = _resolved_exception(raw, status=LoanException.ExceptionStatus.RESOLVED_EDITED)
        record = sync_verified_record_for_loan(raw)
        assert record.reviewer_decision == VerifiedLoanRecord.ReviewerDecision.EDITED
        assert list(record.exceptions_resolved.all()) == [exc]

    def test_accepted_ai_records_yield_ai_accepted_decision(self):
        raw = _raw(loan_id="LG-SYNC4", current_balance=90.0)
        exc = _resolved_exception(raw)
        ai = VerifiedLoanRecordFactory.create_ai_recommendation(exception=exc)
        ai.status = AIRecommendation.RecommendationStatus.ACCEPTED
        ai.suggested_value = "120"
        ai.save()
        record = sync_verified_record_for_loan(raw)
        assert record.reviewer_decision == VerifiedLoanRecord.ReviewerDecision.AI_ACCEPTED
        assert list(record.ai_recommendations_used.all()) == [ai]

    def test_decision_type_param_controls_decision(self):
        raw = _raw(loan_id="LG-SYNC5")
        _resolved_exception(raw)
        record = sync_verified_record_for_loan(
            raw, decision_type=VerifiedLoanRecord.ReviewerDecision.APPROVED
        )
        assert record.reviewer_decision == VerifiedLoanRecord.ReviewerDecision.APPROVED

    def test_wires_verified_by_and_participating_reviewers(self):
        reviewer = UserFactory.create_reviewer()
        raw = _raw(loan_id="LG-SYNC6")
        exc = _resolved_exception(raw, resolved_by=reviewer)
        record = sync_verified_record_for_loan(raw, actor=reviewer)
        assert record.verified_by == reviewer
        assert list(record.exceptions_resolved.all()) == [exc]
        assert list(record.participating_reviewers.all()) == [reviewer]

    def test_actor_without_pk_not_assigned_as_verified_by(self):
        from app.models.user import User

        unsaved = User(username="unsaved_reviewer")
        raw = _raw(loan_id="LG-SYNC7")
        record = sync_verified_record_for_loan(raw, actor=unsaved)
        assert record.verified_by is None

    def test_servicer_record_resolves_to_primary_before_creation(self):
        tape = _batch()
        primary = _raw(loan_id="LG-SYNC8", batch=tape)
        serv_batch = _batch(UploadBatch.SourceType.SERVICER_UPDATE)
        serv_raw = _raw(loan_id="LG-SYNC8", batch=serv_batch)
        record = sync_verified_record_for_loan(serv_raw)
        assert record.raw_record == primary
        assert record.loan_id == "LG-SYNC8"

    def test_servicer_without_primary_returns_none(self):
        serv_batch = _batch(UploadBatch.SourceType.SERVICER_UPDATE)
        serv_raw = _raw(loan_id="LG-SYNC9", batch=serv_batch)
        assert sync_verified_record_for_loan(serv_raw) is None

    def test_already_verified_returns_existing_record(self):
        raw = _raw(loan_id="LG-SYNC10")
        existing = VerifiedLoanRecordFactory.create_verified_record(
            raw_record=raw, canonical_data={"loan_id": "LG-SYNC10"}
        )
        result = sync_verified_record_for_loan(raw)
        assert result == existing
        assert VerifiedLoanRecord.objects.count() == 1

    def test_open_exception_blocks_creation(self):
        raw = _raw(loan_id="LG-SYNC11")
        _resolved_exception(raw, status=LoanException.ExceptionStatus.OPEN)
        assert sync_verified_record_for_loan(raw) is None
        assert VerifiedLoanRecord.objects.count() == 0

    def test_rejected_exception_blocks_creation(self):
        raw = _raw(loan_id="LG-SYNC12")
        _resolved_exception(raw, status=LoanException.ExceptionStatus.REJECTED)
        assert sync_verified_record_for_loan(raw) is None
        assert VerifiedLoanRecord.objects.count() == 0

    def test_creates_single_audit_event(self):
        raw = _raw(loan_id="LG-SYNC13")
        record = sync_verified_record_for_loan(raw)
        event = AuditEvent.objects.get(event_type="VERIFIED_RECORD_CREATED", loan_id="LG-SYNC13")
        assert event.actor is None
        assert event.actor_role == AuditEvent.ActorRole.SYSTEM
        assert event.batch_id == raw.batch_id
        assert event.payload["verified_record_id"] == record.id
        assert event.payload["record_hash"] == record.record_hash

    def test_integrity_error_on_create_returns_existing_or_none(self, monkeypatch):
        raw = _raw(loan_id="LG-SYNC14")

        def _boom(*args, **kwargs):
            raise IntegrityError("duplicate key")

        monkeypatch.setattr(VerifiedLoanRecord, "create_record", _boom)
        assert sync_verified_record_for_loan(raw) is None


@pytest.mark.django_db
class TestProcessCleanRecordsForBatch:
    """Tests for the process_clean_records_for_batch domain entry point."""

    def test_none_batch_returns_zero(self):
        assert process_clean_records_for_batch(None) == 0

    def test_non_loan_tape_batch_returns_zero(self):
        batch = _batch(UploadBatch.SourceType.SERVICER_UPDATE)
        _raw(loan_id="LG-SKIP", batch=batch)
        assert process_clean_records_for_batch(batch) == 0
        assert VerifiedLoanRecord.objects.count() == 0

    def test_empty_batch_returns_zero(self):
        assert process_clean_records_for_batch(_batch()) == 0

    def test_bulk_auto_verifies_clean_records(self):
        batch = _batch()
        raw1 = _raw(loan_id="LG-1", batch=batch, row_number=2, interest_rate="8.5%")
        _raw(loan_id="LG-2", batch=batch, row_number=3)
        created = process_clean_records_for_batch(batch)
        assert created == 2
        vr = VerifiedLoanRecord.objects.get(loan_id="LG-1")
        assert vr.raw_record == raw1
        assert vr.validation_status == VerifiedLoanRecord.ValidationStatus.PASSED_CLEAN
        assert vr.reviewer_decision == VerifiedLoanRecord.ReviewerDecision.AUTO_PASSED
        assert vr.lineage_summary == {"batch_id": batch.id, "auto_verified": True}
        assert vr.canonical_data["interest_rate"] == 8.5
        assert vr.verify_integrity() is True

    def test_skips_records_with_exceptions(self):
        batch = _batch()
        flagged = _raw(loan_id="LG-F", batch=batch, row_number=2)
        _raw(loan_id="LG-C", batch=batch, row_number=3)
        _resolved_exception(flagged)
        created = process_clean_records_for_batch(batch)
        assert created == 1
        assert VerifiedLoanRecord.objects.filter(loan_id="LG-C").exists()
        assert not VerifiedLoanRecord.objects.filter(loan_id="LG-F").exists()

    def test_skips_already_verified_raw_records(self):
        batch = _batch()
        raw = _raw(loan_id="LG-V", batch=batch)
        VerifiedLoanRecordFactory.create_verified_record(
            raw_record=raw, canonical_data={"loan_id": "LG-V"}
        )
        assert process_clean_records_for_batch(batch) == 0

    def test_skips_loan_id_already_verified_in_another_batch(self):
        batch = _batch()
        other_batch = _batch()
        other_raw = _raw(loan_id="LG-SAME", batch=other_batch)
        VerifiedLoanRecordFactory.create_verified_record(
            raw_record=other_raw, canonical_data={"loan_id": "LG-SAME"}
        )
        _raw(loan_id="LG-SAME", batch=batch)
        assert process_clean_records_for_batch(batch) == 0

    def test_deduplicates_same_loan_id_within_batch(self):
        batch = _batch()
        _raw(loan_id="LG-DUP", batch=batch, row_number=2)
        _raw(loan_id="LG-DUP", batch=batch, row_number=3)
        created = process_clean_records_for_batch(batch)
        assert created == 1
        assert VerifiedLoanRecord.objects.filter(loan_id="LG-DUP").count() == 1

    def test_skips_records_without_loan_id(self):
        batch = _batch()
        RawLoanRecord.objects.create(batch=batch, row_number=2, raw_data={"borrower_name": "X"})
        assert process_clean_records_for_batch(batch) == 0

    def test_logs_bulk_audit_events_with_system_role(self):
        batch = _batch()
        _raw(loan_id="LG-A", batch=batch, row_number=2)
        _raw(loan_id="LG-B", batch=batch, row_number=3)
        process_clean_records_for_batch(batch)
        events = AuditEvent.objects.filter(event_type="VERIFIED_RECORD_CREATED", batch_id=batch.id)
        assert events.count() == 2
        assert set(events.values_list("loan_id", flat=True)) == {"LG-A", "LG-B"}
        for event in events:
            assert event.actor is None
            assert event.actor_role == AuditEvent.ActorRole.SYSTEM
            assert event.payload["validation_status"] == (
                VerifiedLoanRecord.ValidationStatus.PASSED_CLEAN
            )

    def test_only_processes_records_belonging_to_batch(self):
        batch_a = _batch()
        batch_b = _batch()
        _raw(loan_id="LG-A1", batch=batch_a)
        _raw(loan_id="LG-B1", batch=batch_b)
        created = process_clean_records_for_batch(batch_a)
        assert created == 1
        assert VerifiedLoanRecord.objects.filter(loan_id="LG-A1").exists()
        assert not VerifiedLoanRecord.objects.filter(loan_id="LG-B1").exists()

    def test_second_run_is_idempotent(self):
        batch = _batch()
        _raw(loan_id="LG-1", batch=batch)
        assert process_clean_records_for_batch(batch) == 1
        assert process_clean_records_for_batch(batch) == 0
        assert VerifiedLoanRecord.objects.filter(loan_id="LG-1").count() == 1
