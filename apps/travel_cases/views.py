from django.contrib.auth import get_user_model
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.permissions import RoleBasedOperationalPermission

from .filters import TravelCaseFilter
from .models import TravelCase
from .serializers import TravelCaseSerializer
from .services import assign_travel_case, close_travel_case, create_travel_case, get_travel_case_timeline, submit_travel_case


class TravelCasePermission(RoleBasedOperationalPermission):
    write_groups = ("Admin", "HR", "BookingManager")


class TravelCaseViewSet(viewsets.ModelViewSet):
    queryset = TravelCase.objects.select_related("employee", "project", "department", "country").all()
    serializer_class = TravelCaseSerializer
    permission_classes = [TravelCasePermission]
    filterset_class = TravelCaseFilter
    search_fields = ["case_number", "employee_name", "badge_number", "route_from", "route_to"]
    ordering_fields = ["created_at", "requested_travel_date", "current_status", "priority"]

    def perform_create(self, serializer):
        travel_case = create_travel_case(serializer.validated_data, self.request.user)
        serializer.instance = travel_case

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        travel_case = submit_travel_case(self.get_object(), request.user)
        return Response(self.get_serializer(travel_case).data)

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        assigned_to_id = request.data.get("assigned_to")
        assigned_to = None
        if assigned_to_id:
            assigned_to = get_user_model().objects.get(pk=assigned_to_id)
        travel_case = assign_travel_case(self.get_object(), assigned_to, request.user)
        return Response(self.get_serializer(travel_case).data)

    @action(detail=True, methods=["get"])
    def timeline(self, request, pk=None):
        travel_case = self.get_object()
        return Response({"travel_case": travel_case.id, "events": get_travel_case_timeline(travel_case)})

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        travel_case = close_travel_case(self.get_object(), request.user)
        return Response(self.get_serializer(travel_case).data)

# Create your views here.
