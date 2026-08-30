"""
Module B: Configurable Validation Engine for LoanGuard AI.

This module implements the strategy pattern validation framework, featuring:
1. 15 Domain Strategy Handlers covering all intentional loan data quality issues.
2. O(N) Batch Frequency Indexing for high-performance duplicate/duplication checks (100k+ records).
3. GenericExpressionRule evaluator for dynamic operator thresholds (IS_NULL, >, <, ==, etc.).
4. ValidationEngine coordinator executing active ValidationRules against RawLoanRecords.
"""

from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from django.db import transaction

from app.management.commands.generate_sample_csv import VALID_US_STATES
from app.models.audit import AuditEvent
from app.models.ingestion import (
    DocumentManifestRecord,
    RawLoanRecord,
    ServicerUpdateRecord,
    UploadBatch,
)
from app.models.validation import LoanException, ValidationRule

# ==============================================================================
# Domain Constants & Fallback Rule Thresholds
# ==============================================================================

COMMON_DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%Y/%m/%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%Y-%m-%d %H:%M:%S",
)
VALID_US_STATES_SET: set[str] = set(VALID_US_STATES)

# Default Rule Parameter Fallback Thresholds
DEFAULT_MIN_INTEREST_RATE: float = 0.0
DEFAULT_MAX_INTEREST_RATE: float = 35.0
DEFAULT_MAX_CURRENT_DPD: int = 30
DEFAULT_MAX_SERVICER_DELTA_DOLLARS: float = 1.0
DEFAULT_MAX_STALE_DAYS: int = 180
DEFAULT_MAX_LOANS_PER_BORROWER_WINDOW: int = 5


@dataclass
class RuleResult:
    """Represents the output evaluation of a validation rule check on a record."""

    is_valid: bool
    field_name: str
    rule_code: str
    severity: int  # ValidationSeverity integer value
    message: str


