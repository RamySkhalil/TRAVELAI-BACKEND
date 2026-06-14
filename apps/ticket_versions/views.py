from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.permissions import RoleBasedOperationalPermission

from .filters import TicketVersionFilter
from .models import TicketVersion
from .serializers import TicketVersionSerializer
from .services import create_ticket_version_from_confirmed_data, confirm_ticket_version, lock_ticket_version, mark_ticket_cancelled


class TicketVersionPermission(RoleBasedOperationalPermission):
    write_groups = ("Admin", "BookingOfficer", "BookingManager")


class TicketVersionViewSet(viewsets.ModelViewSet):
    queryset = TicketVersion.objects.select_related("travel_case", "supplier").all()
    serializer_class = TicketVersionSerializer
    permission_classes = [TicketVersionPermission]
    filterset_class = TicketVersionFilter
    search_fields = ["ticket_number", "pnr", "passenger_name", "airline"]
    ordering_fields = ["created_at", "departure_date", "ticket_status", "ticket_action"]

    def perform_create(self, serializer):
        data = dict(serializer.validated_data)
        travel_case = data.pop("travel_case")
        data.pop("version_number", None)
        ticket_version = create_ticket_version_from_confirmed_data(travel_case, data, self.request.user)
        serializer.instance = ticket_version

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        ticket_version = confirm_ticket_version(self.get_object(), request.user)
        return Response(self.get_serializer(ticket_version).data)

    @action(detail=True, methods=["post"], url_path="mark-cancelled")
    def mark_cancelled(self, request, pk=None):
        ticket_version = mark_ticket_cancelled(self.get_object(), request.data.get("reason", ""), request.user)
        return Response(self.get_serializer(ticket_version).data)

    @action(detail=True, methods=["post"])
    def lock(self, request, pk=None):
        ticket_version = self.get_object()
        lock_ticket_version(ticket_version, request.user)
        return Response(self.get_serializer(ticket_version).data)

# Create your views here.
