from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.permissions import RoleBasedOperationalPermission

from .filters import TravelBillingConfirmationNoteFilter, TravelBillingConfirmationRevisionFilter
from .models import TravelBillingConfirmationNote, TravelBillingConfirmationRevision
from .serializers import TravelBillingConfirmationNoteSerializer, TravelBillingConfirmationRevisionSerializer
from .services import generate_tbcn, mark_finance_accepted, mark_tbcn_paid, send_tbcn_to_finance
from apps.supplier_invoices.models import SupplierInvoice


class BillingConfirmationPermission(RoleBasedOperationalPermission):
    write_groups = ("Admin", "Finance", "BookingManager")


class TravelBillingConfirmationNoteViewSet(viewsets.ModelViewSet):
    queryset = TravelBillingConfirmationNote.objects.select_related("supplier_invoice", "supplier").all()
    serializer_class = TravelBillingConfirmationNoteSerializer
    permission_classes = [BillingConfirmationPermission]
    filterset_class = TravelBillingConfirmationNoteFilter
    search_fields = ["confirmation_no", "supplier_invoice_number", "supplier__name"]
    ordering_fields = ["generated_at", "status", "finance_status", "confirmation_no"]

    @action(detail=False, methods=["post"], url_path="generate-tbcn")
    def generate_tbcn(self, request, pk=None):
        invoice = get_object_or_404(SupplierInvoice, pk=request.data.get("supplier_invoice"))
        tbcn = generate_tbcn(invoice, request.user)
        return Response(self.get_serializer(tbcn).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="send-to-finance")
    def send_to_finance(self, request, pk=None):
        tbcn = send_tbcn_to_finance(self.get_object(), request.user)
        return Response(self.get_serializer(tbcn).data)

    @action(detail=True, methods=["post"], url_path="mark-finance-accepted")
    def mark_finance_accepted(self, request, pk=None):
        tbcn = mark_finance_accepted(self.get_object(), request.user)
        return Response(self.get_serializer(tbcn).data)

    @action(detail=True, methods=["post"], url_path="mark-paid")
    def mark_paid(self, request, pk=None):
        tbcn = mark_tbcn_paid(self.get_object(), request.user)
        return Response(self.get_serializer(tbcn).data)

    @action(detail=True, methods=["post"], url_path="create-revision")
    def create_revision(self, request, pk=None):
        return Response({"detail": "TBCN revision workflow is reserved for Phase 4 implementation."}, status=status.HTTP_501_NOT_IMPLEMENTED)


class TravelBillingConfirmationRevisionViewSet(viewsets.ModelViewSet):
    queryset = TravelBillingConfirmationRevision.objects.select_related("original_tbcn", "revised_by").all()
    serializer_class = TravelBillingConfirmationRevisionSerializer
    permission_classes = [BillingConfirmationPermission]
    filterset_class = TravelBillingConfirmationRevisionFilter
    search_fields = ["original_tbcn__confirmation_no", "reason"]
    ordering_fields = ["revised_at", "revision_number"]

# Create your views here.