@dataclass
class ValidationContext:
    """
    Pre-computed batch-level indices enabling O(1) frequency lookups across 100k+ records.
    Prevents O(N^2) performance degradation during duplicate and repetition validation checks.
    """

    loan_id_counts: Counter = field(default_factory=Counter)
    borrower_id_counts: Counter = field(default_factory=Counter)
    triplet_counts: Counter = field(default_factory=Counter)
    servicer_map: dict[str, list[ServicerUpdateRecord]] = field(default_factory=dict)
    doc_manifest_map: dict[str, list[DocumentManifestRecord]] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        raw_records: Sequence[RawLoanRecord],
        servicer_records: Sequence[ServicerUpdateRecord] | None = None,
        doc_manifest_records: Sequence[DocumentManifestRecord] | None = None,
        include_db_history: bool = True,
    ) -> "ValidationContext":
        """Pre-calculates O(N) batch and DB-wide counts and lookup maps in a single pass."""
        loan_id_counts = Counter()
        borrower_id_counts = Counter()
        triplet_counts = Counter()

        current_batch_ids = cls._extract_batch_ids(raw_records)

        # 1. Index batch records
        cls._index_records(
            records=raw_records,
            loan_id_counts=loan_id_counts,
            borrower_id_counts=borrower_id_counts,
            triplet_counts=triplet_counts,
        )

        # 2. Index historical DB records if requested
        if include_db_history:
            cls._index_db_history(
                current_batch_ids=current_batch_ids,
                loan_id_counts=loan_id_counts,
                borrower_id_counts=borrower_id_counts,
                triplet_counts=triplet_counts,
            )

        # 3. Build servicer update lookup map
        servicer_map = cls._build_servicer_map(
            servicer_records=servicer_records,
            target_loan_ids=list(loan_id_counts.keys()),
        )

        # 4. Build document manifest lookup map
        doc_manifest_map = cls._build_doc_manifest_map(
            doc_manifest_records=doc_manifest_records,
            target_loan_ids=list(loan_id_counts.keys()),
        )

        return cls(
            loan_id_counts=loan_id_counts,
            borrower_id_counts=borrower_id_counts,
            triplet_counts=triplet_counts,
            servicer_map=servicer_map,
            doc_manifest_map=doc_manifest_map,
        )

    @staticmethod
    def _extract_batch_ids(raw_records: Sequence[RawLoanRecord]) -> set[int]:
        batch_ids = set()
        for record in raw_records:
            batch_id = getattr(record, "batch_id", None)
            if batch_id:
                batch_ids.add(batch_id)
        return batch_ids

    @staticmethod
    def _index_raw_data_entry(
        raw_data: dict[str, Any],
        loan_id_counts: Counter,
        borrower_id_counts: Counter,
        triplet_counts: Counter,
    ) -> None:
        if not raw_data or not isinstance(raw_data, dict):
            return

        loan_id = str(raw_data.get("loan_id", "") or "").strip()
        borrower_id = str(raw_data.get("borrower_id", "") or "").strip()
        principal = safe_float(raw_data.get("original_principal"))
        origination_date = str(raw_data.get("origination_date", "") or "").strip()

        if loan_id:
            loan_id_counts[loan_id] += 1
        if borrower_id:
            borrower_id_counts[borrower_id] += 1
        if borrower_id and principal is not None and origination_date:
            triplet_counts[(borrower_id, principal, origination_date)] += 1

    @classmethod
    def _index_records(
        cls,
        records: Sequence[RawLoanRecord],
        loan_id_counts: Counter,
        borrower_id_counts: Counter,
        triplet_counts: Counter,
    ) -> None:
        for record in records:
            batch_source = getattr(getattr(record, "batch", None), "source_type", None)
            if batch_source and batch_source != UploadBatch.SourceType.LOAN_TAPE:
                continue
            raw_data = getattr(record, "raw_data", {}) or {}
            cls._index_raw_data_entry(raw_data, loan_id_counts, borrower_id_counts, triplet_counts)

    @classmethod
    def _index_db_history(
        cls,
        current_batch_ids: set[int],
        loan_id_counts: Counter,
        borrower_id_counts: Counter,
        triplet_counts: Counter,
    ) -> None:
        try:
            db_records = RawLoanRecord.objects.filter(
                batch__source_type=UploadBatch.SourceType.LOAN_TAPE,
            )
            if current_batch_ids:
                db_records = db_records.exclude(batch_id__in=current_batch_ids)

            existing_raw_datas = db_records.values_list("raw_data", flat=True)
            for raw_data in existing_raw_datas:
                cls._index_raw_data_entry(
                    raw_data, loan_id_counts, borrower_id_counts, triplet_counts
                )
        except Exception:
            pass

    @staticmethod
    def _build_servicer_map(
        servicer_records: Sequence[ServicerUpdateRecord] | None,
        target_loan_ids: list[str],
    ) -> dict[str, list[ServicerUpdateRecord]]:
        servicer_map: dict[str, list[ServicerUpdateRecord]] = {}

        if servicer_records:
            for record in servicer_records:
                if record and getattr(record, "loan_id", None):
                    loan_id = str(record.loan_id).strip()
                    if loan_id:
                        servicer_map.setdefault(loan_id, []).append(record)

        try:
            valid_targets = [loan_id for loan_id in target_loan_ids if loan_id]
            if valid_targets:
                db_servicers = ServicerUpdateRecord.objects.filter(
                    loan_id__in=valid_targets
                ).order_by("-created")
                for rec in db_servicers:
                    if rec and getattr(rec, "loan_id", None):
                        loan_id = str(rec.loan_id).strip()
                        if loan_id:
                            lid_records = servicer_map.setdefault(loan_id, [])
                            if rec not in lid_records:
                                lid_records.append(rec)
        except Exception:
            pass

        return servicer_map

    @staticmethod
    def _build_doc_manifest_map(
        doc_manifest_records: Sequence[DocumentManifestRecord] | None,
        target_loan_ids: list[str],
    ) -> dict[str, list[DocumentManifestRecord]]:
        doc_manifest_map: dict[str, list[DocumentManifestRecord]] = {}

        if doc_manifest_records:
            for record in doc_manifest_records:
                if record and getattr(record, "loan_id", None):
                    lid = str(record.loan_id).strip()
                    if lid:
                        doc_manifest_map.setdefault(lid, []).append(record)

        try:
            valid_targets = [lid for lid in target_loan_ids if lid]
            if valid_targets:
                db_docs = DocumentManifestRecord.objects.filter(loan_id__in=valid_targets).order_by(
                    "-created"
                )
                for rec in db_docs:
                    if rec and getattr(rec, "loan_id", None):
                        lid = str(rec.loan_id).strip()
                        if lid:
                            lid_records = doc_manifest_map.setdefault(lid, [])
                            if rec not in lid_records:
                                lid_records.append(rec)
        except Exception:
            pass

        return doc_manifest_map


# ruff: noqa: UP038
def safe_float(value: Any) -> float | None:
    """Safely converts input value to float, handling strings with currency symbols and commas."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, Decimal):
        return float(value)
    string_value = str(value).strip().replace("$", "").replace(",", "")
    if not string_value:
        return None
    try:
        return float(string_value)
    except ValueError:
        return None


def safe_date(value: Any) -> datetime | None:
    """Safely parses string or date input into a datetime object using standard library."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    string_value = str(value).strip()
    if not string_value:
        return None
    try:
        return datetime.fromisoformat(string_value.replace("Z", "+00:00"))
    except ValueError:
        pass

    for format_string in COMMON_DATE_FORMATS:
        try:
            return datetime.strptime(string_value, format_string)
        except ValueError:
            continue

    return None


class BaseValidationRule(ABC):
    """Abstract base class for all validation strategy rule handlers."""

    strategy_key: str = ""

    @abstractmethod
    def validate(
        self,
        raw_record: RawLoanRecord,
        db_rule: ValidationRule,
        context: ValidationContext | None = None,
    ) -> RuleResult | None:
        """
        Evaluates a raw loan record against the validation strategy.

        Returns:
            RuleResult if a validation failure/exception occurs, or None if valid.
        """
        pass


# ==============================================================================
# 1. SPECIFIC STRATEGY HANDLERS (15 intentional data quality rules)
# ==============================================================================


