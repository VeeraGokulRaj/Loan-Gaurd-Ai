"""
Serializers for System Summary Metrics.
"""

from rest_framework import serializers


class SeverityBreakdownSerializer(serializers.Serializer):
    """Serializer for exception severity count breakdown."""

    critical = serializers.IntegerField(read_only=True)
    high = serializers.IntegerField(read_only=True)
    medium = serializers.IntegerField(read_only=True)
    low = serializers.IntegerField(read_only=True)


class SummaryMetricsSerializer(serializers.Serializer):
    """
    Serializer for system-wide summary metrics payload.

    Provides standardized verification status metrics, exception breakdown,
    data quality score, and ingestion volume totals.
    """

    total_raw_loans = serializers.IntegerField(read_only=True)
    total_verified_loans = serializers.IntegerField(read_only=True)
    total_exceptions = serializers.IntegerField(read_only=True)
    clean_verified_count = serializers.IntegerField(read_only=True)
    resolved_verified_count = serializers.IntegerField(read_only=True)
    open_exceptions_count = serializers.IntegerField(read_only=True)
    under_review_exceptions_count = serializers.IntegerField(read_only=True)
    resolved_exceptions_count = serializers.IntegerField(read_only=True)
    rejected_exceptions_count = serializers.IntegerField(read_only=True)
    severity_breakdown = SeverityBreakdownSerializer(read_only=True)
    quality_score = serializers.FloatField(read_only=True)
    total_batches = serializers.IntegerField(read_only=True)
    total_audit_events = serializers.IntegerField(read_only=True)
