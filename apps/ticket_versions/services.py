import os
from decimal import Decimal, InvalidOperation

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_time
from django.utils.text import slugify
from rest_framework.exceptions import ValidationError

from apps.audit_logs.services import create_audit_log
from apps.common.currency import normalize_currency
from apps.common.services.locking import ensure_unlocked, lock_instance
from apps.master_data.models import Supplier
from apps.travel_cases.services import mark_case_ticket_booked

from .models import TicketAction, TicketBillingState, TicketStatus, TicketVersion


TICKET_DOCUMENT_TYPES = {
    "FLIGHT_TICKET",
    "CHANGED_TICKET",
    "REISSUED_TICKET",
    "CANCELLATION",
    "REFUND_NOTE",
}

# A wrongly uploaded document never represents money owed to a supplier.
NON_BILLABLE_TICKET_ACTIONS = frozenset({TicketAction.WRONG_UPLOAD})


def get_next_version_number(travel_case) -> str:
    existing_count = TicketVersion.objects.filter(travel_case=travel_case).count()
    return f"V{existing_count + 1}"


def create_ticket_version_from_confirmed_data(travel_case, data, user) -> TicketVersion:
    data = dict(data)
    data["currency"] = normalize_currency(data.get("currency"))
    with transaction.atomic():
        data["travel_case"] = travel_case
        data["version_number"] = get_next_version_number(travel_case)
        ticket_version = TicketVersion.objects.create(**data)
        mark_case_ticket_booked(travel_case, user)
        # Tickets entered directly as non-draft skip the confirm step, so open
        # their payable here too.
        mark_ticket_awaiting_invoice(ticket_version, user)
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
    _attach_source_document(ticket_version, extraction_job, user)
    create_audit_log(
        user=user,
        action="Ticket Version Created From Extraction",
        entity_type="TicketVersion",
        entity_id=ticket_version.id,
        new_value={"version_number": ticket_version.version_number, "ticket_number": ticket_version.ticket_number},
        metadata={"travel_case": travel_case.id, "extraction_job": extraction_job.id},
    )
    return ticket_version


def _attach_source_document(ticket_version: TicketVersion, extraction_job, user=None) -> None:
    """Copy the confirmed extraction's source file onto the ticket version.

    The original stays on the extraction job for audit; the ticket version keeps
    its own copy in the configured storage (Cloudflare R2/S3 when enabled, local
    filesystem otherwise) so the document is available from ticket history.
    Missing source files (text-only extractions) are skipped silently.
    """
    source_file = getattr(extraction_job, "source_file", None)
    if not source_file:
        return

    try:
        source_file.open("rb")
        content = source_file.read()
    finally:
        source_file.close()

    filename = _ticket_document_filename(ticket_version, source_file.name)
    ticket_version.uploaded_ticket_file.save(filename, ContentFile(content), save=True)
    create_audit_log(
        user=user,
        action="Ticket Document Attached",
        entity_type="TicketVersion",
        entity_id=ticket_version.id,
        new_value={"uploaded_ticket_file": ticket_version.uploaded_ticket_file.name},
        metadata={"extraction_job": extraction_job.id},
    )


def _ticket_document_filename(ticket_version: TicketVersion, source_name: str) -> str:
    extension = os.path.splitext(source_name or "")[1].lower() or ".pdf"
    case_number = slugify(ticket_version.travel_case.case_number) or "ticket"
    version = slugify(ticket_version.version_number) or "v"
    ticket_number = slugify(ticket_version.ticket_number) or "ticket"
    return f"{case_number}-{version}-{ticket_number}{extension}"


