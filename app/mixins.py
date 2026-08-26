from django.contrib.auth.mixins import UserPassesTestMixin
from django.core.exceptions import PermissionDenied

from app.models.user import User


class CategoryRequiredMixin(UserPassesTestMixin):
    """
    Mixin to enforce role-based access for specific user categories.
    Superusers are explicitly restricted from bypassing category checks
    because each category role carries at least one functional restriction.
    """

    allowed_categories = []

    def get_allowed_categories(self):
        return self.allowed_categories

    def test_func(self) -> bool:
        user = self.request.user
        if not user.is_authenticated:
            return False
        if user.is_superuser:
            return False
        return user.category in self.get_allowed_categories()

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            raise PermissionDenied("User is not authorized for this category role.")
        return super().handle_no_permission()


class DataOperatorRequiredMixin(CategoryRequiredMixin):
    """Restricts view access strictly to Data Operator category users."""

    allowed_categories = [User.Category.DATA_OPERATOR]


class ReviewerRequiredMixin(CategoryRequiredMixin):
    """Restricts view access strictly to Reviewer category users."""

    allowed_categories = [User.Category.REVIEWER]


class DataConsumerRequiredMixin(CategoryRequiredMixin):
    """Restricts view access strictly to Data Consumer category users."""

    allowed_categories = [User.Category.DATA_CONSUMER]
