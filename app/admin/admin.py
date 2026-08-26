"""
Django Admin Registrations & Configuration for LoanGuard AI Models.

Registers UploadBatch, RawLoanRecord, FailedImportRow, ServicerUpdateRecord,
DocumentManifestRecord, and AuditEvent in Django Admin with list_select_related
query optimizations and autocomplete_fields.
"""

from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from app.models.audit import AuditEvent
from app.models.ingestion import (
    DocumentManifestRecord,
    FailedImportRow,
    RawLoanRecord,
    ServicerUpdateRecord,
    UploadBatch,
)


@admin.register(UploadBatch)
class UploadBatchAdmin(admin.ModelAdmin):
    """Admin configuration for UploadBatch model with select_related & autocomplete support."""

    list_display = (
        "id",
        "file_name",
        "get_source_type_badge",
        "uploaded_by",
        "total_records",
        "successful_records",
        "failed_records",
        "get_status_badge",
        "created",
    )
    list_filter = ("source_type", "status", "created")
    search_fields = ("file_name", "id", "uploaded_by__username")
    ordering = ("-created",)
    readonly_fields = ("created", "modified")

    # Performance & UX Optimizations
    list_select_related = ("uploaded_by",)
    autocomplete_fields = ("uploaded_by",)

    def get_source_type_badge(self, obj: UploadBatch) -> str:
        """Returns colored badge for source type."""
        colors = {
            UploadBatch.SourceType.LOAN_TAPE: "#0284c7",  # Sky blue
            UploadBatch.SourceType.SERVICER_UPDATE: "#7c3aed",  # Purple
            UploadBatch.SourceType.DOCUMENT_MANIFEST: "#059669",  # Emerald
        }
        color = colors.get(obj.source_type, "#6b7280")
        label = obj.get_source_type_display() if obj.source_type else "Unknown"
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 12px; font-size: 11px; font-weight: 600;">{}</span>',
            color,
            label,
        )

    get_source_type_badge.short_description = _("Source Type")

    def get_status_badge(self, obj: UploadBatch) -> str:
        """Returns colored status badge."""
        colors = {
            UploadBatch.BatchStatus.PROCESSING: "#d97706",  # Amber
            UploadBatch.BatchStatus.INGESTED: "#059669",  # Emerald
            UploadBatch.BatchStatus.PARTIAL_SUCCESS: "#2563eb",  # Blue
            UploadBatch.BatchStatus.FAILED: "#dc2626",  # Red
        }
        color = colors.get(obj.status, "#6b7280")
        label = obj.get_status_display() if obj.status else "Unknown"
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 12px; font-size: 11px; font-weight: 600;">{}</span>',
            color,
            label,
        )

    get_status_badge.short_description = _("Batch Status")


@admin.register(RawLoanRecord)
class RawLoanRecordAdmin(admin.ModelAdmin):
    """Admin configuration for RawLoanRecord model with select_related & autocomplete."""

    list_display = (
        "id",
        "batch",
        "row_number",
        "source_system",
        "created",
    )
    list_filter = ("source_system", "created")
    search_fields = ("batch__file_name", "source_system", "raw_data")
    ordering = ("batch", "row_number")
    readonly_fields = ("created", "modified")

    # Performance & UX Optimizations
    list_select_related = ("batch",)
    autocomplete_fields = ("batch",)


@admin.register(FailedImportRow)
class FailedImportRowAdmin(admin.ModelAdmin):
    """Admin configuration for FailedImportRow model with select_related & autocomplete."""

    list_display = (
        "id",
        "batch",
        "row_number",
        "get_short_failure_reason",
        "created",
    )
    list_filter = ("created",)
    search_fields = ("batch__file_name", "failure_reason", "raw_line")
    ordering = ("batch", "row_number")
    readonly_fields = ("created", "modified")

    # Performance & UX Optimizations
    list_select_related = ("batch",)
    autocomplete_fields = ("batch",)

    def get_short_failure_reason(self, obj: FailedImportRow) -> str:
        """Returns truncated failure reason for clean list display."""
        if not obj.failure_reason:
            return "-"
        return obj.failure_reason[:60] + ("..." if len(obj.failure_reason) > 60 else "")

    get_short_failure_reason.short_description = _("Failure Reason")


