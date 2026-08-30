"""
Module A: Data Ingestion Engine Domain Service for LoanGuard AI.

Provides transactional multi-file CSV ingestion, raw payload storage with zero data loss,
source file lineage preservation, structural CSV parsing error isolation, and aggregated
upload session metrics.
"""

import csv
import time
from typing import Any

from django.db import transaction
from django.utils import timezone

from app.models import (
    AuditEvent,
    DocumentManifestRecord,
    FailedImportRow,
    RawLoanRecord,
    ServicerUpdateRecord,
    UploadBatch,
)


class IngestionService:
    """
    Core Domain Service for Module A Data Ingestion.

    Handles ingestion for Primary Loan Tape, Servicer Update, and Document Manifest CSV files.
    Preserves 100% raw row lineage in JSON format prior to normalization.
    """

    @classmethod
    def _decode_file_stream(cls, file_obj: Any) -> list[str]:
        """
        Safely reads and decodes a file stream using multi-encoding fallback.

        Supports UTF-8 with BOM (utf-8-sig), standard UTF-8, ISO-8859-1, and CP1252.

        Args:
            file_obj: Django UploadedFile or file-like object.

        Returns:
            List of text line strings.
        """
        raw_bytes = file_obj.read()
        # Reset pointer for subsequent reads if needed
        if hasattr(file_obj, "seek"):
            file_obj.seek(0)

        encodings = ["utf-8-sig", "utf-8", "iso-8859-1", "cp1252"]
        for encoding in encodings:
            try:
                text = raw_bytes.decode(encoding)
                return text.splitlines()
            except UnicodeDecodeError:
                continue

        # Ultimate fallback with character replacement
        return raw_bytes.decode("utf-8", errors="replace").splitlines()

    @classmethod
    def _parse_date(cls, val: str | None) -> Any | None:
        """Helper to safely parse YYYY-MM-DD date strings."""
        if not val or not val.strip():
            return None
        clean_val = val.strip()
        try:
            from datetime import datetime

            return datetime.strptime(clean_val, "%Y-%m-%d").date()
        except ValueError:
            return None

    @classmethod
    def _parse_decimal(cls, val: str | None) -> float | None:
        """Helper to safely parse numeric float/decimal values."""
        if not val or not val.strip():
            return None
        try:
            return float(val.strip())
        except ValueError:
            return None

    @classmethod
    def _parse_int(cls, val: str | None) -> int | None:
        """Helper to safely parse integer values."""
        if not val or not val.strip():
            return None
        try:
            return int(float(val.strip()))
        except ValueError:
            return None

    @classmethod
    def _parse_bool(cls, val: str | None) -> bool | None:
        """Helper to safely parse boolean string values."""
        if not val or not val.strip():
            return None
        clean = val.strip().upper()
        if clean in ("TRUE", "1", "YES", "Y"):
            return True
        if clean in ("FALSE", "0", "NO", "N"):
            return False
        return None

    @classmethod
    @transaction.atomic
    def process_single_file(
        cls,
        file_obj: Any,
        user: Any,
        source_type: int,
        source_system_name: str,
    ) -> dict[str, Any]:
        """
        Processes a single uploaded CSV file stream transactionally.

        Creates an UploadBatch instance, parses rows line-by-line, saves RawLoanRecord
        payloads, isolates parsing failures in FailedImportRow, and creates specialized
        domain records for Servicer Updates and Document Manifests.

        Args:
            file_obj: Uploaded CSV file object.
            user: Requesting User (Data Operator).
            source_type: UploadBatch.SourceType integer choice.
            source_system_name: Canonical discriminator name (e.g., 'LOAN_TAPE').

        Returns:
            Dictionary containing batch metadata, metrics, and list of failed rows.
        """
        start_time = time.time()
        file_name = getattr(file_obj, "name", "uploaded_file.csv")

        # 1. Create UploadBatch record in PROCESSING status
        batch = UploadBatch.objects.create(
            file_name=file_name,
            uploaded_by=user,
            source_type=source_type,
            total_records=0,
            successful_records=0,
            failed_records=0,
            status=UploadBatch.BatchStatus.PROCESSING,
        )

        lines = cls._decode_file_stream(file_obj)

        if not lines or len(lines) == 0:
            batch.status = UploadBatch.BatchStatus.FAILED
            batch.save()
            FailedImportRow.objects.create(
                batch=batch,
                row_number=1,
                raw_line="",
                failure_reason="Empty file uploaded or zero bytes present.",
            )
            return {
                "batch_id": batch.id,
                "file_name": file_name,
                "source_type": source_type,
                "total_records": 0,
                "successful_records": 0,
                "failed_records": 1,
                "status": UploadBatch.BatchStatus.FAILED,
                "status_display": "FAILED",
                "failed_rows": [{"row": 1, "reason": "Empty file uploaded"}],
            }

        # 2. Parse CSV header and rows via csv.DictReader
        raw_records_to_create: list[RawLoanRecord] = []
        failed_rows_to_create: list[FailedImportRow] = []
        servicer_records_to_create: list[ServicerUpdateRecord] = []
        document_records_to_create: list[DocumentManifestRecord] = []

        reader = csv.DictReader(lines)
        data_row_count = 0

        for row_idx, row in enumerate(reader, start=2):  # Line 1 is CSV header
            data_row_count += 1
            raw_line_text = str(row)

            # Structural Validation: Check if row has more than 5 null/empty values
            null_fields = [
                k
                for k, v in row.items()
                if v is None or str(v).strip() in ("", "NULL", "None", "null", "none", "NaN", "nan")
            ]
            if len(null_fields) > 5:
                fields_str = ", ".join(null_fields)
                reason_msg = (
                    f"Row has {len(null_fields)} null or empty values "
                    f"(exceeds maximum threshold of 5). Null fields: {fields_str}"
                )
                failed_rows_to_create.append(
                    FailedImportRow(
                        batch=batch,
                        row_number=row_idx,
                        raw_line=raw_line_text,
                        failure_reason=reason_msg,
                    )
                )
                continue

            # Store Raw Loan Record (100% uncleaned raw data preservation)
            raw_records_to_create.append(
                RawLoanRecord(
                    batch=batch,
                    row_number=row_idx,
                    raw_data=dict(row),
                    source_system=row.get("source_system") or source_system_name,
                )
            )

            # Specialized Parsing for Servicer Update File
            if source_type == UploadBatch.SourceType.SERVICER_UPDATE:
                loan_id = (row.get("loan_id") or "").strip() or None
                servicer_records_to_create.append(
                    ServicerUpdateRecord(
                        batch=batch,
                        loan_id=loan_id,
                        updated_current_balance=cls._parse_decimal(
                            row.get("updated_current_balance")
                        ),
                        updated_payment_status=(row.get("updated_payment_status") or "").strip()
                        or None,
                        updated_days_past_due=cls._parse_int(row.get("updated_days_past_due")),
                        last_payment_date=cls._parse_date(row.get("last_payment_date")),
                        servicer_as_of_date=cls._parse_date(row.get("servicer_as_of_date")),
                    )
                )

            # Specialized Parsing for Document Manifest File
            elif source_type == UploadBatch.SourceType.DOCUMENT_MANIFEST:
                loan_id = (row.get("loan_id") or "").strip() or None
                document_records_to_create.append(
                    DocumentManifestRecord(
                        batch=batch,
                        loan_id=loan_id,
                        promissory_note_present=cls._parse_bool(row.get("promissory_note_present"))
                        or False,
                        id_proof_present=cls._parse_bool(row.get("id_proof_present")) or False,
                        income_verification_present=cls._parse_bool(
                            row.get("income_verification_present")
                        )
                        or False,
                        document_verification_status=(
                            row.get("document_verification_status") or "MISSING"
                        ).strip(),
                    )
                )

        # 3. Bulk create DB objects for high performance
        if raw_records_to_create:
            created_raw_records = RawLoanRecord.objects.bulk_create(
                raw_records_to_create, batch_size=1000
            )
            audit_events_data = [
                {
                    "event_type": "LOAN_RECORD_IMPORTED",
                    "actor": user,
                    "actor_role": AuditEvent.ActorRole.DATA_OPERATOR,
                    "loan_id": rec.loan_id,
                    "batch_id": batch.id,
                    "payload": {
                        "raw_record_id": rec.id,
                        "row_number": rec.row_number,
                        "source_system": rec.source_system,
                    },
                }
                for rec in created_raw_records
            ]
            AuditEvent.log_events_bulk(audit_events_data, batch_size=500)

        if failed_rows_to_create:
            FailedImportRow.objects.bulk_create(failed_rows_to_create, batch_size=1000)

        if servicer_records_to_create:
            ServicerUpdateRecord.objects.bulk_create(servicer_records_to_create, batch_size=1000)

        if document_records_to_create:
            DocumentManifestRecord.objects.bulk_create(document_records_to_create, batch_size=1000)

        # 4. Update Batch Summary Metrics & Final Status
        total_rows = data_row_count
        success_count = len(raw_records_to_create)
        failed_count = len(failed_rows_to_create)

        if failed_count == 0 and success_count > 0:
            final_status = UploadBatch.BatchStatus.INGESTED
        elif success_count > 0:
            final_status = UploadBatch.BatchStatus.PARTIAL_SUCCESS
        else:
            final_status = UploadBatch.BatchStatus.FAILED

        batch.total_records = total_rows
        batch.successful_records = success_count
        batch.failed_records = failed_count
        batch.status = final_status
        batch.save()

        # 5. Log Audit Lineage Event
        AuditEvent.log_event(
            event_type="FILE_UPLOADED",
            actor=user,
            batch_id=batch.id,
            payload={
                "file_name": file_name,
                "source_type": source_system_name,
                "total_rows": total_rows,
                "successful_records": success_count,
                "failed_records": failed_count,
                "status": batch.get_status_display(),
            },
        )

        execution_time_ms = round((time.time() - start_time) * 1000, 2)

        return {
            "batch_id": batch.id,
            "file_name": file_name,
            "source_type": source_type,
            "source_type_label": source_system_name,
            "total_records": total_rows,
            "successful_records": success_count,
            "failed_records": failed_count,
            "status": final_status,
            "status_display": batch.get_status_display(),
            "execution_time_ms": execution_time_ms,
            "failed_rows": [
                {
                    "row": fr.row_number,
                    "reason": fr.failure_reason,
                    "raw_line": fr.raw_line,
                }
                for fr in failed_rows_to_create
            ],
        }

    @classmethod
    def process_multi_file_upload(
        cls,
        files_dict: dict[str, Any],
        user: Any,
    ) -> dict[str, Any]:
        """
        Ingests all 3 required CSV files in a single unified session pipeline.

        Args:
            files_dict: Dictionary mapping source keys ('loan_tape', 'servicer_update',
                       'document_manifest') to Django UploadedFile objects.
            user: Requesting User (Data Operator).

        Returns:
            Aggregated session summary metadata containing overall totals and per-file details.
        """
        session_start = time.time()

        file_mappings = [
            ("loan_tape", UploadBatch.SourceType.LOAN_TAPE, "LOAN_TAPE"),
            ("servicer_update", UploadBatch.SourceType.SERVICER_UPDATE, "SERVICER_UPDATE"),
            (
                "document_manifest",
                UploadBatch.SourceType.DOCUMENT_MANIFEST,
                "DOCUMENT_MANIFEST",
            ),
        ]

        batch_results = []
        total_session_rows = 0
        total_session_success = 0
        total_session_failed = 0

        for key, source_type, system_name in file_mappings:
            file_obj = files_dict.get(key)
            if not file_obj:
                continue

            result = cls.process_single_file(
                file_obj=file_obj,
                user=user,
                source_type=source_type,
                source_system_name=system_name,
            )
            batch_results.append(result)
            total_session_rows += result["total_records"]
            total_session_success += result["successful_records"]
            total_session_failed += result["failed_records"]

        total_execution_time_ms = round((time.time() - session_start) * 1000, 2)

        batch_ids = [b["batch_id"] for b in batch_results if "batch_id" in b]
        batch_ids_str = ",".join(str(i) for i in batch_ids)

        # Log session completion audit event
        AuditEvent.log_event(
            event_type="INGESTION_SESSION_COMPLETED",
            actor=user,
            payload={
                "files_processed": len(batch_results),
                "total_rows": total_session_rows,
                "successful_records": total_session_success,
                "failed_records": total_session_failed,
                "execution_time_ms": total_execution_time_ms,
                "batch_ids": batch_ids,
            },
        )

        return {
            "session_completed_at": timezone.now(),
            "execution_time_ms": total_execution_time_ms,
            "total_session_rows": total_session_rows,
            "total_session_success": total_session_success,
            "total_session_failed": total_session_failed,
            "batch_results": batch_results,
            "batch_ids": batch_ids,
            "batch_ids_str": batch_ids_str,
        }
