"""
Views for Loans API.

Provides GET /loans and GET /loans/:id using DRF Generic API views.
"""

from django.db.models import Q
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.generics import ListAPIView, RetrieveAPIView, get_object_or_404
from rest_framework.permissions import AllowAny

from app.api.v1.pagination import StandardAPIPagination
from app.api.v1.serializers.loans import RawLoanRecordSerializer
from app.filters.ingestion import RawLoanRecordFilter
from app.models.ingestion import RawLoanRecord, UploadBatch


class LoanListAPIView(ListAPIView):
    """
    API View for GET /loans.

    Returns paginated list of primary loan tape records.
    """

    permission_classes = (AllowAny,)
    authentication_classes = ()
    serializer_class = RawLoanRecordSerializer
    filterset_class = RawLoanRecordFilter
    filter_backends = (DjangoFilterBackend,)
    pagination_class = StandardAPIPagination
    queryset = (
        RawLoanRecord.objects.filter(batch__source_type=UploadBatch.SourceType.LOAN_TAPE)
        .select_related("batch")
        .order_by("-id")
    )


class LoanDetailAPIView(RetrieveAPIView):
    """
    API View for GET /loans/:id.

    Retrieves a single loan record by database primary key ID or loan_id string.
    """

    permission_classes = (AllowAny,)
    authentication_classes = ()
    serializer_class = RawLoanRecordSerializer
    queryset = RawLoanRecord.objects.filter(
        batch__source_type=UploadBatch.SourceType.LOAN_TAPE
    ).select_related("batch")

    def get_object(self) -> RawLoanRecord:
        identifier = str(self.kwargs.get("pk", "")).strip()
        if identifier.isdigit():
            return get_object_or_404(self.queryset, id=int(identifier))
        return get_object_or_404(
            self.queryset,
            Q(raw_data__loan_id=identifier) | Q(raw_data__loan_id__iexact=identifier),
        )
