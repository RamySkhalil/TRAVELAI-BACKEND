from rest_framework import serializers

from apps.supplier_invoices.serializers import SupplierInvoiceLineSerializer

from .models import TravelBillingConfirmationNote, TravelBillingConfirmationRevision


class TravelBillingConfirmationNoteSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    supplier_code = serializers.CharField(source="supplier.code", read_only=True)
    generated_by_username = serializers.CharField(source="generated_by.username", read_only=True)
    reviewed_by_username = serializers.CharField(source="reviewed_by.username", read_only=True)
    sent_to_finance_by_username = serializers.CharField(source="sent_to_finance_by.username", read_only=True)
    invoice_record_number = serializers.CharField(source="supplier_invoice.invoice_record_number", read_only=True)
    invoice_status = serializers.CharField(source="supplier_invoice.status", read_only=True)
    invoice_lines = SupplierInvoiceLineSerializer(source="supplier_invoice.lines", many=True, read_only=True)

    class Meta:
        model = TravelBillingConfirmationNote
        fields = [
            "id",
            "uid",
            "created_at",
            "updated_at",
            "confirmation_no",
            "supplier_invoice",
            "invoice_record_number",
            "invoice_status",
            "supplier",
            "supplier_name",
            "supplier_code",
            "supplier_invoice_number",
            "total_amount",
            "matched_amount",
            "difference_amount",
            "currency",
            "status",
            "generated_by",
            "generated_by_username",
            "generated_at",
            "reviewed_by",
            "reviewed_by_username",
            "reviewed_at",
            "sent_to_finance_by",
            "sent_to_finance_by_username",
            "sent_to_finance_at",
            "finance_status",
            "pdf_file",
            "is_locked",
            "invoice_lines",
        ]
        read_only_fields = ("uid", "confirmation_no", "is_locked", "created_at", "updated_at")


class TravelBillingConfirmationRevisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = TravelBillingConfirmationRevision
        fields = "__all__"
        read_only_fields = ("created_at",)
