"""
Test cases for app.models.verified.VerifiedLoanRecord.

Covers field defaults, SHA-256 hash computation, tamper detection, partial unique
loan_id and OneToOne raw_record constraints, soft-delete semantics, manager
lineage prefetching, create_record / create_records_bulk workflows, M2M lineage
wiring, audit logging, and boundary / invalid input scenarios.
"""

import hashlib
import json
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone

from app.models.audit import AuditEvent
from app.models.verified import VerifiedLoanRecord, VerifiedLoanRecordManager
from tests.factory.user_factory import UserFactory
from tests.factory.verified_factory import VerifiedLoanRecordFactory


@pytest.mark.django_db
class TestVerifiedLoanRecordModel:
    # ── Positive ──────────────────────────────────────────────

    def test_create_record_with_defaults(self):
        raw = VerifiedLoanRecordFactory.create_raw_record(raw_data={"loan_id": "LG-0001"})
        canonical = {"loan_id": "LG-0001", "borrower_id": "BR-1", "loan_amount": 2500000.0}
        vr = VerifiedLoanRecord.create_record(raw_record=raw, canonical_data=canonical)
        assert vr.loan_id == "LG-0001"
        assert vr.borrower_id == "BR-1"
        assert vr.canonical_data == canonical
        assert vr.validation_status == VerifiedLoanRecord.ValidationStatus.PASSED_CLEAN
        assert vr.reviewer_decision == VerifiedLoanRecord.ReviewerDecision.AUTO_PASSED
        assert len(vr.record_hash) == 64
        assert vr.verify_integrity() is True
        assert vr.is_tampered is False

    def test_create_record_wires_lineage_and_audit(self):
        reviewer = UserFactory.create_reviewer()
        raw = VerifiedLoanRecordFactory.create_raw_record(raw_data={"loan_id": "LG-100"})
        exc = VerifiedLoanRecordFactory.create_exception(raw_record=raw)
        ai = VerifiedLoanRecordFactory.create_ai_recommendation(exception=exc)
        vr = VerifiedLoanRecord.create_record(
            raw_record=raw,
            canonical_data={"loan_id": "LG-100"},
            validation_status=VerifiedLoanRecord.ValidationStatus.RESOLVED_EXCEPTION,
            reviewer_decision=VerifiedLoanRecord.ReviewerDecision.AI_ACCEPTED,
            verified_by=reviewer,
            exceptions=[exc],
            ai_recommendations=[ai],
            participating_reviewers=[reviewer],
            lineage_summary={"changes": ["balance_corrected"]},
        )
        assert vr.validation_status == VerifiedLoanRecord.ValidationStatus.RESOLVED_EXCEPTION
        assert vr.reviewer_decision == VerifiedLoanRecord.ReviewerDecision.AI_ACCEPTED
        assert vr.verified_by == reviewer
        assert vr.lineage_summary == {"changes": ["balance_corrected"]}
        assert list(vr.exceptions_resolved.all()) == [exc]
        assert list(vr.ai_recommendations_used.all()) == [ai]
        assert list(vr.participating_reviewers.all()) == [reviewer]

        audit = AuditEvent.objects.get(event_type="VERIFIED_RECORD_CREATED")
        assert audit.actor == reviewer
        assert audit.actor_role == AuditEvent.ActorRole.REVIEWER
        assert audit.loan_id == "LG-100"
        assert audit.batch_id == raw.batch_id
        assert audit.payload["record_hash"] == vr.record_hash
        assert audit.payload["verified_record_id"] == vr.id

    def test_create_record_without_verified_by_logs_system_role(self):
        raw = VerifiedLoanRecordFactory.create_raw_record()
        VerifiedLoanRecord.create_record(raw_record=raw, canonical_data={"loan_id": "LG-SYS"})
        audit = AuditEvent.objects.get(event_type="VERIFIED_RECORD_CREATED")
        assert audit.actor is None
        assert audit.actor_role == AuditEvent.ActorRole.SYSTEM

    def test_choice_labels(self):
        assert VerifiedLoanRecord.ValidationStatus.PASSED_CLEAN.label == "Passed Validation Cleanly"
        assert VerifiedLoanRecord.ValidationStatus.RESOLVED_EXCEPTION.label == "Resolved Exception"
        assert VerifiedLoanRecord.ReviewerDecision.AUTO_PASSED.label == "Auto Passed (System)"
        assert VerifiedLoanRecord.ReviewerDecision.APPROVED.label == "Approved by Reviewer"
        assert VerifiedLoanRecord.ReviewerDecision.EDITED.label == "Manually Edited & Resolved"
        assert VerifiedLoanRecord.ReviewerDecision.AI_ACCEPTED.label == "Accepted AI Recommendation"
        assert (
            VerifiedLoanRecord.ReviewerDecision.AI_EDITED.label
            == "Edited & Accepted AI Recommendation"
        )

    def test_str_representation(self):
        vr = VerifiedLoanRecordFactory.create_verified_record(canonical_data={"loan_id": "LG-STR"})
        assert str(vr) == f"VerifiedLoanRecord #{vr.id} [Loan: LG-STR] (Passed Validation Cleanly)"

    def test_inherits_base_model(self):
        vr = VerifiedLoanRecordFactory.create_verified_record()
        assert hasattr(vr, "created")
        assert hasattr(vr, "modified")
        assert hasattr(vr, "deleted")
        assert hasattr(vr, "history")
        assert vr.deleted is None

    def test_lineage_summary_defaults_to_empty_dict(self):
        vr = VerifiedLoanRecordFactory.create_verified_record(canonical_data={"loan_id": "LG-LS"})
        assert vr.lineage_summary == {}

    def test_objects_uses_custom_manager(self):
        assert isinstance(VerifiedLoanRecord.objects, VerifiedLoanRecordManager)

    def test_with_lineage_orders_by_verified_at_desc(self):
        t1 = timezone.now() - timedelta(hours=2)
        t2 = timezone.now() - timedelta(hours=1)
        VerifiedLoanRecordFactory.create_verified_record(
            canonical_data={"loan_id": "LG-A"}, verified_at=t1
        )
        VerifiedLoanRecordFactory.create_verified_record(
            canonical_data={"loan_id": "LG-B"}, verified_at=t2
        )
        qs = VerifiedLoanRecord.objects.with_lineage()
        assert list(qs.values_list("loan_id", flat=True)) == ["LG-B", "LG-A"]

    def test_with_lineage_loads_lineage_relations(self):
        reviewer = UserFactory.create_reviewer()
        raw = VerifiedLoanRecordFactory.create_raw_record(raw_data={"loan_id": "LG-LN"})
        exc = VerifiedLoanRecordFactory.create_exception(raw_record=raw)
        ai = VerifiedLoanRecordFactory.create_ai_recommendation(exception=exc)
        vr = VerifiedLoanRecord.create_record(
            raw_record=raw,
            canonical_data={"loan_id": "LG-LN"},
            verified_by=reviewer,
            exceptions=[exc],
            ai_recommendations=[ai],
            participating_reviewers=[reviewer],
        )
        loaded = VerifiedLoanRecord.objects.with_lineage().get(pk=vr.pk)
        assert loaded.raw_record.pk == raw.pk
        assert loaded.verified_by.pk == reviewer.pk
        assert list(loaded.exceptions_resolved.all()) == [exc]
        assert list(loaded.ai_recommendations_used.all()) == [ai]
        assert list(loaded.participating_reviewers.all()) == [reviewer]

    def test_bulk_create_records(self):
        raw1 = VerifiedLoanRecordFactory.create_raw_record(row_number=1)
        raw2 = VerifiedLoanRecordFactory.create_raw_record(row_number=2)
        records = VerifiedLoanRecord.create_records_bulk(
            [
                {"raw_record": raw1, "canonical_data": {"loan_id": "LG-B1"}},
                {
                    "raw_record": raw2,
                    "canonical_data": {"loan_id": "LG-B2"},
                    "reviewer_decision": "APPROVED",
                },
            ]
        )
        assert len(records) == 2
        assert VerifiedLoanRecord.objects.count() == 2
        assert records[0].loan_id == "LG-B1"
        assert records[1].loan_id == "LG-B2"
        assert records[1].reviewer_decision == VerifiedLoanRecord.ReviewerDecision.APPROVED
        assert records[0].verify_integrity() is True

    def test_bulk_create_wires_m2m(self):
        raw1 = VerifiedLoanRecordFactory.create_raw_record(row_number=1)
        exc = VerifiedLoanRecordFactory.create_exception(raw_record=raw1)
        ai = VerifiedLoanRecordFactory.create_ai_recommendation(exception=exc)
        reviewer = UserFactory.create_reviewer()
        records = VerifiedLoanRecord.create_records_bulk(
            [
                {
                    "raw_record": raw1,
                    "canonical_data": {"loan_id": "LG-BM1"},
                    "exceptions": [exc],
                    "ai_recommendations": [ai],
                    "participating_reviewers": [reviewer],
                }
            ]
        )
        rec = records[0]
        assert list(rec.exceptions_resolved.all()) == [exc]
        assert list(rec.ai_recommendations_used.all()) == [ai]
        assert list(rec.participating_reviewers.all()) == [reviewer]

    def test_bulk_create_empty_returns_empty(self):
        assert VerifiedLoanRecord.create_records_bulk([]) == []
        assert VerifiedLoanRecord.objects.count() == 0

    def test_bulk_create_hashes_differ_by_canonical_data(self):
        raw1 = VerifiedLoanRecordFactory.create_raw_record(row_number=1)
        raw2 = VerifiedLoanRecordFactory.create_raw_record(row_number=2)
        records = VerifiedLoanRecord.create_records_bulk(
            [
                {"raw_record": raw1, "canonical_data": {"loan_id": "LG-H1", "amount": 1}},
                {"raw_record": raw2, "canonical_data": {"loan_id": "LG-H2", "amount": 2}},
            ]
        )
        assert records[0].record_hash == records[0].compute_hash()
        assert records[1].record_hash == records[1].compute_hash()
        assert records[0].record_hash != records[1].record_hash

    def test_compute_hash_matches_manual_sha256(self):
        ts = timezone.now().replace(microsecond=0)
        canonical = {"loan_id": "LG-H", "amount": 1000.5}
        vr = VerifiedLoanRecordFactory.create_verified_record(
            canonical_data=canonical, verified_at=ts
        )
        expected = hashlib.sha256(
            f"{json.dumps(canonical, sort_keys=True)}|{ts.isoformat()}".encode()
        ).hexdigest()
        assert vr.compute_hash() == expected
        assert vr.record_hash == expected

    def test_created_record_hash_matches_compute_hash(self):
        vr = VerifiedLoanRecordFactory.create_verified_record(canonical_data={"loan_id": "LG-C"})
        assert vr.record_hash == vr.compute_hash()
        assert vr.verify_integrity() is True
        assert vr.is_tampered is False

    # ── Edge ──────────────────────────────────────────────────

    def test_loan_id_coerced_to_string_and_borrower_id_derived(self):
        raw = VerifiedLoanRecordFactory.create_raw_record()
        vr = VerifiedLoanRecord.create_record(
            raw_record=raw, canonical_data={"loan_id": 12345, "borrower_id": "BR-9"}
        )
        assert vr.loan_id == "12345"
        assert vr.borrower_id == "BR-9"

    def test_borrower_id_empty_when_absent_from_canonical(self):
        vr = VerifiedLoanRecordFactory.create_verified_record(canonical_data={"loan_id": "LG-NOBR"})
        assert vr.borrower_id == ""

    def test_create_record_with_empty_canonical_falls_back_to_blank_loan_id(self):
        raw = VerifiedLoanRecordFactory.create_raw_record()
        vr = VerifiedLoanRecord.create_record(raw_record=raw, canonical_data={})
        assert vr.canonical_data == {}
        assert vr.loan_id == ""

    def test_compute_hash_with_none_canonical_data_uses_empty_dict(self):
        vr = VerifiedLoanRecord(canonical_data=None, verified_at=timezone.now())
        assert len(vr.compute_hash()) == 64
        assert (
            vr.compute_hash()
            == hashlib.sha256(b"{}|" + vr.verified_at.isoformat().encode()).hexdigest()
        )

    def test_is_tampered_detects_canonical_data_mutation(self):
        vr = VerifiedLoanRecordFactory.create_verified_record(
            canonical_data={"loan_id": "LG-T", "amount": 100.0}
        )
        assert vr.is_tampered is False
        vr.canonical_data["amount"] = 99999.0
        vr.save(update_fields=["canonical_data"])
        vr.refresh_from_db()
        assert vr.is_tampered is True
        assert vr.verify_integrity() is False

    def test_is_tampered_detects_verified_at_mutation(self):
        vr = VerifiedLoanRecordFactory.create_verified_record(canonical_data={"loan_id": "LG-T2"})
        vr.verified_at = vr.verified_at + timedelta(days=1)
        vr.save(update_fields=["verified_at"])
        vr.refresh_from_db()
        assert vr.is_tampered is True
        assert vr.verify_integrity() is False

    def test_verify_integrity_false_when_hash_missing(self):
        vr = VerifiedLoanRecordFactory.create_verified_record(
            canonical_data={"loan_id": "LG-NH"}, record_hash=""
        )
        assert vr.verify_integrity() is False
        assert vr.is_tampered is True

    def test_soft_delete_hides_and_allows_loan_id_reuse(self):
        vr = VerifiedLoanRecordFactory.create_verified_record(canonical_data={"loan_id": "LG-SD"})
        vr.delete()
        assert vr.deleted is not None
        assert not VerifiedLoanRecord.objects.filter(pk=vr.pk).exists()
        assert VerifiedLoanRecord.all_objects.filter(pk=vr.pk).exists()
        vr2 = VerifiedLoanRecordFactory.create_verified_record(canonical_data={"loan_id": "LG-SD"})
        assert vr2.loan_id == "LG-SD"

    # ── Boundary ──────────────────────────────────────────────

    def test_loan_id_at_max_length_valid(self):
        vr = VerifiedLoanRecordFactory.create_verified_record(canonical_data={"loan_id": "L" * 100})
        vr.full_clean()

    def test_loan_id_over_max_length_invalid(self):
        vr = VerifiedLoanRecordFactory.create_verified_record(canonical_data={"loan_id": "L" * 100})
        vr.loan_id = "L" * 101
        with pytest.raises(ValidationError):
            vr.full_clean()

    def test_borrower_id_at_max_length_valid(self):
        vr = VerifiedLoanRecordFactory.create_verified_record(
            canonical_data={"loan_id": "LG-B", "borrower_id": "B" * 100}
        )
        vr.full_clean()

    def test_borrower_id_over_max_length_invalid(self):
        vr = VerifiedLoanRecordFactory.create_verified_record(
            canonical_data={"loan_id": "LG-B", "borrower_id": "B" * 100}
        )
        vr.borrower_id = "B" * 101
        with pytest.raises(ValidationError):
            vr.full_clean()

    def test_record_hash_at_max_length_valid(self):
        vr = VerifiedLoanRecordFactory.create_verified_record(
            canonical_data={"loan_id": "LG-RH"}, record_hash="a" * 64
        )
        vr.full_clean()

    def test_record_hash_over_max_length_invalid(self):
        vr = VerifiedLoanRecordFactory.create_verified_record(canonical_data={"loan_id": "LG-RH"})
        vr.record_hash = "a" * 65
        with pytest.raises(ValidationError):
            vr.full_clean()

    def test_empty_canonical_data_is_blank_invalid(self):
        vr = VerifiedLoanRecordFactory.create_verified_record(canonical_data={"loan_id": "LG-ED"})
        vr.canonical_data = {}
        with pytest.raises(ValidationError):
            vr.full_clean()

    # ── Negative / Invalid ────────────────────────────────────

    def test_duplicate_active_loan_id_raises_integrity_error(self):
        VerifiedLoanRecordFactory.create_verified_record(canonical_data={"loan_id": "LG-DUP"})
        with pytest.raises(IntegrityError):
            VerifiedLoanRecordFactory.create_verified_record(canonical_data={"loan_id": "LG-DUP"})

    def test_duplicate_blank_loan_id_raises_integrity_error(self):
        raw1 = VerifiedLoanRecordFactory.create_raw_record(row_number=1)
        raw2 = VerifiedLoanRecordFactory.create_raw_record(row_number=2)
        VerifiedLoanRecord.create_record(raw_record=raw1, canonical_data={})
        with pytest.raises(IntegrityError):
            VerifiedLoanRecord.create_record(raw_record=raw2, canonical_data={})

    def test_one_raw_record_cannot_have_two_verified_records(self):
        raw = VerifiedLoanRecordFactory.create_raw_record()
        VerifiedLoanRecordFactory.create_verified_record(
            raw_record=raw, canonical_data={"loan_id": "LG-U1"}
        )
        with pytest.raises(IntegrityError):
            VerifiedLoanRecordFactory.create_verified_record(
                raw_record=raw, canonical_data={"loan_id": "LG-U2"}
            )

    def test_missing_loan_id_blank_invalid(self):
        raw = VerifiedLoanRecordFactory.create_raw_record()
        vr = VerifiedLoanRecord.objects.create(raw_record=raw)
        with pytest.raises(ValidationError):
            vr.full_clean()

    def test_invalid_validation_status_choice(self):
        vr = VerifiedLoanRecordFactory.create_verified_record(canonical_data={"loan_id": "LG-VS"})
        vr.validation_status = "NOT_A_STATUS"
        with pytest.raises(ValidationError):
            vr.full_clean()

    def test_invalid_reviewer_decision_choice(self):
        vr = VerifiedLoanRecordFactory.create_verified_record(canonical_data={"loan_id": "LG-VD"})
        vr.reviewer_decision = "NOT_A_DECISION"
        with pytest.raises(ValidationError):
            vr.full_clean()

    def test_create_record_with_non_dict_canonical_data_raises(self):
        raw = VerifiedLoanRecordFactory.create_raw_record()
        with pytest.raises(AttributeError):
            VerifiedLoanRecord.create_record(raw_record=raw, canonical_data=["not", "a", "dict"])

    def test_non_json_serializable_canonical_data_raises_type_error(self):
        raw = VerifiedLoanRecordFactory.create_raw_record()
        vr = VerifiedLoanRecord(raw_record=raw, canonical_data={1, 2, 3})
        with pytest.raises(TypeError):
            vr.save()

    def test_bulk_create_without_raw_record_raises(self):
        with pytest.raises(IntegrityError):
            VerifiedLoanRecord.create_records_bulk([{"canonical_data": {"loan_id": "LG-NULL"}}])
