import django_filters

from .models import TicketVersion


class TicketVersionFilter(django_filters.FilterSet):
    class Meta:
        model = TicketVersion
        fields = [
            "travel_case",
            "ticket_status",
            "ticket_action",
            "supplier",
            "departure_date",
        ]
