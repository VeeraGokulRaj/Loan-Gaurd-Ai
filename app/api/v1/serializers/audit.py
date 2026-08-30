"""
Serializer for Audit Trail Events.
"""

from rest_framework import serializers

from app.models.audit import AuditEvent


class AuditEventSerializer(serializers.ModelSerializer):
    """
    Serializer for AuditEvent model.

    Serializes immutable audit ledger records with cryptographic SHA-256 hash chaining,
    actor username, and actor role display labels.
    """

    actor_username = serializers.SerializerMethodField()
    actor_role_display = serializers.CharField(source="get_actor_role_display", read_only=True)

    class Meta:
        model = AuditEvent
        fields = [
            "id",
            "timestamp",
            "loan_id",
            "batch_id",
            "actor",
            "actor_username",
            "actor_role",
            "actor_role_display",
            "event_type",
            "payload",
            "prev_hash",
            "event_hash",
            "created",
            "modified",
        ]
        read_only_fields = fields

    def get_actor_username(self, obj: AuditEvent) -> str:
        """Return username of actor user or System Engine fallback."""
        if obj.actor:
            return obj.actor.username
        return "System Engine"
