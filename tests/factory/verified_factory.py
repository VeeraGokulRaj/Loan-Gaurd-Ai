"""Factory helpers for VerifiedLoanRecord and its prerequisite lineage objects."""

from django.utils import timezone
from faker import Faker

from app.models.ai import AIRecommendation
from app.models.ingestion import RawLoanRecord, UploadBatch
from app.models.validation import LoanException, ValidationRule, ValidationSeverity
from app.models.verified import VerifiedLoanRecord

fake = Faker()

_NOT_SET = object()

DEFAULT_CANONICAL = {
    "loan_id": "LG-0001",
    "borrower_id": "BR-1",
    "loan_amount": 2500000.0,
}


class VerifiedLoanRecordFactory:
    """Factory helpers for building VerifiedLoanRecord lineage test instances."""

    @staticmethod
    def create_batch():
        return UploadBatch.objects.create(
            file_name=f"{fake.word()}_loan_tape.csv",
            source_type=UploadBatch.SourceType.LOAN_TAPE,
            status=UploadBatch.BatchStatus.INGESTED,
        )

    @staticmethod
    def create_raw_record(raw_data=None, batch=None, row_number=1):
        return RawLoanRecord.objects.create(
            batch=batch or VerifiedLoanRecordFactory.create_batch(),
            row_number=row_number,
            raw_data=raw_data if raw_data is not None else {"loan_id": "LG-0001"},
            source_system="LOAN_TAPE",
        )

    @staticmethod
    def create_rule(rule_code="VAL_001", field_name="loan_id"):
        return ValidationRule.objects.create(
            rule_code=rule_code,
            strategy_key=rule_code,
            rule_name=f"Rule {rule_code}",
            field_name=field_name,
            description=f"Description for {rule_code}",
        )

    @staticmethod
    def create_exception(raw_record=None, rule=None):
        record = raw_record or VerifiedLoanRecordFactory.create_raw_record()
        rule = rule or VerifiedLoanRecordFactory.create_rule()
        return LoanException.objects.create(
            batch=record.batch,
            raw_record=record,
            rule=rule,
            rule_code=rule.rule_code,
            field_name=rule.field_name,
            severity=ValidationSeverity.MEDIUM,
            description="Sample exception flagged by validation.",
            status=LoanException.ExceptionStatus.RESOLVED_ACCEPTED,
        )

    @staticmethod
    def create_ai_recommendation(exception=None):
        exc = exception or VerifiedLoanRecordFactory.create_exception()
        return AIRecommendation.objects.create(
            recommendation_type=AIRecommendation.RecommendationType.EXCEPTION_REVIEW,
            exception=exc,
            rule=exc.rule,
            suggested_value="1000",
            explanation="AI suggested correction.",
            confidence_score=0.9,
            status=AIRecommendation.RecommendationStatus.ACCEPTED,
        )

    @staticmethod
    def create_verified_record(
        raw_record=None,
        canonical_data=None,
        verified_at=None,
        record_hash=_NOT_SET,
        **kwargs,
    ):
        raw = raw_record or VerifiedLoanRecordFactory.create_raw_record()
        canonical = DEFAULT_CANONICAL if canonical_data is None else canonical_data
        verified_at = verified_at if verified_at is not None else timezone.now()
        payload, computed_hash = VerifiedLoanRecord._prepare_record_payload(
            raw_record=raw,
            canonical_data=canonical,
            verified_at=verified_at,
            validation_status=kwargs.pop(
                "validation_status", VerifiedLoanRecord.ValidationStatus.PASSED_CLEAN
            ),
            reviewer_decision=kwargs.pop(
                "reviewer_decision", VerifiedLoanRecord.ReviewerDecision.AUTO_PASSED
            ),
            verified_by=kwargs.pop("verified_by", None),
            lineage_summary=kwargs.pop("lineage_summary", None),
        )
        if record_hash is not _NOT_SET:
            payload["record_hash"] = record_hash
        payload.update(kwargs)
        return VerifiedLoanRecord.objects.create(**payload)
