from apps.common.permissions import RoleBasedOperationalPermission
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .serializers import (
    DashboardSummarySerializer,
    CostByMonthResponseSerializer,
    CostByProjectResponseSerializer,
    CostByRouteResponseSerializer,
    CostBySupplierResponseSerializer,
    OperationalKpisResponseSerializer,
    PendingActionsResponseSerializer,
    SupplierAgingResponseSerializer,
    UnbilledTicketsResponseSerializer,
)
from .services import (
    cost_by_month,
    cost_by_project,
    cost_by_route,
    cost_by_supplier,
    dashboard_summary,
    operational_kpis,
    pending_actions_for_user,
    supplier_aging,
    unbilled_tickets_by_supplier,
)


class DashboardPermission(RoleBasedOperationalPermission):
    write_groups = ("Admin",)


class DashboardPlaceholderSerializer(serializers.Serializer):
    pass


class DashboardViewSet(viewsets.ViewSet):
    permission_classes = [DashboardPermission]
    serializer_class = DashboardPlaceholderSerializer

    @extend_schema(responses=DashboardSummarySerializer)
    @action(detail=False, methods=["get"])
    def summary(self, request):
        return Response(dashboard_summary(request.user))

    @extend_schema(responses=OperationalKpisResponseSerializer)
    @action(detail=False, methods=["get"], url_path="operational-kpis")
    def operational_kpis(self, request):
        return Response(operational_kpis(request.user))

    @extend_schema(responses=CostByMonthResponseSerializer)
    @action(detail=False, methods=["get"], url_path="cost-by-month")
    def cost_by_month(self, request):
        return Response(cost_by_month(request.user))

    @extend_schema(responses=CostByProjectResponseSerializer)
    @action(detail=False, methods=["get"], url_path="cost-by-project")
    def cost_by_project(self, request):
        return Response(cost_by_project(request.user))

    @extend_schema(responses=CostBySupplierResponseSerializer)
    @action(detail=False, methods=["get"], url_path="cost-by-supplier")
    def cost_by_supplier(self, request):
        return Response(cost_by_supplier(request.user))

    @extend_schema(responses=CostByRouteResponseSerializer)
    @action(detail=False, methods=["get"], url_path="cost-by-route")
    def cost_by_route(self, request):
        return Response(cost_by_route(request.user))

    @extend_schema(responses=SupplierAgingResponseSerializer)
    @action(detail=False, methods=["get"], url_path="supplier-aging")
    def supplier_aging(self, request):
        return Response(supplier_aging(request.user))

    @extend_schema(responses=UnbilledTicketsResponseSerializer)
    @action(detail=False, methods=["get"], url_path="unbilled-tickets")
    def unbilled_tickets(self, request):
        return Response(unbilled_tickets_by_supplier(request.user))

    @extend_schema(responses=PendingActionsResponseSerializer)
    @action(detail=False, methods=["get"], url_path="pending-actions")
    def pending_actions(self, request):
        return Response(pending_actions_for_user(request.user))

# Create your views here.
