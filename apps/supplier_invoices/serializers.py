from rest_framework import serializers

from apps.common.currency import normalize_currency

from .models import SupplierInvoice, SupplierInvoiceLine


class SupplierInvoiceLineSerializer(serializers.ModelSerializer):
    supplier_invoice_record_number = serializers.CharField(source="supplier_invoice.invoice_record_number", read_only=True)
    travel_case_number = serializers.CharField(source="travel_case.case_number", read_only=True)
    ticket_version_number = serializers.CharField(source="ticket_version.version_number", read_only=True)
    ticket_version_ticket_number = serializers.CharField(source="ticket_version.ticket_number", read_only=True)
    ticket_version_currency = serializers.CharField(source="ticket_version.currency", read_only=True)
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)

    class Meta:
        model = SupplierInvoiceLine
        fields = [
            "id",
            "created_at",
            "updated_at",
            "supplier_invoice",
            "supplier_invoice_record_number",
            "travel_case",
            "travel_case_number",
            "ticket_version",
            "ticket_version_number",
            "ticket_version_ticket_number",
            "ticket_version_currency",
            "employee",
            "employee_name",
            "ticket_number",
            "route_from",
            "route_to",
            "booked_amount",
            "invoiced_amount",
            "difference_amount",
            "currency",
            "account_type",
            "match_status",
            "exception_reason",
            "is_locked",
        ]
        read_only_fields = ("difference_amount", "is_locked", "created_at", "updated_at")

    def validate_currency(self, value):
        return normalize_currency(value)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if self.instance is None and not attrs.get("currency"):
            raise serializers.ValidationError({"currency": "Currency is required for supplier invoice lines."})
        return attrs


class SupplierInvoiceSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    supplier_code = serializers.CharField(source="supplier.code", read_only=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)
    approved_by_username = serializers.CharField(source="approved_by.username", read_only=True)
    lines = SupplierInvoiceLineSerializer(many=True, read_only=True)

    class Meta:
        model = SupplierInvoice
        fields = [
            "id",
            "uid",
            "created_at",
            "updated_at",
            "invoice_record_number",
            "supplier",
            "supplier_name",
            "supplier_code",
            "supplier_invoice_number",
            "invoice_date",
            "received_date",
            "currency",
            "total_amount",
            "invoice_file",
            "status",
            "extracted_data_json",
            "ai_confidence_json",
            "created_by",
            "created_by_username",
            "approved_by",
            "approved_by_username",
            "approved_at",
            "is_locked",
            "locked_at",
            "lines",
        ]
        read_only_fields = (
            "uid",
            "invoice_record_number",
            "created_by",
            "created_by_username",
            "approved_by",
            "approved_by_username",
            "approved_at",
            "is_locked",
            "locked_at",
            "created_at",
            "updated_at",
        )

    def validate_currency(self, value):
        return normalize_currency(value)
