"""
Data Consumer Views for LoanGuard AI.

Provides Class-Based Views (CBV) for Data Consumer workspace using AnyPermissionRequiredMixin.
"""

from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views import View

from app.domain.roles import AppPermission
from app.mixins import AnyPermissionRequiredMixin


class DataConsumerDashboardView(AnyPermissionRequiredMixin, View):
    """
    Class-Based View for Data Consumer Workspace (Category 3).
    """

    permissions_required = [AppPermission.DATA_CONSUMER_CAN_VIEW_VERIFIED_TAPE]

    def get(self, request):
        context = {
            "title": _("Data Consumer Workspace - LoanGuard AI"),
            "user": request.user,
            "role_name": _("Data Consumer"),
        }
        return render(request, "dashboard/placeholder_consumer.html", context)
