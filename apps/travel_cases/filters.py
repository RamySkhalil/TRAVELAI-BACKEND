import django_filters

from .models import TravelCase


class TravelCaseFilter(django_filters.FilterSet):
    class Meta:
        model = TravelCase
        fields = [
            "current_status",
            "account_type",
            "priority",
            "project",
            "employee",
            "requested_travel_date",
            "country",
        ]
