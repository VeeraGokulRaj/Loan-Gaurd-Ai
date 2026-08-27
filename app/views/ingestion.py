"""
Module A: Ingestion Pipeline View Endpoint for LoanGuard AI.

Receives multi-part CSV uploads (Loan Tape, Servicer Update, Document Manifest),
executes IngestionService, and returns real-time HTMX dynamic session summary responses.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _

from app.domain.ingestion import IngestionService
from app.models import FailedImportRow, RawLoanRecord, UploadBatch


@login_required(login_url="login")
def ingest_pipeline_view(request):
    """
    Multi-File Ingestion Pipeline Endpoint (`/ingest/pipeline/`).

    Accepts POST requests containing loan_tape_file, servicer_update_file, and document_manifest_file.
    Validates that all 3 required CSV files are attached, calls IngestionService, and returns
    rendered HTMX session summary.
    """
    if request.method != "POST":
        return HttpResponseBadRequest("Invalid request method. POST required.")

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
    batches = UploadBatch.objects.select_related("uploaded_by").order_by("-created")[:10]
    failed_rows = FailedImportRow.objects.select_related("batch").order_by("-created")[:10]

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

    print(success_rate)

    context = {
        "summary": summary_result,
        "batches": batches,
        "failed_rows": failed_rows,
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
        _("Ingestion pipeline completed successfully! Processed %(rows)d rows across 3 CSV files.")
        % {"rows": summary_result["total_session_rows"]},
    )
    return redirect("dashboard")
