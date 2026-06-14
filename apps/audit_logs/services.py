from typing import Any

from .models import AuditLog


def create_audit_log(
    *,
    user=None,
    action: str,
    entity_type: str,
    entity_id: str | int,
    old_value: Any = None,
    new_value: Any = None,
    metadata: dict | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    return AuditLog.objects.create(
        user=user,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        old_value=old_value,
        new_value=new_value,
        metadata=metadata or {},
        ip_address=ip_address,
    )
