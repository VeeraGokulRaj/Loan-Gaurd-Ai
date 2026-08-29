"""
Domain logic for handling loan exception reviews, field edits, and decision resolutions.
"""

from typing import Any

from django.db import transaction
from django.utils import timezone

from app.models import AuditEvent, LoanException
from app.views.reviewer import ALLOWED_FIELDS


def handle_exception_action(
    loan_exception: LoanException,
    actor: Any,
    action_type: str,
    post_data: Any,
) -> tuple[bool, str, str]:
    """
    Unified domain handler for processing Loan Exception POST actions.

    Returns:
        Tuple of (success: bool, message: str, redirect_target: str).
    """
    allowed_fields = ALLOWED_FIELDS
    comment = post_data.get("reviewer_comment", "").strip()

    if action_type == "save_comment":
        save_reviewer_comment(loan_exception, actor, comment)
        return (
            True,
            f"Exception #EXP-{loan_exception.id} updated successfully.",
            "exception_loan_detail",
        )

    elif action_type == "save_fields":
        success, err_msg = save_exception_field_edits(
            loan_exception=loan_exception,
            actor=actor,
            post_data=post_data,
            allowed_fields=allowed_fields,
            comment=comment,
        )
        if not success:
            return (
                False,
                err_msg or f"Exception #EXP-{loan_exception.id} update failed.",
                "exception_loan_detail",
            )
        return (
            True,
            f"Exception #EXP-{loan_exception.id} updated successfully.",
            "exception_loan_detail",
        )

    elif action_type == "resolve_decision":
        decision = post_data.get("decision", "").strip()
        override_val = post_data.get("override_value", "").strip()
        is_handled = resolve_exception_decision(
            loan_exception=loan_exception,
            actor=actor,
            decision=decision,
            comment=comment,
            override_val=override_val,
        )
        if is_handled:
            return (
                True,
                f"Exception #EXP-{loan_exception.id} updated successfully.",
                "reviewer_dashboard",
            )
        return (
            False,
            f"Invalid decision '{decision}' for Exception #EXP-{loan_exception.id}.",
            "exception_loan_detail",
        )

    return False, f"Invalid action for Exception #EXP-{loan_exception.id}.", "exception_loan_detail"


def save_reviewer_comment(
    loan_exception: LoanException,
    actor: Any,
    comment: str,
) -> bool:
    """
    Saves a reviewer comment on an exception without changing its status.
    """
    comment_clean = comment.strip()
    if not comment_clean:
        return False

    loan_exception.reviewer_comment = comment_clean
    loan_exception.save()

    log_exception_audit_event(
        event_type="REVIEWER_COMMENT_ADDED",
        actor=actor,
        loan_exception=loan_exception,
        payload={"exception_id": loan_exception.id, "comment": comment_clean},
    )
    return True


def log_exception_audit_event(
    event_type: str,
    actor: Any,
    loan_exception: LoanException,
    payload: dict[str, Any],
) -> None:
    """
    Helper to log audit events for loan exception actions consistently.
    """
    AuditEvent.log_event(
        event_type=event_type,
        actor=actor,
        actor_role=AuditEvent.ActorRole.REVIEWER,
        loan_id=loan_exception.loan_id,
        batch_id=loan_exception.batch_id,
        payload=payload,
    )


