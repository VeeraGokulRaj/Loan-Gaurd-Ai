"""
Views for Loan Exceptions API.

Provides GET /exceptions using DRF Generic ListAPIView.
"""

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny

from app.api.v1.pagination import StandardAPIPagination
from app.api.v1.serializers.exceptions import LoanExceptionSerializer
from app.filters.reviewer import LoanExceptionFilter
from app.models.validation import LoanException


class ExceptionListAPIView(ListAPIView):
    """
    API View for GET /exceptions.

    Returns paginated list of loan exceptions using LoanExceptionFilter.
    """

    permission_classes = (AllowAny,)
    authentication_classes = ()
    serializer_class = LoanExceptionSerializer
    filterset_class = LoanExceptionFilter
    filter_backends = (DjangoFilterBackend,)
    pagination_class = StandardAPIPagination
    queryset = LoanException.objects.select_related(
        "batch", "raw_record", "rule", "resolved_by"
    ).order_by("-severity", "-created")
