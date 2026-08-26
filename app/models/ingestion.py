"""
Module A: Data Ingestion Engine Models for LoanGuard AI.

This module defines the database models required for raw file upload ingestion,
source file lineage preservation, import failure row isolation, and multi-file
servicer update and document manifest records.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from app.models.base import BaseModel


class UploadBatch(BaseModel):
    """
    Represents a single file upload batch in the ingestion pipeline.

    Aggregates metadata for uploaded files (Loan Tape, Servicer Update, Document Manifest),
    the operator who uploaded it, upload timestamps, and summary ingestion metrics.
    Inherits soft-deletion, timestamps, and history from BaseModel.
    """

    class SourceType(models.IntegerChoices):
        LOAN_TAPE = 1, _("Primary Loan Tape")
        SERVICER_UPDATE = 2, _("Servicer Update File")
        DOCUMENT_MANIFEST = 3, _("Document Manifest Ledger")

    class BatchStatus(models.IntegerChoices):
        PROCESSING = 1, _("Processing")
        INGESTED = 2, _("Ingested")
        PARTIAL_SUCCESS = 3, _("Partial Success")
        FAILED = 4, _("Failed")

    file_name = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text=_("Original filename of the uploaded CSV file (e.g., loan_tape.csv)."),
    )
    source_type = models.IntegerField(
        choices=SourceType.choices,
        default=SourceType.LOAN_TAPE,
        null=True,
        blank=True,
        help_text=_(
            "Type/category of the uploaded source file (Loan Tape, Servicer Update, Document Manifest)."
        ),
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="upload_batches",
        help_text=_("User (Data Operator) who uploaded this file batch."),
    )
    total_records = models.IntegerField(
        default=0,
        null=True,
        blank=True,
        help_text=_("Total number of data rows present in the uploaded CSV file."),
    )
    successful_records = models.IntegerField(
        default=0,
        null=True,
        blank=True,
        help_text=_("Count of rows successfully parsed and stored as raw loan records."),
    )
    failed_records = models.IntegerField(
        default=0,
        null=True,
        blank=True,
        help_text=_("Count of rows that failed initial CSV parsing or structural validation."),
    )
    status = models.IntegerField(
        choices=BatchStatus.choices,
        default=BatchStatus.PROCESSING,
        null=True,
        blank=True,
        help_text=_(
            "Current execution status of the upload batch (Processing, Ingested, Partial Success, Failed)."
        ),
    )

    class Meta:
        verbose_name = _("Upload Batch")
        verbose_name_plural = _("Upload Batches")
        ordering = ["-created"]

    def __str__(self) -> str:
        return f"Batch #{self.id} - {self.file_name or 'Unnamed'} ({self.get_source_type_display() if self.source_type else 'Unknown'})"


class RawLoanRecord(BaseModel):
    """
    Stores raw, uncleaned CSV row data as a JSON dictionary payload.

    Preserves line-by-line audit lineage back to the exact source file and row number,
    guaranteeing zero data loss prior to normalization or schema transformation.
    Inherits soft-deletion, timestamps, and history from BaseModel.
    """

    batch = models.ForeignKey(
        UploadBatch,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="raw_records",
        help_text=_("Upload batch associated with this raw loan record."),
    )
    row_number = models.IntegerField(
        null=True,
        blank=True,
        help_text=_("Lineage: Exact line number of this record in the source CSV file."),
    )
    raw_data = models.JSONField(
        default=dict,
        null=True,
        blank=True,
        help_text=_(
            "Uncleaned raw string dictionary payload representing exact CSV row values, preserving 100% of original CSV columns."
        ),
    )
    source_system = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text=_("Source system or file discriminator for audit lineage tracking."),
    )

    class Meta:
        verbose_name = _("Raw Loan Record")
        verbose_name_plural = _("Raw Loan Records")
        ordering = ["batch", "row_number"]

    def __str__(self) -> str:
        return f"Raw Record Batch #{self.batch_id} Row #{self.row_number}"


class FailedImportRow(BaseModel):
    """
    Isolates CSV rows that failed basic structural parsing or character encoding checks.

    Enables data operators to inspect raw unparsed line text and failure reasons to fix CSV issues.
    Inherits soft-deletion, timestamps, and history from BaseModel.
    """

    batch = models.ForeignKey(
        UploadBatch,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="failed_rows",
        help_text=_("Upload batch in which this row failure occurred."),
    )
    row_number = models.IntegerField(
        null=True,
        blank=True,
        help_text=_("Line number in the source CSV file where parsing failed."),
    )
    raw_line = models.TextField(
        null=True,
        blank=True,
        help_text=_("Unparsed raw text content of the failing CSV line."),
    )
    failure_reason = models.TextField(
        null=True,
        blank=True,
        help_text=_(
            "Detailed error message explaining why row parsing or structural check failed."
        ),
    )

    class Meta:
        verbose_name = _("Failed Import Row")
        verbose_name_plural = _("Failed Import Rows")
        ordering = ["batch", "row_number"]

    def __str__(self) -> str:
        return f"Failed Row Batch #{self.batch_id} Row #{self.row_number}"


class ServicerUpdateRecord(BaseModel):
    """
    Stores structured servicing update data parsed from servicer CSV files.

    Used by the validation engine for cross-file reconciliation against primary loan tapes.
    Full raw CSV content is preserved in RawLoanRecord.raw_data for zero-data-loss lineage.
    Inherits soft-deletion, timestamps, and history from BaseModel.
    """

    batch = models.ForeignKey(
        UploadBatch,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="servicer_records",
        help_text=_("Upload batch associated with this servicer update record."),
    )
    loan_id = models.CharField(
        max_length=100,
        db_index=True,
        null=True,
        blank=True,
        help_text=_("Unique loan identifier matching the primary loan tape."),
    )
    updated_current_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("Latest current principal balance reported by the servicer."),
    )
    updated_payment_status = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text=_("Payment status reported by the servicer (e.g. Current, 30 Days Delinquent)."),
    )
    updated_days_past_due = models.IntegerField(
        null=True,
        blank=True,
        help_text=_("Days past due (DPD) counter reported by the servicer."),
    )
    last_payment_date = models.DateField(
        null=True,
        blank=True,
        help_text=_("Date of the last recorded borrower payment."),
    )
    servicer_as_of_date = models.DateField(
        null=True,
        blank=True,
        help_text=_("As-of date of the servicer update report."),
    )

    class Meta:
        verbose_name = _("Servicer Update Record")
        verbose_name_plural = _("Servicer Update Records")
        ordering = ["batch", "loan_id"]

    def __str__(self) -> str:
        return f"Servicer Update Loan #{self.loan_id} (Batch #{self.batch_id})"


class DocumentManifestRecord(BaseModel):
    """
    Stores structured document completeness checklist items parsed from document manifest CSV files.

    Used by the validation engine to enforce document presence verification rules.
    Full raw CSV content is preserved in RawLoanRecord.raw_data for zero-data-loss lineage.
    Inherits soft-deletion, timestamps, and history from BaseModel.
    """

    batch = models.ForeignKey(
        UploadBatch,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="document_manifest_records",
        help_text=_("Upload batch associated with this document manifest record."),
    )
    loan_id = models.CharField(
        max_length=100,
        db_index=True,
        null=True,
        blank=True,
        help_text=_("Unique loan identifier matching the primary loan tape."),
    )
    promissory_note_present = models.BooleanField(
        default=False,
        null=True,
        blank=True,
        help_text=_("Flag indicating whether a signed Promissory Note is present."),
    )
    id_proof_present = models.BooleanField(
        default=False,
        null=True,
        blank=True,
        help_text=_("Flag indicating whether borrower identity proof document is present."),
    )
    income_verification_present = models.BooleanField(
        default=False,
        null=True,
        blank=True,
        help_text=_("Flag indicating whether borrower income verification document is present."),
    )
    document_verification_status = models.CharField(
        max_length=50,
        default="MISSING",
        null=True,
        blank=True,
        help_text=_("Overall document verification status (e.g. COMPLETE, PARTIAL, MISSING)."),
    )

    class Meta:
        verbose_name = _("Document Manifest Record")
        verbose_name_plural = _("Document Manifest Records")
        ordering = ["batch", "loan_id"]

    def __str__(self) -> str:
        return f"Document Manifest Loan #{self.loan_id} Status: {self.document_verification_status}"
