"""
Data Consumer Views for LoanGuard AI.

Provides Class-Based Views (CBV) for Data Consumer workspace using AnyPermissionRequiredMixin,
django-filter FilterSet classes, 10-item pagination, data quality score metrics, and export capabilities.
"""

import csv
import json
from typing import Any

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import DetailView, ListView

from app.domain.roles import AppPermission
from app.filters.data_consumer import VerifiedLoanRecordFilter
from app.mixins import AnyPermissionRequiredMixin
from app.models.audit import AuditEvent
from app.models.ingestion import RawLoanRecord, UploadBatch
from app.models.validation import LoanException
from app.models.verified import VerifiedLoanRecord


class DataConsumerDashboardView(LoginRequiredMixin, AnyPermissionRequiredMixin, View):
    """
    Class-Based View for Data Consumer Workspace Main Index Dashboard (`/consumer/`).

    Renders data quality score meter, verification metrics breakdown, and navigation links.
    """

    permissions_required = [AppPermission.DATA_CONSUMER_CAN_VIEW_VERIFIED_TAPE]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        total_raw_tape = RawLoanRecord.objects.filter(
            batch__source_type=UploadBatch.SourceType.LOAN_TAPE
        ).count()
        total_verified = VerifiedLoanRecord.objects.count()
        total_exceptions = LoanException.objects.count()

        clean_verified_count = VerifiedLoanRecord.objects.filter(
            validation_status=VerifiedLoanRecord.ValidationStatus.PASSED_CLEAN
        ).count()
        resolved_verified_count = VerifiedLoanRecord.objects.filter(
            validation_status=VerifiedLoanRecord.ValidationStatus.RESOLVED_EXCEPTION
        ).count()

        if total_raw_tape > 0:
            quality_score = round((total_verified / total_raw_tape) * 100, 1)
        else:
            quality_score = 100.0 if total_verified > 0 else 0.0

        recent_verified_records = VerifiedLoanRecord.objects.with_lineage()[:5]

        context = {
            "title": _("Data Consumer Workspace - LoanGuard AI"),
            "user": request.user,
            "role_name": _("Data Consumer"),
            "total_raw": total_raw_tape,
            "total_verified": total_verified,
            "total_exceptions": total_exceptions,
            "clean_verified_count": clean_verified_count,
            "resolved_verified_count": resolved_verified_count,
            "quality_score": quality_score,
            "recent_verified_records": recent_verified_records,
        }
        return render(request, "dashboard/consumer/index.html", context)


