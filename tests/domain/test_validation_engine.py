"""
Test cases for app.domain.validation_engine.

Covers the parser helpers (safe_float / safe_date), ValidationContext batch
indexing, the 15 domain strategy handlers, the GenericExpressionRule fallback,
strategy resolution, and the ValidationEngine.validate_batch coordinator with
positive, negative, edge and boundary/invalid input scenarios.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.validation_engine import (
    BalanceExceedsPrincipalRule,
    ClosedLoanPositiveBalanceRule,
    DuplicateBorrowerTripletRule,
    DuplicateLoanIdRule,
    GenericExpressionRule,
    InvalidDateFormatRule,
    InvalidStateCodeRule,
    MaturityBeforeOriginationRule,
    MissingDocumentStatusRule,
    MissingLoanIdRule,
    NegativePrincipalBalanceRule,
    OutOfRangeInterestRateRule,
    ServicerUpdateConflictRule,
    StaleRecordRule,
    StatusDpdInconsistencyRule,
    SuspiciousBorrowerDuplicationRule,
    ValidationContext,
    ValidationEngine,
    safe_date,
    safe_float,
)
from app.models import (
    DocumentManifestRecord,
    LoanException,
    RawLoanRecord,
    ServicerUpdateRecord,
    UploadBatch,
    ValidationRule,
    ValidationSeverity,
)


def _raw_record(raw_data=None, row_number=1):
    """Builds an unsaved RawLoanRecord instance (no DB write)."""
    return RawLoanRecord(row_number=row_number, raw_data=raw_data or {})


def _db_rule(
    rule_code="VAL_TEST",
    strategy_key="MISSING_LOAN_ID",
    field_name="loan_id",
    severity=ValidationSeverity.HIGH,
    parameters=None,
):
    """Builds an unsaved ValidationRule instance (no DB write)."""
    return ValidationRule(
        rule_code=rule_code,
        strategy_key=strategy_key,
        rule_name=f"Rule {rule_code}",
        field_name=field_name,
        description="test rule",
        severity=severity,
        is_active=True,
        parameters=parameters or {},
    )


# =============================================================================
# Parser helpers
# =============================================================================


class TestSafeFloat:
    def test_integer_input(self):
        assert safe_float(5) == 5.0

    def test_float_input(self):
        assert safe_float(5.5) == 5.5

    def test_decimal_input(self):
        from decimal import Decimal

        assert safe_float(Decimal("5.50")) == 5.5

    def test_none_input(self):
        assert safe_float(None) is None

    def test_plain_numeric_string(self):
        assert safe_float("1500.25") == 1500.25

    def test_currency_symbol_stripped(self):
        assert safe_float("$1,500.25") == 1500.25

    def test_commas_stripped(self):
        assert safe_float("2,500,000.00") == 2500000.0

    def test_negative_string(self):
        assert safe_float("-12.5") == -12.5

    def test_whitespace_padded_string(self):
        assert safe_float("  88.5  ") == 88.5

    def test_empty_string_returns_none(self):
        assert safe_float("") is None

    def test_whitespace_only_returns_none(self):
        assert safe_float("   ") is None

    def test_non_numeric_string_returns_none(self):
        assert safe_float("not-a-number") is None


class TestSafeDate:
    def test_none_returns_none(self):
        assert safe_date(None) is None

    def test_empty_string_returns_none(self):
        assert safe_date("") is None

    def test_whitespace_only_returns_none(self):
        assert safe_date("   ") is None

    def test_datetime_input_returns_same(self):
        dt = datetime(2025, 6, 1, 12, 0, 0)
        assert safe_date(dt) == dt

    def test_iso_8601_parse(self):
        assert safe_date("2025-06-01") == datetime(2025, 6, 1)

    def test_us_mm_dd_yyyy_parse(self):
        assert safe_date("06/01/2025") == datetime(2025, 6, 1)

    def test_dash_dd_mm_yyyy_parse(self):
        assert safe_date("01-06-2025") == datetime(2025, 6, 1)

    def test_zulu_suffix_handled(self):
        assert safe_date("2025-06-01T12:00:00Z").tzinfo is not None

    def test_with_time_parse(self):
        assert safe_date("2025-06-01 10:30:00") == datetime(2025, 6, 1, 10, 30, 0)

    def test_invalid_calendar_date_returns_none(self):
        assert safe_date("2025-13-45") is None

    def test_gibberish_returns_none(self):
        assert safe_date("not-a-date") is None


# =============================================================================
# ValidationContext.build
# =============================================================================


@pytest.mark.django_db
class TestValidationContextBuild:
    def test_empty_inputs(self):
        ctx = ValidationContext.build([])
        assert ctx.loan_id_counts == {}
        assert ctx.borrower_id_counts == {}
        assert ctx.triplet_counts == {}
        assert ctx.servicer_map == {}
        assert ctx.doc_manifest_map == {}

    def test_loan_id_frequencies(self):
        records = [
            _raw_record({"loan_id": "LG-1"}),
            _raw_record({"loan_id": "LG-1"}),
            _raw_record({"loan_id": "LG-2"}),
            _raw_record({"loan_id": "  LG-2  "}),
            _raw_record({"loan_id": None}),
        ]
        ctx = ValidationContext.build(records)
        assert ctx.loan_id_counts["LG-1"] == 2
        assert ctx.loan_id_counts["LG-2"] == 2
        assert ctx.loan_id_counts.get("None") is None

    def test_borrower_id_frequencies_respect_whitespace_trim(self):
        records = [
            _raw_record({"borrower_id": "B-1"}),
            _raw_record({"borrower_id": "B-1"}),
            _raw_record({"borrower_id": " B-1 "}),
        ]
        ctx = ValidationContext.build(records)
        assert ctx.borrower_id_counts["B-1"] == 3

    def test_triplet_only_counted_when_all_fields_present(self):
        records = [
            _raw_record(
                {
                    "borrower_id": "B-1",
                    "original_principal": "1000",
                    "origination_date": "2024-01-01",
                }
            ),
            _raw_record(
                {
                    "borrower_id": "B-1",
                    "original_principal": "1000",
                    "origination_date": "2024-01-01",
                }
            ),
            _raw_record(
                {
                    "borrower_id": "B-2",
                    "original_principal": "2000",
                    "origination_date": "2024-01-01",
                }
            ),
            _raw_record(
                {"borrower_id": "B-3", "original_principal": None, "origination_date": "2024-01-01"}
            ),
        ]
        ctx = ValidationContext.build(records)
        assert ctx.triplet_counts[("B-1", 1000.0, "2024-01-01")] == 2
        assert ctx.triplet_counts[("B-2", 2000.0, "2024-01-01")] == 1

    def test_servicer_and_doc_manifest_maps_built(self):
        batch = UploadBatch.objects.create(file_name="batch.csv")
        ServicerUpdateRecord.objects.create(
            batch=batch, loan_id="LG-1", updated_current_balance=100
        )
        DocumentManifestRecord.objects.create(
            batch=batch, loan_id="LG-2", document_verification_status="COMPLETE"
        )
        ctx = ValidationContext.build(
            [],
            [ServicerUpdateRecord.objects.get(loan_id="LG-1")],
            [DocumentManifestRecord.objects.get(loan_id="LG-2")],
        )
        assert "LG-1" in ctx.servicer_map
        assert "LG-2" in ctx.doc_manifest_map


# =============================================================================
# Strategy handlers
# =============================================================================


class TestMissingLoanIdRule:
    def _validate(self, raw_data):
        return MissingLoanIdRule().validate(_raw_record(raw_data), _db_rule())

    def test_missing_key_flagged(self):
        result = self._validate({})
        assert result is not None
        assert result.is_valid is False
        assert result.rule_code == "VAL_TEST"

    def test_empty_string_flagged(self):
        result = self._validate({"loan_id": "   "})
        assert result is not None
        assert result.is_valid is False

    def test_none_value_flagged(self):
        result = self._validate({"loan_id": None})
        assert result is not None

    def test_present_loan_id_passes(self):
        assert self._validate({"loan_id": "LG-0001"}) is None

    def test_severity_and_message_carried(self):
        result = self._validate({})
        assert result.severity == ValidationSeverity.HIGH
        assert "missing" in result.message.lower()


class TestDuplicateLoanIdRule:
    def _validate(self, raw_data, context=None):
        return DuplicateLoanIdRule().validate(_raw_record(raw_data), _db_rule(), context)

    def test_no_context_returns_none(self):
        assert self._validate({"loan_id": "LG-1"}) is None

    def test_unique_loan_id_passes(self):
        ctx = ValidationContext(loan_id_counts={"LG-1": 1})
        assert self._validate({"loan_id": "LG-1"}, ctx) is None

    def test_duplicate_loan_id_flagged(self):
        ctx = ValidationContext(loan_id_counts={"LG-1": 3})
        result = self._validate({"loan_id": "LG-1"}, ctx)
        assert result is not None
        assert result.is_valid is False
        assert "3 occurrences" in result.message

    def test_empty_loan_id_skipped(self):
        ctx = ValidationContext(loan_id_counts={"": 5})
        assert self._validate({"loan_id": ""}, ctx) is None


class TestDuplicateBorrowerTripletRule:
    def _validate(self, raw_data, context=None):
        return DuplicateBorrowerTripletRule().validate(
            _raw_record(raw_data),
            _db_rule(strategy_key="DUPLICATE_BORROWER_TRIPLET", field_name="borrower_id"),
            context,
        )

    def _ctx(self, counts):
        return ValidationContext(triplet_counts=counts)

    def test_duplicate_triplet_flagged(self):
        ctx = self._ctx({("B-1", 1000.0, "2024-01-01"): 2})
        result = self._validate(
            {"borrower_id": "B-1", "original_principal": "1000", "origination_date": "2024-01-01"},
            ctx,
        )
        assert result is not None
        assert result.is_valid is False
        assert "Duplicate borrower triplet" in result.message

    def test_unique_triplet_passes(self):
        ctx = self._ctx({("B-1", 1000.0, "2024-01-01"): 1})
        assert (
            self._validate(
                {
                    "borrower_id": "B-1",
                    "original_principal": "1000",
                    "origination_date": "2024-01-01",
                },
                ctx,
            )
            is None
        )

    def test_missing_borrower_skipped(self):
        assert (
            self._validate({"original_principal": "1000", "origination_date": "2024-01-01"}) is None
        )

    def test_missing_principal_skipped(self):
        ctx = self._ctx({("B-1", 1000.0, "2024-01-01"): 3})
        assert self._validate({"borrower_id": "B-1", "origination_date": "2024-01-01"}, ctx) is None

    def test_missing_origination_date_skipped(self):
        ctx = self._ctx({("B-1", 1000.0, "2024-01-01"): 3})
        assert self._validate({"borrower_id": "B-1", "original_principal": "1000"}, ctx) is None


class TestInvalidDateFormatRule:
    def _validate(self, raw_data, field_name="origination_date"):
        return InvalidDateFormatRule().validate(
            _raw_record(raw_data),
            _db_rule(strategy_key="INVALID_DATE_FORMAT", field_name=field_name),
        )

    def test_unparseable_date_flagged(self):
        result = self._validate({"origination_date": "NOT_A_DATE"})
        assert result is not None
        assert result.is_valid is False
        assert "unparseable date" in result.message

    def test_invalid_calendar_date_flagged(self):
        result = self._validate({"origination_date": "2024-13-45"})
        assert result is not None

    def test_valid_iso_date_passes(self):
        assert self._validate({"origination_date": "2024-01-15"}) is None

    def test_empty_date_skipped(self):
        assert self._validate({"origination_date": ""}) is None

    def test_missing_date_skipped(self):
        assert self._validate({}) is None


class TestMaturityBeforeOriginationRule:
    def _validate(self, raw_data):
        return MaturityBeforeOriginationRule().validate(
            _raw_record(raw_data), _db_rule(strategy_key="MATURITY_BEFORE_ORIGINATION")
        )

    def test_maturity_before_origination_flagged(self):
        result = self._validate({"origination_date": "2024-06-01", "maturity_date": "2023-06-01"})
        assert result is not None
        assert result.is_valid is False

    def test_maturity_equal_to_origination_flagged(self):
        # Boundary: equal dates are also invalid (<= comparison)
        result = self._validate({"origination_date": "2024-06-01", "maturity_date": "2024-06-01"})
        assert result is not None
        assert result.is_valid is False

    def test_maturity_after_origination_passes(self):
        assert (
            self._validate({"origination_date": "2024-06-01", "maturity_date": "2030-06-01"})
            is None
        )

    def test_missing_dates_skipped(self):
        assert self._validate({"origination_date": "2024-06-01"}) is None
        assert self._validate({"maturity_date": "2030-06-01"}) is None

    def test_unparseable_dates_skipped(self):
        assert self._validate({"origination_date": "junk", "maturity_date": "more junk"}) is None


class TestNegativePrincipalBalanceRule:
    def _validate(self, raw_data):
        return NegativePrincipalBalanceRule().validate(
            _raw_record(raw_data), _db_rule(strategy_key="NEGATIVE_PRINCIPAL_BALANCE")
        )

    def test_negative_original_principal_flagged(self):
        result = self._validate({"original_principal": "-500"})
        assert result is not None
        assert result.is_valid is False
        assert result.field_name == "original_principal"

    def test_negative_current_balance_flagged(self):
        result = self._validate({"current_balance": "-10.5"})
        assert result is not None
        assert result.field_name == "current_balance"

    def test_zero_values_pass(self):
        # Boundary: zero is not negative
        assert self._validate({"original_principal": "0", "current_balance": "0"}) is None

    def test_positive_values_pass(self):
        assert self._validate({"original_principal": "250000", "current_balance": "230000"}) is None

    def test_missing_values_pass(self):
        assert self._validate({}) is None


class TestBalanceExceedsPrincipalRule:
    def _validate(self, raw_data):
        return BalanceExceedsPrincipalRule().validate(
            _raw_record(raw_data), _db_rule(strategy_key="BALANCE_EXCEEDS_PRINCIPAL")
        )

    def test_balance_exceeds_principal_flagged(self):
        result = self._validate({"original_principal": "1000", "current_balance": "1500"})
        assert result is not None
        assert result.is_valid is False

    def test_equal_balance_passes(self):
        # Boundary: balance == principal is allowed (only > is flagged)
        assert self._validate({"original_principal": "1000", "current_balance": "1000"}) is None

    def test_balance_less_than_principal_passes(self):
        assert self._validate({"original_principal": "1000", "current_balance": "900"}) is None

    def test_missing_fields_skipped(self):
        assert self._validate({"current_balance": "1500"}) is None
        assert self._validate({"original_principal": "1000"}) is None


class TestOutOfRangeInterestRateRule:
    def _validate(self, raw_data, parameters=None):
        return OutOfRangeInterestRateRule().validate(
            _raw_record(raw_data),
            _db_rule(
                strategy_key="OUT_OF_RANGE_INTEREST_RATE",
                field_name="interest_rate",
                parameters=parameters,
            ),
        )

    def test_parameterized_range_flags_above_max(self):
        result = self._validate({"interest_rate": "36.5"}, {"min_rate": 0.0, "max_rate": 35.0})
        assert result is not None
        assert result.is_valid is False

    def test_rate_at_lower_bound_valid(self):
        # Boundary: exactly at min is valid (outside requires < min)
        assert self._validate({"interest_rate": "0.0"}, {"min_rate": 0.0, "max_rate": 35.0}) is None

    def test_rate_at_upper_bound_valid(self):
        # Boundary: exactly at max is valid (outside requires > max)
        assert (
            self._validate({"interest_rate": "35.0"}, {"min_rate": 0.0, "max_rate": 35.0}) is None
        )

    def test_rate_just_below_min_flagged(self):
        result = self._validate({"interest_rate": "-0.01"}, {"min_rate": 0.0, "max_rate": 35.0})
        assert result is not None

    def test_rate_just_above_max_flagged(self):
        result = self._validate({"interest_rate": "35.01"}, {"min_rate": 0.0, "max_rate": 35.0})
        assert result is not None

    def test_fractional_decimal_converted_to_percentage(self):
        # 0.065 interpreted as fraction -> 6.5%
        assert (
            self._validate({"interest_rate": "0.065"}, {"min_rate": 0.0, "max_rate": 35.0}) is None
        )

    def test_missing_rate_skipped(self):
        assert self._validate({}) is None

    def test_default_range_used_when_no_parameters(self):
        result = self._validate({"interest_rate": "50"})
        assert result is not None


class TestStatusDpdInconsistencyRule:
    def _validate(self, raw_data, parameters=None):
        return StatusDpdInconsistencyRule().validate(
            _raw_record(raw_data),
            _db_rule(
                strategy_key="STATUS_DPD_INCONSISTENCY",
                field_name="payment_status",
                parameters=parameters,
            ),
        )

    def test_current_with_high_dpd_flagged(self):
        result = self._validate({"payment_status": "Current", "days_past_due": "60"})
        assert result is not None
        assert result.is_valid is False

    def test_current_with_dpd_at_threshold_passes(self):
        # Boundary: dpd == max_current_dpd is allowed (only > is flagged)
        assert (
            self._validate(
                {"payment_status": "CURRENT", "days_past_due": "30"}, {"max_current_dpd": 30}
            )
            is None
        )

    def test_current_with_dpd_over_threshold_flagged(self):
        assert (
            self._validate(
                {"payment_status": "CURRENT", "days_past_due": "31"}, {"max_current_dpd": 30}
            )
            is not None
        )

    def test_non_current_status_with_high_dpd_passes(self):
        assert self._validate({"payment_status": "LATE_90", "days_past_due": "120"}) is None

    def test_lowercase_status_uppercased(self):
        assert self._validate({"payment_status": "performing", "days_past_due": "45"}) is not None

    def test_missing_fields_skipped(self):
        assert self._validate({"payment_status": "CURRENT"}) is None
        assert self._validate({"days_past_due": "60"}) is None


class TestMissingDocumentStatusRule:
    def _validate(self, raw_data, context=None):
        return MissingDocumentStatusRule().validate(
            _raw_record(raw_data),
            _db_rule(strategy_key="MISSING_DOCUMENT_STATUS", field_name="document_status"),
            context,
        )

    def test_missing_raw_document_status_flagged(self):
        result = self._validate({})
        assert result is not None
        assert result.is_valid is False

    def test_raw_document_status_missing_marker_flagged(self):
        assert self._validate({"document_status": "MISSING"}) is not None
        assert self._validate({"document_status": "PENDING"}) is not None
        assert self._validate({"document_status": "UNVERIFIED"}) is not None

    def test_raw_document_status_complete_passes(self):
        assert self._validate({"document_status": "COMPLETE"}) is None

    def test_manifest_missing_status_flagged(self):
        doc = DocumentManifestRecord(document_verification_status="MISSING")
        ctx = ValidationContext(doc_manifest_map={"LG-1": doc})
        result = self._validate({"loan_id": "LG-1"}, ctx)
        assert result is not None
        assert result.is_valid is False

    def test_manifest_complete_all_docs_present_passes(self):
        doc = DocumentManifestRecord(
            document_verification_status="COMPLETE",
            promissory_note_present=True,
            id_proof_present=True,
            income_verification_present=True,
        )
        ctx = ValidationContext(doc_manifest_map={"LG-1": doc})
        assert self._validate({"loan_id": "LG-1"}, ctx) is None

    def test_manifest_complete_but_missing_doc_flagged(self):
        doc = DocumentManifestRecord(
            document_verification_status="COMPLETE",
            promissory_note_present=False,
            id_proof_present=True,
            income_verification_present=True,
        )
        ctx = ValidationContext(doc_manifest_map={"LG-1": doc})
        assert self._validate({"loan_id": "LG-1"}, ctx) is not None


class TestServicerUpdateConflictRule:
    def _validate(self, raw_data, context=None, parameters=None):
        return ServicerUpdateConflictRule().validate(
            _raw_record(raw_data),
            _db_rule(
                strategy_key="SERVICER_UPDATE_CONFLICT",
                field_name="current_balance",
                parameters=parameters,
            ),
            context,
        )

    def _ctx(self, balance):
        servicer = ServicerUpdateRecord(loan_id="LG-1", updated_current_balance=balance)
        return ValidationContext(servicer_map={"LG-1": servicer})

    def test_conflict_above_delta_flagged(self):
        result = self._validate(
            {"loan_id": "LG-1", "current_balance": "2500000.00"}, self._ctx(100.0)
        )
        assert result is not None
        assert result.is_valid is False

    def test_delta_equal_to_max_passes(self):
        # Boundary: delta == max_delta_dollars is allowed (only > is flagged)
        assert (
            self._validate(
                {"loan_id": "LG-1", "current_balance": "101.00"},
                self._ctx(100.0),
                {"max_delta_dollars": 1.0},
            )
            is None
        )

    def test_small_delta_passes(self):
        assert (
            self._validate({"loan_id": "LG-1", "current_balance": "100.50"}, self._ctx(100.0))
            is None
        )

    def test_no_servicer_record_skipped(self):
        assert self._validate({"loan_id": "LG-9", "current_balance": "500"}) is None

    def test_servicer_null_balance_skipped(self):
        ctx = ValidationContext(
            servicer_map={
                "LG-1": ServicerUpdateRecord(loan_id="LG-1", updated_current_balance=None)
            }
        )
        assert self._validate({"loan_id": "LG-1", "current_balance": "500"}, ctx) is None


class TestStaleRecordRule:
    def _validate(self, raw_data, parameters=None):
        return StaleRecordRule().validate(
            _raw_record(raw_data),
            _db_rule(
                strategy_key="STALE_RECORD", field_name="last_updated_at", parameters=parameters
            ),
        )

    def test_stale_record_flagged(self):
        old = (datetime.now(UTC) - timedelta(days=200)).strftime("%Y-%m-%d")
        result = self._validate({"last_updated_at": old})
        assert result is not None
        assert result.is_valid is False

    def test_recent_record_passes(self):
        recent = (datetime.now(UTC) - timedelta(days=10)).strftime("%Y-%m-%d")
        assert self._validate({"last_updated_at": recent}) is None

    def test_record_at_threshold_passes(self):
        # Boundary: age == max_stale_days is allowed (only > is flagged)
        on_threshold = (datetime.now(UTC) - timedelta(days=180)).strftime("%Y-%m-%d")
        assert self._validate({"last_updated_at": on_threshold}, {"max_stale_days": 180}) is None

    def test_record_over_threshold_flagged(self):
        old = (datetime.now(UTC) - timedelta(days=181)).strftime("%Y-%m-%d")
        assert self._validate({"last_updated_at": old}, {"max_stale_days": 180}) is not None

    def test_missing_date_skipped(self):
        assert self._validate({}) is None

    def test_unparseable_date_skipped(self):
        assert self._validate({"last_updated_at": "junk"}) is None


class TestInvalidStateCodeRule:
    def _validate(self, raw_data):
        return InvalidStateCodeRule().validate(
            _raw_record(raw_data), _db_rule(strategy_key="INVALID_STATE_CODE")
        )

    def test_invalid_state_flagged(self):
        result = self._validate({"borrower_state": "ZZ"})
        assert result is not None
        assert result.is_valid is False

    def test_numeric_state_flagged(self):
        assert self._validate({"state": "99"}) is not None

    def test_valid_state_passes(self):
        assert self._validate({"borrower_state": "TX"}) is None

    def test_state_from_fallback_field(self):
        assert self._validate({"state": "CA"}) is None

    def test_lowercase_state_uppercased(self):
        assert self._validate({"borrower_state": "ny"}) is None

    def test_empty_state_skipped(self):
        assert self._validate({}) is None


class TestSuspiciousBorrowerDuplicationRule:
    def _validate(self, raw_data, context=None, parameters=None):
        return SuspiciousBorrowerDuplicationRule().validate(
            _raw_record(raw_data),
            _db_rule(
                strategy_key="SUSPICIOUS_BORROWER_DUPLICATION",
                field_name="borrower_id",
                parameters=parameters,
            ),
            context,
        )

    def test_count_over_max_flagged(self):
        ctx = ValidationContext(borrower_id_counts={"B-1": 6})
        result = self._validate({"borrower_id": "B-1"}, ctx)
        assert result is not None
        assert result.is_valid is False

    def test_count_at_max_passes(self):
        # Boundary: count == max_loans_per_window is allowed (only > is flagged)
        ctx = ValidationContext(borrower_id_counts={"B-1": 5})
        assert self._validate({"borrower_id": "B-1"}, ctx) is None

    def test_count_under_max_passes(self):
        ctx = ValidationContext(borrower_id_counts={"B-1": 3})
        assert self._validate({"borrower_id": "B-1"}, ctx) is None

    def test_custom_max_parameter(self):
        ctx = ValidationContext(borrower_id_counts={"B-1": 3})
        result = self._validate({"borrower_id": "B-1"}, ctx, {"max_loans_per_window": 2})
        assert result is not None

    def test_missing_borrower_skipped(self):
        assert self._validate({}) is None

    def test_no_context_skipped(self):
        assert self._validate({"borrower_id": "B-1"}) is None


class TestClosedLoanPositiveBalanceRule:
    def _validate(self, raw_data):
        return ClosedLoanPositiveBalanceRule().validate(
            _raw_record(raw_data), _db_rule(strategy_key="CLOSED_LOAN_POSITIVE_BALANCE")
        )

    def test_closed_positive_balance_flagged(self):
        result = self._validate({"payment_status": "CLOSED", "current_balance": "500"})
        assert result is not None
        assert result.is_valid is False

    def test_closed_zero_balance_passes(self):
        assert self._validate({"payment_status": "CLOSED", "current_balance": "0"}) is None

    def test_paid_off_with_balance_flagged(self):
        assert self._validate({"payment_status": "PAID_OFF", "current_balance": "10"}) is not None

    def test_active_positive_balance_passes(self):
        assert self._validate({"payment_status": "ACTIVE", "current_balance": "500"}) is None

    def test_missing_status_skipped(self):
        assert self._validate({"current_balance": "500"}) is None


class TestGenericExpressionRule:
    def _validate(self, raw_data, field_name, parameters):
        return GenericExpressionRule().validate(
            _raw_record(raw_data),
            _db_rule(
                strategy_key="GENERIC_EXPRESSION", field_name=field_name, parameters=parameters
            ),
        )

    def test_missing_field_name_skipped(self):
        rule = _db_rule(strategy_key="GENERIC_EXPRESSION", field_name=None)
        assert GenericExpressionRule().validate(_raw_record({"x": 1}), rule) is None

    def test_is_null_empty_value_flagged(self):
        result = self._validate({"name": ""}, "name", {"operator": "IS_NULL"})
        assert result is not None
        assert result.is_valid is False

    def test_is_null_missing_value_flagged(self):
        assert self._validate({}, "name", {"operator": "IS_NULL"}) is not None

    def test_not_null_present_value_flagged(self):
        assert self._validate({"name": "Bob"}, "name", {"operator": "NOT_NULL"}) is not None

    def test_not_null_empty_value_passes(self):
        assert self._validate({"name": ""}, "name", {"operator": "NOT_NULL"}) is None

    def test_gt_operator(self):
        assert (
            self._validate({"score": "11"}, "score", {"operator": ">", "target_value": 10})
            is not None
        )
        assert (
            self._validate({"score": "10"}, "score", {"operator": ">", "target_value": 10}) is None
        )

    def test_ge_operator_boundary(self):
        assert (
            self._validate({"score": "10"}, "score", {"operator": ">=", "target_value": 10})
            is not None
        )
        assert (
            self._validate({"score": "9"}, "score", {"operator": ">=", "target_value": 10}) is None
        )

    def test_lt_operator(self):
        assert (
            self._validate({"score": "9"}, "score", {"operator": "<", "target_value": 10})
            is not None
        )
        assert (
            self._validate({"score": "10"}, "score", {"operator": "<", "target_value": 10}) is None
        )

    def test_le_operator_boundary(self):
        assert (
            self._validate({"score": "10"}, "score", {"operator": "<=", "target_value": 10})
            is not None
        )
        assert (
            self._validate({"score": "11"}, "score", {"operator": "<=", "target_value": 10}) is None
        )

    def test_eq_operator_case_insensitive(self):
        assert (
            self._validate(
                {"status": "closed"}, "status", {"operator": "==", "target_value": "CLOSED"}
            )
            is not None
        )
        assert (
            self._validate(
                {"status": "open"}, "status", {"operator": "==", "target_value": "CLOSED"}
            )
            is None
        )

    def test_neq_operator(self):
        assert (
            self._validate(
                {"status": "open"}, "status", {"operator": "!=", "target_value": "CLOSED"}
            )
            is not None
        )
        assert (
            self._validate(
                {"status": "closed"}, "status", {"operator": "!=", "target_value": "CLOSED"}
            )
            is None
        )

    def test_in_operator(self):
        params = {"operator": "IN", "target_value": ["RED", "GREEN"]}
        assert self._validate({"color": "green"}, "color", params) is not None
        assert self._validate({"color": "blue"}, "color", params) is None

    def test_not_in_operator(self):
        params = {"operator": "NOT_IN", "target_value": ["RED", "GREEN"]}
        assert self._validate({"color": "blue"}, "color", params) is not None
        assert self._validate({"color": "green"}, "color", params) is None

    def test_missing_operator_defaults_to_is_null(self):
        # When the operator key is absent the default IS_NULL semantics apply.
        assert self._validate({"name": ""}, "name", {}) is not None
        assert self._validate({"name": "Bob"}, "name", {}) is None

    def test_non_numeric_comparison_returns_none(self):
        assert (
            self._validate({"score": "abc"}, "score", {"operator": ">", "target_value": 10}) is None
        )


# =============================================================================
# ValidationEngine coordinator
# =============================================================================


@pytest.mark.django_db
class TestValidationEngineStrategyResolution:
    def test_direct_strategy_key(self):
        rule = _db_rule(strategy_key="MISSING_LOAN_ID")
        assert isinstance(ValidationEngine.get_strategy(rule), MissingLoanIdRule)

    def test_rule_code_fallback(self):
        # rule_code is used as the strategy lookup key when strategy_key is empty;
        # resolution only succeeds if the code aliases a known strategy key.
        rule = _db_rule(strategy_key="", rule_code="MISSING_LOAN_ID")
        assert isinstance(ValidationEngine.get_strategy(rule), MissingLoanIdRule)

    def test_alias_resolved(self):
        assert isinstance(
            ValidationEngine.get_strategy(_db_rule(strategy_key="STATUS_VS_DPD_INCONSISTENCY")),
            StatusDpdInconsistencyRule,
        )
        assert isinstance(
            ValidationEngine.get_strategy(_db_rule(strategy_key="SERVICER_BALANCE_CONFLICT")),
            ServicerUpdateConflictRule,
        )

    def test_unknown_key_uses_generic_rule(self):
        assert isinstance(
            ValidationEngine.get_strategy(_db_rule(strategy_key="NO_SUCH_RULE")),
            GenericExpressionRule,
        )

    def test_empty_key_uses_generic_rule(self):
        assert isinstance(
            ValidationEngine.get_strategy(_db_rule(strategy_key="")), GenericExpressionRule
        )

    def test_strategy_case_insensitive(self):
        rule = _db_rule(strategy_key="missing_loan_id")
        assert isinstance(ValidationEngine.get_strategy(rule), MissingLoanIdRule)


@pytest.mark.django_db
class TestValidationEngineValidateBatch:
    def setup_method(self):
        self.user = None
        self.batch = UploadBatch.objects.create(
            file_name="loan_tape.csv",
            source_type=UploadBatch.SourceType.LOAN_TAPE,
            status=UploadBatch.BatchStatus.INGESTED,
            total_records=2,
            successful_records=2,
        )

    def _rule(self, **kwargs):
        defaults = dict(
            rule_code="VAL_001",
            strategy_key="MISSING_LOAN_ID",
            rule_name="Missing Loan ID",
            field_name="loan_id",
            description="Flags missing loan ids.",
            severity=ValidationSeverity.CRITICAL,
            is_active=True,
        )
        defaults.update(kwargs)
        return ValidationRule.objects.create(**defaults)

    def _record(self, raw_data, row_number=1):
        return RawLoanRecord.objects.create(
            batch=self.batch, row_number=row_number, raw_data=raw_data
        )

    def test_empty_batch_returns_empty(self):
        assert ValidationEngine.validate_batch(self.batch) == []
        assert LoanException.objects.count() == 0

    def test_no_active_rules_returns_empty(self):
        self._record({"loan_id": ""})
        result = ValidationEngine.validate_batch(self.batch)
        assert result == []
        assert LoanException.objects.count() == 0

    def test_inactive_rule_not_executed(self):
        self._rule(is_active=False)
        self._record({})
        assert ValidationEngine.validate_batch(self.batch) == []

    def test_flagging_record_creates_exception(self):
        self._rule()
        self._record({})
        created = ValidationEngine.validate_batch(self.batch)
        assert len(created) == 1
        exc = LoanException.objects.get()
        assert exc.batch == self.batch
        assert exc.status == LoanException.ExceptionStatus.OPEN
        assert exc.rule_code == "VAL_001"
        assert exc.field_name == "loan_id"
        assert exc.severity == ValidationSeverity.CRITICAL
        assert exc.raw_record is not None

    def test_clean_record_creates_no_exception(self):
        self._rule()
        self._record({"loan_id": "LG-0001"})
        assert ValidationEngine.validate_batch(self.batch) == []

    def test_multiple_rules_multiple_exceptions(self):
        rule_a = self._rule(
            rule_code="VAL_001",
            strategy_key="MISSING_LOAN_ID",
            severity=ValidationSeverity.CRITICAL,
        )
        rule_b = self._rule(
            rule_code="VAL_006",
            strategy_key="NEGATIVE_PRINCIPAL_BALANCE",
            field_name="original_principal",
            severity=ValidationSeverity.CRITICAL,
        )
        self._record({"original_principal": "-500"})
        created = ValidationEngine.validate_batch(self.batch)
        assert len(created) == 2
        codes = {exc.rule_code for exc in created}
        assert codes == {"VAL_001", "VAL_006"}
        assert {exc.rule_id for exc in created} == {rule_a.id, rule_b.id}

    def test_re_run_is_idempotent_for_open_exceptions(self):
        self._rule()
        self._record({})
        first_run = ValidationEngine.validate_batch(self.batch)
        assert len(first_run) == 1
        second_run = ValidationEngine.validate_batch(self.batch)
        assert len(second_run) == 1
        assert (
            LoanException.objects.filter(
                batch=self.batch, status=LoanException.ExceptionStatus.OPEN
            ).count()
            == 1
        )
        assert LoanException.objects.count() == 1

    def test_re_run_preserves_resolved_exceptions(self):
        rule = self._rule()
        record = self._record({})
        LoanException.objects.create(
            batch=self.batch,
            raw_record=record,
            rule=rule,
            rule_code="VAL_001",
            field_name="loan_id",
            severity=ValidationSeverity.CRITICAL,
            description="already handled",
            status=LoanException.ExceptionStatus.RESOLVED_ACCEPTED,
        )
        ValidationEngine.validate_batch(self.batch)
        # Resolved exception kept, plus one freshly created OPEN exception.
        assert LoanException.objects.count() == 2
        assert (
            LoanException.objects.filter(
                status=LoanException.ExceptionStatus.RESOLVED_ACCEPTED
            ).count()
            == 1
        )

    def test_rule_evaluation_exception_captured_as_exception(self, monkeypatch):
        class ExplodingRule:
            strategy_key = "EXPLODING"

            def validate(self, **kwargs):
                raise RuntimeError("boom")

        monkeypatch.setitem(ValidationEngine.STRATEGY_MAP, "EXPLODING", ExplodingRule())
        self._rule(rule_code="VAL_099", strategy_key="EXPLODING")
        self._record({})
        created = ValidationEngine.validate_batch(self.batch)
        assert len(created) == 1
        assert "Rule evaluation exception" in created[0].description
        assert "boom" in created[0].description
        assert created[0].status == LoanException.ExceptionStatus.OPEN

    def test_active_rules_resolved_for_multiple_records(self):
        self._rule()
        self._record({}, row_number=1)
        self._record({"loan_id": "LG-1"}, row_number=2)
        created = ValidationEngine.validate_batch(self.batch)
        assert len(created) == 1