class MissingLoanIdRule(BaseValidationRule):
    """VAL_001: Missing Loan ID"""

    strategy_key = "MISSING_LOAN_ID"

    def validate(
        self,
        raw_record: RawLoanRecord,
        db_rule: ValidationRule,
        context: ValidationContext | None = None,
    ) -> RuleResult | None:
        record_data = raw_record.raw_data or {}
        loan_id = str(record_data.get("loan_id", "") or "").strip()
        if not loan_id:
            return RuleResult(
                is_valid=False,
                field_name=db_rule.field_name or "loan_id",
                rule_code=db_rule.rule_code,
                severity=db_rule.severity,
                message=f"Row #{raw_record.row_number}: Primary loan_id identifier is missing or empty.",
            )
        return None


class DuplicateLoanIdRule(BaseValidationRule):
    """VAL_002: Duplicate Loan ID (Optimized O(1) Lookup)"""

    strategy_key = "DUPLICATE_LOAN_ID"

    def validate(
        self,
        raw_record: RawLoanRecord,
        db_rule: ValidationRule,
        context: ValidationContext | None = None,
    ) -> RuleResult | None:
        record_data = raw_record.raw_data or {}
        loan_id = str(record_data.get("loan_id", "") or "").strip()
        if not loan_id:
            return None  # MissingLoanIdRule handles empty IDs

        if context and context.loan_id_counts:
            # O(1) frequency lookup replacing O(N) linear loop
            matching_count = context.loan_id_counts.get(loan_id, 0)
            if matching_count > 1:
                return RuleResult(
                    is_valid=False,
                    field_name=db_rule.field_name or "loan_id",
                    rule_code=db_rule.rule_code,
                    severity=db_rule.severity,
                    message=f"Duplicate loan_id '{loan_id}' detected ({matching_count} total occurrences across database/batch).",
                )
        return None


class DuplicateBorrowerTripletRule(BaseValidationRule):
    """VAL_003: Duplicate Borrower Triplet (borrower_id, original_principal, origination_date) (Optimized O(1) Lookup)"""

    strategy_key = "DUPLICATE_BORROWER_TRIPLET"

    def validate(
        self,
        raw_record: RawLoanRecord,
        db_rule: ValidationRule,
        context: ValidationContext | None = None,
    ) -> RuleResult | None:
        record_data = raw_record.raw_data or {}
        borrower_id = str(record_data.get("borrower_id", "") or "").strip()
        principal = safe_float(record_data.get("original_principal"))
        origination_date = str(record_data.get("origination_date", "") or "").strip()

        if not borrower_id or principal is None or not origination_date:
            return None

        triplet = (borrower_id, principal, origination_date)

        if context and context.triplet_counts:
            # O(1) frequency lookup replacing O(N) linear loop
            matching_count = context.triplet_counts.get(triplet, 0)
            if matching_count > 1:
                return RuleResult(
                    is_valid=False,
                    field_name=db_rule.field_name or "borrower_id",
                    rule_code=db_rule.rule_code,
                    severity=db_rule.severity,
                    message=(
                        f"Duplicate borrower triplet detected for borrower '{borrower_id}' "
                        f"with principal ${principal:,.2f} and origination date '{origination_date}' "
                        f"({matching_count} total occurrences across database/batch)."
                    ),
                )
        return None


class InvalidDateFormatRule(BaseValidationRule):
    """VAL_004: Invalid Date Formats"""

    strategy_key = "INVALID_DATE_FORMAT"

    def validate(
        self,
        raw_record: RawLoanRecord,
        db_rule: ValidationRule,
        context: ValidationContext | None = None,
    ) -> RuleResult | None:
        record_data = raw_record.raw_data or {}
        field_name = db_rule.field_name or "origination_date"
        value_string = str(record_data.get(field_name, "") or "").strip()

        if not value_string:
            return None  # Missing checks handled separately

        parsed_datetime = safe_date(value_string)
        if parsed_datetime is None:
            return RuleResult(
                is_valid=False,
                field_name=field_name,
                rule_code=db_rule.rule_code,
                severity=db_rule.severity,
                message=f"Field '{field_name}' has unparseable date string: '{value_string}'.",
            )
        return None


class MaturityBeforeOriginationRule(BaseValidationRule):
    """VAL_005: Maturity Date Before Origination Date"""

    strategy_key = "MATURITY_BEFORE_ORIGINATION"

    def validate(
        self,
        raw_record: RawLoanRecord,
        db_rule: ValidationRule,
        context: ValidationContext | None = None,
    ) -> RuleResult | None:
        record_data = raw_record.raw_data or {}
        origination_date_string = str(record_data.get("origination_date", "") or "").strip()
        maturity_date_string = str(record_data.get("maturity_date", "") or "").strip()

        origination_datetime = safe_date(origination_date_string)
        maturity_datetime = safe_date(maturity_date_string)

        if origination_datetime and maturity_datetime:
            if maturity_datetime <= origination_datetime:
                return RuleResult(
                    is_valid=False,
                    field_name=db_rule.field_name or "maturity_date",
                    rule_code=db_rule.rule_code,
                    severity=db_rule.severity,
                    message=(
                        f"Maturity date ({maturity_datetime.strftime('%Y-%m-%d')}) is on or before "
                        f"origination date ({origination_datetime.strftime('%Y-%m-%d')})."
                    ),
                )
        return None


