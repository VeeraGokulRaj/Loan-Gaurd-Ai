"""
Serializer for Loan Exceptions.
"""

from rest_framework import serializers

from app.models.validation import LoanException


class LoanExceptionSerializer(serializers.ModelSerializer):
    """
    Serializer for LoanException model.

    Serializes exception queue items with display strings for severity and status,
    resolved_by username, and computed loan metadata properties.
    """

    loan_id = serializers.CharField(read_only=True)
    borrower_id = serializers.CharField(read_only=True)
    borrower_name = serializers.CharField(read_only=True)
    severity_display = serializers.CharField(source="get_severity_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    resolved_by_username = serializers.SerializerMethodField()

    class Meta:
        model = LoanException
        fields = [
            "id",
            "loan_id",
            "borrower_id",
            "borrower_name",
            "batch",
            "raw_record",
            "rule",
            "rule_code",
            "field_name",
            "severity",
            "severity_display",
            "description",
            "status",
            "status_display",
            "reviewer_comment",
            "override_value",
            "resolved_by",
            "resolved_by_username",
            "resolved_at",
            "created",
            "modified",
        ]
        read_only_fields = fields

    def get_resolved_by_username(self, obj: LoanException) -> str | None:
        """Return username of resolving reviewer if available."""
        if obj.resolved_by:
            return obj.resolved_by.username
        return None
