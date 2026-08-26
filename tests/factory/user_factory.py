from faker import Faker

from app.models.user import User

fake = Faker()


class UserFactory:
    """Factory helper for creating User test instances with Faker support."""

    @staticmethod
    def create_user(
        username=None,
        email=None,
        phone=None,
        category=None,
        password="password123",
        **kwargs,
    ) -> User:
        final_username = username or f"{fake.user_name()}_{fake.random_number(digits=5)}"
        final_email = email or fake.email()
        final_phone = phone or fake.phone_number()[:20]

        user = User(
            username=final_username,
            email=final_email,
            phone=final_phone,
            category=category,
            **kwargs,
        )
        user.set_password(password)
        user.save()
        return user

    @staticmethod
    def create_data_operator(
        username=None,
        email=None,
        phone=None,
        password="password123",
        **kwargs,
    ) -> User:
        return UserFactory.create_user(
            username=username,
            email=email,
            phone=phone,
            category=User.Category.DATA_OPERATOR,
            password=password,
            **kwargs,
        )

    @staticmethod
    def create_reviewer(
        username=None,
        email=None,
        phone=None,
        password="password123",
        **kwargs,
    ) -> User:
        return UserFactory.create_user(
            username=username,
            email=email,
            phone=phone,
            category=User.Category.REVIEWER,
            password=password,
            **kwargs,
        )

    @staticmethod
    def create_data_consumer(
        username=None,
        email=None,
        phone=None,
        password="password123",
        **kwargs,
    ) -> User:
        return UserFactory.create_user(
            username=username,
            email=email,
            phone=phone,
            category=User.Category.DATA_CONSUMER,
            password=password,
            **kwargs,
        )

    @staticmethod
    def create_superuser(
        username=None,
        email=None,
        password="adminpassword123",
        **kwargs,
    ) -> User:
        final_username = username or f"admin_{fake.user_name()}_{fake.random_number(digits=5)}"
        final_email = email or fake.email()

        user = User(
            username=final_username,
            email=final_email,
            is_staff=True,
            is_superuser=True,
            category=None,
            **kwargs,
        )
        user.set_password(password)
        user.save()
        return user