class NegativePrincipalBalanceRule(BaseValidationRule):
    """VAL_006: Negative Principal Balance"""

    strategy_key = "NEGATIVE_PRINCIPAL_BALANCE"

    def validate(
        self,
        raw_record: RawLoanRecord,
        db_rule: ValidationRule,
        context: ValidationContext | None = None,
    ) -> RuleResult | None:
        record_data = raw_record.raw_data or {}
        original_principal = safe_float(record_data.get("original_principal"))
        current_balance = safe_float(record_data.get("current_balance"))

        if original_principal is not None and original_principal < 0:
            return RuleResult(
                is_valid=False,
                field_name="original_principal",
                rule_code=db_rule.rule_code,
                severity=db_rule.severity,
                message=f"Original principal cannot be negative: ${original_principal:,.2f}.",
            )

        if current_balance is not None and current_balance < 0:
            return RuleResult(
                is_valid=False,
                field_name="current_balance",
                rule_code=db_rule.rule_code,
                severity=db_rule.severity,
                message=f"Current balance cannot be negative: ${current_balance:,.2f}.",
            )

        return None


class BalanceExceedsPrincipalRule(BaseValidationRule):
    """VAL_007: Current Balance Greater Than Original Principal"""

    strategy_key = "BALANCE_EXCEEDS_PRINCIPAL"

    def validate(
        self,
        raw_record: RawLoanRecord,
        db_rule: ValidationRule,
        context: ValidationContext | None = None,
    ) -> RuleResult | None:
        record_data = raw_record.raw_data or {}
        original_principal = safe_float(record_data.get("original_principal"))
        current_balance = safe_float(record_data.get("current_balance"))

        if original_principal is not None and current_balance is not None:
            if current_balance > original_principal:
                return RuleResult(
                    is_valid=False,
                    field_name=db_rule.field_name or "current_balance",
                    rule_code=db_rule.rule_code,
                    severity=db_rule.severity,
                    message=(
                        f"Current balance (${current_balance:,.2f}) exceeds original principal "
                        f"(${original_principal:,.2f})."
                    ),
                )
        return None


class OutOfRangeInterestRateRule(BaseValidationRule):
    """VAL_008: Interest Rate Outside Expected Range"""

    strategy_key = "OUT_OF_RANGE_INTEREST_RATE"

    def validate(
        self,
        raw_record: RawLoanRecord,
        db_rule: ValidationRule,
        context: ValidationContext | None = None,
    ) -> RuleResult | None:
        record_data = raw_record.raw_data or {}
        interest_rate_value = safe_float(record_data.get("interest_rate"))

        if interest_rate_value is None:
            return None

        # Convert fraction to percentage if stored as decimal (e.g., 0.065 -> 6.5)
        rate_percentage = (
            interest_rate_value * 100.0 if 0.0 < interest_rate_value <= 1.0 else interest_rate_value
        )

        parameters = db_rule.parameters or {}
        min_rate = (
            safe_float(parameters.get("min_rate"))
            if parameters.get("min_rate") is not None
            else DEFAULT_MIN_INTEREST_RATE
        )
        max_rate = (
            safe_float(parameters.get("max_rate"))
            if parameters.get("max_rate") is not None
            else DEFAULT_MAX_INTEREST_RATE
        )

        if min_rate is None:
            min_rate = DEFAULT_MIN_INTEREST_RATE
        if max_rate is None:
            max_rate = DEFAULT_MAX_INTEREST_RATE

        if rate_percentage < min_rate or rate_percentage > max_rate:
            return RuleResult(
                is_valid=False,
                field_name=db_rule.field_name or "interest_rate",
                rule_code=db_rule.rule_code,
                severity=db_rule.severity,
                message=(
                    f"Interest rate of {rate_percentage:.2f}% is outside allowed range "
                    f"[{min_rate:.1f}%, {max_rate:.1f}%]."
                ),
            )
        return None


class StatusDpdInconsistencyRule(BaseValidationRule):
    """VAL_009: Payment Status Inconsistent With Days Past Due"""

    strategy_key = "STATUS_DPD_INCONSISTENCY"

    def validate(
        self,
        raw_record: RawLoanRecord,
        db_rule: ValidationRule,
        context: ValidationContext | None = None,
    ) -> RuleResult | None:
        record_data = raw_record.raw_data or {}
        payment_status = str(record_data.get("payment_status", "") or "").strip().upper()
        days_past_due_value = safe_float(record_data.get("days_past_due"))

        if not payment_status or days_past_due_value is None:
            return None

        parameters = db_rule.parameters or {}
        max_current_dpd = int(parameters.get("max_current_dpd", DEFAULT_MAX_CURRENT_DPD))

        if payment_status in ["CURRENT", "PERFORMING"] and days_past_due_value > max_current_dpd:
            return RuleResult(
                is_valid=False,
                field_name=db_rule.field_name or "payment_status",
                rule_code=db_rule.rule_code,
                severity=db_rule.severity,
                message=(
                    f"Loan status is '{payment_status}' but days past due ({int(days_past_due_value)}) exceeds "
                    f"threshold of {max_current_dpd} days."
                ),
            )
        return None


