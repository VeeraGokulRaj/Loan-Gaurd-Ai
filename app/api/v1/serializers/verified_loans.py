"""
Serializer for Verified Loan Records.
"""

from rest_framework import serializers

from app.models.verified import VerifiedLoanRecord


class VerifiedLoanRecordSerializer(serializers.ModelSerializer):
    """
    Serializer for VerifiedLoanRecord model.

    Serializes verified clean loan records with computed cryptographic hash status,
    validation status displays, reviewer decisions, and lineage metadata.
    """

    validation_status_display = serializers.CharField(
        source="get_validation_status_display", read_only=True
    )
    reviewer_decision_display = serializers.CharField(
        source="get_reviewer_decision_display", read_only=True
    )
    verified_by_username = serializers.SerializerMethodField()
    is_tampered = serializers.BooleanField(read_only=True)
    computed_hash = serializers.SerializerMethodField()

    class Meta:
        model = VerifiedLoanRecord
        fields = [
            "id",
            "loan_id",
            "borrower_id",
            "raw_record",
            "canonical_data",
            "validation_status",
            "validation_status_display",
            "reviewer_decision",
            "reviewer_decision_display",
            "verified_at",
            "verified_by",
            "verified_by_username",
            "record_hash",
            "is_tampered",
            "computed_hash",
            "created",
            "modified",
        ]
        read_only_fields = fields

    def get_verified_by_username(self, obj: VerifiedLoanRecord) -> str:
        """Return username of verifier user or System Auto-Passed fallback."""
        if obj.verified_by:
            return obj.verified_by.username
        return "System Auto-Passed"

    def get_computed_hash(self, obj: VerifiedLoanRecord) -> str:
        """Return dynamically computed SHA-256 fingerprint for verification check."""
        return obj.compute_hash()
