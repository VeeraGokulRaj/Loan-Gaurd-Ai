"""
Module B & Module C: Validation Engine and Exception Queue Models for LoanGuard AI.

This module defines the database models required for:
1. ValidationRule: Configurable rule definitions, severity assignments, strategy keys, and rule thresholds.
2. LoanException: Ingestion validation flags, reviewer exception queue items, review status, and overrides.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from app.models.base import BaseModel
from app.models.ingestion import RawLoanRecord, UploadBatch


class ValidationSeverity(models.IntegerChoices):
    """Severity classification levels for validation exceptions."""

    LOW = 1, _("Low")
    MEDIUM = 2, _("Medium")
    HIGH = 3, _("High")
    CRITICAL = 4, _("Critical")


class ValidationRule(BaseModel):
    """
    Configurable validation rule definition loaded into the database registry.

    Decouples rule metadata, severity levels, active toggles, and parameter thresholds from code.
    Maps rule execution directly to a strategy handler via `strategy_key`.
    Inherits soft-deletion, timestamps, and history from BaseModel.
    """

    rule_code = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        help_text=_("Display identifier for the rule (e.g., VAL_001 or VL-0001)."),
    )
    strategy_key = models.CharField(
        max_length=50,
        db_index=True,
        blank=True,
        null=True,
        help_text=_(
            "Explicit internal strategy handler key (e.g., MISSING_LOAN_ID, MATURITY_BEFORE_ORIGINATION)."
        ),
    )
    rule_name = models.CharField(
        max_length=255,
        help_text=_(
            "Human-readable rule title (e.g., Missing Loan ID, Maturity Before Origination)."
        ),
    )
    field_name = models.CharField(
        max_length=100,
        db_index=True,
        help_text=_(
            "Primary target loan dataset field evaluated by this rule (e.g., loan_id, current_balance)."
        ),
    )
    description = models.TextField(
        help_text=_("Detailed description of the validation check and business logic rule."),
    )
    severity = models.IntegerField(
        choices=ValidationSeverity.choices,
        default=ValidationSeverity.MEDIUM,
        help_text=_(
            "Default severity level assigned to exceptions flagged by this rule (Low, Medium, High, Critical)."
        ),
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text=_(
            "Toggle to dynamically enable or disable rule execution in the Validation Engine."
        ),
    )
    parameters = models.JSONField(
        default=dict,
        blank=True,
        null=True,
        help_text=_(
            "Configurable parameter thresholds and options for the rule strategy (e.g., {'max_rate': 35.0})."
        ),
    )

    class Meta:
        verbose_name = _("Validation Rule")
        verbose_name_plural = _("Validation Rules")
        ordering = ["rule_code"]

    def __str__(self) -> str:
        return f"{self.rule_code} - {self.rule_name} ({self.get_severity_display()})"


class LoanException(BaseModel):
    """
    Represents an exception flagged on a raw loan record by the Validation Engine.

    Serves as the core item in the Reviewer Exception Queue workspace for inspection,
    AI copilot recommendation review, reviewer overrides, comments, and status transitions.
    Inherits soft-deletion, timestamps, and history from BaseModel.
    """

    class ExceptionStatus(models.IntegerChoices):
        OPEN = 1, _("Open")
        UNDER_REVIEW = 2, _("Under Review")
        RESOLVED_ACCEPTED = 3, _("Resolved (Accepted)")
        RESOLVED_EDITED = 4, _("Resolved (Edited)")
        REJECTED = 5, _("Rejected")

    batch = models.ForeignKey(
        UploadBatch,
        on_delete=models.CASCADE,
        related_name="exceptions",
        help_text=_("Upload batch during which this exception was flagged."),
    )
    raw_record = models.ForeignKey(
        RawLoanRecord,
        on_delete=models.CASCADE,
        related_name="exceptions",
        help_text=_("Associated raw loan record that violated the validation rule."),
    )
    rule = models.ForeignKey(
        ValidationRule,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exceptions",
        help_text=_("Validation rule definition that generated this exception."),
    )
    rule_code = models.CharField(
        max_length=50,
        db_index=True,
        help_text=_("Rule code at time of exception creation for lineage preservation."),
    )
    field_name = models.CharField(
        max_length=100,
        db_index=True,
        help_text=_("Target field name associated with the exception (e.g., current_balance)."),
    )
    severity = models.IntegerField(
        choices=ValidationSeverity.choices,
        default=ValidationSeverity.MEDIUM,
        db_index=True,
        help_text=_("Severity level assigned to this exception instance."),
    )
    description = models.TextField(
        help_text=_("Specific error message or failure explanation for this record."),
    )
    status = models.IntegerField(
        choices=ExceptionStatus.choices,
        default=ExceptionStatus.OPEN,
        db_index=True,
        help_text=_(
            "Current review status of the exception (Open, Under Review, Resolved Accepted, Resolved Edited, Rejected)."
        ),
    )

    # Reviewer Override & Resolution Metadata
    reviewer_comment = models.TextField(
        blank=True,
        null=True,
        help_text=_("Comments or notes recorded by the Reviewer during exception handling."),
    )
    override_value = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text=_(
            "Manual override value entered or accepted by the Reviewer for the flagged field."
        ),
    )
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_exceptions",
        help_text=_("Reviewer user account that resolved or rejected this exception."),
    )
    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Timestamp when this exception was resolved or rejected."),
    )

    class Meta:
        verbose_name = _("Loan Exception")
        verbose_name_plural = _("Loan Exceptions")
        ordering = ["-severity", "-created"]

    def __str__(self) -> str:
        record_str = f"Record #{self.raw_record_id}" if self.raw_record_id else "Unknown Record"
        return f"[{self.get_severity_display()}] {self.rule_code} on {record_str} - {self.get_status_display()}"
