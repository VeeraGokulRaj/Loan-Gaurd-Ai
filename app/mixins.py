from django.contrib.auth.mixins import UserPassesTestMixin
from django.core.exceptions import PermissionDenied


class AnyPermissionRequiredMixin(UserPassesTestMixin):
    """
    Simba-style mixin: Checks if user possesses ANY of the required category permissions.
    Superusers are explicitly restricted from bypassing category checks
    because each category role carries at least one functional restriction.
    """

    permissions_required = []

    def get_permissions_required(self):
        return self.permissions_required

    def test_func(self) -> bool:
        user = self.request.user
        if not user.is_authenticated or user.is_superuser:
            return False
        perms = self.get_permissions_required()
        return any(user.has_category_perm(perm) for perm in perms)

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            raise PermissionDenied("User does not possess the required category permissions.")
        return super().handle_no_permission()
