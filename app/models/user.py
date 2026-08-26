from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from .base import BaseModel


class User(AbstractUser, BaseModel):
    class Category(models.IntegerChoices):
        DATA_OPERATOR = 1, _("Data Operator")
        REVIEWER = 2, _("Reviewer")
        DATA_CONSUMER = 3, _("Data Consumer")

    username = models.CharField(
        max_length=150,
        unique=True,
        help_text=_("Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only."),
        error_messages={
            "unique": _("A user with that username already exists."),
        },
    )
    phone = models.CharField(
        max_length=20,
        blank=True,
        help_text=_("Contact phone number of the user."),
    )
    email = models.EmailField(
        blank=True,
        help_text=_("Email address of the user."),
    )
    category = models.IntegerField(
        choices=Category.choices,
        null=True,
        blank=True,
        help_text=_("User category role (Data Operator, Reviewer, Data Consumer)."),
    )

    class Meta:
        verbose_name = _("User")
        verbose_name_plural = _("Users")

    def clean(self):
        super().clean()
        if self.is_superuser and self.category is not None:
            raise ValidationError(
                _("Superusers cannot be assigned to any of the 3 user categories.")
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def is_data_operator(self) -> bool:
        return not self.is_superuser and self.category == self.Category.DATA_OPERATOR

    @property
    def is_reviewer(self) -> bool:
        return not self.is_superuser and self.category == self.Category.REVIEWER

    @property
    def is_data_consumer(self) -> bool:
        return not self.is_superuser and self.category == self.Category.DATA_CONSUMER

    def get_category_permissions(self) -> list[str]:
        """Dynamically returns permission codenames assigned to the user's Category."""
        if self.is_superuser or not self.category:
            return []
        from app.domain.roles import get_all_permissions_for_category

        return get_all_permissions_for_category(self.category)

    def has_category_perm(self, perm: str) -> bool:
        """Checks if the user possesses a specific category permission without superuser bypass."""
        if self.is_superuser or not self.category:
            return False
        return perm in self.get_category_permissions()

    def __str__(self) -> str:
        if self.category is not None:
            return f"{self.username} ({self.get_category_display()})"
        return self.username
