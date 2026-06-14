from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit_logs.services import create_audit_log
from apps.common.services.locking import ensure_unlocked, lock_instance

from .models import TicketAction, TicketStatus, TicketVersion


def get_next_version_number(travel_case) -> str:
    existing_count = TicketVersion.objects.filter(travel_case=travel_case).count()
    return f"V{existing_count + 1}"


def create_ticket_version_from_confirmed_data(travel_case, data, user) -> TicketVersion:
    data = dict(data)
    data["travel_case"] = travel_case
    data["version_number"] = get_next_version_number(travel_case)
    ticket_version = TicketVersion.objects.create(**data)
    create_audit_log(
        user=user,
        action="Ticket Version Created",
        entity_type="TicketVersion",
        entity_id=ticket_version.id,
        new_value={"version_number": ticket_version.version_number, "ticket_number": ticket_version.ticket_number},
        metadata={"travel_case": travel_case.id},
    )
    return ticket_version


def lock_ticket_version(ticket_version: TicketVersion, user=None) -> TicketVersion:
    locked = lock_instance(ticket_version)
    create_audit_log(
        user=user,
        action="Ticket Locked",
        entity_type="TicketVersion",
        entity_id=ticket_version.id,
        new_value={"is_locked": True},
    )
    return locked


def confirm_ticket_version(ticket_version: TicketVersion, user) -> TicketVersion:
    ensure_unlocked(ticket_version)
    old_value = {"ticket_status": ticket_version.ticket_status, "confirmed_by": ticket_version.confirmed_by_id}
    ticket_version.confirmed_by = user
    ticket_version.confirmed_at = timezone.now()
    if ticket_version.ticket_status == TicketStatus.DRAFT:
        ticket_version.ticket_status = TicketStatus.ACTIVE
    ticket_version.save(update_fields=["confirmed_by", "confirmed_at", "ticket_status", "updated_at"])
    create_audit_log(
        user=user,
        action="Ticket Confirmed",
        entity_type="TicketVersion",
        entity_id=ticket_version.id,
        old_value=old_value,
        new_value={"ticket_status": ticket_version.ticket_status, "confirmed_by": user.id},
    )
    return ticket_version


def mark_ticket_cancelled(ticket_version: TicketVersion, reason: str, user) -> TicketVersion:
    ensure_unlocked(ticket_version)
    if not reason:
        raise ValidationError("Cancellation reason is required.")
    old_status = ticket_version.ticket_status
    ticket_version.ticket_status = TicketStatus.CANCELLED
    ticket_version.ticket_action = TicketAction.CANCELLATION
    ticket_version.change_reason = reason
    ticket_version.save(update_fields=["ticket_status", "ticket_action", "change_reason", "updated_at"])
    create_audit_log(
        user=user,
        action="Ticket Cancelled",
        entity_type="TicketVersion",
        entity_id=ticket_version.id,
        old_value={"ticket_status": old_status},
        new_value={"ticket_status": ticket_version.ticket_status, "change_reason": reason},
    )
    return ticket_version
