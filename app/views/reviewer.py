"""
Reviewer Views for LoanGuard AI.

Provides Class-Based Views (CBV) for Reviewer workspace using AnyPermissionRequiredMixin.
"""

from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views import View

from app.domain.roles import AppPermission
from app.mixins import AnyPermissionRequiredMixin


class ReviewerDashboardView(AnyPermissionRequiredMixin, View):
    """
    Class-Based View for Reviewer Workspace (Category 2).
    """

    permissions_required = [AppPermission.REVIEWER_CAN_INSPECT_EXCEPTIONS]

    def get(self, request):
        context = {
            "title": _("Reviewer Workspace - LoanGuard AI"),
            "user": request.user,
            "role_name": _("Reviewer"),
        }
        return render(request, "dashboard/placeholder_reviewer.html", context)
