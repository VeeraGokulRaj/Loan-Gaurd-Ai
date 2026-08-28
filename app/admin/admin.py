"""
Django Admin Registrations & Configuration for LoanGuard AI Models.

Registers UploadBatch, RawLoanRecord, FailedImportRow, ServicerUpdateRecord,
DocumentManifestRecord, and AuditEvent in Django Admin with list_select_related
query optimizations and autocomplete_fields.
"""

from django.contrib import admin, messages
from django.shortcuts import redirect, render
from django.urls import path
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from app.domain.validation_service import (
    DEFAULT_VALIDATION_FILE_PATH,
    ValidationRuleJsonService,
    get_validation_json_path,
)
from app.models.audit import AuditEvent
from app.models.ingestion import (
    DocumentManifestRecord,
    FailedImportRow,
    RawLoanRecord,
    ServicerUpdateRecord,
    UploadBatch,
)
from app.models.validation import LoanException, ValidationRule, ValidationSeverity


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


@admin.register(ValidationRule)
class ValidationRuleAdmin(admin.ModelAdmin):
    """Admin configuration for ValidationRule model with severity badges & JSON management support."""

    list_display = (
        "id",
        "rule_code",
        "strategy_key",
        "rule_name",
        "field_name",
        "get_severity_badge",
        "get_active_badge",
        "created",
    )
    list_filter = ("severity", "is_active", "created")
    search_fields = ("rule_code", "strategy_key", "rule_name", "field_name", "description")
    ordering = ("rule_code",)
    readonly_fields = ("created", "modified")
    actions = ["sync_database_from_validation_json"]

    def get_urls(self):
        """Adds custom URL route for managing validation.json in Django Admin."""
        urls = super().get_urls()
        custom_urls = [
            path(
                "manage-validation-json/",
                self.admin_site.admin_view(self.manage_validation_json_view),
                name="app_validationrule_manage_validation_json",
            ),
        ]
        return custom_urls + urls

    def _get_context(self, request, json_content: str):
        """Helper to construct rich context for manage_validation_json template."""
        total_rules = ValidationRule.objects.count()
        active_rules = ValidationRule.objects.filter(is_active=True).count()
        critical_count = ValidationRule.objects.filter(severity=ValidationSeverity.CRITICAL).count()
        high_count = ValidationRule.objects.filter(severity=ValidationSeverity.HIGH).count()
        medium_count = ValidationRule.objects.filter(severity=ValidationSeverity.MEDIUM).count()
        low_count = ValidationRule.objects.filter(severity=ValidationSeverity.LOW).count()

        file_rule_count = 0
        try:
            import json

            data = json.loads(json_content)
            if isinstance(data, list):
                file_rule_count = len(data)
        except Exception:
            file_rule_count = 0

        return {
            **self.admin_site.each_context(request),
            "title": _("Manage Validation Rules JSON (validation.json)"),
            "json_content": json_content,
            "total_rules": total_rules,
            "active_rules": active_rules,
            "critical_count": critical_count,
            "high_count": high_count,
            "medium_count": medium_count,
            "low_count": low_count,
            "file_rule_count": file_rule_count,
            "validation_file_path": DEFAULT_VALIDATION_FILE_PATH,
            "opts": self.model._meta,
        }

    def manage_validation_json_view(self, request):
        """
        Admin view to inspect, upload, edit, validate, and persist validation.json.

        Handles validation gracefully and reports invalid JSON syntax, missing fields,
        or duplicate rule codes via Django messages framework.
        """
        json_file_path = get_validation_json_path()

        if request.method == "POST":
            action = request.POST.get("action")

            if action in ["reload_file", "Reload File"]:
                messages.info(request, f"Reloaded {json_file_path.name} content from disk.")
                return redirect("admin:app_validationrule_manage_validation_json")

            raw_json = ""
            if request.FILES.get("json_file"):
                uploaded_file = request.FILES["json_file"]
                try:
                    raw_json = uploaded_file.read().decode("utf-8")
                except Exception as exc:
                    messages.error(request, f"Error reading uploaded file: {str(exc)}")
                    return redirect("admin:app_validationrule_manage_validation_json")
            else:
                raw_json = request.POST.get("json_content", "")

            valid_rules, errors = ValidationRuleJsonService.validate_raw_json(raw_json)

            if errors:
                for err in errors:
                    messages.error(request, f"Validation Error: {err}")
                return render(
                    request,
                    "admin/manage_validation_json.html",
                    self._get_context(request, raw_json),
                )

            try:
                saved_path = ValidationRuleJsonService.save_to_file(valid_rules)
                created_cnt, updated_cnt = ValidationRuleJsonService.seed_database(valid_rules)

                messages.success(
                    request,
                    f"Successfully validated & saved {len(valid_rules)} rules to {saved_path.as_posix()}! "
                    f"Database updated: {created_cnt} created, {updated_cnt} updated.",
                )
                return redirect("admin:app_validationrule_changelist")
            except Exception as exc:
                messages.error(request, f"Failed to persist validation rules: {str(exc)}")
                return render(
                    request,
                    "admin/manage_validation_json.html",
                    self._get_context(request, raw_json),
                )

        initial_json = ""
        if json_file_path.exists():
            with open(json_file_path, encoding="utf-8") as f:
                initial_json = f.read()

        return render(
            request,
            "admin/manage_validation_json.html",
            self._get_context(request, initial_json),
        )

    def sync_database_from_validation_json(self, request, queryset):
        """Admin action to trigger DB sync directly from current validation.json file."""
        json_file_path = get_validation_json_path()
        if not json_file_path.exists():
            messages.error(request, f"Validation JSON file not found at {json_file_path}")
            return

        with open(json_file_path, encoding="utf-8") as f:
            raw_content = f.read()

        valid_rules, errors = ValidationRuleJsonService.validate_raw_json(raw_content)
        if errors:
            for err in errors:
                messages.error(request, f"❌ {err}")
            return

        created_cnt, updated_cnt = ValidationRuleJsonService.seed_database(valid_rules)
        messages.success(
            request,
            f"✅ Synced validation.json successfully! {created_cnt} created, {updated_cnt} updated in database.",
        )

    sync_database_from_validation_json.short_description = _(
        "🔄 Sync Database Validation Rules from validation.json File"
    )

    def get_severity_badge(self, obj: ValidationRule) -> str:
        """Returns colored badge for validation severity level."""
        colors = {
            ValidationSeverity.LOW: "#2563eb",  # Blue
            ValidationSeverity.MEDIUM: "#d97706",  # Amber
            ValidationSeverity.HIGH: "#ea580c",  # Orange
            ValidationSeverity.CRITICAL: "#dc2626",  # Red
        }
        color = colors.get(obj.severity, "#6b7280")
        label = obj.get_severity_display() if obj.severity else "Unknown"
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 12px; font-size: 11px; font-weight: 600;">{}</span>',
            color,
            label,
        )

    get_severity_badge.short_description = _("Severity")

    def get_active_badge(self, obj: ValidationRule) -> str:
        """Returns colored badge for active/disabled status."""
        if obj.is_active:
            return format_html(
                '<span style="background-color: #059669; color: white; padding: 3px 8px; border-radius: 12px; font-size: 11px; font-weight: 600;">{}</span>',
                "ACTIVE",
            )
        return format_html(
            '<span style="background-color: #6b7280; color: white; padding: 3px 8px; border-radius: 12px; font-size: 11px; font-weight: 600;">{}</span>',
            "DISABLED",
        )

    get_active_badge.short_description = _("Is Active")