class MissingDocumentStatusRule(BaseValidationRule):
    """VAL_010: Missing Document Status (reconciled against DocumentManifestRecord or raw payload)"""

    strategy_key = "MISSING_DOCUMENT_STATUS"

    def validate(
        self,
        raw_record: RawLoanRecord,
        db_rule: ValidationRule,
        context: ValidationContext | None = None,
    ) -> RuleResult | None:
        record_data = raw_record.raw_data or {}
        loan_id = str(record_data.get("loan_id", "") or "").strip()

        # Check cross-file document manifest records from O(1) context map if available
        if context and context.doc_manifest_map and loan_id in context.doc_manifest_map:
            doc_records = context.doc_manifest_map.get(loan_id) or []
            if not isinstance(doc_records, list):
                doc_records = [doc_records]

            for document_record in doc_records:
                if not document_record:
                    continue

                verification_status = (
                    str(getattr(document_record, "document_verification_status", "") or "")
                    .strip()
                    .upper()
                )
                note_present = bool(getattr(document_record, "promissory_note_present", False))
                id_present = bool(getattr(document_record, "id_proof_present", False))
                income_present = bool(
                    getattr(document_record, "income_verification_present", False)
                )

                if verification_status in [
                    "MISSING",
                    "INCOMPLETE",
                    "PENDING",
                    "UNVERIFIED",
                    "NONE",
                    "",
                ] or not (note_present and id_present and income_present):
                    return RuleResult(
                        is_valid=False,
                        field_name=db_rule.field_name or "document_status",
                        rule_code=db_rule.rule_code,
                        severity=db_rule.severity,
                        message=(
                            f"Document manifest check failed for loan '{loan_id}': "
                            f"Verification Status '{verification_status or 'MISSING'}', Note: {note_present}, "
                            f"ID: {id_present}, Income: {income_present}."
                        ),
                    )

            if doc_records:
                return None

        # Fallback to checking raw loan tape fields
        document_status = str(record_data.get("document_status", "") or "").strip().upper()
        if not document_status or document_status in [
            "MISSING",
            "INCOMPLETE",
            "PENDING",
            "UNVERIFIED",
            "NONE",
        ]:
            return RuleResult(
                is_valid=False,
                field_name=db_rule.field_name or "document_status",
                rule_code=db_rule.rule_code,
                severity=db_rule.severity,
                message=f"Missing or incomplete document status: '{document_status or 'EMPTY'}'.",
            )
        return None


class ServicerUpdateConflictRule(BaseValidationRule):
    """VAL_011: Conflicting Values Between loan_tape.csv and servicer_update.csv"""

    strategy_key = "SERVICER_UPDATE_CONFLICT"

    def validate(
        self,
        raw_record: RawLoanRecord,
        db_rule: ValidationRule,
        context: ValidationContext | None = None,
    ) -> RuleResult | None:
        record_data = raw_record.raw_data or {}
        loan_id = str(record_data.get("loan_id", "") or "").strip()
        tape_balance = safe_float(record_data.get("current_balance"))

        if (
            not loan_id
            or tape_balance is None
            or not context
            or not context.servicer_map
            or loan_id not in context.servicer_map
        ):
            return None

        servicer_records = context.servicer_map.get(loan_id) or []
        if not isinstance(servicer_records, list):
            servicer_records = [servicer_records]

        parameters = db_rule.parameters or {}
        max_delta_dollars = (
            safe_float(parameters.get("max_delta_dollars"))
            if parameters.get("max_delta_dollars") is not None
            else DEFAULT_MAX_SERVICER_DELTA_DOLLARS
        )
        if max_delta_dollars is None:
            max_delta_dollars = DEFAULT_MAX_SERVICER_DELTA_DOLLARS

        for servicer_record in servicer_records:
            if not servicer_record:
                continue

            servicer_balance = safe_float(getattr(servicer_record, "updated_current_balance", None))
            if servicer_balance is None:
                continue

            delta_dollars = abs(tape_balance - servicer_balance)
            if delta_dollars > max_delta_dollars:
                return RuleResult(
                    is_valid=False,
                    field_name=db_rule.field_name or "current_balance",
                    rule_code=db_rule.rule_code,
                    severity=db_rule.severity,
                    message=(
                        f"Servicer balance conflict for loan '{loan_id}': Tape balance (${tape_balance:,.2f}) "
                        f"differs from Servicer update balance (${servicer_balance:,.2f}) by ${delta_dollars:,.2f} "
                        f"(max allowed delta: ${max_delta_dollars:,.2f})."
                    ),
                )

        return None


