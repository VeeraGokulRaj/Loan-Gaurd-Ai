from pathlib import Path

from django.core.management.base import BaseCommand

from app.domain.roles import sync_category_permissions
from app.domain.user_service import (
    USER_MOCK_FILE_PATH,
    UserJsonValidator,
    UserSeederService,
    get_user_mock_file_path,
)


class Command(BaseCommand):
    help = f"Seeds database with mock users from {USER_MOCK_FILE_PATH} and triggers permission synchronization"

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            default=None,
            help=f"Path to user mock JSON file (default: {USER_MOCK_FILE_PATH})",
        )

    def handle(self, *args, **options):
        custom_file = options.get("file")
        if custom_file:
            file_path = Path(custom_file)
            if not file_path.is_absolute():
                base_dir = Path(__file__).resolve().parent.parent.parent.parent
                file_path = base_dir / custom_file
        else:
            file_path = get_user_mock_file_path()

        if not file_path.exists():
            self.stderr.write(self.style.ERROR(f"Users JSON file not found at {file_path}"))
            return

        with open(file_path, encoding="utf-8") as f:
            raw_content = f.read()

        valid_users, errors = UserJsonValidator.validate_raw_json(raw_content)
        if errors:
            self.stderr.write(self.style.ERROR("Validation failed for users.json:"))
            for err in errors:
                self.stderr.write(self.style.ERROR(f"  - {err}"))
            return

        created_cnt, updated_cnt = UserSeederService.seed_database(valid_users)
        deleted_legacy_perms = sync_category_permissions()

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded {len(valid_users)} users! ({created_cnt} created, {updated_cnt} updated).\n"
                f"Permissions synchronized (cleared {deleted_legacy_perms} legacy permission overrides)."
            )
        )
