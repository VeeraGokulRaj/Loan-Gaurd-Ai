"""
Views for Audit Trail API.

Provides GET /audit/:loanId using DRF Generic ListAPIView.
"""

from django.db.models import Q
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny

from app.api.v1.pagination import StandardAPIPagination
from app.api.v1.serializers.audit import AuditEventSerializer
from app.models.audit import AuditEvent


class LoanAuditTrailAPIView(ListAPIView):
    """
    API View for GET /audit/:loanId.

    Retrieves cryptographic audit event ledger records associated with a specific loan ID.
    """

    permission_classes = (AllowAny,)
    authentication_classes = ()
    serializer_class = AuditEventSerializer
    pagination_class = StandardAPIPagination

    def get_queryset(self):
        loan_id = str(self.kwargs.get("loan_id", "")).strip()
        if not loan_id:
            return AuditEvent.objects.none()

        return (
            AuditEvent.objects.select_related("actor")
            .filter(
                Q(loan_id__icontains=loan_id)
                | Q(payload__loan_id__icontains=loan_id)
                | Q(payload__icontains=loan_id)
            )
            .order_by("-timestamp", "-id")
        )
