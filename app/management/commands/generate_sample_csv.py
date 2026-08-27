"""
Django Management Command: generate_sample_csv

Generates realistic, randomized sample CSV datasets for LoanGuard AI:
1. files/csv/loan_tape.csv
2. files/csv/servicer_update.csv
3. files/csv/document_manifest.csv

Refactored following Clean Code Principles:
- Single Responsibility Principle (SRP)
- Stepdown Rule (Top-to-bottom reading hierarchy)
- Domain-Expressive Contextual Naming
"""

import csv
import os
import random
from datetime import datetime, timedelta
from typing import Any

from django.core.management.base import BaseCommand

# =============================================================================
# Domain Constants & Lookups
# =============================================================================

VALID_US_STATES = ["CA", "NY", "TX", "FL", "IL", "PA", "OH", "GA", "NC", "WA"]
INVALID_STATE_CODES = ["ZZ", "99", "XX", "XX1"]
LOAN_TYPES = ["PERSONAL", "AUTO", "MORTGAGE", "STUDENT", "COMMERCIAL"]
LOAN_PURPOSES = [
    "DEBT_CONSOLIDATION",
    "AUTO_PURCHASE",
    "HOME_PURCHASE",
    "EDUCATION",
    "BUSINESS_EXPANSION",
]
CREDIT_GRADES = ["A", "B", "C", "D", "E", "F"]
EMPLOYMENT_LENGTHS = ["< 1 year", "1 year", "2 years", "5 years", "10+ years"]
INCOME_BANDS = ["<$25k", "$25k-$50k", "$50k-$75k", "$75k-$100k", "$100k+"]
PAYMENT_STATUSES = ["CURRENT", "CLOSED", "LATE_30", "LATE_60", "LATE_90", "DEFAULT"]
SERVICER_NAMES = ["ApexServicing", "BetaServicing", "GammaServicing", "DeltaServicing"]
SOURCE_SYSTEMS = ["LOS_ALPHA", "SERVICER_CSV", "ORIGINATION_API", "MANUAL_EXCEL"]
INVALID_DATE_FORMATS = ["15/01/2023", "2024.05.10", "INVALID_DATE", "2024-13-45", "05-10-2024"]

INTENTIONAL_ISSUES = [
    "MISSING_LOAN_ID",
    "DUPLICATE_LOAN_ID",
    "DUPLICATE_BORROWER_TRIPLE",
    "INVALID_DATE_FORMAT",
    "MATURITY_BEFORE_ORIGINATION",
    "NEGATIVE_PRINCIPAL_BALANCE",
    "BALANCE_EXCEEDS_PRINCIPAL",
    "OUT_OF_RANGE_INTEREST_RATE",
    "STATUS_DPD_INCONSISTENT",
    "MISSING_DOCUMENT_STATUS",
    "SERVICER_CONFLICT",
    "STALE_LAST_UPDATED",
    "INVALID_STATE_CODE",
    "SUSPICIOUS_REPEATED_BORROWER",
    "CLOSED_WITH_POSITIVE_BALANCE",
]


