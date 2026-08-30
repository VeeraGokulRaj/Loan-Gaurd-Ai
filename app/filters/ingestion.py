"""
Ingestion Engine FilterSet Classes for LoanGuard AI.

Provides django-filter FilterSet classes using standard ModelChoiceFilter,
ChoiceFilter, and CharFilter definitions.
"""

import django_filters
from django.db.models import Q

from app.models import FailedImportRow, UploadBatch


class UploadBatchFilter(django_filters.FilterSet):
    """
    FilterSet for UploadBatch list views.

    Uses ChoiceFilter for source_type & status choices and CharFilter for search query `q`.
    """

    q = django_filters.CharFilter(method="filter_by_query", label="Search")
    source_type = django_filters.ChoiceFilter(
        choices=UploadBatch.SourceType.choices,
        empty_label="All Source Types",
    )
    status = django_filters.ChoiceFilter(
        choices=UploadBatch.BatchStatus.choices,
        empty_label="All Statuses",
    )

    class Meta:
        model = UploadBatch
        fields = ["q", "source_type", "status"]

    def filter_by_query(self, queryset, name, value):
        if not value:
            return queryset
        val = str(value).strip()
        clean_id = val[1:].strip() if val.startswith("#") else val
        if clean_id.isdigit():
            return queryset.filter(Q(file_name__icontains=val) | Q(id=int(clean_id)))
        return queryset.filter(file_name__icontains=val)


class FailedImportRowFilter(django_filters.FilterSet):
    """
    FilterSet for FailedImportRow list views.

    Uses ModelChoiceFilter for UploadBatch selection and CharFilter for search query `q`.
    """

    q = django_filters.CharFilter(method="filter_by_query", label="Search")
    batch_id = django_filters.ModelChoiceFilter(
        queryset=UploadBatch.objects.all(),
        field_name="batch",
        empty_label="All Batches",
        to_field_name="id",
    )

    class Meta:
        model = FailedImportRow
        fields = ["q", "batch_id"]

    def filter_by_query(self, queryset, name, value):
        if not value:
            return queryset
        val = str(value).strip()
        clean_q = val[1:].strip() if val.startswith("#") else val
        if clean_q.isdigit():
            return queryset.filter(
                Q(batch_id=int(clean_q))
                | Q(failure_reason__icontains=val)
                | Q(raw_line__icontains=val)
            )
        return queryset.filter(
            Q(failure_reason__icontains=val)
            | Q(raw_line__icontains=val)
            | Q(batch__file_name__icontains=val)
        )


class RawLoanRecordFilter(django_filters.FilterSet):
    """
    FilterSet for RawLoanRecord list views.

    Uses ModelChoiceFilter for UploadBatch selection, ChoiceFilter for source_type,
    and CharFilter for search query `q`.
    """

    q = django_filters.CharFilter(method="filter_by_query", label="Search")
    batch_id = django_filters.ModelChoiceFilter(
        queryset=UploadBatch.objects.all(),
        field_name="batch",
        empty_label="All Batches",
        to_field_name="id",
    )
    source_type = django_filters.ChoiceFilter(
        field_name="batch__source_type",
        choices=UploadBatch.SourceType.choices,
        empty_label="All Source Types",
    )

    class Meta:
        from app.models import RawLoanRecord

        model = RawLoanRecord
        fields = ["q", "batch_id", "source_type"]

    def filter_by_query(self, queryset, name, value):
        if not value:
            return queryset
        val = str(value).strip()
        clean_q = val[1:].strip() if val.startswith("#") else val
        if clean_q.isdigit():
            return queryset.filter(
                Q(id=int(clean_q))
                | Q(raw_data__loan_id__icontains=val)
                | Q(raw_data__borrower_id__icontains=val)
                | Q(raw_data__icontains=val)
            )
        return queryset.filter(
            Q(raw_data__loan_id__icontains=val)
            | Q(raw_data__borrower_id__icontains=val)
            | Q(raw_data__icontains=val)
        )
