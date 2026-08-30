"""
Views for Verified Loans API.

Provides GET /verified-loans and GET /verified-loans/:id using DRF Generic API views.
"""

from django.db.models import Q
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.generics import ListAPIView, RetrieveAPIView, get_object_or_404
from rest_framework.permissions import AllowAny

from app.api.v1.pagination import StandardAPIPagination
from app.api.v1.serializers.verified_loans import VerifiedLoanRecordSerializer
from app.filters.data_consumer import VerifiedLoanRecordFilter
from app.models.verified import VerifiedLoanRecord


class VerifiedLoanListAPIView(ListAPIView):
    """
    API View for GET /verified-loans.

    Returns paginated list of verified clean loan records using VerifiedLoanRecordFilter.
    """

    permission_classes = (AllowAny,)
    authentication_classes = ()
    serializer_class = VerifiedLoanRecordSerializer
    filterset_class = VerifiedLoanRecordFilter
    filter_backends = (DjangoFilterBackend,)
    pagination_class = StandardAPIPagination
    queryset = VerifiedLoanRecord.objects.with_lineage()


class VerifiedLoanDetailAPIView(RetrieveAPIView):
    """
    API View for GET /verified-loans/:id.

    Retrieves a single verified loan record by database primary key ID or loan_id string.
    """

    permission_classes = (AllowAny,)
    authentication_classes = ()
    serializer_class = VerifiedLoanRecordSerializer
    queryset = VerifiedLoanRecord.objects.with_lineage()

    def get_object(self) -> VerifiedLoanRecord:
        identifier = str(self.kwargs.get("pk", "")).strip()
        if identifier.isdigit():
            return get_object_or_404(self.queryset, id=int(identifier))
        return get_object_or_404(
            self.queryset,
            Q(loan_id=identifier) | Q(loan_id__iexact=identifier),
        )
