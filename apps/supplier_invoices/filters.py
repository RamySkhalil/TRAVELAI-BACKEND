import django_filters

from .models import SupplierInvoice, SupplierInvoiceLine


class SupplierInvoiceFilter(django_filters.FilterSet):
    class Meta:
        model = SupplierInvoice
        fields = [
            "supplier",
            "status",
            "invoice_date",
            "received_date",
            "currency",
        ]


class SupplierInvoiceLineFilter(django_filters.FilterSet):
    class Meta:
        model = SupplierInvoiceLine
        fields = [
            "supplier_invoice",
            "travel_case",
            "ticket_version",
            "match_status",
            "account_type",
        ]
