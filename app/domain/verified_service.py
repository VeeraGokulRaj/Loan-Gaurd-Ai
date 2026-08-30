"""
Domain logic for Module E: Verified Loan Record Management & Automatic Creation.

Provides high-level service functions to transition clean raw loan records
and resolved loan exceptions into canonical, immutable VerifiedLoanRecord entries.
"""

import logging
from typing import Any

from django.db import IntegrityError, transaction

from app.models.ai import AIRecommendation
from app.models.ingestion import RawLoanRecord, UploadBatch
from app.models.validation import LoanException
from app.models.verified import VerifiedLoanRecord

logger = logging.getLogger(__name__)


@transaction.atomic
def sync_verified_record_for_loan(
    raw_record: RawLoanRecord | None,
    actor: Any = None,
    decision_type: str | None = None,
) -> VerifiedLoanRecord | None:
    """
    Entry point for creating a VerifiedLoanRecord once all exceptions are resolved or clean.
    Ensures raw_record points to the Primary Loan Tape record (LOAN_TAPE).
    """

    raw_record = get_primary_record(raw_record)
    if not raw_record:
        return None

    is_eligible, _ = validate_loan_eligibility_for_verification(raw_record)
    if not is_eligible:
        return getattr(raw_record, "verified_record", None)

    all_exceptions = list(LoanException.objects.filter(raw_record=raw_record))

    # Filter resolved exceptions in Python memory to avoid duplicate SQL queries
    resolved_statuses = {
        LoanException.ExceptionStatus.RESOLVED_ACCEPTED,
        LoanException.ExceptionStatus.RESOLVED_EDITED,
    }
    resolved_exceptions = [exc for exc in all_exceptions if exc.status in resolved_statuses]

    # Fetch AI recommendations only if exceptions exist
    if all_exceptions:
        ai_recommendations = list(
            AIRecommendation.objects.filter(
                exception__in=all_exceptions,
                status__in=[
                    AIRecommendation.RecommendationStatus.ACCEPTED,
                    AIRecommendation.RecommendationStatus.EDITED,
                    AIRecommendation.RecommendationStatus.REJECTED,
                ],
            )
        )
    else:
        ai_recommendations = []

    reviewers = collect_participating_reviewers(
        actor=actor,
        resolved_exceptions=resolved_exceptions,
        ai_recommendations=ai_recommendations,
    )

    validation_status, reviewer_decision = determine_verification_outcomes(
        all_exceptions=all_exceptions,
        resolved_exceptions=resolved_exceptions,
        ai_recommendations=ai_recommendations,
        decision_type=decision_type,
    )

    canonical_data = build_canonical_data(raw_record, resolved_exceptions=resolved_exceptions)

    lineage_summary = {
        "raw_record_id": raw_record.id,
        "batch_id": raw_record.batch_id,
        "total_exception_count": len(all_exceptions),
        "resolved_exception_count": len(resolved_exceptions),
        "ai_recommendation_count": len(ai_recommendations),
        "participating_reviewer_ids": [r.id for r in reviewers if hasattr(r, "id")],
    }

    try:
        verified_record = VerifiedLoanRecord.create_record(
            raw_record=raw_record,
            canonical_data=canonical_data,
            validation_status=validation_status,
            reviewer_decision=reviewer_decision,
            verified_by=actor if (actor and hasattr(actor, "pk") and actor.pk) else None,
            exceptions=resolved_exceptions,
            ai_recommendations=ai_recommendations,
            participating_reviewers=reviewers,
            lineage_summary=lineage_summary,
        )
    except IntegrityError as exc:
        logger.warning(
            "IntegrityError creating VerifiedLoanRecord for Loan '%s': %s",
            raw_record.loan_id,
            exc,
        )
        return getattr(raw_record, "verified_record", None)

    logger.info(
        "Created VerifiedLoanRecord #%s for Loan '%s' [Status: %s, Decision: %s]",
        verified_record.id,
        raw_record.loan_id,
        validation_status,
        reviewer_decision,
    )
    return verified_record


def get_primary_record(raw_record: RawLoanRecord | None) -> RawLoanRecord | None:
    """
    If raw_record came from servicer_update or doc_manifest,
    resolve the primary LOAN_TAPE record.
    """
    if not raw_record:
        return None

    if raw_record.batch and raw_record.batch.source_type != UploadBatch.SourceType.LOAN_TAPE:
        primary_record = (
            RawLoanRecord.objects.filter(
                batch__source_type=UploadBatch.SourceType.LOAN_TAPE,
                loan_id=raw_record.loan_id,
            )
            .order_by("-created", "-id")
            .first()
        )
        return primary_record

    return raw_record


def validate_loan_eligibility_for_verification(
    raw_record: RawLoanRecord | None,
) -> tuple[bool, str]:
    """
    Validates whether a raw loan record is eligible to become a VerifiedLoanRecord.

    Returns:
        tuple[bool, str]: (is_eligible, reason_message)
    """
    if not raw_record:
        return False, "RawLoanRecord instance is None."

    if hasattr(raw_record, "verified_record") and raw_record.verified_record:
        return False, f"Loan '{raw_record.loan_id}' is already verified."

    # Single DB query to inspect exception status set for this raw record
    statuses = set(
        LoanException.objects.filter(raw_record=raw_record).values_list("status", flat=True)
    )

    unresolved_set = {
        LoanException.ExceptionStatus.OPEN,
        LoanException.ExceptionStatus.UNDER_REVIEW,
    }
    unresolved_count = sum(1 for s in statuses if s in unresolved_set)
    if unresolved_count > 0:
        return False, f"Loan '{raw_record.loan_id}' has {unresolved_count} unresolved exception(s)."

    if LoanException.ExceptionStatus.REJECTED in statuses:
        return (
            False,
            f"Loan '{raw_record.loan_id}' contains a rejected exception and cannot be verified.",
        )

    return True, "Eligible for verification."


