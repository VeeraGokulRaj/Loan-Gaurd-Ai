"""
Views for Summary Metrics API.

Provides GET /summary.
"""

from typing import Any

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from app.api.v1.serializers.summary import SummaryMetricsSerializer
from app.models.audit import AuditEvent
from app.models.ingestion import RawLoanRecord, UploadBatch
from app.models.validation import LoanException, ValidationSeverity
from app.models.verified import VerifiedLoanRecord


class SummaryMetricsAPIView(APIView):
    """
    API View for GET /summary.

    Returns system-wide verification metrics, quality score, exception queue totals,
    and ingestion statistics.
    """

    permission_classes = (AllowAny,)
    authentication_classes = ()
    serializer_class = SummaryMetricsSerializer

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        metrics_data = calculate_summary_metrics()
        serializer = self.serializer_class(metrics_data)
        return Response(serializer.data, status=status.HTTP_200_OK)


# --- Helper Functions (Stepdown Rule) ---


def calculate_summary_metrics() -> dict[str, Any]:
    """
    Computes system-wide aggregate metrics using domain logic identical to workspace dashboards.
    """
    total_raw_tape = RawLoanRecord.objects.filter(
        batch__source_type=UploadBatch.SourceType.LOAN_TAPE
    ).count()

    total_raw_loans = total_raw_tape if total_raw_tape > 0 else RawLoanRecord.objects.count()
    total_verified_loans = VerifiedLoanRecord.objects.count()
    total_exceptions = LoanException.objects.count()

    clean_verified_count = VerifiedLoanRecord.objects.filter(
        validation_status=VerifiedLoanRecord.ValidationStatus.PASSED_CLEAN
    ).count()
    resolved_verified_count = VerifiedLoanRecord.objects.filter(
        validation_status=VerifiedLoanRecord.ValidationStatus.RESOLVED_EXCEPTION
    ).count()

    open_exceptions_count = LoanException.objects.filter(
        status=LoanException.ExceptionStatus.OPEN
    ).count()
    under_review_exceptions_count = LoanException.objects.filter(
        status=LoanException.ExceptionStatus.UNDER_REVIEW
    ).count()
    resolved_exceptions_count = LoanException.objects.filter(
        status__in=[
            LoanException.ExceptionStatus.RESOLVED_ACCEPTED,
            LoanException.ExceptionStatus.RESOLVED_EDITED,
        ]
    ).count()
    rejected_exceptions_count = LoanException.objects.filter(
        status=LoanException.ExceptionStatus.REJECTED
    ).count()

    severity_breakdown = {
        "critical": LoanException.objects.filter(severity=ValidationSeverity.CRITICAL).count(),
        "high": LoanException.objects.filter(severity=ValidationSeverity.HIGH).count(),
        "medium": LoanException.objects.filter(severity=ValidationSeverity.MEDIUM).count(),
        "low": LoanException.objects.filter(severity=ValidationSeverity.LOW).count(),
    }

    if total_raw_loans > 0:
        quality_score = round((total_verified_loans / total_raw_loans) * 100, 1)
    else:
        quality_score = 100.0 if total_verified_loans > 0 else 0.0

    total_batches = UploadBatch.objects.count()
    total_audit_events = AuditEvent.objects.count()

    return {
        "total_raw_loans": total_raw_loans,
        "total_verified_loans": total_verified_loans,
        "total_exceptions": total_exceptions,
        "clean_verified_count": clean_verified_count,
        "resolved_verified_count": resolved_verified_count,
        "open_exceptions_count": open_exceptions_count,
        "under_review_exceptions_count": under_review_exceptions_count,
        "resolved_exceptions_count": resolved_exceptions_count,
        "rejected_exceptions_count": rejected_exceptions_count,
        "severity_breakdown": severity_breakdown,
        "quality_score": quality_score,
        "total_batches": total_batches,
        "total_audit_events": total_audit_events,
    }
