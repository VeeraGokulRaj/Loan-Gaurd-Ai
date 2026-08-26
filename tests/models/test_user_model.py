import pytest
from django.core.exceptions import ValidationError
from django.test import RequestFactory

from app.mixins import (
    DataConsumerRequiredMixin,
    DataOperatorRequiredMixin,
    ReviewerRequiredMixin,
)
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

    def test_create_category_users(self):
        operator = UserFactory.create_data_operator()
        self.assert_user_has_category_role(operator, User.Category.DATA_OPERATOR)
        assert operator.is_data_operator is True
        assert operator.is_reviewer is False
        assert operator.is_data_consumer is False

        reviewer = UserFactory.create_reviewer()
        self.assert_user_has_category_role(reviewer, User.Category.REVIEWER)
        assert reviewer.is_reviewer is True
        assert reviewer.is_data_operator is False

        consumer = UserFactory.create_data_consumer()
        self.assert_user_has_category_role(consumer, User.Category.DATA_CONSUMER)
        assert consumer.is_data_consumer is True

    def test_superuser_cannot_be_assigned_category(self):
        superuser = UserFactory.create_superuser()
        self.assert_superuser_has_no_category(superuser)

        superuser.category = User.Category.DATA_OPERATOR
        with pytest.raises(ValidationError) as exc_info:
            superuser.full_clean()
        assert "Superusers cannot be assigned to any of the 3 user categories." in str(
            exc_info.value
        )

    def test_superuser_role_properties_always_false(self):
        superuser = User(username="admin", is_superuser=True, category=User.Category.DATA_OPERATOR)
        assert superuser.is_data_operator is False
        assert superuser.is_reviewer is False
        assert superuser.is_data_consumer is False

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

    def test_role_based_restriction_mixins(self):
        factory = RequestFactory()
        request = factory.get("/")

        operator = UserFactory.create_data_operator()
        reviewer = UserFactory.create_reviewer()
        superuser = UserFactory.create_superuser()

        # Data Operator Mixin check
        op_mixin = DataOperatorRequiredMixin()
        request.user = operator
        op_mixin.request = request
        assert op_mixin.test_func() is True

        request.user = reviewer
        op_mixin.request = request
        assert op_mixin.test_func() is False

        request.user = superuser
        op_mixin.request = request
        assert op_mixin.test_func() is False

        # Reviewer Mixin check
        rev_mixin = ReviewerRequiredMixin()
        request.user = reviewer
        rev_mixin.request = request
        assert rev_mixin.test_func() is True

        request.user = superuser
        rev_mixin.request = request
        assert rev_mixin.test_func() is False

        # Data Consumer Mixin check
        consumer = UserFactory.create_data_consumer()
        con_mixin = DataConsumerRequiredMixin()
        request.user = consumer
        con_mixin.request = request
        assert con_mixin.test_func() is True
