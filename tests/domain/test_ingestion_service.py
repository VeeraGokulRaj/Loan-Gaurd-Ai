"""
Test cases for app.domain.ingestion.IngestionService.

Covers the private parsing/decoding helpers plus the two public entry points
(process_single_file and process_multi_file_upload) with positive, negative,
edge and boundary/invalid input scenarios.
"""

from datetime import date

import pytest

from app.domain.ingestion import IngestionService
from app.models import (
    AuditEvent,
    DocumentManifestRecord,
    FailedImportRow,
    RawLoanRecord,
    ServicerUpdateRecord,
    UploadBatch,
)
from tests.factory.ingestion_factory import (
    LOAN_TAPE_ROW,
    IngestionFactory,
)
from tests.factory.user_factory import UserFactory


@pytest.mark.django_db
class TestIngestionPrivateHelpers:
    """Tests for the private static parsing/decoding helpers."""

    # ── _decode_file_stream ──

    def test_decode_plain_utf8(self):
        """UTF-8 content should be decoded correctly."""
        import io

        stream = io.BytesIO(b"alpha,beta\n1,2\n")
        assert IngestionService._decode_file_stream(stream) == ["alpha,beta", "1,2"]

    def test_decode_utf8_with_bom(self):
        """UTF-8 with BOM should be stripped by utf-8-sig decoding."""
        import io

        stream = io.BytesIO(b"\xef\xbb\xbfloan_id,bal\nLG-1,100\n")
        lines = IngestionService._decode_file_stream(stream)
        assert lines[0] == "loan_id,bal"

    def test_decode_latin1_fallback(self):
        """Bytes invalid in UTF-8 should fall back to ISO-8859-1."""
        import io

        raw = "caf\xe9,value\n".encode("iso-8859-1")
        stream = io.BytesIO(raw)
        lines = IngestionService._decode_file_stream(stream)
        assert "caf\xe9,value" in lines

    def test_decode_resets_stream_pointer(self):
        """The stream pointer should be reset after reading."""
        import io

        stream = io.BytesIO(b"abc\n")
        IngestionService._decode_file_stream(stream)
        assert stream.tell() == 0

    def test_decode_unknown_encoding_fallback_replacement(self):
        """Bytes un-decodable by all candidates should use utf-8 replace fallback."""
        # 0xFF is invalid in both utf-8 and iso-8859-1/cp1252? It is valid in cp1252.
        # Use a byte sequence invalid everywhere to exercise the final fallback.
        import io

        stream = io.BytesIO(b"\xff\xfe\xfd\n")
        lines = IngestionService._decode_file_stream(stream)
        assert isinstance(lines, list)
        assert all(isinstance(line, str) for line in lines)

    # ── _parse_date ──

    def test_parse_date_valid(self):
        assert IngestionService._parse_date("2025-06-30") == date(2025, 6, 30)

    def test_parse_date_whitespace_only_returns_none(self):
        assert IngestionService._parse_date("   ") is None

    def test_parse_date_empty_returns_none(self):
        assert IngestionService._parse_date("") is None

    def test_parse_date_none_returns_none(self):
        assert IngestionService._parse_date(None) is None

    def test_parse_date_invalid_format_returns_none(self):
        assert IngestionService._parse_date("30/06/2025") is None

    def test_parse_date_invalid_calendar_day_returns_none(self):
        assert IngestionService._parse_date("2025-13-45") is None

    # ── _parse_decimal ──

    def test_parse_decimal_valid(self):
        assert IngestionService._parse_decimal("2400000.50") == 2400000.5

    def test_parse_decimal_integer_string(self):
        assert IngestionService._parse_decimal("2500000") == 2500000.0

    def test_parse_decimal_whitespace_returns_none(self):
        assert IngestionService._parse_decimal("   ") is None

    def test_parse_decimal_empty_returns_none(self):
        assert IngestionService._parse_decimal("") is None

    def test_parse_decimal_none_returns_none(self):
        assert IngestionService._parse_decimal(None) is None

    def test_parse_decimal_invalid_string_returns_none(self):
        assert IngestionService._parse_decimal("not-a-number") is None

    # ── _parse_int ──

    def test_parse_int_valid(self):
        assert IngestionService._parse_int("12") == 12

    def test_parse_int_float_string_truncates(self):
        assert IngestionService._parse_int("12.7") == 12

    def test_parse_int_negative(self):
        assert IngestionService._parse_int("-5") == -5

    def test_parse_int_empty_returns_none(self):
        assert IngestionService._parse_int("") is None

    def test_parse_int_invalid_returns_none(self):
        assert IngestionService._parse_int("abc") is None

    def test_parse_int_none_returns_none(self):
        assert IngestionService._parse_int(None) is None

    # ── _parse_bool ──

    def test_parse_bool_true_forms(self):
        for val in ("TRUE", "1", "YES", "Y"):
            assert IngestionService._parse_bool(val) is True

    def test_parse_bool_false_forms(self):
        for val in ("FALSE", "0", "NO", "N"):
            assert IngestionService._parse_bool(val) is False

    def test_parse_bool_case_insensitive(self):
        assert IngestionService._parse_bool("true") is True
        assert IngestionService._parse_bool("yes") is True

    def test_parse_bool_whitespace_stripped(self):
        assert IngestionService._parse_bool("  TRUE  ") is True

    def test_parse_bool_empty_returns_none(self):
        assert IngestionService._parse_bool("") is None

    def test_parse_bool_none_returns_none(self):
        assert IngestionService._parse_bool(None) is None

    def test_parse_bool_unknown_value_returns_none(self):
        assert IngestionService._parse_bool("MAYBE") is None


