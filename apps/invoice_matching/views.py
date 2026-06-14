from django.shortcuts import get_object_or_404
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.permissions import RoleBasedOperationalPermission
from apps.supplier_invoices.models import SupplierInvoice, SupplierInvoiceLine
from apps.supplier_invoices.serializers import SupplierInvoiceLineSerializer, SupplierInvoiceSerializer

from .services import match_invoice as match_invoice_service
from .services import resolve_invoice_line_exception


class InvoiceMatchingPermission(RoleBasedOperationalPermission):
    write_groups = ("Admin", "HR", "Finance", "BookingManager")


class InvoiceMatchingPlaceholderSerializer(serializers.Serializer):
    pass


class InvoiceMatchingViewSet(viewsets.ViewSet):
    permission_classes = [InvoiceMatchingPermission]
    serializer_class = InvoiceMatchingPlaceholderSerializer

    @action(detail=False, methods=["post"], url_path="match-invoice")
    def match_invoice(self, request):
        invoice = get_object_or_404(SupplierInvoice, pk=request.data.get("supplier_invoice"))
        invoice = match_invoice_service(invoice, request.user)
        return Response(SupplierInvoiceSerializer(invoice, context={"request": request}).data)

    @action(detail=False, methods=["post"], url_path="resolve-exception")
    def resolve_exception(self, request):
        line = get_object_or_404(SupplierInvoiceLine, pk=request.data.get("line"))
        line = resolve_invoice_line_exception(line, request.data.get("reason", ""), request.user)
        return Response(SupplierInvoiceLineSerializer(line, context={"request": request}).data)

# Create your views here.
