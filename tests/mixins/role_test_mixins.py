from app.domain.roles import get_all_permissions_for_category
from app.models.user import User


class RoleTestCaseMixin:
    """Helper test mixin to verify role-based access restrictions and permissions."""

    def assert_user_has_category_role(self, user: User, expected_category: int):
        assert user.category == expected_category
        assert not user.is_superuser
        expected_perms = get_all_permissions_for_category(expected_category)
        assert set(user.get_category_permissions()) == set(expected_perms)

    def assert_superuser_has_no_category(self, user: User):
        assert user.is_superuser
        assert user.category is None
        assert not user.is_data_operator
        assert not user.is_reviewer
        assert not user.is_data_consumer
        assert user.get_category_permissions() == []
