"""
Data Operator Views for LoanGuard AI.

Provides Class-Based Views (CBV) for Data Operator dashboard workspace
and multi-file CSV ingestion pipeline execution using AnyPermissionRequiredMixin.
"""

import time
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Sum
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.generic import View

from app.domain.ingestion import IngestionService
from app.domain.roles import AppPermission
from app.domain.validation_engine import ValidationEngine
from app.domain.validation_service import ValidationRuleJsonService
from app.filters import FailedImportRowFilter, UploadBatchFilter
from app.mixins import AnyPermissionRequiredMixin
from app.models import (
    AuditEvent,
    FailedImportRow,
    LoanException,
    RawLoanRecord,
    UploadBatch,
    ValidationRule,
    ValidationSeverity,
)

REQUIRED_TYPE_FOR_VALIDATION = {
    UploadBatch.SourceType.LOAN_TAPE,
    UploadBatch.SourceType.SERVICER_UPDATE,
    UploadBatch.SourceType.DOCUMENT_MANIFEST,
}


class OperatorDashboardView(AnyPermissionRequiredMixin, View):
    """
    Class-Based View for Data Operator Dashboard.

    Provides full management workspace for Data Operators:
    1. File Upload Dropzone (Loan Tape, Servicer Update, Document Manifest)
    2. Real-time Ingestion Metrics Banner
    3. Upload Batches History Table (Paginated & Filterable via HTMX)
    4. Actionable Failed Import Rows Table (Paginated & Filterable via HTMX)
    """

    permissions_required = [AppPermission.DATA_OPERATOR_CAN_VIEW_INGESTION_SUMMARY]

    def get(self, request):
        user = request.user

        # Fetch paginated upload batches (Page 1)
        batch_qs = UploadBatch.objects.select_related("uploaded_by").order_by("-created")
        batch_filter = UploadBatchFilter(request.GET, queryset=batch_qs)
        batch_paginator = Paginator(batch_filter.qs, 10)
        batches_page = batch_paginator.get_page(1)

        # Fetch paginated failed import rows (Page 1)
        failed_qs = FailedImportRow.objects.select_related("batch").order_by("-created")
        failed_filter = FailedImportRowFilter(request.GET, queryset=failed_qs)
        failed_paginator = Paginator(failed_filter.qs, 10)
        failed_rows_page = failed_paginator.get_page(1)

        # Fetch upload batches with failures for failed row batch filter dropdown
        failed_batches = UploadBatch.objects.filter(
            id__in=FailedImportRow.objects.values_list("batch_id", flat=True).distinct()
        ).order_by("-created")
        if not failed_batches.exists():
            failed_batches = UploadBatch.objects.order_by("-created")[:20]

        # Calculate summary metrics
        total_batches = UploadBatch.objects.count()
        total_raw_records = RawLoanRecord.objects.count()
        total_failed_rows = FailedImportRow.objects.count()

        total_ingested_records = (
            UploadBatch.objects.aggregate(total=Sum("successful_records"))["total"] or 0
        )
        total_attempted_records = (
            UploadBatch.objects.aggregate(total=Sum("total_records"))["total"] or 0
        )

        if total_attempted_records > 0:
            success_rate = (
                Decimal(total_ingested_records) / Decimal(total_attempted_records) * Decimal("100")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            success_rate = 100.0

        selected_batch_id = request.GET.get("batch_id", "")

        context = {
            "title": _("Data Operator Workspace - LoanGuard AI"),
            "user": user,
            "filter": batch_filter,
            "failed_filter": failed_filter,
            "selected_batch_id": selected_batch_id,
            "batches_page": batches_page,
            "batches": batches_page.object_list,
            "failed_rows_page": failed_rows_page,
            "failed_rows": failed_rows_page.object_list,
            "failed_batches": failed_batches,
            "total_batches": total_batches,
            "total_raw_records": total_raw_records,
            "total_failed_rows": total_failed_rows,
            "success_rate": success_rate,
            "source_type_choices": UploadBatch.SourceType.choices,
        }
        return render(request, "dashboard/operator/index.html", context)


class BatchListView(AnyPermissionRequiredMixin, View):
    """
    Class-Based View for Ingestion Batch Database List endpoint (`/ingest/batches/`).

    Provides HTMX-driven pagination, filename/batch ID search, source type, and status filtering
    using UploadBatchFilter FilterSet class.
    """

    permissions_required = [AppPermission.DATA_OPERATOR_CAN_VIEW_INGESTION_SUMMARY]

    def get(self, request):
        batch_qs = UploadBatch.objects.select_related("uploaded_by").order_by("-created")
        batch_filter = UploadBatchFilter(request.GET, queryset=batch_qs)

        paginator = Paginator(batch_filter.qs, 10)
        batches_page = paginator.get_page(request.GET.get("page", 1))

        context = {
            "filter": batch_filter,
            "batches_page": batches_page,
            "batches": batches_page.object_list,
            "tab_visible": True,
        }
        return render(request, "dashboard/operator/includes/batches_tab.html", context)


class FailedRowListView(AnyPermissionRequiredMixin, View):
    """
    Class-Based View for Failed Import Rows List endpoint (`/ingest/failed-rows/`).

    Provides HTMX-driven pagination, search (reason/raw_line/filename), and failed batch filtering
    using FailedImportRowFilter FilterSet class.
    """

    permissions_required = [AppPermission.DATA_OPERATOR_CAN_VIEW_INGESTION_SUMMARY]

    def get(self, request):
        failed_qs = FailedImportRow.objects.select_related("batch").order_by("-created")
        failed_filter = FailedImportRowFilter(request.GET, queryset=failed_qs)

        paginator = Paginator(failed_filter.qs, 10)
        failed_rows_page = paginator.get_page(request.GET.get("page", 1))

        failed_batches = UploadBatch.objects.filter(
            id__in=FailedImportRow.objects.values_list("batch_id", flat=True).distinct()
        ).order_by("-created")
        if not failed_batches.exists():
            failed_batches = UploadBatch.objects.order_by("-created")[:20]

        total_failed_rows = FailedImportRow.objects.count()

        context = {
            "filter": failed_filter,
            "failed_rows_page": failed_rows_page,
            "failed_rows": failed_rows_page.object_list,
            "failed_batches": failed_batches,
            "selected_batch_id": failed_filter.data.get("batch_id", ""),
            "total_failed_rows": total_failed_rows,
            "tab_visible": True,
        }
        return render(request, "dashboard/operator/includes/failed_tab.html", context)


class IngestPipelineView(AnyPermissionRequiredMixin, View):
    """
    Class-Based View for Multi-File Ingestion Pipeline Endpoint (`/ingest/pipeline/`).

    Accepts POST requests containing loan_tape_file, servicer_update_file, and document_manifest_file.
    Validates that all 3 required CSV files are attached, calls IngestionService, and returns
    rendered HTMX session summary.
    """

    permissions_required = [AppPermission.DATA_OPERATOR_CAN_UPLOAD_CSV]

    def post(self, request):
        loan_tape_file = request.FILES.get("loan_tape_file")
        servicer_update_file = request.FILES.get("servicer_update_file")
        document_manifest_file = request.FILES.get("document_manifest_file")

        is_htmx = request.headers.get("HX-Request") == "true"

        # Enforce strict requirement: User must provide ALL 3 CSV files before starting pipeline
        missing_files = []
        if not loan_tape_file:
            missing_files.append(_("Primary Loan Tape"))
        if not servicer_update_file:
            missing_files.append(_("Servicer Update"))
        if not document_manifest_file:
            missing_files.append(_("Document Manifest"))

        if missing_files:
            error_msg = _(
                "Ingestion blocked: All 3 CSV files must be selected. Missing: %(missing)s."
            ) % {"missing": ", ".join([str(m) for m in missing_files])}

            if is_htmx:
                html_error = f"""
                <div class="rounded-xl bg-red-500/10 border border-red-500/30 p-5 text-red-300 text-sm flex items-start gap-3 backdrop-blur-xl">
                    <svg class="w-5 h-5 text-red-400 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path>
                    </svg>
                    <div>
                        <h4 class="font-bold text-white mb-1">Missing Required Ingestion Files</h4>
                        <p>{error_msg}</p>
                    </div>
                </div>
                """
                return HttpResponse(html_error, status=400)

            messages.error(request, error_msg)
            return redirect("dashboard")

        files_dict = {
            "loan_tape": loan_tape_file,
            "servicer_update": servicer_update_file,
            "document_manifest": document_manifest_file,
        }

        # Execute Ingestion Pipeline Domain Service
        summary_result = IngestionService.process_multi_file_upload(
            files_dict=files_dict,
            user=request.user,
        )

        # Compute updated overall dashboard metrics for OOB HTMX components
        batch_qs = UploadBatch.objects.select_related("uploaded_by").order_by("-created")
        batch_paginator = Paginator(batch_qs, 10)
        batches_page = batch_paginator.get_page(1)

        failed_qs = FailedImportRow.objects.select_related("batch").order_by("-created")
        failed_paginator = Paginator(failed_qs, 10)
        failed_rows_page = failed_paginator.get_page(1)

        failed_batches = UploadBatch.objects.filter(
            id__in=FailedImportRow.objects.values_list("batch_id", flat=True).distinct()
        ).order_by("-created")
        if not failed_batches.exists():
            failed_batches = UploadBatch.objects.order_by("-created")[:20]

        total_batches = UploadBatch.objects.count()
        total_raw_records = RawLoanRecord.objects.count()
        total_failed_rows = FailedImportRow.objects.count()

        total_ingested = UploadBatch.objects.aggregate(t=Sum("successful_records"))["t"] or 0
        total_attempted = UploadBatch.objects.aggregate(t=Sum("total_records"))["t"] or 0

        if total_attempted > 0:
            success_rate = (
                Decimal(total_ingested) / Decimal(total_attempted) * Decimal("100")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            success_rate = 100.0

        context = {
            "summary": summary_result,
            "batches_page": batches_page,
            "batches": batches_page.object_list,
            "failed_rows_page": failed_rows_page,
            "failed_rows": failed_rows_page.object_list,
            "failed_batches": failed_batches,
            "total_batches": total_batches,
            "total_raw_records": total_raw_records,
            "total_failed_rows": total_failed_rows,
            "success_rate": success_rate,
            "source_type_choices": UploadBatch.SourceType.choices,
        }

        if is_htmx:
            return render(
                request,
                "dashboard/operator/includes/session_summary_partial.html",
                context,
            )

        messages.success(
            request,
            _(
                "Ingestion pipeline completed successfully! Processed %(rows)d rows across 3 CSV files."
            )
            % {"rows": summary_result["total_session_rows"]},
        )
        return redirect("dashboard")


class ExecuteValidationView(LoginRequiredMixin, AnyPermissionRequiredMixin, View):
    """
    Class-Based View for Data Operator to manually execute the Validation Engine.

    Restricted strictly to users with DATA_OPERATOR_CAN_TRIGGER_VALIDATION permission.
    Evaluates active ValidationRules against RawLoanRecords for target UploadBatch(es).
    Returns real-time execution metrics and progress banner via HTMX.
    """

    permissions_required = [AppPermission.DATA_OPERATOR_CAN_TRIGGER_VALIDATION]

    def post(
        self, request: HttpRequest, batch_id: int | None = None, *args: Any, **kwargs: Any
    ) -> HttpResponse:
        start_time = time.time()
        user = request.user

        # 1. Ensure Validation Rules exist in Database (Auto-seed if empty)
        active_rules_count = ValidationRule.objects.filter(is_active=True).count()
        if active_rules_count == 0:
            valid_rules, _ = ValidationRuleJsonService.load_and_validate_json()
            if valid_rules:
                ValidationRuleJsonService.seed_database(valid_rules)
                active_rules_count = ValidationRule.objects.filter(is_active=True).count()

        # 2. Resolve target batch(es) to evaluate
        batch_ids_param = request.GET.get("batch_ids") or request.POST.get("batch_ids")

        if batch_ids_param:
            try:
                parsed_ids = [
                    int(i.strip()) for i in str(batch_ids_param).split(",") if i.strip().isdigit()
                ]
                batches = list(
                    UploadBatch.objects.filter(id__in=parsed_ids).exclude(
                        status=UploadBatch.BatchStatus.FAILED
                    )
                )
            except Exception:
                batches = []
        else:
            batches = []

        if not batches:
            html_error = """
            <div class="p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-300 text-sm flex items-center gap-3">
                <svg class="w-5 h-5 text-amber-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path>
                </svg>
                <span>No valid upload batches found to execute validation engine against. Please ingest CSV files first.</span>
            </div>
            """
            return HttpResponse(html_error, status=400)

        # Enforce validation requirement: Validation runs only when all 3 files are selected / present
        source_types_present = {b.source_type for b in batches}

        if not REQUIRED_TYPE_FOR_VALIDATION.issubset(source_types_present):
            html_error = """
            <div class="p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-300 text-sm flex items-center gap-3">
                <svg class="w-5 h-5 text-amber-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path>
                </svg>
                <span>Validation can only be run when all 3 CSV files (Primary Loan Tape, Servicer Update, and Document Manifest) are selected.</span>
            </div>
            """
            return HttpResponse(html_error, status=400)

        # Parse user decision for include_db_history toggle (defaults to False unless checked)
        raw_include_history = request.POST.get("include_db_history") or request.GET.get(
            "include_db_history"
        )
        include_db_history = str(raw_include_history).lower() in ["true", "on", "1", "yes"]

        # 3. Execute Validation Engine for target primary loan tape batches
        loan_tape_batches = [
            b for b in batches if b.source_type == UploadBatch.SourceType.LOAN_TAPE
        ]
        target_batches = loan_tape_batches if loan_tape_batches else batches

        total_records_evaluated = 0
        total_exceptions_created = 0
        processed_batch_ids = []

        for batch in target_batches:
            records_count = batch.raw_records.count()
            total_records_evaluated += records_count
            created_exceptions = ValidationEngine.validate_batch(
                batch, include_db_history=include_db_history
            )
            total_exceptions_created += len(created_exceptions)
            processed_batch_ids.append(batch.id)

        execution_time_ms = round((time.time() - start_time) * 1000, 2)

        # 4. Log Audit Trail Event
        AuditEvent.log_event(
            event_type="VALIDATION_ENGINE_EXECUTED",
            actor=user,
            payload={
                "batch_ids": processed_batch_ids,
                "active_rules_evaluated": active_rules_count,
                "total_records_evaluated": total_records_evaluated,
                "exceptions_flagged": total_exceptions_created,
                "execution_time_ms": execution_time_ms,
            },
        )

        # 5. Compute Severity Breakdown
        critical_count = LoanException.objects.filter(
            batch_id__in=processed_batch_ids, severity=ValidationSeverity.CRITICAL
        ).count()
        high_count = LoanException.objects.filter(
            batch_id__in=processed_batch_ids, severity=ValidationSeverity.HIGH
        ).count()
        medium_count = LoanException.objects.filter(
            batch_id__in=processed_batch_ids, severity=ValidationSeverity.MEDIUM
        ).count()
        low_count = LoanException.objects.filter(
            batch_id__in=processed_batch_ids, severity=ValidationSeverity.LOW
        ).count()

        context = {
            "execution_time_ms": execution_time_ms,
            "active_rules_count": active_rules_count,
            "total_records_evaluated": total_records_evaluated,
            "total_exceptions_created": total_exceptions_created,
            "processed_batch_ids": processed_batch_ids,
            "target_batch_id": batch_id,
            "critical_count": critical_count,
            "high_count": high_count,
            "medium_count": medium_count,
            "low_count": low_count,
        }

        return render(
            request,
            "dashboard/operator/includes/validation_progress_partial.html",
            context,
        )
