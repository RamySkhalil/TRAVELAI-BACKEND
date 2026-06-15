from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_time
from rest_framework.exceptions import ValidationError

from apps.audit_logs.services import create_audit_log
from apps.common.services.locking import ensure_unlocked, lock_instance
from apps.master_data.models import Supplier

from .models import TicketAction, TicketStatus, TicketVersion


TICKET_DOCUMENT_TYPES = {
    "FLIGHT_TICKET",
    "CHANGED_TICKET",
    "REISSUED_TICKET",
    "CANCELLATION",
    "REFUND_NOTE",
}


def get_next_version_number(travel_case) -> str:
    existing_count = TicketVersion.objects.filter(travel_case=travel_case).count()
    return f"V{existing_count + 1}"


def create_ticket_version_from_confirmed_data(travel_case, data, user) -> TicketVersion:
    data = dict(data)
    with transaction.atomic():
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


def create_ticket_version_from_confirmed_extraction(extraction_job, travel_case, ticket_action: str, overrides=None, user=None) -> TicketVersion:
    from apps.ai_extraction.models import ExtractionStatus

    if not travel_case:
        raise ValidationError({"travel_case": "Travel case is required."})
    if extraction_job.status != ExtractionStatus.CONFIRMED:
        raise ValidationError({"extraction_job": "Extraction job must be confirmed before creating a TicketVersion."})
    if extraction_job.document_type not in TICKET_DOCUMENT_TYPES:
        raise ValidationError({"extraction_job": "Extraction job document type is not ticket-related."})
    if ticket_action not in TicketAction.values:
        raise ValidationError({"ticket_action": "Select a valid ticket action."})

    normalized_data = dict(extraction_job.normalized_data or {})
    merged_data = {**normalized_data, **(overrides or {})}
    ticket_data = _ticket_data_from_extraction(merged_data, ticket_action, extraction_job)
    ticket_version = create_ticket_version_from_confirmed_data(travel_case, ticket_data, user)
    create_audit_log(
        user=user,
        action="Ticket Version Created From Extraction",
        entity_type="TicketVersion",
        entity_id=ticket_version.id,
        new_value={"version_number": ticket_version.version_number, "ticket_number": ticket_version.ticket_number},
        metadata={"travel_case": travel_case.id, "extraction_job": extraction_job.id},
    )
    return ticket_version


def _ticket_data_from_extraction(data: dict, ticket_action: str, extraction_job) -> dict:
    return {
        "ticket_action": ticket_action,
        "passenger_name": _required_text(data, "passenger_name"),
        "ticket_number": _required_text(data, "ticket_number"),
        "pnr": _required_text(data, "pnr", fallback_keys=("booking_reference",)),
        "airline": data.get("airline") or data.get("supplier") or "",
        "route_from": _required_text(data, "route_from"),
        "route_to": _required_text(data, "route_to"),
        "departure_date": _required_date(data, "departure_date"),
        "departure_time": _optional_time(data.get("departure_time")),
        "arrival_date": _optional_date(data.get("arrival_date")),
        "arrival_time": _optional_time(data.get("arrival_time")),
        "amount": _required_decimal(data, "amount"),
        "currency": str(data.get("currency") or "USD").upper()[:3],
        "supplier": _supplier_from_extraction(data.get("supplier")),
        "ticket_status": data.get("ticket_status") if data.get("ticket_status") in TicketStatus.values else TicketStatus.DRAFT,
        "penalty_amount": _optional_decimal(data.get("penalty_amount")),
        "change_reason": data.get("change_reason") or "",
        "extracted_data_json": extraction_job.normalized_data or {},
        "ai_confidence_json": extraction_job.confidence_json or data.get("confidence") or {},
    }


def _required_text(data: dict, key: str, fallback_keys=()) -> str:
    value = data.get(key)
    for fallback_key in fallback_keys:
        if value:
            break
        value = data.get(fallback_key)
    value = str(value or "").strip()
    if not value:
        raise ValidationError({key: "This field is required in the confirmed extraction data."})
    return value


def _required_date(data: dict, key: str):
    value = _optional_date(data.get(key))
    if value is None:
        raise ValidationError({key: "A valid date is required in the confirmed extraction data."})
    return value


def _optional_date(value):
    if not value:
        return None
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        return value
    parsed = parse_date(str(value))
    if parsed is None:
        raise ValidationError("Date values must use YYYY-MM-DD format.")
    return parsed


def _optional_time(value):
    if not value:
        return None
    if hasattr(value, "hour") and hasattr(value, "minute"):
        return value
    parsed = parse_time(str(value))
    if parsed is None:
        raise ValidationError("Time values must use HH:MM[:SS] format.")
    return parsed


def _required_decimal(data: dict, key: str) -> Decimal:
    value = data.get(key)
    if value in (None, ""):
        raise ValidationError({key: "This field is required in the confirmed extraction data."})
    return _optional_decimal(value)


def _optional_decimal(value) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError("Amount values must be valid decimals.") from exc


def _supplier_from_extraction(value) -> Supplier:
    if not value:
        raise ValidationError({"supplier": "Supplier is required in the confirmed extraction data."})
    if isinstance(value, int):
        supplier = Supplier.objects.filter(pk=value).first()
        if not supplier:
            raise ValidationError({"supplier": "Supplier from extraction data was not found in master data."})
        return supplier
    text = str(value).strip()
    if text.isdigit():
        supplier = Supplier.objects.filter(pk=int(text)).first()
        if not supplier:
            raise ValidationError({"supplier": "Supplier from extraction data was not found in master data."})
        return supplier
    supplier = Supplier.objects.filter(code__iexact=text).first() or Supplier.objects.filter(name__iexact=text).first()
    if not supplier:
        raise ValidationError({"supplier": "Supplier from extraction data was not found in master data."})
    return supplier


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
