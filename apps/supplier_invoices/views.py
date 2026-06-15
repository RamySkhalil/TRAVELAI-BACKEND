from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.ai_extraction.models import DocumentExtractionJob
from apps.common.permissions import RoleBasedOperationalPermission
from apps.master_data.models import Supplier

from .filters import SupplierInvoiceFilter, SupplierInvoiceLineFilter
from .models import SupplierInvoice, SupplierInvoiceLine
from .serializers import SupplierInvoiceLineSerializer, SupplierInvoiceSerializer
from .services import approve_supplier_invoice, create_invoice_line, create_supplier_invoice, create_supplier_invoice_from_confirmed_extraction, lock_supplier_invoice


class SupplierInvoicePermission(RoleBasedOperationalPermission):
    write_groups = ("Admin", "HR", "Finance", "BookingManager")


class SupplierInvoiceLinePermission(RoleBasedOperationalPermission):
    write_groups = ("Admin", "HR", "Finance", "BookingManager")


class SupplierInvoiceViewSet(viewsets.ModelViewSet):
    queryset = SupplierInvoice.objects.select_related("supplier", "created_by", "approved_by").prefetch_related("lines").all()
    serializer_class = SupplierInvoiceSerializer
    permission_classes = [SupplierInvoicePermission]
    filterset_class = SupplierInvoiceFilter
    search_fields = ["invoice_record_number", "supplier_invoice_number", "supplier__name"]
    ordering_fields = ["created_at", "invoice_date", "received_date", "status", "total_amount"]

    def perform_create(self, serializer):
        invoice = create_supplier_invoice(serializer.validated_data, self.request.user)
        serializer.instance = invoice

    @action(detail=False, methods=["post"], url_path="create-from-extraction")
    def create_from_extraction(self, request):
        extraction_job = self._get_extraction_job(request.data.get("extraction_job"))
        supplier = get_object_or_404(Supplier, pk=request.data.get("supplier"))
        invoice = create_supplier_invoice_from_confirmed_extraction(
            extraction_job=extraction_job,
            supplier=supplier,
            overrides=request.data.get("overrides") or {},
            user=request.user,
        )
        return Response(self.get_serializer(invoice).data)

    def _get_extraction_job(self, lookup):
        queryset = DocumentExtractionJob.objects.all()
        if str(lookup or "").isdigit():
            return get_object_or_404(queryset, pk=lookup)
        return get_object_or_404(queryset, uid=lookup)

    @action(detail=True, methods=["post"])
    def extract(self, request, pk=None):
        return Response({"detail": "Supplier invoice extraction is reserved for Phase 4 workflow implementation."}, status=status.HTTP_501_NOT_IMPLEMENTED)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        invoice = approve_supplier_invoice(self.get_object(), request.user)
        return Response(self.get_serializer(invoice).data)

    @action(detail=True, methods=["post"])
    def lock(self, request, pk=None):
        invoice = self.get_object()
        lock_supplier_invoice(invoice, request.user)
        return Response(self.get_serializer(invoice).data)


class SupplierInvoiceLineViewSet(viewsets.ModelViewSet):
    queryset = SupplierInvoiceLine.objects.select_related("supplier_invoice", "travel_case", "ticket_version", "employee").all()
    serializer_class = SupplierInvoiceLineSerializer
    permission_classes = [SupplierInvoiceLinePermission]
    filterset_class = SupplierInvoiceLineFilter
    search_fields = ["ticket_number", "route_from", "route_to", "exception_reason"]
    ordering_fields = ["created_at", "match_status", "ticket_number", "invoiced_amount"]

    def perform_create(self, serializer):
        data = dict(serializer.validated_data)
        invoice = data.pop("supplier_invoice")
        line = create_invoice_line(invoice, data, self.request.user)
        serializer.instance = line

# Create your views here.
