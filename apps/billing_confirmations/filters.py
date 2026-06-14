import django_filters

from .models import TravelBillingConfirmationNote, TravelBillingConfirmationRevision


class TravelBillingConfirmationNoteFilter(django_filters.FilterSet):
    class Meta:
        model = TravelBillingConfirmationNote
        fields = [
            "supplier_invoice",
            "supplier",
            "status",
            "finance_status",
            "generated_at",
        ]


class TravelBillingConfirmationRevisionFilter(django_filters.FilterSet):
    class Meta:
        model = TravelBillingConfirmationRevision
        fields = ["original_tbcn", "revision_number", "revised_by", "revised_at"]
