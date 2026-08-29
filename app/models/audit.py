"""
Audit Event Model for LoanGuard AI.

Implements a unified, append-only AuditEvent ledger table with SHA-256 cryptographic
hash chaining for complete regulatory auditability across the entire loan lifecycle.
"""

import hashlib
import json
import uuid
from typing import Any

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .base import BaseModel


class AuditEvent(BaseModel):
    """
    Append-only audit log entry preserving cryptographic provenance and historical records.

    Captures system events, user actions (Data Operator, Reviewer, Data Consumer),
    AI copilot actions, and status mutations with SHA-256 hash chaining to ensure tamper-evidence.
    Inherits soft-deletion, timestamps, and history from BaseModel.
    """

    class ActorRole(models.IntegerChoices):
        SYSTEM = 1, _("System Engine")
        DATA_OPERATOR = 2, _("Data Operator")
        REVIEWER = 3, _("Reviewer")
        AI_COPILOT = 4, _("AI Copilot")
        DATA_CONSUMER = 5, _("Data Consumer")

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text=_("Unique UUID primary key for the audit event record."),
    )
    timestamp = models.DateTimeField(
        default=timezone.now,
        db_index=True,
        null=True,
        blank=True,
        help_text=_("Timestamp when the audit event occurred."),
    )
    loan_id = models.CharField(
        max_length=100,
        db_index=True,
        null=True,
        blank=True,
        help_text=_("Associated loan ID for traceability across downstream models."),
    )
    batch_id = models.IntegerField(
        null=True,
        blank=True,
        help_text=_("Associated upload batch ID for file lineage tracking."),
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
        help_text=_("User account responsible for triggering the audit event."),
    )
    actor_role = models.IntegerField(
        choices=ActorRole.choices,
        default=ActorRole.SYSTEM,
        null=True,
        blank=True,
        help_text=_(
            "Role categorization of the event trigger (System, Operator, Reviewer, AI Copilot, Consumer)."
        ),
    )
    event_type = models.CharField(
        max_length=100,
        db_index=True,
        null=True,
        blank=True,
        help_text=_(
            "Categorical identifier for the audit event (e.g. FILE_UPLOADED, EXCEPTION_RESOLVED)."
        ),
    )
    payload = models.JSONField(
        default=dict,
        null=True,
        blank=True,
        help_text=_(
            "Structured JSON payload containing event context, metadata, and state snapshots."
        ),
    )
    prev_hash = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text=_(
            "SHA-256 hash of the immediately preceding audit event record for chain integrity."
        ),
    )
    event_hash = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text=_("SHA-256 hash computed over this record's payload, metadata, and prev_hash."),
    )

    class Meta:
        verbose_name = _("Audit Event")
        verbose_name_plural = _("Audit Events")
        ordering = ["-timestamp", "-id"]

    def __str__(self) -> str:
        return f"AuditEvent [{self.event_type}] at {self.timestamp} (Loan: {self.loan_id or 'N/A'})"

    @classmethod
    def _resolve_actor_role(cls, actor_role=None, actor=None) -> int:
        """Determines the ActorRole integer choice cleanly from an int, str, or User instance."""
        if isinstance(actor_role, int):
            return actor_role

        if isinstance(actor_role, str):
            clean_role = actor_role.upper().replace("OPERATOR", "DATA_OPERATOR")
            return getattr(cls.ActorRole, clean_role, cls.ActorRole.SYSTEM)

        if actor and getattr(actor, "category", None):
            category_map = {
                1: cls.ActorRole.DATA_OPERATOR,
                2: cls.ActorRole.REVIEWER,
                3: cls.ActorRole.DATA_CONSUMER,
            }
            return category_map.get(actor.category, cls.ActorRole.SYSTEM)

        return cls.ActorRole.SYSTEM

    @classmethod
    def log_event(
        cls,
        event_type: str,
        actor=None,
        actor_role=None,
        loan_id: str | None = None,
        batch_id: int | None = None,
        payload: dict | None = None,
    ) -> "AuditEvent":
        """
        Creates and appends a new AuditEvent with SHA-256 cryptographic hash chaining.

        Args:
            event_type: String code identifying event (e.g. 'FILE_UPLOADED', 'EXCEPTION_CREATED')
            actor: User instance or None
            actor_role: ActorRole choice, integer, or string discriminator
            loan_id: Optional string loan identifier
            batch_id: Optional integer batch identifier
            payload: Optional context payload dictionary

        Returns:
            The created AuditEvent instance.
        """
        payload = payload or {}
        last_event = cls.objects.order_by("-timestamp", "-id").first()
        prev_hash = last_event.event_hash if (last_event and last_event.event_hash) else "0" * 64

        ts = timezone.now()
        ts_str = ts.isoformat()
        actor_name = getattr(actor, "username", str(actor_role or "SYSTEM"))

        payload_str = json.dumps(payload, sort_keys=True)
        hash_input = (
            f"{prev_hash}|{ts_str}|{event_type}|{actor_name}|{loan_id or ''}|{payload_str}".encode()
        )
        event_hash = hashlib.sha256(hash_input).hexdigest()

        role_val = cls._resolve_actor_role(actor_role=actor_role, actor=actor)

        return cls.objects.create(
            timestamp=ts,
            event_type=event_type,
            actor=actor,
            actor_role=role_val,
            loan_id=loan_id,
            batch_id=batch_id,
            payload=payload,
            prev_hash=prev_hash,
            event_hash=event_hash,
        )

    @classmethod
    def log_events_bulk(
        cls,
        events_data: list[dict[str, Any]],
        batch_size: int = 500,
    ) -> list["AuditEvent"]:
        """
        Bulk creates and appends multiple AuditEvent records while maintaining
        SHA-256 cryptographic hash chain integrity across sequence.

        Args:
            events_data: List of event dictionaries containing keys:
                - event_type (str, required)
                - actor (User, optional)
                - actor_role (int|str|ActorRole, optional)
                - loan_id (str, optional)
                - batch_id (int, optional)
                - payload (dict, optional)
            batch_size: Database insert batch size (default 500)

        Returns:
            list[AuditEvent]: Created AuditEvent instances.
        """
        if not events_data:
            return []

        last_event = cls.objects.order_by("-timestamp", "-id").first()
        current_prev_hash = (
            last_event.event_hash if (last_event and last_event.event_hash) else "0" * 64
        )

        audit_objects: list[AuditEvent] = []
        for data in events_data:
            event_type = data.get("event_type", "UNKNOWN_EVENT")
            actor = data.get("actor")
            actor_role = data.get("actor_role")
            loan_id = data.get("loan_id")
            batch_id = data.get("batch_id")
            payload = data.get("payload") or {}

            ts = timezone.now()
            ts_str = ts.isoformat()
            actor_name = getattr(actor, "username", str(actor_role or "SYSTEM"))

            payload_str = json.dumps(payload, sort_keys=True)
            hash_input = f"{current_prev_hash}|{ts_str}|{event_type}|{actor_name}|{loan_id or ''}|{payload_str}".encode()
            event_hash = hashlib.sha256(hash_input).hexdigest()
            role_val = cls._resolve_actor_role(actor_role=actor_role, actor=actor)

            audit_objects.append(
                cls(
                    timestamp=ts,
                    event_type=event_type,
                    actor=actor,
                    actor_role=role_val,
                    loan_id=loan_id,
                    batch_id=batch_id,
                    payload=payload,
                    prev_hash=current_prev_hash,
                    event_hash=event_hash,
                )
            )
            current_prev_hash = event_hash

        return cls.objects.bulk_create(audit_objects, batch_size=batch_size)

    @classmethod
    def log(cls, *args, **kwargs) -> "AuditEvent":
        """Alias for log_event."""
        return cls.log_event(*args, **kwargs)