@admin.register(LoanException)
class LoanExceptionAdmin(admin.ModelAdmin):
    """Admin configuration for LoanException model with list_select_related & autocomplete support."""

    list_display = (
        "id",
        "rule_code",
        "field_name",
        "batch",
        "raw_record",
        "get_severity_badge",
        "get_status_badge",
        "resolved_by",
        "created",
    )
    list_filter = ("severity", "status", "created")
    search_fields = (
        "rule_code",
        "field_name",
        "description",
        "override_value",
        "reviewer_comment",
        "batch__file_name",
    )
    ordering = ("-severity", "-created")
    readonly_fields = ("created", "modified")

    # Performance & UX Optimizations
    list_select_related = ("batch", "raw_record", "rule", "resolved_by")
    autocomplete_fields = ("batch", "raw_record", "rule", "resolved_by")

    def get_severity_badge(self, obj: LoanException) -> str:
        """Returns colored badge for exception severity level."""
        colors = {
            ValidationSeverity.LOW: "#2563eb",  # Blue
            ValidationSeverity.MEDIUM: "#d97706",  # Amber
            ValidationSeverity.HIGH: "#ea580c",  # Orange
            ValidationSeverity.CRITICAL: "#dc2626",  # Red
        }
        color = colors.get(obj.severity, "#6b7280")
        label = obj.get_severity_display() if obj.severity else "Unknown"
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 12px; font-size: 11px; font-weight: 600;">{}</span>',
            color,
            label,
        )

    get_severity_badge.short_description = _("Severity")

    def get_status_badge(self, obj: LoanException) -> str:
        """Returns colored badge for exception status."""
        status_colors = {
            LoanException.ExceptionStatus.OPEN: "#dc2626",  # Red
            LoanException.ExceptionStatus.UNDER_REVIEW: "#d97706",  # Amber
            LoanException.ExceptionStatus.RESOLVED_ACCEPTED: "#059669",  # Emerald
            LoanException.ExceptionStatus.RESOLVED_EDITED: "#7c3aed",  # Purple
            LoanException.ExceptionStatus.REJECTED: "#4b5563",  # Gray
        }
        color = status_colors.get(obj.status, "#6b7280")
        label = obj.get_status_display() if obj.status else "Unknown"
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 12px; font-size: 11px; font-weight: 600;">{}</span>',
            color,
            label,
        )

    get_status_badge.short_description = _("Exception Status")