def _ticket_data_from_extraction(data: dict, ticket_action: str, extraction_job) -> dict:
    supplier_value = data.get("supplier")
    airline_fallback = supplier_value if isinstance(supplier_value, str) else ""
    return {
        "ticket_action": ticket_action,
        "passenger_name": _required_text(data, "passenger_name"),
        "ticket_number": _required_text(data, "ticket_number"),
        "pnr": _required_text(data, "pnr", fallback_keys=("booking_reference",)),
        "airline": data.get("airline") or airline_fallback or "",
        "route_from": _required_text(data, "route_from"),
        "route_to": _required_text(data, "route_to"),
        "departure_date": _required_date(data, "departure_date"),
        "departure_time": _optional_time(data.get("departure_time")),
        "arrival_date": _optional_date(data.get("arrival_date")),
        "arrival_time": _optional_time(data.get("arrival_time")),
        "amount": _required_decimal(data, "amount"),
        "currency": normalize_currency(data.get("currency")),
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


def _set_billing_state(ticket_version: TicketVersion, state: str, user, *, audit_action: str, note=None, metadata=None) -> TicketVersion:
    """Persist a billing state change and audit it. No-ops when nothing changes."""
    note_changes = note is not None and note != ticket_version.billing_state_note
    if ticket_version.billing_state == state and not note_changes:
        return ticket_version
    old_value = {
        "billing_state": ticket_version.billing_state,
        "billing_state_note": ticket_version.billing_state_note,
    }
    update_fields = ["billing_state", "billing_state_changed_at", "updated_at"]
    ticket_version.billing_state = state
    ticket_version.billing_state_changed_at = timezone.now()
    if note is not None:
        ticket_version.billing_state_note = note
        update_fields.append("billing_state_note")
    ticket_version.save(update_fields=update_fields)
    create_audit_log(
        user=user,
        action=audit_action,
        entity_type="TicketVersion",
        entity_id=ticket_version.id,
        old_value=old_value,
        new_value={"billing_state": ticket_version.billing_state, "billing_state_note": ticket_version.billing_state_note},
        metadata=metadata or {"travel_case": ticket_version.travel_case_id},
    )
    return ticket_version


def mark_ticket_awaiting_invoice(ticket_version: TicketVersion, user=None) -> TicketVersion:
    """Open the payable for a confirmed ticket so it is visible before invoicing.

    Only a confirmed, billable ticket becomes a payable: a ``DRAFT`` ticket is not
    a committed cost yet, and a wrongly uploaded document never is. Tickets that
    were already invoiced, or that a human deliberately marked not billable, are
    left alone so this can be called from any confirmation path.
    """
    if ticket_version.ticket_status == TicketStatus.DRAFT:
        return ticket_version
    if ticket_version.ticket_action in NON_BILLABLE_TICKET_ACTIONS:
        return ticket_version
    if ticket_version.billing_state != TicketBillingState.NOT_BILLABLE:
        return ticket_version
    if ticket_version.billing_state_note:
        return ticket_version
    return _set_billing_state(
        ticket_version,
        TicketBillingState.AWAITING_INVOICE,
        user,
        audit_action="Ticket Awaiting Supplier Invoice",
    )


def mark_ticket_invoiced(ticket_version: TicketVersion, user=None, metadata=None) -> TicketVersion:
    """Close the payable once a supplier invoice line points at this ticket."""
    return _set_billing_state(
        ticket_version,
        TicketBillingState.INVOICED,
        user,
        audit_action="Ticket Invoiced",
        metadata=metadata,
    )


def mark_ticket_not_billable(ticket_version: TicketVersion, reason: str, user=None) -> TicketVersion:
    """Take a ticket out of the awaiting-invoice queue when no invoice will arrive.

    Needed for cancellations that carry no penalty and duplicate uploads, which
    would otherwise age in the queue forever. A reason is mandatory and audited.
    """
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("A reason is required to mark a ticket not billable.")
    if ticket_version.billing_state == TicketBillingState.INVOICED:
        raise ValidationError("An invoiced ticket cannot be marked not billable.")
    return _set_billing_state(
        ticket_version,
        TicketBillingState.NOT_BILLABLE,
        user,
        audit_action="Ticket Marked Not Billable",
        note=reason,
    )


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
    mark_ticket_awaiting_invoice(ticket_version, user)
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