@admin.register(ServicerUpdateRecord)
class ServicerUpdateRecordAdmin(admin.ModelAdmin):
    """Admin configuration for ServicerUpdateRecord model with select_related & autocomplete."""

    list_display = (
        "id",
        "loan_id",
        "batch",
        "updated_current_balance",
        "updated_payment_status",
        "updated_days_past_due",
        "last_payment_date",
        "servicer_as_of_date",
        "created",
    )
    list_filter = ("updated_payment_status", "servicer_as_of_date", "created")
    search_fields = ("loan_id", "batch__file_name", "updated_payment_status")
    ordering = ("batch", "loan_id")
    readonly_fields = ("created", "modified")

    # Performance & UX Optimizations
    list_select_related = ("batch",)
    autocomplete_fields = ("batch",)


@admin.register(DocumentManifestRecord)
class DocumentManifestRecordAdmin(admin.ModelAdmin):
    """Admin configuration for DocumentManifestRecord model with select_related & autocomplete."""

    list_display = (
        "id",
        "loan_id",
        "batch",
        "promissory_note_present",
        "id_proof_present",
        "income_verification_present",
        "get_verification_status_badge",
        "created",
    )
    list_filter = (
        "document_verification_status",
        "promissory_note_present",
        "id_proof_present",
        "income_verification_present",
        "created",
    )
    search_fields = ("loan_id", "batch__file_name", "document_verification_status")
    ordering = ("batch", "loan_id")
    readonly_fields = ("created", "modified")

    # Performance & UX Optimizations
    list_select_related = ("batch",)
    autocomplete_fields = ("batch",)

    def get_verification_status_badge(self, obj: DocumentManifestRecord) -> str:
        """Returns colored badge for document verification status."""
        status_colors = {
            "COMPLETE": "#059669",  # Emerald
            "PARTIAL": "#d97706",  # Amber
            "MISSING": "#dc2626",  # Red
        }
        status = (obj.document_verification_status or "MISSING").upper()
        color = status_colors.get(status, "#6b7280")
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 12px; font-size: 11px; font-weight: 600;">{}</span>',
            color,
            status,
        )

    get_verification_status_badge.short_description = _("Doc Status")


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    """
    Admin configuration for AuditEvent model with select_related & autocomplete.

    Enforces append-only audit trail rules by marking hash fields and timestamp as read-only.
    """

    list_display = (
        "id",
        "timestamp",
        "event_type",
        "loan_id",
        "batch_id",
        "actor",
        "get_actor_role_badge",
        "get_short_event_hash",
    )
    list_filter = ("actor_role", "event_type", "timestamp")
    search_fields = ("loan_id", "event_type", "actor__username", "event_hash", "prev_hash")
    ordering = ("-timestamp", "-id")
    readonly_fields = ("id", "timestamp", "prev_hash", "event_hash", "created", "modified")

    # Performance & UX Optimizations
    list_select_related = ("actor",)
    autocomplete_fields = ("actor",)

    def get_actor_role_badge(self, obj: AuditEvent) -> str:
        """Returns colored badge for actor role."""
        colors = {
            AuditEvent.ActorRole.SYSTEM: "#4b5563",  # Gray
            AuditEvent.ActorRole.DATA_OPERATOR: "#0284c7",  # Sky Blue
            AuditEvent.ActorRole.REVIEWER: "#7c3aed",  # Purple
            AuditEvent.ActorRole.AI_COPILOT: "#d97706",  # Amber
            AuditEvent.ActorRole.DATA_CONSUMER: "#059669",  # Emerald
        }
        color = colors.get(obj.actor_role, "#6b7280")
        label = obj.get_actor_role_display() if obj.actor_role else "System"
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 12px; font-size: 11px; font-weight: 600;">{}</span>',
            color,
            label,
        )

    get_actor_role_badge.short_description = _("Actor Role")

    def get_short_event_hash(self, obj: AuditEvent) -> str:
        """Returns truncated SHA-256 hash for list display."""
        if not obj.event_hash:
            return "-"
        return format_html(
            '<code style="font-family: monospace; font-size: 11px; color: #0284c7;">{}...</code>',
            obj.event_hash[:12],
        )

    get_short_event_hash.short_description = _("SHA-256 Hash")

    def has_delete_permission(self, request, obj=None) -> bool:
        """Audit events are append-only; standard deletion disabled in admin interface."""
        return request.user.is_superuser
