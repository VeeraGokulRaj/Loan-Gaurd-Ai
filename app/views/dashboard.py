"""
Dashboard Views & Role Routing Engine for LoanGuard AI.

Implements single dashboard entrypoint `/` that routes users based on their logged-in
role category (Data Operator, Reviewer, Data Consumer).
"""

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

from app.models import FailedImportRow, RawLoanRecord, UploadBatch, User


@login_required(login_url="login")
def dashboard_view(request):
    """
    Single Dashboard Router View (`/`).

    Routes authenticated users based on their role category:
    - DATA_OPERATOR (Category 1) or Superuser -> Data Operator Dashboard
    - REVIEWER (Category 2) -> Reviewer Dashboard Placeholder
    - DATA_CONSUMER (Category 3) -> Data Consumer Dashboard Placeholder
    """
    user: User = request.user

    # Route based on user role category
    if user.is_reviewer:
        return reviewer_dashboard_view(request)

    if user.is_data_consumer:
        return data_consumer_dashboard_view(request)

    # Default for Data Operator (Category 1) or Superuser
    return operator_dashboard_view(request)


def operator_dashboard_view(request):
    """
    Data Operator Dashboard View.

    Provides full management workspace for Data Operators:
    1. File Upload Dropzone (Loan Tape, Servicer Update, Document Manifest)
    2. Real-time Ingestion Metrics Banner
    3. Recent Upload Batches History Table
    4. Actionable Failed Import Rows Table
    """
    user = request.user

    # Fetch recent upload batches
    batches = UploadBatch.objects.select_related("uploaded_by").order_by("-created")[:10]

    # Fetch recent failed import rows
    failed_rows = FailedImportRow.objects.select_related("batch").order_by("-created")[:10]

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
        success_rate = round((total_ingested_records / total_attempted_records) * 100, 1)
    else:
        success_rate = 100.0

    context = {
        "title": _("Data Operator Workspace - LoanGuard AI"),
        "user": user,
        "batches": batches,
        "failed_rows": failed_rows,
        "total_batches": total_batches,
        "total_raw_records": total_raw_records,
        "total_failed_rows": total_failed_rows,
        "success_rate": success_rate,
        "source_type_choices": UploadBatch.SourceType.choices,
    }
    return render(request, "dashboard/operator/index.html", context)


def reviewer_dashboard_view(request):
    """
    Reviewer Dashboard View Placeholder (Category 2).
    """
    context = {
        "title": _("Reviewer Workspace - LoanGuard AI"),
        "user": request.user,
        "role_name": _("Reviewer"),
    }
    return render(request, "dashboard/placeholder_reviewer.html", context)


def data_consumer_dashboard_view(request):
    """
    Data Consumer Dashboard View Placeholder (Category 3).
    """
    context = {
        "title": _("Data Consumer Workspace - LoanGuard AI"),
        "user": request.user,
        "role_name": _("Data Consumer"),
    }
    return render(request, "dashboard/placeholder_consumer.html", context)
