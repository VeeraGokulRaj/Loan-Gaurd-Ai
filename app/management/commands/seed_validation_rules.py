"""
Management command to seed configurable validation rules from validation.json into the database.

Usage:
    python manage.py seed_validation_rules
    python manage.py seed_validation_rules --file path/to/custom_validation.json
"""

from django.core.management.base import BaseCommand, CommandError

from app.domain.validation_service import ValidationRuleJsonService


class Command(BaseCommand):
    help = (
        "Seeds configurable validation rules from validation.json into the ValidationRule DB model."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            help="Custom path to validation rules JSON file.",
        )

    def handle(self, *args, **options):
        file_path = options.get("file")
        self.stdout.write(self.style.NOTICE("Parsing and validating rules JSON payload..."))

        valid_rules, errors = ValidationRuleJsonService.load_and_validate_json(file_path)

        if errors:
            self.stderr.write(self.style.ERROR("Validation failed for validation rules JSON:"))
            for err in errors:
                self.stderr.write(self.style.ERROR(f"  - {err}"))
            raise CommandError("Failed to seed validation rules due to JSON validation errors.")

        created_count, updated_count = ValidationRuleJsonService.seed_database(valid_rules)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully processed validation rules! Created: {created_count}, Updated: {updated_count}."
            )
        )
