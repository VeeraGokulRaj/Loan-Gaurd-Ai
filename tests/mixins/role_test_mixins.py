from app.models.user import User


class RoleTestCaseMixin:
    """Helper test mixin to verify role-based access restrictions."""

    def assert_user_has_category_role(self, user: User, expected_category: int):
        assert user.category == expected_category
        assert not user.is_superuser

    def assert_superuser_has_no_category(self, user: User):
        assert user.is_superuser
        assert user.category is None
        assert not user.is_data_operator
        assert not user.is_reviewer
        assert not user.is_data_consumer