class StaleRecordRule(BaseValidationRule):
    """VAL_012: Stale Records Based on last_updated_at"""

    strategy_key = "STALE_RECORD"

    def validate(
        self,
        raw_record: RawLoanRecord,
        db_rule: ValidationRule,
        context: ValidationContext | None = None,
    ) -> RuleResult | None:
        record_data = raw_record.raw_data or {}
        updated_date_string = str(
            record_data.get("last_updated_at", "") or record_data.get("as_of_date", "") or ""
        ).strip()
        updated_datetime = safe_date(updated_date_string)

        if not updated_datetime:
            return None

        parameters = db_rule.parameters or {}
        max_stale_days = int(parameters.get("max_stale_days", DEFAULT_MAX_STALE_DAYS))

        now_datetime = datetime.now(UTC)
        if updated_datetime.tzinfo is None:
            updated_datetime = updated_datetime.replace(tzinfo=UTC)

        age_in_days = (now_datetime - updated_datetime).days
        if age_in_days > max_stale_days:
            return RuleResult(
                is_valid=False,
                field_name=db_rule.field_name or "last_updated_at",
                rule_code=db_rule.rule_code,
                severity=db_rule.severity,
                message=(
                    f"Record is stale: last updated {age_in_days} days ago "
                    f"({updated_datetime.strftime('%Y-%m-%d')}), exceeding threshold of {max_stale_days} days."
                ),
            )
        return None


class InvalidStateCodeRule(BaseValidationRule):
    """VAL_013: Invalid State Codes"""

    strategy_key = "INVALID_STATE_CODE"

    def validate(
        self,
        raw_record: RawLoanRecord,
        db_rule: ValidationRule,
        context: ValidationContext | None = None,
    ) -> RuleResult | None:
        record_data = raw_record.raw_data or {}
        state_value = (
            str(record_data.get("borrower_state", "") or record_data.get("state", "") or "")
            .strip()
            .upper()
        )

        if not state_value:
            return None  # Missing state checked separately if required

        if state_value not in VALID_US_STATES_SET:
            return RuleResult(
                is_valid=False,
                field_name=db_rule.field_name or "borrower_state",
                rule_code=db_rule.rule_code,
                severity=db_rule.severity,
                message=f"Invalid US state code: '{state_value}'. Must be a valid 2-letter state abbreviation.",
            )
        return None


class SuspiciousBorrowerDuplicationRule(BaseValidationRule):
    """VAL_014: Suspiciously Repeated Borrower Records (Optimized O(1) Lookup)"""

    strategy_key = "SUSPICIOUS_BORROWER_DUPLICATION"

    def validate(
        self,
        raw_record: RawLoanRecord,
        db_rule: ValidationRule,
        context: ValidationContext | None = None,
    ) -> RuleResult | None:
        record_data = raw_record.raw_data or {}
        borrower_id = str(record_data.get("borrower_id", "") or "").strip()

        if not borrower_id or not context or not context.borrower_id_counts:
            return None

        parameters = db_rule.parameters or {}
        max_loans = int(
            parameters.get("max_loans_per_window", DEFAULT_MAX_LOANS_PER_BORROWER_WINDOW)
        )

        # O(1) frequency lookup replacing O(N) linear loop
        borrower_count = context.borrower_id_counts.get(borrower_id, 0)

        if borrower_count > max_loans:
            return RuleResult(
                is_valid=False,
                field_name=db_rule.field_name or "borrower_id",
                rule_code=db_rule.rule_code,
                severity=db_rule.severity,
                message=(
                    f"Suspicious borrower duplication: borrower '{borrower_id}' is associated "
                    f"with {borrower_count} loan records in database (max allowed: {max_loans})."
                ),
            )
        return None


class ClosedLoanPositiveBalanceRule(BaseValidationRule):
    """VAL_015: Loans Marked Closed But Showing Positive Balance"""

    strategy_key = "CLOSED_LOAN_POSITIVE_BALANCE"

    def validate(
        self,
        raw_record: RawLoanRecord,
        db_rule: ValidationRule,
        context: ValidationContext | None = None,
    ) -> RuleResult | None:
        record_data = raw_record.raw_data or {}
        payment_status = str(record_data.get("payment_status", "") or "").strip().upper()
        current_balance = safe_float(record_data.get("current_balance"))

        if (
            payment_status in ["CLOSED", "PAID_OFF", "SETTLED"]
            and current_balance is not None
            and current_balance > 0.0
        ):
            return RuleResult(
                is_valid=False,
                field_name=db_rule.field_name or "current_balance",
                rule_code=db_rule.rule_code,
                severity=db_rule.severity,
                message=(
                    f"Loan is marked '{payment_status}' but retains a positive current balance "
                    f"of ${current_balance:,.2f}."
                ),
            )
        return None


# ==============================================================================
# 2. GENERIC EXPRESSION EVALUATOR (GenericExpressionRule)
# ==============================================================================


