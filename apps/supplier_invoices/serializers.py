from rest_framework import serializers

from .models import SupplierInvoice, SupplierInvoiceLine


class SupplierInvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupplierInvoice
        fields = "__all__"
        read_only_fields = ("uid", "is_locked", "locked_at", "created_at", "updated_at")


class SupplierInvoiceLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupplierInvoiceLine
        fields = "__all__"
        read_only_fields = ("difference_amount", "is_locked", "created_at", "updated_at")
