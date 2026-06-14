from apps.audit_logs.services import create_audit_log


def update_permit_status(permit, status, user, *, old_status=None):
    old_status = permit.status if old_status is None else old_status
    permit.status = status
    permit.save(update_fields=["status", "updated_at"])
    create_audit_log(
        user=user,
        action="Permit Status Updated",
        entity_type="Permit",
        entity_id=permit.id,
        old_value={"status": old_status},
        new_value={"status": permit.status},
    )
    return permit


def attach_permit_file(permit, file, user):
    old_value = {"attachment": permit.attachment.name if permit.attachment else ""}
    permit.attachment = file
    permit.save(update_fields=["attachment", "updated_at"])
    create_audit_log(
        user=user,
        action="Permit File Attached",
        entity_type="Permit",
        entity_id=permit.id,
        old_value=old_value,
        new_value={"attachment": permit.attachment.name},
    )
    return permit
