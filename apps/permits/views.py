from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.permissions import RoleBasedOperationalPermission

from .filters import PermitFilter
from .models import Permit
from .serializers import PermitSerializer
from .services import attach_permit_file, update_permit_status


class PermitPermission(RoleBasedOperationalPermission):
    write_groups = ("Admin", "HR", "BookingOfficer", "BookingManager")


class PermitViewSet(viewsets.ModelViewSet):
    queryset = Permit.objects.select_related("travel_case", "created_by").all()
    serializer_class = PermitSerializer
    permission_classes = [PermitPermission]
    filterset_class = PermitFilter
    search_fields = ["notes"]
    ordering_fields = ["created_at", "expiry_date", "status", "permit_type"]

    def perform_update(self, serializer):
        old_status = self.get_object().status
        permit = serializer.save()
        if "status" in serializer.validated_data and permit.status != old_status:
            update_permit_status(permit, permit.status, self.request.user, old_status=old_status)

    @action(detail=True, methods=["post"], url_path="upload-attachment")
    def upload_attachment(self, request, pk=None):
        file = request.FILES.get("attachment")
        if not file:
            return Response({"detail": "attachment file is required."}, status=status.HTTP_400_BAD_REQUEST)
        permit = attach_permit_file(self.get_object(), file, request.user)
        return Response(self.get_serializer(permit).data)

# Create your views here.
