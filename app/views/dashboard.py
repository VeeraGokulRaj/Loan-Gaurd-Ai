"""
Dashboard Views & Role Routing Engine for LoanGuard AI.

Implements single method-based dashboard entrypoint `/` that routes users based on their logged-in
role category (Data Operator, Reviewer, Data Consumer). Explicitly blocks superusers.
"""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext_lazy as _

from app.models import User
from app.views.data_consumer import DataConsumerDashboardView
from app.views.data_operator import OperatorDashboardView
from app.views.reviewer import ReviewerDashboardView


@login_required(login_url="login")
def dashboard_view(request):
    """
    Single Dashboard Router Method-Based View (`/`).

    Blocks superusers explicitly and routes authenticated users based on their role category:
    - DATA_OPERATOR (Category 1) -> OperatorDashboardView (CBV in data_operator.py)
    - REVIEWER (Category 2) -> ReviewerDashboardView (CBV in reviewer.py)
    - DATA_CONSUMER (Category 3) -> DataConsumerDashboardView (CBV in data_consumer.py)
    """
    user: User = request.user

    # Explicitly block superusers from accessing dashboard
    if user.is_superuser:
        raise PermissionDenied(
            _("Superusers are not permitted to access the operational dashboard.")
        )

    # Route based on user role category
    if user.is_reviewer:
        return ReviewerDashboardView.as_view()(request)

    if user.is_data_consumer:
        return DataConsumerDashboardView.as_view()(request)

    if user.is_data_operator:
        return OperatorDashboardView.as_view()(request)

    raise PermissionDenied(_("User does not belong to a valid role category."))
