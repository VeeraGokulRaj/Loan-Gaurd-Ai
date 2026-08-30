"""
Serializer for Raw Loan Records.
"""

from rest_framework import serializers

from app.models.ingestion import RawLoanRecord


class RawLoanRecordSerializer(serializers.ModelSerializer):
    """
    Serializer for RawLoanRecord model.

    Serializes ingested loan tape raw records with computed properties
    for loan_id, borrower_id, and borrower_name.
    """

    loan_id = serializers.CharField(read_only=True)
    borrower_id = serializers.CharField(read_only=True)
    borrower_name = serializers.SerializerMethodField()

    class Meta:
        model = RawLoanRecord
        fields = [
            "id",
            "loan_id",
            "borrower_id",
            "borrower_name",
            "batch",
            "row_number",
            "raw_data",
            "created",
            "modified",
        ]
        read_only_fields = fields

    def get_borrower_name(self, obj: RawLoanRecord) -> str:
        """Extract borrower name from raw_data payload."""
        data = obj.raw_data or {}
        if not isinstance(data, dict):
            return ""
        return str(
            data.get("borrower_name") or data.get("borrower_full_name") or data.get("name") or ""
        ).strip()
