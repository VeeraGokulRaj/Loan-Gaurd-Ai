"""
Reviewer Workspace FilterSet Classes for LoanGuard AI.

Provides django-filter FilterSet classes for LoanException queue filtering.
"""

import django_filters
from django.db.models import Q

from app.models import LoanException, UploadBatch, ValidationSeverity


class LoanExceptionFilter(django_filters.FilterSet):
    """
    FilterSet for LoanException list views in Reviewer Workspace.

    Uses ChoiceFilter for severity & status choices, ModelChoiceFilter for batch,
    and CharFilter for search query `q`.
    """

    q = django_filters.CharFilter(method="filter_by_query", label="Search")
    severity = django_filters.ChoiceFilter(
        choices=ValidationSeverity.choices,
        empty_label="All Severities",
    )
    status = django_filters.ChoiceFilter(
        choices=LoanException.ExceptionStatus.choices,
        empty_label="All Statuses",
    )
    batch_id = django_filters.ModelChoiceFilter(
        queryset=UploadBatch.objects.all(),
        field_name="batch",
        empty_label="All Batches",
        to_field_name="id",
    )

    class Meta:
        model = LoanException
        fields = ["q", "severity", "status", "batch_id"]

    def filter_by_query(self, queryset, name, value):
        if not value:
            return queryset
        val = str(value).strip()
        clean_q = val[1:].strip() if val.startswith("#") else val
        if clean_q.isdigit():
            return queryset.filter(
                Q(id=int(clean_q))
                | Q(rule_code__icontains=val)
                | Q(field_name__icontains=val)
                | Q(description__icontains=val)
                | Q(raw_record__raw_data__icontains=val)
            )
        return queryset.filter(
            Q(rule_code__icontains=val)
            | Q(field_name__icontains=val)
            | Q(description__icontains=val)
            | Q(raw_record__raw_data__icontains=val)
        )