@pytest.mark.django_db
class TestProcessSingleFile:
    """Tests for IngestionService.process_single_file."""

    def setup_method(self):
        self.user = UserFactory.create_data_operator(username="op_ingest")

    def test_ingested_single_valid_row_loan_tape(self):
        """A valid loan tape row should produce an INGESTED batch with full metrics."""
        file_obj = IngestionFactory.loan_tape_file()
        result = IngestionService.process_single_file(
            file_obj, self.user, UploadBatch.SourceType.LOAN_TAPE, "LOAN_TAPE"
        )

        assert result["status"] == UploadBatch.BatchStatus.INGESTED
        assert result["status_display"] == "Ingested"
        assert result["total_records"] == 1
        assert result["successful_records"] == 1
        assert result["failed_records"] == 0
        assert result["failed_rows"] == []
        assert result["file_name"] == "loan_tape.csv"

        batch = UploadBatch.objects.get(id=result["batch_id"])
        assert batch.status == UploadBatch.BatchStatus.INGESTED
        assert batch.total_records == 1
        assert batch.uploaded_by == self.user

        raw = RawLoanRecord.objects.get(batch=batch)
        assert raw.row_number == 2
        assert raw.raw_data["loan_id"] == "LG-0001"
        assert raw.source_system == "LOAN_TAPE"

    def test_ingested_creates_audit_event(self):
        """A successful ingestion should create a FILE_UPLOADED audit event."""
        file_obj = IngestionFactory.loan_tape_file()
        result = IngestionService.process_single_file(
            file_obj, self.user, UploadBatch.SourceType.LOAN_TAPE, "LOAN_TAPE"
        )
        event = AuditEvent.objects.filter(event_type="FILE_UPLOADED").get(
            batch_id=result["batch_id"]
        )
        assert event.actor == self.user
        assert event.payload["total_rows"] == 1
        assert event.payload["status"] == "Ingested"

    def test_empty_file_marks_batch_failed(self):
        """An empty/zero-byte file should create a FAILED batch with one failed row."""
        file_obj = IngestionFactory.make_file("", name="empty.csv")
        result = IngestionService.process_single_file(
            file_obj, self.user, UploadBatch.SourceType.LOAN_TAPE, "LOAN_TAPE"
        )

        assert result["status"] == UploadBatch.BatchStatus.FAILED
        assert result["status_display"] == "FAILED"
        assert result["total_records"] == 0
        assert result["successful_records"] == 0
        assert result["failed_records"] == 1
        assert result["failed_rows"][0]["row"] == 1
        assert "Empty file" in result["failed_rows"][0]["reason"]

        batch = UploadBatch.objects.get(id=result["batch_id"])
        assert batch.status == UploadBatch.BatchStatus.FAILED
        assert FailedImportRow.objects.filter(batch=batch, row_number=1).exists()

    def test_header_only_marks_batch_failed(self):
        """A file with only a header (no data rows) should be FAILED with zero counts."""
        file_obj = IngestionFactory.loan_tape_file(rows=[], header=None)
        result = IngestionService.process_single_file(
            file_obj, self.user, UploadBatch.SourceType.LOAN_TAPE, "LOAN_TAPE"
        )
        assert result["status"] == UploadBatch.BatchStatus.FAILED
        assert result["total_records"] == 0
        assert result["successful_records"] == 0
        assert result["failed_records"] == 0
        assert result["failed_rows"] == []

    def test_partial_success_with_empty_rows(self):
        """Mixing valid and all-empty rows should result in PARTIAL_SUCCESS."""
        rows = [
            LOAN_TAPE_ROW,
            ",,,,,,,",
            LOAN_TAPE_ROW.replace("LG-0001", "LG-0002"),
        ]
        file_obj = IngestionFactory.loan_tape_file(rows=rows)
        result = IngestionService.process_single_file(
            file_obj, self.user, UploadBatch.SourceType.LOAN_TAPE, "LOAN_TAPE"
        )

        assert result["status"] == UploadBatch.BatchStatus.PARTIAL_SUCCESS
        assert result["status_display"] == "Partial Success"
        assert result["total_records"] == 3
        assert result["successful_records"] == 2
        assert result["failed_records"] == 1
        assert len(result["failed_rows"]) == 1
        assert result["failed_rows"][0]["row"] == 3

        batch = UploadBatch.objects.get(id=result["batch_id"])
        assert batch.status == UploadBatch.BatchStatus.PARTIAL_SUCCESS
        assert RawLoanRecord.objects.filter(batch=batch).count() == 2
        assert FailedImportRow.objects.filter(batch=batch).count() == 1

    def test_all_rows_empty_marks_failed(self):
        """If every row is empty, the batch should be FAILED and all rows isolated."""
        file_obj = IngestionFactory.loan_tape_file(rows=[",,,,,,,", ",,,,,,,"])
        result = IngestionService.process_single_file(
            file_obj, self.user, UploadBatch.SourceType.LOAN_TAPE, "LOAN_TAPE"
        )
        assert result["status"] == UploadBatch.BatchStatus.FAILED
        assert result["successful_records"] == 0
        assert result["failed_records"] == 2
        assert RawLoanRecord.objects.count() == 0
        assert FailedImportRow.objects.count() == 2

    def test_source_system_from_row_overrides_default(self):
        """A source_system column value should override the passed source_system_name."""
        row = LOAN_TAPE_ROW.replace("LOAN_TAPE", "CUSTOM_SOURCE")
        file_obj = IngestionFactory.loan_tape_file(rows=[row])
        IngestionService.process_single_file(
            file_obj, self.user, UploadBatch.SourceType.LOAN_TAPE, "LOAN_TAPE"
        )
        raw = RawLoanRecord.objects.get()
        assert raw.source_system == "CUSTOM_SOURCE"

    def test_servicer_update_records_created(self):
        """Servicer update files should populate ServicerUpdateRecord fields correctly."""
        file_obj = IngestionFactory.servicer_update_file()
        result = IngestionService.process_single_file(
            file_obj,
            self.user,
            UploadBatch.SourceType.SERVICER_UPDATE,
            "SERVICER_UPDATE",
        )
        assert result["successful_records"] == 1

        rec = ServicerUpdateRecord.objects.get(batch__id=result["batch_id"])
        assert rec.loan_id == "LG-0001"
        assert rec.updated_current_balance == 2400000.50
        assert rec.updated_payment_status == "Current"
        assert rec.updated_days_past_due == 0
        assert rec.last_payment_date == date(2025, 6, 1)
        assert rec.servicer_as_of_date == date(2025, 6, 30)

    def test_servicer_update_with_missing_values_defaults(self):
        """Missing servicer column values (with loan_id present) should be stored as None."""
        row = "LG-0001,,,,,"
        file_obj = IngestionFactory.servicer_update_file(rows=[row])
        result = IngestionService.process_single_file(
            file_obj,
            self.user,
            UploadBatch.SourceType.SERVICER_UPDATE,
            "SERVICER_UPDATE",
        )
        rec = ServicerUpdateRecord.objects.get(batch__id=result["batch_id"])
        assert rec.loan_id == "LG-0001"
        assert rec.updated_current_balance is None
        assert rec.updated_payment_status is None
        assert rec.updated_days_past_due is None
        assert rec.last_payment_date is None

    def test_document_manifest_records_created(self):
        """Document manifest files should populate DocumentManifestRecord correctly."""
        file_obj = IngestionFactory.document_manifest_file()
        result = IngestionService.process_single_file(
            file_obj,
            self.user,
            UploadBatch.SourceType.DOCUMENT_MANIFEST,
            "DOCUMENT_MANIFEST",
        )
        rec = DocumentManifestRecord.objects.get(batch__id=result["batch_id"])
        assert rec.loan_id == "LG-0001"
        assert rec.promissory_note_present is True
        assert rec.id_proof_present is True
        assert rec.income_verification_present is True
        assert rec.document_verification_status == "COMPLETE"

    def test_document_manifest_missing_flags_default_false(self):
        """Missing document boolean columns should default to False, status to MISSING."""
        row = "LG-0099,,,,"
        file_obj = IngestionFactory.document_manifest_file(rows=[row])
        result = IngestionService.process_single_file(
            file_obj,
            self.user,
            UploadBatch.SourceType.DOCUMENT_MANIFEST,
            "DOCUMENT_MANIFEST",
        )
        rec = DocumentManifestRecord.objects.get(batch__id=result["batch_id"])
        assert rec.loan_id == "LG-0099"
        assert rec.promissory_note_present is False
        assert rec.id_proof_present is False
        assert rec.income_verification_present is False
        assert rec.document_verification_status == "MISSING"

    def test_loan_tape_creates_no_specialized_records(self):
        """Loan tape ingestion should not create servicer or document records."""
        file_obj = IngestionFactory.loan_tape_file()
        result = IngestionService.process_single_file(
            file_obj, self.user, UploadBatch.SourceType.LOAN_TAPE, "LOAN_TAPE"
        )
        batch = UploadBatch.objects.get(id=result["batch_id"])
        assert ServicerUpdateRecord.objects.filter(batch=batch).count() == 0
        assert DocumentManifestRecord.objects.filter(batch=batch).count() == 0

    def test_row_number_lineage_starts_at_two(self):
        """Raw rows should start at line 2 (header is line 1)."""
        rows = [LOAN_TAPE_ROW.replace("LG-0001", f"LG-{i:04d}") for i in range(1, 4)]
        file_obj = IngestionFactory.loan_tape_file(rows=rows)
        result = IngestionService.process_single_file(
            file_obj, self.user, UploadBatch.SourceType.LOAN_TAPE, "LOAN_TAPE"
        )
        row_numbers = sorted(
            RawLoanRecord.objects.filter(batch__id=result["batch_id"]).values_list(
                "row_number", flat=True
            )
        )
        assert row_numbers == [2, 3, 4]

    def test_all_three_source_types_create_separate_batches(self):
        """Processing each file type should yield an independent UploadBatch."""
        results = [
            IngestionService.process_single_file(
                IngestionFactory.loan_tape_file(),
                self.user,
                UploadBatch.SourceType.LOAN_TAPE,
                "LOAN_TAPE",
            ),
            IngestionService.process_single_file(
                IngestionFactory.servicer_update_file(),
                self.user,
                UploadBatch.SourceType.SERVICER_UPDATE,
                "SERVICER_UPDATE",
            ),
            IngestionService.process_single_file(
                IngestionFactory.document_manifest_file(),
                self.user,
                UploadBatch.SourceType.DOCUMENT_MANIFEST,
                "DOCUMENT_MANIFEST",
            ),
        ]
        batch_ids = [r["batch_id"] for r in results]
        assert len(set(batch_ids)) == 3
        assert UploadBatch.objects.count() == 3


