"""
Standard API v1 Pagination for LoanGuard AI.
"""

from rest_framework.pagination import PageNumberPagination


class StandardAPIPagination(PageNumberPagination):
    """
    Standard DRF PageNumberPagination with default page_size = 20.
    """

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
