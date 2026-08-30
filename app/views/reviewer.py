"""
Module C: Reviewer Workspace & Exception Queue Views for LoanGuard AI.

Provides exception management dashboard workspace strictly for Reviewer role.
Split into ReviewerDashboardView (main workspace index) and LoanExceptionListView (HTMX tab/table view).
Supports HTMX pagination, filtering, search, severity badges, and AI recommendation controls.
"""

import json
from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import View

from app.domain.ai_assistant import (
    LLMProviderRegistry,
    generate_ai_rule_recommendation,
    generate_exception_ai_recommendation,
    process_ai_recommendation_decision,
)
from app.domain.roles import AppPermission
from app.filters.reviewer import LoanExceptionFilter
from app.mixins import AnyPermissionRequiredMixin
from app.models import AIRecommendation, AuditEvent, LoanException, ValidationSeverity

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

        ai_rec = (
            loan_exception.ai_recommendations.filter(
                recommendation_type=AIRecommendation.RecommendationType.EXCEPTION_REVIEW
            )
            .order_by("-created")
            .first()
        )
        current_target_value = (
            raw_data.get(loan_exception.field_name, "") if loan_exception.field_name else ""
        )

        context = {
            "title": f"Loan Exception - #EXP-{loan_exception.id}",
            "exc": loan_exception,
            "ai_rec": ai_rec,
            "raw_data": raw_data,
            "current_target_value": current_target_value,
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


class OpenAICopilotModalView(LoginRequiredMixin, AnyPermissionRequiredMixin, View):
    """
    Class-Based View for rendering the initial AI Copilot Overlay Modal (`/reviewer/ai/modal/` or `/reviewer/exceptions/<int:pk>/ai/modal/`).
    Checks if a pending AIRecommendation already exists for the exception.
    """

    permissions_required = [AppPermission.REVIEWER_CAN_INSPECT_EXCEPTIONS]

    def get(
        self, request: HttpRequest, pk: int | None = None, *args: Any, **kwargs: Any
    ) -> HttpResponse:
        loan_exception = None
        if pk:
            loan_exception = LoanException.objects.filter(pk=pk).first()

        pending_ai_rec = None
        if loan_exception:
            pending_ai_rec = (
                loan_exception.ai_recommendations.filter(
                    status=AIRecommendation.RecommendationStatus.PENDING
                )
                .order_by("-created")
                .first()
            )

        if pending_ai_rec:
            raw_data = (
                loan_exception.raw_record.raw_data
                if (
                    loan_exception
                    and loan_exception.raw_record
                    and loan_exception.raw_record.raw_data
                )
                else {}
            )
            current_target_value = (
                raw_data.get(loan_exception.field_name, "")
                if (loan_exception and loan_exception.field_name)
                else ""
            )
            target_field_name = (
                loan_exception.field_name
                if (loan_exception and loan_exception.field_name)
                else (
                    pending_ai_rec.exception.field_name
                    if (
                        pending_ai_rec
                        and pending_ai_rec.exception
                        and pending_ai_rec.exception.field_name
                    )
                    else "general"
                )
            )
            rule_json_pretty = (
                json.dumps(pending_ai_rec.suggested_rule_data or {}, indent=2)
                if (pending_ai_rec and pending_ai_rec.suggested_rule_data)
                else ""
            )
            context = {
                "exc": loan_exception,
                "ai_rec": pending_ai_rec,
                "raw_data": raw_data,
                "current_target_value": current_target_value,
                "is_pending_review_notice": True,
                "user": request.user,
                "target_field_name": target_field_name,
                "rule_data": pending_ai_rec.suggested_rule_data or {},
                "rule_json_pretty": rule_json_pretty,
            }
            return render(
                request, "dashboard/reviewer/ai_modal/ai_modal_pending_wrapper.html", context
            )

        providers = LLMProviderRegistry.list_available_providers()
        context = {
            "exc": loan_exception,
            "providers": providers,
            "user": request.user,
        }
        return render(request, "dashboard/reviewer/ai_modal/ai_modal_initial.html", context)


class GenerateAIRuleView(LoginRequiredMixin, AnyPermissionRequiredMixin, View):
    """
    Class-Based View for generating AI Validation Rules from natural language (`/reviewer/ai/rules/generate/`).
    """

    permissions_required = [AppPermission.REVIEWER_CAN_INSPECT_EXCEPTIONS]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        prompt_text = request.POST.get("prompt_text", "").strip()
        exception_id = request.POST.get("exception_id", "")
        model_choice_raw = request.POST.get("model_choice", "")

        try:
            model_choice = (
                int(model_choice_raw)
                if model_choice_raw
                else AIRecommendation.ModelProvider.OPENCODE_ZEN
            )
        except ValueError:
            model_choice = AIRecommendation.ModelProvider.OPENCODE_ZEN

        if not prompt_text:
            prompt_text = "Validate loan record for missing fields, inconsistent balances, or invalid payment statuses."

        ai_rec = generate_ai_rule_recommendation(
            prompt_text=prompt_text,
            user=request.user,
            model_choice=model_choice,
        )

        loan_exception = None
        if exception_id and exception_id.isdigit():
            loan_exception = LoanException.objects.filter(pk=int(exception_id)).first()

        rule_data = ai_rec.suggested_rule_data or {}
        rule_json_pretty = json.dumps(rule_data, indent=2)
        target_field_name = (
            loan_exception.field_name
            if loan_exception
            else (
                rule_data.get("field_name")
                if isinstance(rule_data, dict) and rule_data.get("field_name")
                else "general"
            )
        )

        context = {
            "ai_rec": ai_rec,
            "exc": loan_exception,
            "user": request.user,
            "rule_data": rule_data,
            "rule_json_pretty": rule_json_pretty,
            "target_field_name": target_field_name,
        }
        return render(request, "dashboard/reviewer/ai_modal/ai_modal_response.html", context)


class GenerateAIRecommendationView(LoginRequiredMixin, AnyPermissionRequiredMixin, View):
    """
    Class-Based View for generating AI recommendations on a Loan Exception (`/reviewer/exceptions/<int:pk>/ai/generate/`).
    """

    permissions_required = [AppPermission.REVIEWER_CAN_INSPECT_EXCEPTIONS]

    def post(self, request: HttpRequest, pk: int, *args: Any, **kwargs: Any) -> HttpResponse:
        loan_exception = get_object_or_404(
            LoanException.objects.select_related("batch", "raw_record", "rule", "resolved_by"),
            pk=pk,
        )

        model_choice_raw = request.POST.get("model_choice", "")
        try:
            model_choice = (
                int(model_choice_raw)
                if model_choice_raw
                else AIRecommendation.ModelProvider.OPENCODE_ZEN
            )
        except ValueError:
            model_choice = AIRecommendation.ModelProvider.OPENCODE_ZEN

        ai_rec = generate_exception_ai_recommendation(
            loan_exception=loan_exception,
            user=request.user,
            model_choice=model_choice,
        )

        raw_data = (
            loan_exception.raw_record.raw_data
            if (loan_exception.raw_record and loan_exception.raw_record.raw_data)
            else {}
        )
        current_target_value = (
            raw_data.get(loan_exception.field_name, "") if loan_exception.field_name else ""
        )
        target_field_name = (
            loan_exception.field_name
            if (loan_exception and loan_exception.field_name)
            else "general"
        )

        context = {
            "exc": loan_exception,
            "ai_rec": ai_rec,
            "raw_data": raw_data,
            "current_target_value": current_target_value,
            "user": request.user,
            "target_field_name": target_field_name,
        }

        if request.headers.get("HX-Request") or getattr(request, "htmx", False):
            return render(request, "dashboard/reviewer/ai_modal/ai_modal_response.html", context)

        return redirect("exception_loan_detail", pk=loan_exception.id)


class ProcessAIRecommendationView(LoginRequiredMixin, AnyPermissionRequiredMixin, View):
    """
    Class-Based View for processing reviewer decision (Accept, Edit, Reject) on AI Recommendation (`/reviewer/ai/<int:pk>/decision/`).
    Automates redirection to current page without manual save/refresh prompts.
    """

    permissions_required = [AppPermission.REVIEWER_CAN_INSPECT_EXCEPTIONS]

    def post(self, request: HttpRequest, pk: int, *args: Any, **kwargs: Any) -> HttpResponse:
        ai_rec = get_object_or_404(
            AIRecommendation.objects.select_related("exception", "rule"),
            pk=pk,
        )
        loan_exception = ai_rec.exception

        action = request.POST.get("action", "").strip().lower()
        comment = request.POST.get("reviewer_comment", "").strip()
        edited_value = request.POST.get("edited_value", "").strip()

        edited_rule_data = None
        rule_json_raw = request.POST.get("rule_json_raw", "").strip()

        if ai_rec.recommendation_type == AIRecommendation.RecommendationType.RULE_GENERATION:
            if rule_json_raw:
                try:
                    parsed_payload = json.loads(rule_json_raw)
                    if isinstance(parsed_payload, dict):
                        edited_rule_data = parsed_payload
                        orig_data = ai_rec.suggested_rule_data or {}
                        if edited_rule_data != orig_data and action in ("accept", "edit"):
                            action = "edit"
                except json.JSONDecodeError as exc_json:
                    rule_data = ai_rec.suggested_rule_data or {}
                    context = {
                        "ai_rec": ai_rec,
                        "exc": loan_exception,
                        "user": request.user,
                        "rule_data": rule_data,
                        "rule_json_pretty": rule_json_raw,
                        "json_error": f"Invalid JSON syntax: {exc_json}",
                        "target_field_name": "general",
                    }
                    return render(
                        request, "dashboard/reviewer/ai_modal/ai_modal_response.html", context
                    )
            elif action == "edit":
                rule_code = request.POST.get("rule_code", "").strip()
                rule_name = request.POST.get("rule_name", "").strip()
                field_name = request.POST.get("field_name", "").strip()
                severity = request.POST.get("severity", "2").strip()
                strategy_key = request.POST.get("strategy_key", "").strip()

                edited_rule_data = {
                    "rule_code": rule_code,
                    "rule_name": rule_name,
                    "field_name": field_name,
                    "severity": int(severity) if severity.isdigit() else 2,
                    "strategy_key": strategy_key,
                }

        success, message = process_ai_recommendation_decision(
            recommendation=ai_rec,
            action=action,
            actor=request.user,
            comment=comment,
            edited_value=edited_value,
            edited_rule_data=edited_rule_data,
        )

        if success:
            messages.success(request, message)
        else:
            messages.error(request, message)

        referer = request.META.get("HTTP_REFERER", "")
        if loan_exception and not referer:
            redirect_url = f"/reviewer/exceptions/{loan_exception.id}/detail/"
        elif referer:
            redirect_url = referer
        else:
            redirect_url = "/reviewer/"

        if request.headers.get("HX-Request") or getattr(request, "htmx", False):
            response = HttpResponse(status=204)
            response["HX-Redirect"] = redirect_url
            return response

        return redirect(redirect_url)