class VerifiedLoanListView(LoginRequiredMixin, AnyPermissionRequiredMixin, ListView):
    """
    Class-Based View for Verified Loan Dataset List Page (`/consumer/verified-loans/`).

    Uses VerifiedLoanRecordFilter for django-filter search/filtering,
    strict 10-item pagination (`paginate_by = 10`), and pre-fetched lineage relations.
    """

    model = VerifiedLoanRecord
    template_name = "dashboard/consumer/list.html"
    context_object_name = "verified_records"
    paginate_by = 10
    permissions_required = [AppPermission.DATA_CONSUMER_CAN_VIEW_VERIFIED_TAPE]

    def get_queryset(self):
        qs = VerifiedLoanRecord.objects.with_lineage()
        self.filterset = VerifiedLoanRecordFilter(self.request.GET, queryset=qs)
        return self.filterset.qs

    def get_template_names(self):
        if self.request.headers.get("HX-Request"):
            return ["dashboard/consumer/includes/verified_loans_tab.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["filter"] = self.filterset
        context["title"] = _("Verified Loan Records - Data Consumer Workspace")
        context["total_count"] = self.filterset.qs.count()
        context["validation_status_choices"] = VerifiedLoanRecord.ValidationStatus.choices
        context["reviewer_decision_choices"] = VerifiedLoanRecord.ReviewerDecision.choices

        total_raw_tape = RawLoanRecord.objects.filter(
            batch__source_type=UploadBatch.SourceType.LOAN_TAPE
        ).count()
        total_verified = VerifiedLoanRecord.objects.count()
        context["quality_score"] = (
            round((total_verified / total_raw_tape) * 100, 1) if total_raw_tape > 0 else 100.0
        )
        return context


class VerifiedLoanDetailView(LoginRequiredMixin, AnyPermissionRequiredMixin, DetailView):
    """
    Class-Based View for Verified Loan Detail Page (`/consumer/verified-loans/<int:pk>/detail/`).

    Displays standardized canonical JSON payload, field-by-field breakdown, verifier info,
    and cryptographic SHA-256 integrity verification.
    """

    model = VerifiedLoanRecord
    template_name = "dashboard/consumer/detail.html"
    context_object_name = "verified_record"
    permissions_required = [AppPermission.DATA_CONSUMER_CAN_VIEW_VERIFIED_TAPE]

    def get_queryset(self):
        return VerifiedLoanRecord.objects.with_lineage()

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        record: VerifiedLoanRecord = self.object
        context["title"] = _("Verified Loan #%(loan_id)s Detail") % {"loan_id": record.loan_id}
        context["is_tampered"] = record.is_tampered
        context["computed_hash"] = record.compute_hash()
        return context


class VerifiedLoanHistoryView(LoginRequiredMixin, AnyPermissionRequiredMixin, DetailView):
    """
    Class-Based View for Verified Loan Verification History (`/consumer/verified-loans/<int:pk>/history/`).

    Displays historical verification lineage, associated raw record, resolved exceptions,
    and accepted AI recommendations.
    """

    model = VerifiedLoanRecord
    template_name = "dashboard/consumer/history.html"
    context_object_name = "verified_record"
    permissions_required = [AppPermission.DATA_CONSUMER_CAN_INSPECT_AUDIT_TRAIL]

    def get_queryset(self):
        return VerifiedLoanRecord.objects.with_lineage()

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        record: VerifiedLoanRecord = self.object
        context["title"] = _("Verification History - Loan #%(loan_id)s") % {
            "loan_id": record.loan_id
        }
        context["resolved_exceptions"] = record.exceptions_resolved.all()
        context["ai_recommendations"] = record.ai_recommendations_used.all()
        context["participating_reviewers"] = record.participating_reviewers.all()
        return context


class VerifiedLoanAuditTrailView(LoginRequiredMixin, AnyPermissionRequiredMixin, DetailView):
    """
    Class-Based View for Verified Loan Audit Trail Ledger (`/consumer/verified-loans/<int:pk>/audit/`).

    Displays cryptographic hash-chained AuditEvent logs associated with the loan record.
    """

    model = VerifiedLoanRecord
    template_name = "dashboard/consumer/audit.html"
    context_object_name = "verified_record"
    permissions_required = [AppPermission.DATA_CONSUMER_CAN_INSPECT_AUDIT_TRAIL]

    def get_queryset(self):
        return VerifiedLoanRecord.objects.with_lineage()

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        record: VerifiedLoanRecord = self.object
        context["title"] = _("Audit Trail Ledger - Loan #%(loan_id)s") % {"loan_id": record.loan_id}

        # Query AuditEvents matching this loan_id or payload raw_record_id
        audit_query = Q(loan_id=record.loan_id)
        if record.raw_record_id:
            audit_query |= Q(payload__raw_record_id=record.raw_record_id)

        context["audit_events"] = AuditEvent.objects.filter(audit_query).order_by(
            "-timestamp", "-id"
        )
        return context


class ExportVerifiedLoansView(LoginRequiredMixin, AnyPermissionRequiredMixin, View):
    """
    Class-Based View for Exporting Verified Datasets (`/consumer/verified-loans/export/`).

    Supports CSV and JSON downloads of canonical verified records.
    """

    permissions_required = [AppPermission.DATA_CONSUMER_CAN_EXPORT_DATASET]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        export_format = request.GET.get("format", "csv").lower()
        filterset = VerifiedLoanRecordFilter(request.GET, queryset=VerifiedLoanRecord.objects.all())
        records = list(filterset.qs.select_related("raw_record", "verified_by"))

        # Bulk log audit trail events for all exported verified record rows
        audit_events = [
            {
                "event_type": "VERIFIED_RECORD_EXPORTED",
                "actor": request.user,
                "actor_role": AuditEvent.ActorRole.DATA_CONSUMER,
                "loan_id": rec.loan_id,
                "batch_id": getattr(rec.raw_record, "batch_id", None),
                "payload": {
                    "verified_record_id": rec.id,
                    "format": export_format,
                    "record_hash": rec.record_hash,
                    "validation_status": rec.get_validation_status_display(),
                },
            }
            for rec in records
        ]
        if audit_events:
            AuditEvent.log_events_bulk(audit_events, batch_size=500)

        if export_format == "json":
            data = [
                {
                    "id": rec.id,
                    "loan_id": rec.loan_id,
                    "borrower_id": rec.borrower_id,
                    "validation_status": rec.validation_status,
                    "reviewer_decision": rec.reviewer_decision,
                    "verified_at": rec.verified_at.isoformat() if rec.verified_at else None,
                    "verified_by": rec.verified_by.username if rec.verified_by else None,
                    "record_hash": rec.record_hash,
                    "canonical_data": rec.canonical_data,
                }
                for rec in records
            ]
            response = HttpResponse(json.dumps(data, indent=2), content_type="application/json")
            response["Content-Disposition"] = 'attachment; filename="verified_loans_export.json"'
            return response

        # Default CSV export
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="verified_loans_export.csv"'

        writer = csv.writer(response)
        writer.writerow(
            [
                "Verified Record ID",
                "Loan ID",
                "Borrower ID",
                "Validation Status",
                "Reviewer Decision",
                "Verified At",
                "Verified By",
                "Record SHA-256 Hash",
                "Original Principal",
                "Current Balance",
                "Interest Rate",
            ]
        )

        for rec in records:
            canon = rec.canonical_data or {}
            writer.writerow(
                [
                    rec.id,
                    rec.loan_id,
                    rec.borrower_id or "",
                    rec.get_validation_status_display(),
                    rec.get_reviewer_decision_display(),
                    rec.verified_at.strftime("%Y-%m-%d %H:%M:%S") if rec.verified_at else "",
                    rec.verified_by.username if rec.verified_by else "System Auto-Passed",
                    rec.record_hash,
                    canon.get("original_principal", ""),
                    canon.get("current_balance", ""),
                    canon.get("interest_rate", ""),
                ]
            )

        return response
