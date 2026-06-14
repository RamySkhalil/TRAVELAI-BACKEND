import django_filters

from .models import Permit


class PermitFilter(django_filters.FilterSet):
    class Meta:
        model = Permit
        fields = [
            "travel_case",
            "permit_type",
            "status",
            "expiry_date",
        ]
