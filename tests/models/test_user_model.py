import pytest
from django.core.exceptions import ValidationError
from django.test import RequestFactory

from app.domain.roles import ROLE_PERMISSIONS, AppPermission
from app.mixins import AnyPermissionRequiredMixin
from app.models.user import User
from tests.factory.user_factory import UserFactory
from tests.mixins.role_test_mixins import RoleTestCaseMixin


@pytest.mark.django_db
class TestUserModel(RoleTestCaseMixin):
    def test_user_inherits_abstract_user_and_base_model(self):
        user = UserFactory.create_user(username="base_test")
        assert isinstance(user, User)
        assert hasattr(user, "created")
        assert hasattr(user, "modified")
        assert hasattr(user, "history")

    def test_category_integer_choices(self):
        assert User.Category.DATA_OPERATOR == 1
        assert User.Category.REVIEWER == 2
        assert User.Category.DATA_CONSUMER == 3

        assert User.Category.DATA_OPERATOR.label == "Data Operator"
        assert User.Category.REVIEWER.label == "Reviewer"
        assert User.Category.DATA_CONSUMER.label == "Data Consumer"

    def test_app_permissions_mapping(self):
        # Data Operator Permissions
        op_perms = ROLE_PERMISSIONS[User.Category.DATA_OPERATOR]
        assert AppPermission.DATA_OPERATOR_CAN_UPLOAD_CSV in op_perms
        assert AppPermission.DATA_OPERATOR_CAN_VIEW_INGESTION_SUMMARY in op_perms
        assert AppPermission.DATA_OPERATOR_CAN_TRIGGER_VALIDATION in op_perms

        # Reviewer Permissions
        rev_perms = ROLE_PERMISSIONS[User.Category.REVIEWER]
        assert AppPermission.REVIEWER_CAN_INSPECT_EXCEPTIONS in rev_perms
        assert AppPermission.REVIEWER_CAN_TRIGGER_AI_COPILOT in rev_perms
        assert AppPermission.REVIEWER_CAN_MANAGE_AI_SUGGESTIONS in rev_perms
        assert AppPermission.REVIEWER_CAN_EDIT_FIELDS_AND_COMMENT in rev_perms
        assert AppPermission.REVIEWER_CAN_APPROVE_REJECT_RECORDS in rev_perms

        # Data Consumer Permissions
        con_perms = ROLE_PERMISSIONS[User.Category.DATA_CONSUMER]
        assert AppPermission.DATA_CONSUMER_CAN_VIEW_VERIFIED_TAPE in con_perms
        assert AppPermission.DATA_CONSUMER_CAN_INSPECT_AUDIT_TRAIL in con_perms
        assert AppPermission.DATA_CONSUMER_CAN_EXPORT_DATASET in con_perms

    def test_create_category_users_and_dynamic_permissions(self):
        operator = UserFactory.create_data_operator()
        self.assert_user_has_category_role(operator, User.Category.DATA_OPERATOR)
        assert operator.is_data_operator is True
        assert operator.has_category_perm(AppPermission.DATA_OPERATOR_CAN_UPLOAD_CSV.value) is True
        assert (
            operator.has_category_perm(AppPermission.REVIEWER_CAN_INSPECT_EXCEPTIONS.value) is False
        )

        reviewer = UserFactory.create_reviewer()
        self.assert_user_has_category_role(reviewer, User.Category.REVIEWER)
        assert reviewer.is_reviewer is True
        assert (
            reviewer.has_category_perm(AppPermission.REVIEWER_CAN_TRIGGER_AI_COPILOT.value) is True
        )
        assert reviewer.has_category_perm(AppPermission.DATA_OPERATOR_CAN_UPLOAD_CSV.value) is False

        consumer = UserFactory.create_data_consumer()
        self.assert_user_has_category_role(consumer, User.Category.DATA_CONSUMER)
        assert consumer.is_data_consumer is True
        assert (
            consumer.has_category_perm(AppPermission.DATA_CONSUMER_CAN_EXPORT_DATASET.value) is True
        )

    def test_superuser_cannot_be_assigned_category(self):
        superuser = UserFactory.create_superuser()
        self.assert_superuser_has_no_category(superuser)

        superuser.category = User.Category.DATA_OPERATOR
        with pytest.raises(ValidationError) as exc_info:
            superuser.full_clean()
        assert "Superusers cannot be assigned to any of the 3 user categories." in str(
            exc_info.value
        )

    def test_superuser_role_properties_and_permissions_always_false(self):
        superuser = User(username="admin", is_superuser=True, category=User.Category.DATA_OPERATOR)
        assert superuser.is_data_operator is False
        assert superuser.is_reviewer is False
        assert superuser.is_data_consumer is False
        assert superuser.get_category_permissions() == []
        assert (
            superuser.has_category_perm(AppPermission.DATA_OPERATOR_CAN_UPLOAD_CSV.value) is False
        )

    def test_user_str_representation(self):
        operator = UserFactory.create_data_operator(username="jane_op")
        assert str(operator) == "jane_op (Data Operator)"

        plain_user = UserFactory.create_user(username="plain_user")
        assert str(plain_user) == "plain_user"

    def test_fields_have_lazy_help_texts(self):
        username_field = User._meta.get_field("username")
        phone_field = User._meta.get_field("phone")
        email_field = User._meta.get_field("email")
        category_field = User._meta.get_field("category")

        assert (
            str(username_field.help_text)
            == "Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only."
        )
        assert str(phone_field.help_text) == "Contact phone number of the user."
        assert str(email_field.help_text) == "Email address of the user."
        assert (
            str(category_field.help_text)
            == "User category role (Data Operator, Reviewer, Data Consumer)."
        )

    def test_any_permission_required_mixin(self):
        factory = RequestFactory()
        request = factory.get("/")

        operator = UserFactory.create_data_operator()
        reviewer = UserFactory.create_reviewer()
        superuser = UserFactory.create_superuser()

        class DummyView(AnyPermissionRequiredMixin):
            permissions_required = [AppPermission.DATA_OPERATOR_CAN_UPLOAD_CSV.value]

        view = DummyView()

        # Operator with permission -> True
        request.user = operator
        view.request = request
        assert view.test_func() is True

        # Reviewer without operator permission -> False
        request.user = reviewer
        view.request = request
        assert view.test_func() is False

        # Superuser -> False (no superuser bypass on category permissions)
        request.user = superuser
        view.request = request
        assert view.test_func() is False
