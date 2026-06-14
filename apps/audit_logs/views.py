from rest_framework import mixins, viewsets

from apps.common.permissions import CanViewAuditLogs

from .filters import AuditLogFilter
from .models import AuditLog
from .serializers import AuditLogSerializer


class AuditLogViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = AuditLog.objects.select_related("user").all()
    serializer_class = AuditLogSerializer
    permission_classes = [CanViewAuditLogs]
    filterset_class = AuditLogFilter
    search_fields = ["action", "entity_type", "entity_id"]
    ordering_fields = ["created_at", "action", "entity_type"]

# Create your views here.
