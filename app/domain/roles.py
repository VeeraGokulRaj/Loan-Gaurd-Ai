from django.db.models import TextChoices

from app.models.user import User


class AppPermission(TextChoices):
    # --- Data Operator Permissions ---
    DATA_OPERATOR_CAN_UPLOAD_CSV = (
        "data_operator:can_upload_csv",
        "Can upload raw CSV files (loan_tape, servicer_update, document_manifest)",
    )
    DATA_OPERATOR_CAN_VIEW_INGESTION_SUMMARY = (
        "data_operator:can_view_ingestion_summary",
        "Can view ingestion summary and failed import rows",
    )
    DATA_OPERATOR_CAN_TRIGGER_VALIDATION = (
        "data_operator:can_trigger_validation",
        "Can trigger validation engine",
    )

    # --- Reviewer Permissions ---
    REVIEWER_CAN_INSPECT_EXCEPTIONS = (
        "reviewer:can_inspect_exceptions",
        "Can inspect exception queue and filter by severity",
    )
    REVIEWER_CAN_TRIGGER_AI_COPILOT = (
        "reviewer:can_trigger_ai_copilot",
        "Can trigger AI Copilot for explanations and suggestions",
    )
    REVIEWER_CAN_MANAGE_AI_SUGGESTIONS = (
        "reviewer:can_manage_ai_suggestions",
        "Can accept, reject, or edit AI suggestions",
    )
    REVIEWER_CAN_EDIT_FIELDS_AND_COMMENT = (
        "reviewer:can_edit_fields_and_comment",
        "Can edit allowed loan fields and add comments",
    )
    REVIEWER_CAN_APPROVE_REJECT_RECORDS = (
        "reviewer:can_approve_reject_records",
        "Can approve or reject loan records and create verified records",
    )

    # --- Data Consumer Permissions ---
    DATA_CONSUMER_CAN_VIEW_VERIFIED_TAPE = (
        "data_consumer:can_view_verified_tape",
        "Can view verified loan tape & data quality score meter",
    )
    DATA_CONSUMER_CAN_INSPECT_AUDIT_TRAIL = (
        "data_consumer:can_inspect_audit_trail",
        "Can inspect complete audit trail and SHA-256 hashes",
    )
    DATA_CONSUMER_CAN_EXPORT_DATASET = (
        "data_consumer:can_export_dataset",
        "Can export verified dataset (CSV/JSON) & query REST APIs",
    )


# Mapping of Category choices to dynamic AppPermission lists
ROLE_PERMISSIONS = {
    User.Category.DATA_OPERATOR: [
        AppPermission.DATA_OPERATOR_CAN_UPLOAD_CSV,
        AppPermission.DATA_OPERATOR_CAN_VIEW_INGESTION_SUMMARY,
        AppPermission.DATA_OPERATOR_CAN_TRIGGER_VALIDATION,
    ],
    User.Category.REVIEWER: [
        AppPermission.REVIEWER_CAN_INSPECT_EXCEPTIONS,
        AppPermission.REVIEWER_CAN_TRIGGER_AI_COPILOT,
        AppPermission.REVIEWER_CAN_MANAGE_AI_SUGGESTIONS,
        AppPermission.REVIEWER_CAN_EDIT_FIELDS_AND_COMMENT,
        AppPermission.REVIEWER_CAN_APPROVE_REJECT_RECORDS,
    ],
    User.Category.DATA_CONSUMER: [
        AppPermission.DATA_CONSUMER_CAN_VIEW_VERIFIED_TAPE,
        AppPermission.DATA_CONSUMER_CAN_INSPECT_AUDIT_TRAIL,
        AppPermission.DATA_CONSUMER_CAN_EXPORT_DATASET,
    ],
}


def get_all_permissions_for_category(category: int) -> list[str]:
    """Returns all string permission codenames assigned to a given Category choice."""
    perms = ROLE_PERMISSIONS.get(category, [])
    return [perm.value for perm in perms]


def sync_category_permissions() -> int:
    """
    Clears legacy static user_permissions rows for category users
    to enforce dynamic AppPermission and ROLE_PERMISSIONS resolution.
    """
    covered_codenames = AppPermission.values
    deleted_count, _ = User.user_permissions.through.objects.filter(
        user__category__in=[
            User.Category.DATA_OPERATOR,
            User.Category.REVIEWER,
            User.Category.DATA_CONSUMER,
        ],
        permission__codename__in=covered_codenames,
    ).delete()
    return deleted_count
