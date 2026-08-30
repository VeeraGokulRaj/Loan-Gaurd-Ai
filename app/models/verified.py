"""
Module E: Verified Loan Record & Cryptographic Hash Lineage Model for LoanGuard AI.

This module defines the database model for VerifiedLoanRecord:
1. Canonical loan data storage following secondary market standards.
2. SHA-256 cryptographic hash computation for tamper-evidence.
3. Multi-entity lineage linkages to RawLoanRecord, LoanExceptions, AIRecommendations, and Reviewers.
4. VerifiedLoanRecordManager for zero N+1 query prefetching across lineages.
"""

import hashlib
import json
from typing import Any

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from safedelete.managers import SafeDeleteManager

from app.models.ai import AIRecommendation
from app.models.base import BaseModel
from app.models.ingestion import RawLoanRecord
from app.models.validation import LoanException


class VerifiedLoanRecordManager(SafeDeleteManager):
    """Custom Model Manager combining SafeDelete behavior and lineage prefetching."""

    def with_lineage(self):
        """Returns QuerySet with select_related and prefetch_related for zero N+1 queries."""
        return (
            self.select_related("raw_record", "verified_by")
            .prefetch_related(
                "exceptions_resolved", "ai_recommendations_used", "participating_reviewers"
            )
            .order_by("-verified_at", "-id")
        )