def collect_participating_reviewers(
    actor: Any = None,
    resolved_exceptions: list[LoanException] | None = None,
    ai_recommendations: list[AIRecommendation] | None = None,
) -> list[Any]:
    """
    Reusable domain helper to aggregate unique human reviewers involved in inspecting/resolving a loan.
    """
    reviewers = set()
    if actor and hasattr(actor, "pk") and actor.pk:
        reviewers.add(actor)

    if resolved_exceptions:
        for exc in resolved_exceptions:
            if exc.resolved_by and exc.resolved_by.pk:
                reviewers.add(exc.resolved_by)

    if ai_recommendations:
        for ai_rec in ai_recommendations:
            if ai_rec.reviewed_by and ai_rec.reviewed_by.pk:
                reviewers.add(ai_rec.reviewed_by)

    return list(reviewers)


def determine_verification_outcomes(
    all_exceptions: list[LoanException],
    resolved_exceptions: list[LoanException],
    ai_recommendations: list[AIRecommendation],
    decision_type: str | None = None,
) -> tuple[str, str]:
    """
    Reusable domain helper to compute validation_status and reviewer_decision choices.
    """
    if not all_exceptions:
        return (
            VerifiedLoanRecord.ValidationStatus.PASSED_CLEAN,
            VerifiedLoanRecord.ReviewerDecision.AUTO_PASSED,
        )

    status = VerifiedLoanRecord.ValidationStatus.RESOLVED_EXCEPTION

    if decision_type:
        decision = decision_type
    elif any(
        rec.status == AIRecommendation.RecommendationStatus.ACCEPTED for rec in ai_recommendations
    ):
        decision = VerifiedLoanRecord.ReviewerDecision.AI_ACCEPTED
    elif any(
        rec.status == AIRecommendation.RecommendationStatus.EDITED for rec in ai_recommendations
    ):
        decision = VerifiedLoanRecord.ReviewerDecision.AI_EDITED
    elif any(
        exc.status == LoanException.ExceptionStatus.RESOLVED_EDITED for exc in resolved_exceptions
    ):
        decision = VerifiedLoanRecord.ReviewerDecision.EDITED
    else:
        decision = VerifiedLoanRecord.ReviewerDecision.APPROVED

    return status, decision


# ruff: noqa: UP038
def build_canonical_data(
    raw_record: RawLoanRecord,
    resolved_exceptions: list[LoanException] | None = None,
) -> dict[str, Any]:
    """
    Extracts and standardizes canonical loan JSON payload from raw record data.
    Incorporates any resolved exception overrides.
    """
    raw_data = raw_record.raw_data or {}
    canonical = dict(raw_data)

    if resolved_exceptions:
        for exc in resolved_exceptions:
            if exc.override_value is not None and exc.field_name:
                canonical[exc.field_name] = exc.override_value

    canonical["loan_id"] = str(canonical.get("loan_id", "") or "").strip()
    canonical["borrower_id"] = str(canonical.get("borrower_id", "") or "").strip()

    for num_field in ("original_principal", "original_balance", "current_balance", "interest_rate"):
        if num_field in canonical and canonical[num_field] is not None:
            val = canonical[num_field]
            if isinstance(val, (int, float)):
                canonical[num_field] = float(val)
            elif isinstance(val, str):
                cleaned_val = val.replace("$", "").replace(",", "").replace("%", "").strip()
                try:
                    canonical[num_field] = float(cleaned_val)
                except (ValueError, TypeError):
                    pass

    return canonical


@transaction.atomic
def process_clean_records_for_batch(batch: UploadBatch) -> int:
    """
    Identifies all RawLoanRecords in an UploadBatch that passed validation with ZERO exceptions,
    and bulk creates VerifiedLoanRecord entries for them.

    Returns:
        Number of clean VerifiedLoanRecords created.
    """
    if not batch or batch.source_type != UploadBatch.SourceType.LOAN_TAPE:
        logger.debug(
            "Batch #%s is not a Primary Loan Tape (source_type=%s). Skipping auto-verification.",
            getattr(batch, "id", None),
            getattr(batch, "source_type", None),
        )
        return 0

    flagged_record_ids = set(
        LoanException.objects.filter(batch=batch).values_list("raw_record_id", flat=True)
    )

    clean_raw_records = list(
        RawLoanRecord.objects.filter(
            batch=batch,
            verified_record__isnull=True,
        ).exclude(id__in=flagged_record_ids)
    )

    if not clean_raw_records:
        return 0

    records_data = []
    for raw_rec in clean_raw_records:
        canonical = build_canonical_data(raw_rec)
        records_data.append(
            {
                "raw_record": raw_rec,
                "canonical_data": canonical,
                "validation_status": VerifiedLoanRecord.ValidationStatus.PASSED_CLEAN,
                "reviewer_decision": VerifiedLoanRecord.ReviewerDecision.AUTO_PASSED,
                "lineage_summary": {"batch_id": batch.id, "auto_verified": True},
            }
        )

    created = VerifiedLoanRecord.create_records_bulk(records_data, batch_size=1000)
    logger.info("Bulk auto-verified %d clean records for Batch #%s", len(created), batch.id)
    return len(created)
