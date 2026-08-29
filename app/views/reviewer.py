"""
Module C: Reviewer Workspace & Exception Queue Views for LoanGuard AI.

Provides exception management dashboard workspace strictly for Reviewer role.
Split into ReviewerDashboardView (main workspace index) and LoanExceptionListView (HTMX tab/table view).
Supports HTMX pagination, filtering, search, severity badges, and AI recommendation controls.
"""

from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import View

from app.domain.roles import AppPermission
from app.filters.reviewer import LoanExceptionFilter
from app.mixins import AnyPermissionRequiredMixin
from app.models import AuditEvent, LoanException, ValidationSeverity

ALLOWED_FIELDS = [
    "loan_id",
    "borrower_id",
    "borrower_name",
    "original_balance",
    "original_principal",
    "current_balance",
    "interest_rate",
    "loan_term",
    "origination_date",
    "maturity_date",
    "payment_status",
    "days_past_due",
    "property_state",
    "state",
    "borrower_state",
    "credit_score",
    "document_status",
    "last_updated_at",
    "as_of_date",
]


class ReviewerDashboardView(LoginRequiredMixin, AnyPermissionRequiredMixin, View):
    """
    Class-Based View for Reviewer Workspace Main Dashboard (`/reviewer/`).

    Restricted strictly to users with REVIEWER_CAN_INSPECT_EXCEPTIONS permission.
    Renders full Reviewer workspace index with header, metrics summary cards, tab navigation,
    and initial Loan Exceptions Queue tab page 1.
    """

    permissions_required = [AppPermission.REVIEWER_CAN_INSPECT_EXCEPTIONS]

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        user = request.user
        if not user.is_authenticated:
            return self.handle_no_permission()

        # Enforce strict Role Permission Check for Reviewer
        if not (
            user.is_superuser
            or user.is_reviewer
            or user.has_category_perm(AppPermission.REVIEWER_CAN_INSPECT_EXCEPTIONS)
        ):
            raise PermissionDenied(
                "Access Denied: Only Reviewers can access the Exception Queue workspace."
            )
        return super().dispatch(request, *args, **kwargs)

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        user = request.user
        all_exceptions = LoanException.objects.all()

        base_qs = LoanException.objects.select_related(
            "batch", "raw_record", "rule", "resolved_by"
        ).order_by("-severity", "-created")

        exc_filter = LoanExceptionFilter(request.GET, queryset=base_qs)
        paginator = Paginator(exc_filter.qs, 10)
        exceptions_page = paginator.get_page(request.GET.get("page", 1))

        total_exceptions = all_exceptions.count()
        open_exceptions = all_exceptions.filter(status=LoanException.ExceptionStatus.OPEN).count()
        under_review_exceptions = all_exceptions.filter(
            status=LoanException.ExceptionStatus.UNDER_REVIEW
        ).count()
        resolved_exceptions = all_exceptions.filter(
            status__in=[
                LoanException.ExceptionStatus.RESOLVED_ACCEPTED,
                LoanException.ExceptionStatus.RESOLVED_EDITED,
            ]
        ).count()
        rejected_exceptions = all_exceptions.filter(
            status__in=[LoanException.ExceptionStatus.REJECTED]
        ).count()

        # Severity breakdown metrics
        critical_count = all_exceptions.filter(severity=ValidationSeverity.CRITICAL).count()
        high_count = all_exceptions.filter(severity=ValidationSeverity.HIGH).count()
        medium_count = all_exceptions.filter(severity=ValidationSeverity.MEDIUM).count()
        low_count = all_exceptions.filter(severity=ValidationSeverity.LOW).count()

        context = {
            "title": "Reviewer Workspace - Loan Exception Queue",
            "user": user,
            "filter": exc_filter,
            "exceptions_page": exceptions_page,
            "page_obj": exceptions_page,
            "exceptions": exceptions_page.object_list,
            "total_exceptions": total_exceptions,
            "open_exceptions": open_exceptions,
            "under_review_exceptions": under_review_exceptions,
            "resolved_exceptions": resolved_exceptions,
            "rejected_exceptions": rejected_exceptions,
            "critical_count": critical_count,
            "high_count": high_count,
            "medium_count": medium_count,
            "low_count": low_count,
            "severity_choices": ValidationSeverity.choices,
            "status_choices": LoanException.ExceptionStatus.choices,
            "current_severity": request.GET.get("severity", ""),
            "current_status": request.GET.get("status", ""),
            "search_query": request.GET.get("q", ""),
            "tab_visible": True,
        }
        return render(request, "dashboard/reviewer/index.html", context)


