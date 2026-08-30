"""
URL configuration for LoanGuard AI Module H (Verified Records API).
"""

from django.urls import path

from app.api.v1.views.audit import LoanAuditTrailAPIView
from app.api.v1.views.exceptions import ExceptionListAPIView
from app.api.v1.views.loans import LoanDetailAPIView, LoanListAPIView
from app.api.v1.views.summary import SummaryMetricsAPIView
from app.api.v1.views.verified_loans import (
    VerifiedLoanDetailAPIView,
    VerifiedLoanListAPIView,
)

urlpatterns = [
    path("loans/", LoanListAPIView.as_view(), name="api_loans_list"),
    path("loans/<str:pk>/", LoanDetailAPIView.as_view(), name="api_loan_detail"),
    path("exceptions/", ExceptionListAPIView.as_view(), name="api_exceptions_list"),
    path("verified-loans/", VerifiedLoanListAPIView.as_view(), name="api_verified_loans_list"),
    path(
        "verified-loans/<str:pk>/",
        VerifiedLoanDetailAPIView.as_view(),
        name="api_verified_loan_detail",
    ),
    path("audit/<str:loan_id>/", LoanAuditTrailAPIView.as_view(), name="api_loan_audit_trail"),
    path("summary/", SummaryMetricsAPIView.as_view(), name="api_summary_metrics"),
]
