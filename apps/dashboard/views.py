from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import serializers

from apps.billing_confirmations.models import TravelBillingConfirmationNote
from apps.common.permissions import RoleBasedOperationalPermission
from apps.supplier_invoices.models import SupplierInvoice
from apps.ticket_versions.models import TicketVersion
from apps.travel_cases.models import TravelCase


class DashboardPermission(RoleBasedOperationalPermission):
    write_groups = ("Admin",)


class DashboardPlaceholderSerializer(serializers.Serializer):
    pass


class DashboardViewSet(viewsets.ViewSet):
    permission_classes = [DashboardPermission]
    serializer_class = DashboardPlaceholderSerializer

    @action(detail=False, methods=["get"])
    def summary(self, request):
        return Response(
            {
                "travel_cases": TravelCase.objects.count(),
                "ticket_versions": TicketVersion.objects.count(),
                "supplier_invoices": SupplierInvoice.objects.count(),
                "tbcn": TravelBillingConfirmationNote.objects.count(),
            }
        )

    @action(detail=False, methods=["get"], url_path="cost-by-month")
    def cost_by_month(self, request):
        return Response({"results": []})

    @action(detail=False, methods=["get"], url_path="cost-by-project")
    def cost_by_project(self, request):
        return Response({"results": []})

    @action(detail=False, methods=["get"], url_path="supplier-aging")
    def supplier_aging(self, request):
        return Response({"results": []})

    @action(detail=False, methods=["get"], url_path="pending-actions")
    def pending_actions(self, request):
        return Response({"results": []})

# Create your views here.