@transaction.atomic
def save_exception_field_edits(
    loan_exception: LoanException,
    actor: Any,
    post_data: Any,
    allowed_fields: list[str],
    comment: str | None = None,
) -> tuple[bool, str | None]:
    """
    Updates allowed raw record fields for an exception based on submitted form data.

    Returns:
        Tuple of (success: bool, error_message: Optional[str]).
    """
    if not loan_exception.raw_record:
        return False, f"Exception #EXP-{loan_exception.id} has no associated raw record."

    raw_data = loan_exception.raw_record.raw_data or {}
    edited_fields: dict[str, dict[str, str]] = {}

    for field in allowed_fields:
        if field in post_data:
            new_val = str(post_data.get(field, "")).strip()
            old_val = str(raw_data.get(field, ""))
            if new_val != old_val:
                edited_fields[field] = {"old": old_val, "new": new_val}
                raw_data[field] = new_val

    comment_clean = comment.strip() if comment else ""

    if edited_fields:
        loan_exception.raw_record.raw_data = raw_data
        loan_exception.raw_record.save()

        if loan_exception.field_name in edited_fields:
            loan_exception.override_value = edited_fields[loan_exception.field_name]["new"]

        apply_exception_resolution(
            loan_exception=loan_exception,
            actor=actor,
            status=LoanException.ExceptionStatus.RESOLVED_EDITED,
            comment=comment_clean,
        )

        log_exception_audit_event(
            event_type="LOAN_RECORD_FIELD_EDITED",
            actor=actor,
            loan_exception=loan_exception,
            payload={
                "exception_id": loan_exception.id,
                "edits": edited_fields,
                "comment": comment_clean,
            },
        )

    return True, None


def apply_exception_resolution(
    loan_exception: LoanException,
    actor: Any,
    status: LoanException.ExceptionStatus,
    comment: str | None = None,
) -> None:
    """
    Applies resolution metadata (status, resolved_by, resolved_at, optional comment) to an exception.
    """
    loan_exception.status = status
    loan_exception.resolved_by = actor
    loan_exception.resolved_at = timezone.now()
    if comment and comment.strip():
        loan_exception.reviewer_comment = comment.strip()
    loan_exception.save()


@transaction.atomic
def resolve_exception_decision(
    loan_exception: LoanException,
    actor: Any,
    decision: str,
    comment: str | None = None,
    override_val: str | None = None,
) -> bool:
    """
    Resolves an exception with a decision ('approve', 'reject', or 'correct').

    Returns:
        True if the decision was processed successfully, False otherwise.
    """
    comment_clean = comment.strip() if comment else ""
    override_clean = override_val.strip() if override_val else ""

    if decision == "approve":
        apply_exception_resolution(
            loan_exception=loan_exception,
            actor=actor,
            status=LoanException.ExceptionStatus.RESOLVED_ACCEPTED,
            comment=comment_clean,
        )
        log_exception_audit_event(
            event_type="LOAN_APPROVED",
            actor=actor,
            loan_exception=loan_exception,
            payload={
                "exception_id": loan_exception.id,
                "decision": "APPROVED",
                "comment": comment_clean,
            },
        )
        return True

    elif decision == "reject":
        apply_exception_resolution(
            loan_exception=loan_exception,
            actor=actor,
            status=LoanException.ExceptionStatus.REJECTED,
            comment=comment_clean,
        )
        log_exception_audit_event(
            event_type="LOAN_REJECTED",
            actor=actor,
            loan_exception=loan_exception,
            payload={
                "exception_id": loan_exception.id,
                "decision": "REJECTED",
                "comment": comment_clean,
            },
        )
        return True

    elif decision == "correct":
        loan_exception.override_value = override_clean

        if loan_exception.raw_record and loan_exception.field_name:
            raw_data = loan_exception.raw_record.raw_data or {}
            raw_data[loan_exception.field_name] = override_clean
            loan_exception.raw_record.raw_data = raw_data
            loan_exception.raw_record.save()

        apply_exception_resolution(
            loan_exception=loan_exception,
            actor=actor,
            status=LoanException.ExceptionStatus.RESOLVED_EDITED,
            comment=comment_clean,
        )

        log_exception_audit_event(
            event_type="EXCEPTION_RESOLVED_EDITED",
            actor=actor,
            loan_exception=loan_exception,
            payload={
                "exception_id": loan_exception.id,
                "field": loan_exception.field_name,
                "override_value": override_clean,
                "comment": comment_clean,
            },
        )
        return True

    return False