class LoanExceptionListView(LoginRequiredMixin, AnyPermissionRequiredMixin, View):
    """
    Class-Based View for Loan Exception Queue List endpoint (`/reviewer/exceptions/`).

    Provides HTMX-driven pagination, search, and severity/status filtering
    rendering `dashboard/reviewer/includes/exceptions_tab.html`.
    """

    permissions_required = [AppPermission.REVIEWER_CAN_INSPECT_EXCEPTIONS]

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        user = request.user
        if not user.is_authenticated:
            return self.handle_no_permission()

        # Enforce strict Role Permission Check for Reviewer
        if not (
            user.is_superuser
            or user.is_reviewer
            or user.has_category_perm(AppPermission.REVIEWER_CAN_INSPECT_EXCEPTIONS)
        ):
            raise PermissionDenied(
                "Access Denied: Only Reviewers can access the Exception Queue workspace."
            )
        return super().dispatch(request, *args, **kwargs)

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        base_qs = LoanException.objects.select_related(
            "batch", "raw_record", "rule", "resolved_by"
        ).order_by("-severity", "-created")

        exc_filter = LoanExceptionFilter(request.GET, queryset=base_qs)
        paginator = Paginator(exc_filter.qs, 10)
        exceptions_page = paginator.get_page(request.GET.get("page", 1))

        context = {
            "filter": exc_filter,
            "exceptions_page": exceptions_page,
            "page_obj": exceptions_page,
            "exceptions": exceptions_page.object_list,
            "severity_choices": ValidationSeverity.choices,
            "status_choices": LoanException.ExceptionStatus.choices,
            "tab_visible": True,
        }
        return render(request, "dashboard/reviewer/includes/exceptions_tab.html", context)


class ExceptionLoanDetailView(LoginRequiredMixin, AnyPermissionRequiredMixin, View):
    """
    Class-Based View for Exception Detail View (`/reviewer/exceptions/<int:pk>/detail/`).

    Consolidates exception details inspection, review comments, decision status transitions
    (Approve, Reject, Correct), and editing allowed loan fields into a single unified view.
    """

    permissions_required = [AppPermission.REVIEWER_CAN_INSPECT_EXCEPTIONS]
    ALLOWED_FIELDS = ALLOWED_FIELDS

    def get(self, request: HttpRequest, pk: int, *args: Any, **kwargs: Any) -> HttpResponse:
        loan_exception = get_object_or_404(
            LoanException.objects.select_related("batch", "raw_record", "rule", "resolved_by"),
            pk=pk,
        )
        raw_data = (
            loan_exception.raw_record.raw_data
            if (loan_exception.raw_record and loan_exception.raw_record.raw_data)
            else {}
        )

        field_list = []
        for field in self.ALLOWED_FIELDS:
            field_list.append(
                {
                    "key": field,
                    "label": field.replace("_", " ").title(),
                    "value": raw_data.get(field, ""),
                    "is_target": (field == loan_exception.field_name),
                }
            )

        related_exceptions = (
            LoanException.objects.filter(raw_record=loan_exception.raw_record)
            .exclude(pk=loan_exception.pk)
            .order_by("-severity")
        )

        context = {
            "title": f"Loan Exception - #EXP-{loan_exception.id}",
            "exc": loan_exception,
            "raw_data": raw_data,
            "field_list": field_list,
            "related_exceptions": related_exceptions,
            "user": request.user,
        }
        return render(request, "dashboard/reviewer/detail/exception_detail.html", context)

    def post(self, request: HttpRequest, pk: int, *args: Any, **kwargs: Any) -> HttpResponse:
        from app.domain.exception_handling import handle_exception_action

        loan_exception = get_object_or_404(
            LoanException.objects.select_related("raw_record"), pk=pk
        )
        action_type = request.POST.get("action_type", "").strip()

        success, message, redirect_target = handle_exception_action(
            loan_exception=loan_exception,
            actor=request.user,
            action_type=action_type,
            post_data=request.POST,
        )

        if success:
            messages.success(request, message)
        else:
            messages.error(request, message)

        if redirect_target == "reviewer_dashboard":
            return redirect("reviewer_dashboard")
        return redirect("exception_loan_detail", pk=loan_exception.id)


class ExceptionActionHistoryView(LoginRequiredMixin, AnyPermissionRequiredMixin, View):
    """
    Class-Based View for Option 2 in Exception Queue Dropdown:
    Track Action History (`/reviewer/exceptions/<int:pk>/history/`).
    """

    permissions_required = [AppPermission.REVIEWER_CAN_INSPECT_EXCEPTIONS]

    def get(self, request: HttpRequest, pk: int, *args: Any, **kwargs: Any) -> HttpResponse:
        excepetion = get_object_or_404(
            LoanException.objects.select_related("batch", "raw_record", "rule", "resolved_by"),
            pk=pk,
        )
        loan_id = excepetion.loan_id

        qs = AuditEvent.objects.select_related("actor").order_by("-timestamp")
        if loan_id:
            audit_events = qs.filter(Q(loan_id=loan_id) | Q(payload__exception_id=excepetion.id))
        else:
            audit_events = qs.filter(payload__exception_id=excepetion.id)

        context = {
            "title": f"Action History - #EXP-{excepetion.id}",
            "exc": excepetion,
            "audit_events": audit_events,
            "user": request.user,
        }
        return render(request, "dashboard/reviewer/detail/exception_history.html", context)