class Command(BaseCommand):
    """
    Management command orchestrator for sample CSV dataset generation.
    Enforces Single Responsibility Principle (SRP) for CLI interaction and File I/O.
    """

    help = "Generates sample CSV datasets (loan_tape, servicer_update, document_manifest) following SRP & Clean Code principles."

    # -------------------------------------------------------------------------
    # 1. Command Entry Point (Stepdown Level 1)
    # -------------------------------------------------------------------------

    def add_arguments(self, parser):
        parser.add_argument(
            "--rows",
            type=int,
            default=None,
            help="Total rows for loan_tape.csv (default: random between 1000 and 5000).",
        )
        parser.add_argument(
            "--output-dir",
            type=str,
            default="files/csv",
            help="Target output directory (default: files/csv).",
        )

    def handle(self, *args, **options):
        output_directory = options["output_dir"]
        total_rows = options["rows"] or random.randint(1000, 5000)

        self._ensure_output_directory_exists(output_directory)

        self.stdout.write(
            self.style.NOTICE(
                f"Generating {total_rows:,} sample loan records into '{output_directory}'..."
            )
        )

        dataset = SampleDatasetGenerator(total_rows).generate_all()

        self._write_dataset_files(dataset, output_directory)
        self._print_completion_summary(dataset, output_directory)

    # -------------------------------------------------------------------------
    # 2. File I/O & Output Helpers (Stepdown Level 2)
    # -------------------------------------------------------------------------

    def _ensure_output_directory_exists(self, directory_path: str) -> None:
        os.makedirs(directory_path, exist_ok=True)

    def _write_dataset_files(
        self, dataset: dict[str, list[dict[str, Any]]], output_dir: str
    ) -> None:
        self._write_csv_file(os.path.join(output_dir, "loan_tape.csv"), dataset["loan_tape"])
        self._write_csv_file(
            os.path.join(output_dir, "servicer_update.csv"), dataset["servicer_update"]
        )
        self._write_csv_file(
            os.path.join(output_dir, "document_manifest.csv"), dataset["document_manifest"]
        )

    def _write_csv_file(self, file_path: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        fieldnames = list(rows[0].keys())
        with open(file_path, mode="w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def _print_completion_summary(
        self, dataset: dict[str, list[dict[str, Any]]], output_dir: str
    ) -> None:
        self.stdout.write(
            self.style.SUCCESS(f"\nSuccessfully generated sample CSV files in '{output_dir}':")
        )
        self.stdout.write(f"  • loan_tape.csv: {len(dataset['loan_tape']):,} rows")
        self.stdout.write(f"  • servicer_update.csv: {len(dataset['servicer_update']):,} rows")
        self.stdout.write(f"  • document_manifest.csv: {len(dataset['document_manifest']):,} rows")
        self.stdout.write(
            self.style.SUCCESS(
                "  • Included ALL 15 Intentional Data Issues + Extra unmapped columns!"
            )
        )


class SampleDatasetGenerator:
    """
    Domain service responsible for constructing synchronized multi-file loan datasets.
    Follows Stepdown Rule for record building and anomaly mutations.
    """

    def __init__(self, target_row_count: int):
        self.target_row_count = target_row_count
        self.run_timestamp_tag = datetime.now().strftime("%m%d%H%M")
        self.base_sequence_offset = self._compute_base_sequence_offset()
        self.created_loan_ids: list[str] = []
        self.created_borrower_triples: list[tuple[str, float, str]] = []
        self.suspicious_borrower_ids = [f"BW-REPEAT-{k}" for k in range(1, 5)]
        self.base_date = datetime(2023, 1, 1)
        self.today_date = datetime.now()

    # -------------------------------------------------------------------------
    # 1. Dataset Orchestrator (Stepdown Level 1)
    # -------------------------------------------------------------------------

    def generate_all(self) -> dict[str, list[dict[str, Any]]]:
        loan_tape_rows: list[dict[str, Any]] = []
        servicer_update_rows: list[dict[str, Any]] = []
        document_manifest_rows: list[dict[str, Any]] = []

        issue_assignments = self._assign_intentional_issues()

        for row_index in range(1, self.target_row_count + 1):
            assigned_issue = issue_assignments.get(row_index)
            tape_row, servicer_row, doc_row = self._build_record_triplet(row_index, assigned_issue)

            loan_tape_rows.append(tape_row)

            if servicer_row:
                servicer_update_rows.append(servicer_row)
                if random.random() < 0.02 and tape_row["loan_id"]:
                    servicer_update_rows.append(self._build_duplicate_servicer_row(servicer_row))

            if doc_row:
                document_manifest_rows.append(doc_row)
                if random.random() < 0.02 and tape_row["loan_id"]:
                    document_manifest_rows.append(self._build_duplicate_document_row(doc_row))

        return {
            "loan_tape": loan_tape_rows,
            "servicer_update": servicer_update_rows,
            "document_manifest": document_manifest_rows,
        }

    # -------------------------------------------------------------------------
    # 2. Record Triplet Builder (Stepdown Level 2)
    # -------------------------------------------------------------------------

    def _build_record_triplet(
        self, row_index: int, assigned_issue: str | None
    ) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
        # Generate default clean domain attributes
        seq_num = self.base_sequence_offset + row_index
        loan_id = f"LN-{self.run_timestamp_tag}-{seq_num}"
        borrower_id = f"BW-{self.run_timestamp_tag}-{seq_num}"

        orig_date_dt = self.base_date + timedelta(days=random.randint(0, 365))
        term_months = random.choice([24, 36, 48, 60, 120, 360])
        mat_date_dt = orig_date_dt + timedelta(days=term_months * 30)

        orig_date_str = orig_date_dt.strftime("%Y-%m-%d")
        mat_date_str = mat_date_dt.strftime("%Y-%m-%d")

        orig_principal = round(random.uniform(10000.0, 300000.0), 2)
        curr_balance = round(orig_principal * random.uniform(0.15, 0.85), 2)
        interest_rate = round(random.uniform(4.5, 18.5), 2)
        borrower_state = random.choice(VALID_US_STATES)
        payment_status = random.choice(PAYMENT_STATUSES)
        days_past_due = self._compute_consistent_days_past_due(payment_status)

        servicer_name = random.choice(SERVICER_NAMES)
        last_pmt_date_str = (orig_date_dt + timedelta(days=60)).strftime("%Y-%m-%d")
        last_updated_str = self.today_date.strftime("%Y-%m-%d")
        doc_status = "VERIFIED" if payment_status == "CURRENT" else "PENDING"
        source_sys = random.choice(SOURCE_SYSTEMS)

        # Generate unmapped extra column payloads
        extra_columns = self._generate_extra_unmapped_columns(row_index)

        # Apply intentional issue mutations if assigned
        mutation_context = self._apply_issue_mutation(
            assigned_issue=assigned_issue,
            loan_id=loan_id,
            borrower_id=borrower_id,
            orig_date_str=orig_date_str,
            mat_date_str=mat_date_str,
            orig_principal=orig_principal,
            curr_balance=curr_balance,
            interest_rate=interest_rate,
            borrower_state=borrower_state,
            payment_status=payment_status,
            days_past_due=days_past_due,
            doc_status=doc_status,
            last_updated_str=last_updated_str,
        )

        # Update local variables after mutation
        loan_id = mutation_context["loan_id"]
        borrower_id = mutation_context["borrower_id"]
        orig_date_str = mutation_context["orig_date_str"]
        mat_date_str = mutation_context["mat_date_str"]
        orig_principal = mutation_context["orig_principal"]
        curr_balance = mutation_context["curr_balance"]
        interest_rate = mutation_context["interest_rate"]
        borrower_state = mutation_context["borrower_state"]
        payment_status = mutation_context["payment_status"]
        days_past_due = mutation_context["days_past_due"]
        doc_status = mutation_context["doc_status"]
        last_updated_str = mutation_context["last_updated_str"]

        # Track valid IDs for duplicate generation
        if loan_id:
            self.created_loan_ids.append(loan_id)
        if borrower_id and orig_principal and orig_date_str:
            self.created_borrower_triples.append((borrower_id, orig_principal, orig_date_str))

        # 1. Build Primary Loan Tape Row
        tape_row = {
            "loan_id": loan_id,
            "borrower_id": borrower_id,
            "loan_type": random.choice(LOAN_TYPES),
            "origination_date": orig_date_str,
            "maturity_date": mat_date_str,
            "original_principal": orig_principal,
            "current_balance": curr_balance,
            "interest_rate": interest_rate,
            "term_months": term_months,
            "borrower_state": borrower_state,
            "loan_purpose": random.choice(LOAN_PURPOSES),
            "credit_grade": random.choice(CREDIT_GRADES),
            "employment_length": random.choice(EMPLOYMENT_LENGTHS),
            "income_band": random.choice(INCOME_BANDS),
            "payment_status": payment_status,
            "days_past_due": days_past_due,
            "servicer_name": servicer_name,
            "last_payment_date": last_pmt_date_str,
            "last_updated_at": last_updated_str,
            "document_status": doc_status,
            "source_system": source_sys,
            "custom_risk_score": extra_columns["custom_risk_score"],
            "unmapped_vendor_data": extra_columns["unmapped_vendor_data"],
        }

        # 2. Build Servicer Update Row
        servicer_row = None
        if loan_id or random.random() < 0.05:
            upd_balance = mutation_context["servicer_balance_override"] or curr_balance
            upd_status = mutation_context["servicer_status_override"] or payment_status
            upd_date = mutation_context["servicer_date_override"] or last_pmt_date_str

            servicer_row = {
                "loan_id": loan_id or random.choice(["", "LN-ORPHAN-999"]),
                "servicer_id": f"SERV-0{random.randint(1, 3)}",
                "updated_current_balance": upd_balance,
                "updated_payment_status": upd_status,
                "updated_days_past_due": days_past_due,
                "last_payment_date": upd_date,
                "servicer_as_of_date": self.today_date.strftime("%Y-%m-%d"),
                "unexpected_audit_flag": extra_columns["unexpected_audit_flag"],
                "servicer_raw_notes": extra_columns["servicer_raw_notes"],
            }

        # 3. Build Document Manifest Row
        doc_row = None
        if loan_id or random.random() < 0.05:
            if (
                assigned_issue == "MISSING_DOCUMENT_STATUS"
                or mutation_context["doc_status_override"] == "MISSING"
            ):
                note_flag, id_flag, inc_flag = "FALSE", "FALSE", "FALSE"
            else:
                note_flag = (
                    "TRUE" if doc_status == "VERIFIED" else random.choice(["TRUE", "FALSE", ""])
                )
                id_flag = (
                    "TRUE" if doc_status == "VERIFIED" else random.choice(["TRUE", "FALSE", ""])
                )
                inc_flag = (
                    "TRUE" if doc_status == "VERIFIED" else random.choice(["TRUE", "FALSE", ""])
                )

            doc_upload_date = mutation_context["doc_date_override"] or orig_date_str

            doc_row = {
                "loan_id": loan_id or random.choice(["", "LN-ORPHAN-888"]),
                "promissory_note_present": note_flag,
                "id_proof_present": id_flag,
                "income_verification_present": inc_flag,
                "document_verification_status": doc_status or "MISSING",
                "uploaded_at": doc_upload_date,
                "legacy_servicer_code": extra_columns["legacy_servicer_code"],
                "ocr_confidence_score": extra_columns["ocr_confidence_score"],
            }

        return tape_row, servicer_row, doc_row

    # -------------------------------------------------------------------------
    # 3. Anomaly Mutation Engine (Stepdown Level 3)
    # -------------------------------------------------------------------------

    def _apply_issue_mutation(self, assigned_issue: str | None, **ctx) -> dict[str, Any]:
        result = dict(ctx)
        result["servicer_balance_override"] = None
        result["servicer_status_override"] = None
        result["servicer_date_override"] = None
        result["doc_status_override"] = None
        result["doc_date_override"] = None

        if not assigned_issue:
            return result

        if assigned_issue == "MISSING_LOAN_ID":
            result["loan_id"] = ""

        elif assigned_issue == "DUPLICATE_LOAN_ID" and self.created_loan_ids:
            result["loan_id"] = random.choice(self.created_loan_ids)

        elif assigned_issue == "DUPLICATE_BORROWER_TRIPLE" and self.created_borrower_triples:
            prev_b_id, prev_principal, prev_orig_date = random.choice(self.created_borrower_triples)
            result["borrower_id"] = prev_b_id
            result["orig_principal"] = prev_principal
            result["orig_date_str"] = prev_orig_date

        elif assigned_issue == "INVALID_DATE_FORMAT":
            invalid_d = random.choice(INVALID_DATE_FORMATS)
            result["orig_date_str"] = invalid_d
            result["servicer_date_override"] = invalid_d
            result["doc_date_override"] = invalid_d

        elif assigned_issue == "MATURITY_BEFORE_ORIGINATION":
            result["orig_date_str"] = "2024-05-10"
            result["mat_date_str"] = "2022-05-10"

        elif assigned_issue == "NEGATIVE_PRINCIPAL_BALANCE":
            result["orig_principal"] = -1 * abs(result["orig_principal"])
            result["servicer_balance_override"] = -1 * abs(result["curr_balance"])

        elif assigned_issue == "BALANCE_EXCEEDS_PRINCIPAL":
            result["orig_principal"] = 15000.00
            result["curr_balance"] = 28500.00

        elif assigned_issue == "OUT_OF_RANGE_INTEREST_RATE":
            result["interest_rate"] = random.choice([45.00, 120.00, -5.00])

        elif assigned_issue == "STATUS_DPD_INCONSISTENT":
            scenario = random.choice(
                ["current_with_dpd", "late60_low_dpd", "late90_low_dpd", "default_zero_dpd"]
            )
            if scenario == "current_with_dpd":
                result["payment_status"] = "CURRENT"
                result["days_past_due"] = random.choice([30, 45, 90])
            elif scenario == "late60_low_dpd":
                result["payment_status"] = "LATE_60"
                result["days_past_due"] = random.choice([0, 15])
            elif scenario == "late90_low_dpd":
                result["payment_status"] = "LATE_90"
                result["days_past_due"] = random.choice([0, 30])
            else:
                result["payment_status"] = "DEFAULT"
                result["days_past_due"] = 0

        elif assigned_issue == "MISSING_DOCUMENT_STATUS":
            result["doc_status"] = random.choice(["", "NULL", "MISSING"])
            result["doc_status_override"] = "MISSING"

        elif assigned_issue == "SERVICER_CONFLICT":
            result["curr_balance"] = 18500.50
            result["payment_status"] = "CURRENT"
            result["servicer_balance_override"] = 12100.00
            result["servicer_status_override"] = "LATE_45"

        elif assigned_issue == "STALE_LAST_UPDATED":
            result["last_updated_str"] = "2020-01-01"

        elif assigned_issue == "INVALID_STATE_CODE":
            result["borrower_state"] = random.choice(INVALID_STATE_CODES)

        elif assigned_issue == "SUSPICIOUS_REPEATED_BORROWER":
            result["borrower_id"] = random.choice(self.suspicious_borrower_ids)

        elif assigned_issue == "CLOSED_WITH_POSITIVE_BALANCE":
            result["payment_status"] = "CLOSED"
            result["curr_balance"] = 45000.00

        return result

    # -------------------------------------------------------------------------
    # 4. Domain & Utility Helpers (Stepdown Level 4)
    # -------------------------------------------------------------------------

    def _compute_base_sequence_offset(self) -> int:
        offset = 10000
        try:
            from app.models import RawLoanRecord

            count_existing = RawLoanRecord.objects.count()
            if count_existing > 0:
                offset += count_existing
        except Exception:
            pass
        return offset

    def _assign_intentional_issues(self) -> dict[int, str]:
        issue_assignments: dict[int, str] = {}
        for idx in range(1, self.target_row_count + 1):
            if random.random() < 0.30:
                issue_assignments[idx] = random.choice(INTENTIONAL_ISSUES)
        return issue_assignments

    def _compute_consistent_days_past_due(self, payment_status: str) -> int:
        if payment_status in ["CURRENT", "CLOSED"]:
            return 0
        if payment_status == "LATE_30":
            return random.randint(30, 59)
        if payment_status == "LATE_60":
            return random.randint(60, 89)
        if payment_status == "LATE_90":
            return random.randint(90, 119)
        if payment_status == "DEFAULT":
            return random.randint(120, 360)
        return 0

    def _generate_extra_unmapped_columns(self, index: int) -> dict[str, str]:
        return {
            "custom_risk_score": f"EXT-RISK-{random.randint(100, 999)}"
            if random.random() < 0.20
            else "",
            "unmapped_vendor_data": f"VENDOR-TAG-{random.randint(10, 99)}"
            if random.random() < 0.20
            else "",
            "unexpected_audit_flag": f"AUDIT-FLAG-{random.randint(1, 5)}"
            if random.random() < 0.20
            else "",
            "servicer_raw_notes": f"Servicer note line {index}" if random.random() < 0.20 else "",
            "legacy_servicer_code": f"LEGACY-CODE-{random.randint(1000, 9999)}"
            if random.random() < 0.20
            else "",
            "ocr_confidence_score": f"{round(random.uniform(0.50, 0.99), 2)}"
            if random.random() < 0.20
            else "",
        }

    # ruff: noqa: UP038
    def _build_duplicate_servicer_row(self, base_row: dict[str, Any]) -> dict[str, Any]:
        duplicate = dict(base_row)
        if isinstance(duplicate.get("updated_current_balance"), (int, float)):
            duplicate["updated_current_balance"] = round(
                duplicate["updated_current_balance"] + 100.0, 2
            )
        return duplicate

    def _build_duplicate_document_row(self, base_row: dict[str, Any]) -> dict[str, Any]:
        duplicate = dict(base_row)
        duplicate["document_verification_status"] = "INCOMPLETE"
        return duplicate
