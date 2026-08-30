"""
Data Consumer Workspace FilterSet Classes for LoanGuard AI.

Provides django-filter FilterSet classes for VerifiedLoanRecord dataset filtering.
"""

import django_filters
from django.db.models import Q

from app.models.verified import VerifiedLoanRecord


class VerifiedLoanRecordFilter(django_filters.FilterSet):
    """
    FilterSet for VerifiedLoanRecord list views in Data Consumer Workspace.

    Uses ChoiceFilter for validation_status & reviewer_decision,
    and CharFilter for search query `q`.
    """

    q = django_filters.CharFilter(method="filter_by_query", label="Search")
    validation_status = django_filters.ChoiceFilter(
        choices=VerifiedLoanRecord.ValidationStatus.choices,
        empty_label="All Validation Outcomes",
    )
    reviewer_decision = django_filters.ChoiceFilter(
        choices=VerifiedLoanRecord.ReviewerDecision.choices,
        empty_label="All Reviewer Decisions",
    )

    class Meta:
        model = VerifiedLoanRecord
        fields = ["q", "validation_status", "reviewer_decision"]

    def filter_by_query(self, queryset, name, value):
        if not value:
            return queryset
        val = str(value).strip()
        clean_q = val[1:].strip() if val.startswith("#") else val
        if clean_q.isdigit():
            return queryset.filter(
                Q(id=int(clean_q))
                | Q(loan_id__icontains=val)
                | Q(borrower_id__icontains=val)
                | Q(record_hash__icontains=val)
                | Q(canonical_data__icontains=val)
            )
        return queryset.filter(
            Q(loan_id__icontains=val)
            | Q(borrower_id__icontains=val)
            | Q(record_hash__icontains=val)
            | Q(canonical_data__icontains=val)
        )
