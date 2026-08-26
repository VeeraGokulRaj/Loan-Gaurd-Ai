from django.core.management.base import BaseCommand

from app.domain.roles import AppPermission
from app.models import User


class Command(BaseCommand):
    help = (
        "Clears legacy static user_permissions rows for category users "
        "that are now resolved dynamically via AppPermission and ROLE_PERMISSIONS."
    )

    def handle(self, *args, **kwargs):
        covered_codenames = AppPermission.values
        category_users = User.objects.filter(
            category__in=[
                User.Category.DATA_OPERATOR,
                User.Category.REVIEWER,
                User.Category.DATA_CONSUMER,
            ],
            user_permissions__codename__in=covered_codenames,
        ).distinct()

        affected_count = category_users.count()
        deleted_count, _ = User.user_permissions.through.objects.filter(
            user__category__in=[
                User.Category.DATA_OPERATOR,
                User.Category.REVIEWER,
                User.Category.DATA_CONSUMER,
            ],
            permission__codename__in=covered_codenames,
        ).delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Sync complete: Cleared {deleted_count} legacy static permission rows "
                f"across {affected_count} category users for dynamic ROLE_PERMISSIONS resolution."
            )
        )
