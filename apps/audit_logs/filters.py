import django_filters

from .models import AuditLog


class AuditLogFilter(django_filters.FilterSet):
    class Meta:
        model = AuditLog
        fields = [
            "entity_type",
            "entity_id",
            "user",
            "action",
            "created_at",
        ]