class VerifiedLoanRecord(BaseModel):
    """
    Immutable canonical representation of verified, clean loan records.

    Stores standardized loan payload, raw-to-verified lineage linkages,
    resolution decisions, participating reviewers, accepted AI recommendations,
    and SHA-256 cryptographic fingerprint (`record_hash`) for data integrity verification.
    """

    class ValidationStatus(models.TextChoices):
        PASSED_CLEAN = "PASSED_CLEAN", _("Passed Validation Cleanly")
        RESOLVED_EXCEPTION = "RESOLVED_EXCEPTION", _("Resolved Exception")

    class ReviewerDecision(models.TextChoices):
        AUTO_PASSED = "AUTO_PASSED", _("Auto Passed (System)")
        APPROVED = "APPROVED", _("Approved by Reviewer")
        EDITED = "EDITED", _("Manually Edited & Resolved")
        AI_ACCEPTED = "AI_ACCEPTED", _("Accepted AI Recommendation")
        AI_EDITED = "AI_EDITED", _("Edited & Accepted AI Recommendation")

    raw_record = models.OneToOneField(
        RawLoanRecord,
        on_delete=models.CASCADE,
        related_name="verified_record",
        help_text=_("Associated raw loan record source file lineage."),
    )
    loan_id = models.CharField(
        max_length=100,
        db_index=True,
        help_text=_("Unique business identifier for the loan."),
    )
    borrower_id = models.CharField(
        max_length=100,
        db_index=True,
        blank=True,
        null=True,
        help_text=_("Unique business identifier for the primary borrower."),
    )
    canonical_data = models.JSONField(
        default=dict,
        help_text=_("Cleaned, standardized, verified loan JSON data payload."),
    )
    validation_status = models.CharField(
        max_length=50,
        choices=ValidationStatus.choices,
        default=ValidationStatus.PASSED_CLEAN,
        db_index=True,
        help_text=_(
            "Categorical status of validation outcome (Passed Clean vs Resolved Exception)."
        ),
    )
    reviewer_decision = models.CharField(
        max_length=50,
        choices=ReviewerDecision.choices,
        default=ReviewerDecision.AUTO_PASSED,
        db_index=True,
        help_text=_("Decision action that brought the loan to verified status."),
    )
    exceptions_resolved = models.ManyToManyField(
        LoanException,
        related_name="verified_records",
        blank=True,
        help_text=_("All validation exceptions flagged and resolved for this loan."),
    )
    ai_recommendations_used = models.ManyToManyField(
        AIRecommendation,
        related_name="verified_records",
        blank=True,
        help_text=_("All AI recommendations accepted or edited during verification."),
    )
    participating_reviewers = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="participated_verified_records",
        blank=True,
        help_text=_("All human reviewers who interacted with exceptions for this loan."),
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_records",
        help_text=_("Primary reviewer who executed the final verification approval."),
    )
    verified_at = models.DateTimeField(
        default=timezone.now,
        db_index=True,
        help_text=_("Timestamp when the record achieved verified status."),
    )
    record_hash = models.CharField(
        max_length=64,
        db_index=True,
        help_text=_("SHA-256 cryptographic hash computed over canonical_data + verified_at."),
    )
    lineage_summary = models.JSONField(
        default=dict,
        blank=True,
        help_text=_(
            "Detailed breakdown listing field changes, AI suggestions, and reviewer actions."
        ),
    )

    objects = VerifiedLoanRecordManager()

    class Meta:
        verbose_name = _("Verified Loan Record")
        verbose_name_plural = _("Verified Loan Records")
        ordering = ["-verified_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["loan_id"],
                condition=models.Q(deleted__isnull=True),
                name="unique_active_verified_loan_id",
            )
        ]

    def __str__(self) -> str:
        return f"VerifiedLoanRecord #{self.id} [Loan: {self.loan_id}] ({self.get_validation_status_display()})"

    def compute_hash(self) -> str:
        """Computes SHA-256 hash over canonical loan payload and timestamp."""
        ts_iso = (self.verified_at or timezone.now()).isoformat()
        payload_json = json.dumps(self.canonical_data or {}, sort_keys=True)
        hash_input = f"{payload_json}|{ts_iso}".encode()
        return hashlib.sha256(hash_input).hexdigest()

    def verify_integrity(self) -> bool:
        """Returns True if record_hash matches computed hash, False if tampered."""
        return bool(self.record_hash) and self.record_hash == self.compute_hash()

    @property
    def is_tampered(self) -> bool:
        """Property returning True if database tampering is detected (record_hash mismatch)."""
        return not self.verify_integrity()

    @staticmethod
    def _prepare_record_payload(
        raw_record: RawLoanRecord | None,
        canonical_data: dict[str, Any],
        verified_at: Any,
        validation_status: str,
        reviewer_decision: str,
        verified_by: Any = None,
        lineage_summary: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        """
        DRY helper to compute SHA-256 hash and format dict payload for single and bulk creation.

        Returns:
            Tuple of (kwargs_dict, record_hash).
        """
        canonical_clean = canonical_data or {}
        ts_iso = verified_at.isoformat()

        sorted_json = json.dumps(canonical_clean, sort_keys=True)
        hash_input = f"{sorted_json}|{ts_iso}".encode()
        rec_hash = hashlib.sha256(hash_input).hexdigest()

        loan_id = str(canonical_clean.get("loan_id", getattr(raw_record, "loan_id", "")))
        borrower_id = str(
            canonical_clean.get("borrower_id", getattr(raw_record, "borrower_id", ""))
        )

        payload = {
            "raw_record": raw_record,
            "loan_id": loan_id,
            "borrower_id": borrower_id,
            "canonical_data": canonical_clean,
            "validation_status": validation_status,
            "reviewer_decision": reviewer_decision,
            "verified_by": verified_by,
            "verified_at": verified_at,
            "record_hash": rec_hash,
            "lineage_summary": lineage_summary or {},
        }
        return payload, rec_hash

    @classmethod
    @transaction.atomic
    def create_record(
        cls,
        raw_record: RawLoanRecord,
        canonical_data: dict[str, Any],
        validation_status: str = ValidationStatus.PASSED_CLEAN,
        reviewer_decision: str = ReviewerDecision.AUTO_PASSED,
        verified_by: Any = None,
        exceptions: list[LoanException] | None = None,
        ai_recommendations: list[AIRecommendation] | None = None,
        participating_reviewers: list[Any] | None = None,
        lineage_summary: dict[str, Any] | None = None,
    ) -> "VerifiedLoanRecord":
        """
        Clean entry-point classmethod for creating a VerifiedLoanRecord instance (similar to AuditEvent.log_event).
        Computes SHA-256 hash automatically, connects lineage relations, and logs audit trail event inside an atomic transaction.
        """
        now = timezone.now()
        payload, rec_hash = cls._prepare_record_payload(
            raw_record=raw_record,
            canonical_data=canonical_data,
            verified_at=now,
            validation_status=validation_status,
            reviewer_decision=reviewer_decision,
            verified_by=verified_by,
            lineage_summary=lineage_summary,
        )

        verified_record = cls.objects.create(**payload)

        if exceptions:
            verified_record.exceptions_resolved.set(exceptions)
        if ai_recommendations:
            verified_record.ai_recommendations_used.set(ai_recommendations)
        if participating_reviewers:
            verified_record.participating_reviewers.set(participating_reviewers)

        from app.models.audit import AuditEvent

        AuditEvent.log_event(
            event_type="VERIFIED_RECORD_CREATED",
            actor=verified_by,
            actor_role=AuditEvent.ActorRole.REVIEWER
            if verified_by
            else AuditEvent.ActorRole.SYSTEM,
            loan_id=payload["loan_id"],
            batch_id=getattr(raw_record, "batch_id", None),
            payload={
                "verified_record_id": verified_record.id,
                "loan_id": payload["loan_id"],
                "record_hash": rec_hash,
                "validation_status": validation_status,
                "reviewer_decision": reviewer_decision,
            },
        )

        return verified_record

    @classmethod
    @transaction.atomic
    def create_records_bulk(
        cls,
        records_data: list[dict[str, Any]],
        batch_size: int = 1000,
    ) -> list["VerifiedLoanRecord"]:
        """
        Bulk creates VerifiedLoanRecord entries with automatic SHA-256 hash generation
        and ManyToMany relationship persistence inside an atomic transaction.
        """
        if not records_data:
            return []

        now = timezone.now()
        verified_objects: list[VerifiedLoanRecord] = []

        for data in records_data:
            payload, _ = cls._prepare_record_payload(
                raw_record=data.get("raw_record"),
                canonical_data=data.get("canonical_data") or {},
                verified_at=now,
                validation_status=data.get("validation_status", cls.ValidationStatus.PASSED_CLEAN),
                reviewer_decision=data.get("reviewer_decision", cls.ReviewerDecision.AUTO_PASSED),
                verified_by=data.get("verified_by"),
                lineage_summary=data.get("lineage_summary"),
            )
            verified_objects.append(cls(**payload))

        created_records = cls.objects.bulk_create(verified_objects, batch_size=batch_size)

        for record, data in zip(created_records, records_data, strict=False):
            exceptions = data.get("exceptions")
            ai_recs = data.get("ai_recommendations")
            reviewers = data.get("participating_reviewers")

            if exceptions:
                record.exceptions_resolved.set(exceptions)
            if ai_recs:
                record.ai_recommendations_used.set(ai_recs)
            if reviewers:
                record.participating_reviewers.set(reviewers)

        return created_records