class GenericExpressionRule(BaseValidationRule):
    """
    Evaluates dynamic, parameterized rules (IS_NULL, NOT_NULL, >, <, >=, <=, ==, !=, IN, NOT_IN)
    when no custom strategy class is mapped.
    """

    strategy_key = "GENERIC_EXPRESSION"

    def validate(
        self,
        raw_record: RawLoanRecord,
        db_rule: ValidationRule,
        context: ValidationContext | None = None,
    ) -> RuleResult | None:
        record_data = raw_record.raw_data or {}
        field_name = db_rule.field_name
        if not field_name:
            return None

        value = record_data.get(field_name)
        parameters = db_rule.parameters or {}
        operator = str(parameters.get("operator", "IS_NULL")).strip().upper()
        if operator == "=":
            operator = "=="
        target_value = parameters.get("target_value")

        is_failing = False
        fail_reason = ""

        if operator == "IS_NULL":
            if value is None or str(value).strip() == "":
                is_failing = True
                fail_reason = f"Required field '{field_name}' is missing or empty."
        elif operator == "NOT_NULL":
            if value is not None and str(value).strip() != "":
                is_failing = True
                fail_reason = f"Field '{field_name}' is expected to be null or empty."
        elif operator in [">", "<", ">=", "<="]:
            numeric_value = safe_float(value)
            target_number = safe_float(target_value)
            if numeric_value is not None and target_number is not None:
                if operator == ">" and numeric_value > target_number:
                    is_failing = True
                    fail_reason = (
                        f"Field '{field_name}' ({numeric_value}) > target ({target_number})."
                    )
                elif operator == "<" and numeric_value < target_number:
                    is_failing = True
                    fail_reason = (
                        f"Field '{field_name}' ({numeric_value}) < target ({target_number})."
                    )
                elif operator == ">=" and numeric_value >= target_number:
                    is_failing = True
                    fail_reason = (
                        f"Field '{field_name}' ({numeric_value}) >= target ({target_number})."
                    )
                elif operator == "<=" and numeric_value <= target_number:
                    is_failing = True
                    fail_reason = (
                        f"Field '{field_name}' ({numeric_value}) <= target ({target_number})."
                    )
        elif operator == "==":
            if str(value).strip().upper() == str(target_value).strip().upper():
                is_failing = True
                fail_reason = f"Field '{field_name}' equals restricted value '{target_value}'."
        elif operator == "!=":
            if str(value).strip().upper() != str(target_value).strip().upper():
                is_failing = True
                fail_reason = (
                    f"Field '{field_name}' does not match expected value '{target_value}'."
                )
        elif operator == "IN":
            target_list = [
                str(item).strip().upper()
                for item in (target_value if isinstance(target_value, list) else [])
            ]
            if str(value).strip().upper() in target_list:
                is_failing = True
                fail_reason = (
                    f"Field '{field_name}' value '{value}' is in restricted list {target_list}."
                )
        elif operator == "NOT_IN":
            target_list = [
                str(item).strip().upper()
                for item in (target_value if isinstance(target_value, list) else [])
            ]
            if str(value).strip().upper() not in target_list:
                is_failing = True
                fail_reason = (
                    f"Field '{field_name}' value '{value}' is not in allowed list {target_list}."
                )

        if is_failing:
            return RuleResult(
                is_valid=False,
                field_name=field_name,
                rule_code=db_rule.rule_code,
                severity=db_rule.severity,
                message=fail_reason,
            )

        return None


# ==============================================================================
# 3. VALIDATION ENGINE COORDINATOR
# ==============================================================================