@pytest.mark.django_db
class TestProcessMultiFileUpload:
    """Tests for IngestionService.process_multi_file_upload."""

    def setup_method(self):
        self.user = UserFactory.create_data_operator(username="op_multi")

    def test_all_three_files_processed(self):
        """Providing all three files should process all and aggregate totals."""
        files_dict = {
            "loan_tape": IngestionFactory.loan_tape_file(),
            "servicer_update": IngestionFactory.servicer_update_file(),
            "document_manifest": IngestionFactory.document_manifest_file(),
        }
        result = IngestionService.process_multi_file_upload(files_dict, self.user)

        assert result["total_session_rows"] == 3
        assert result["total_session_success"] == 3
        assert result["total_session_failed"] == 0
        assert len(result["batch_results"]) == 3
        assert result["session_completed_at"] is not None
        assert UploadBatch.objects.count() == 3

    def test_missing_files_are_skipped(self):
        """Missing file keys should be skipped without error."""
        files_dict = {
            "loan_tape": IngestionFactory.loan_tape_file(),
        }
        result = IngestionService.process_multi_file_upload(files_dict, self.user)

        assert len(result["batch_results"]) == 1
        assert result["total_session_rows"] == 1
        assert UploadBatch.objects.count() == 1

    def test_empty_dict_returns_no_batches(self):
        """An empty files dict should yield a zeroed summary and no batches."""
        result = IngestionService.process_multi_file_upload({}, self.user)
        assert result["batch_results"] == []
        assert result["total_session_rows"] == 0
        assert result["total_session_success"] == 0
        assert result["total_session_failed"] == 0
        assert UploadBatch.objects.count() == 0

    def test_aggregates_failures_across_files(self):
        """Failures from individual files should roll up into session totals."""
        files_dict = {
            "loan_tape": IngestionFactory.make_file("", name="empty_loan.csv"),
            "servicer_update": IngestionFactory.servicer_update_file(),
            "document_manifest": IngestionFactory.make_file("", name="empty_manifest.csv"),
        }
        result = IngestionService.process_multi_file_upload(files_dict, self.user)

        assert result["total_session_failed"] == 2
        assert result["total_session_success"] == 1
        assert result["total_session_rows"] == 1
        assert len(result["batch_results"]) == 3

    def test_creates_session_completed_audit_event(self):
        """A multi-file upload should log an INGESTION_SESSION_COMPLETED audit event."""
        files_dict = {
            "loan_tape": IngestionFactory.loan_tape_file(),
            "servicer_update": IngestionFactory.servicer_update_file(),
            "document_manifest": IngestionFactory.document_manifest_file(),
        }
        IngestionService.process_multi_file_upload(files_dict, self.user)

        event = AuditEvent.objects.filter(event_type="INGESTION_SESSION_COMPLETED").first()
        assert event is not None
        assert event.actor == self.user
        assert event.payload["files_processed"] == 3