class ValidationEngine:
    """
    Coordinates loading active rules, mapping strategy handlers, executing batch checks,
    and bulk-persisting generated LoanException entries into the database.
    """

    STRATEGY_MAP: dict[str, BaseValidationRule] = {
        "MISSING_LOAN_ID": MissingLoanIdRule(),
        "DUPLICATE_LOAN_ID": DuplicateLoanIdRule(),
        "DUPLICATE_BORROWER_TRIPLET": DuplicateBorrowerTripletRule(),
        "INVALID_DATE_FORMAT": InvalidDateFormatRule(),
        "MATURITY_BEFORE_ORIGINATION": MaturityBeforeOriginationRule(),
        "NEGATIVE_PRINCIPAL_BALANCE": NegativePrincipalBalanceRule(),
        "BALANCE_EXCEEDS_PRINCIPAL": BalanceExceedsPrincipalRule(),
        "OUT_OF_RANGE_INTEREST_RATE": OutOfRangeInterestRateRule(),
        "STATUS_DPD_INCONSISTENCY": StatusDpdInconsistencyRule(),
        "MISSING_DOCUMENT_STATUS": MissingDocumentStatusRule(),
        "SERVICER_UPDATE_CONFLICT": ServicerUpdateConflictRule(),
        "STALE_RECORD": StaleRecordRule(),
        "INVALID_STATE_CODE": InvalidStateCodeRule(),
        "SUSPICIOUS_BORROWER_DUPLICATION": SuspiciousBorrowerDuplicationRule(),
        "CLOSED_LOAN_POSITIVE_BALANCE": ClosedLoanPositiveBalanceRule(),
    }
    # Clean explicit strategy aliases mapping alternative keys to canonical strategy keys
    STRATEGY_ALIASES: dict[str, str] = {
        "STATUS_VS_DPD_INCONSISTENCY": "STATUS_DPD_INCONSISTENCY",
        "STATUS_DPD_INCONSISTENT": "STATUS_DPD_INCONSISTENCY",
        "SERVICER_BALANCE_CONFLICT": "SERVICER_UPDATE_CONFLICT",
        "SERVICER_CONFLICT": "SERVICER_UPDATE_CONFLICT",
        "DUPLICATE_BORROWER_TRIPLE": "DUPLICATE_BORROWER_TRIPLET",
        "NEGATIVE_BALANCE": "NEGATIVE_PRINCIPAL_BALANCE",
        "STALE_LAST_UPDATED": "STALE_RECORD",
        "SUSPICIOUS_REPEATED_BORROWER": "SUSPICIOUS_BORROWER_DUPLICATION",
        "CLOSED_WITH_POSITIVE_BALANCE": "CLOSED_LOAN_POSITIVE_BALANCE",
    }
    GENERIC_RULE_HANDLER = GenericExpressionRule()

    @classmethod
    def get_strategy(cls, db_rule: ValidationRule) -> BaseValidationRule:
        """
        Dynamically looks up strategy handler by db_rule.strategy_key or db_rule.rule_code.
        Resolves aliases before falling back to GenericExpressionRule.
        """
        raw_key = str(db_rule.strategy_key or db_rule.rule_code or "").strip().upper()
        # Resolve key alias if present
        canonical_key = cls.STRATEGY_ALIASES.get(raw_key, raw_key)
        return cls.STRATEGY_MAP.get(canonical_key, cls.GENERIC_RULE_HANDLER)

    @classmethod
    @transaction.atomic
    def validate_batch(
        cls,
        batch: UploadBatch,
        include_db_history: bool = True,
    ) -> list[LoanException]:
        """
        Executes all active ValidationRules against RawLoanRecords for the given UploadBatch.
        Utilizes O(N) batch indexing via ValidationContext for high performance (100k+ records).

        Returns:
            list[LoanException]: Newly created exception instances.
        """
        raw_records = list(RawLoanRecord.objects.filter(batch=batch).order_by("row_number"))
        if not raw_records:
            return []

        # Load active DB rules
        active_rules = list(ValidationRule.objects.filter(is_active=True).order_by("rule_code"))
        if not active_rules:
            return []

        # Pre-fetch Servicer updates and Document manifests for cross-file reconciliation
        servicer_records = list(ServicerUpdateRecord.objects.filter(batch=batch))
        doc_manifest_records = list(DocumentManifestRecord.objects.filter(batch=batch))

        # Build O(N) ValidationContext pre-calculating frequency maps for O(1) lookups
        context = ValidationContext.build(
            raw_records=raw_records,
            servicer_records=servicer_records,
            doc_manifest_records=doc_manifest_records,
            include_db_history=include_db_history,
        )

        # Clear existing OPEN exceptions for this batch to allow idempotent re-running
        LoanException.objects.filter(
            batch=batch, status=LoanException.ExceptionStatus.OPEN
        ).delete()

        exceptions_to_create: list[LoanException] = []

        for record in raw_records:
            for db_rule in active_rules:
                strategy = cls.get_strategy(db_rule)
                try:
                    rule_result = strategy.validate(
                        raw_record=record,
                        db_rule=db_rule,
                        context=context,
                    )
                    if rule_result and not rule_result.is_valid:
                        exceptions_to_create.append(
                            LoanException(
                                batch=batch,
                                raw_record=record,
                                rule=db_rule,
                                rule_code=db_rule.rule_code,
                                field_name=rule_result.field_name,
                                severity=rule_result.severity,
                                description=rule_result.message,
                                status=LoanException.ExceptionStatus.OPEN,
                            )
                        )
                except Exception as exception:
                    # Defensive fallback log on rule evaluation error
                    exceptions_to_create.append(
                        LoanException(
                            batch=batch,
                            raw_record=record,
                            rule=db_rule,
                            rule_code=db_rule.rule_code,
                            field_name=db_rule.field_name or "unknown",
                            severity=db_rule.severity,
                            description=f"Rule evaluation exception: {str(exception)}",
                            status=LoanException.ExceptionStatus.OPEN,
                        )
                    )

        if exceptions_to_create:
            created_exceptions = LoanException.objects.bulk_create(
                exceptions_to_create, batch_size=500
            )
            audit_events_data = [
                {
                    "event_type": "EXCEPTION_CREATED",
                    "actor": None,
                    "actor_role": AuditEvent.ActorRole.SYSTEM,
                    "loan_id": exc.loan_id,
                    "batch_id": exc.batch_id,
                    "payload": {
                        "exception_id": str(exc.id),
                        "rule_code": exc.rule_code,
                        "field_name": exc.field_name,
                        "severity": exc.get_severity_display(),
                        "description": exc.description,
                        "status": exc.get_status_display(),
                        "raw_record_id": exc.raw_record_id,
                    },
                }
                for exc in created_exceptions
            ]
            AuditEvent.log_events_bulk(audit_events_data, batch_size=500)

        # Auto-create VerifiedLoanRecords for records in batch that passed validation cleanly with 0 exceptions
        from app.domain.verified_service import process_clean_records_for_batch

        process_clean_records_for_batch(batch)

        return exceptions_to_create
