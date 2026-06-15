from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils.dateparse import parse_date
from rest_framework.exceptions import ValidationError

from apps.ai_extraction.models import DocumentExtractionJob, DocumentType, ExtractionStatus
from apps.audit_logs.services import create_audit_log
from apps.common.services.locking import ensure_unlocked, lock_instance
from apps.common.services.sequences import generate_sequence
from apps.master_data.models import Supplier
from apps.travel_cases.models import AccountType

from .models import MatchStatus, SupplierInvoice, SupplierInvoiceLine, SupplierInvoiceStatus


FINANCE_READY_INVOICE_STATUSES = {
    SupplierInvoiceStatus.TBCN_GENERATED,
}


def next_supplier_invoice_record_number(country_code: str, year: int | None = None) -> str:
    target_year = year or date.today().year
    return generate_sequence(f"SIR-{country_code}", target_year, "{code}-{year}-{number:06d}")


def lock_supplier_invoice(invoice: SupplierInvoice) -> SupplierInvoice:
    return lock_instance(invoice)


def ensure_invoice_editable(invoice: SupplierInvoice) -> None:
    ensure_unlocked(invoice)


def is_supplier_invoice_finance_ready(invoice: SupplierInvoice) -> bool:
    """Finance readiness is controlled by an active, system-generated TBCN."""
    if invoice.status not in FINANCE_READY_INVOICE_STATUSES:
        return False
    return invoice.billing_confirmations.exclude(status__in=["CANCELLED", "REVISED"]).exists()


def create_supplier_invoice(data, user) -> SupplierInvoice:
    data = dict(data)
    country_code = data.pop("country_code", "LY")
    invoice_date = data.get("invoice_date") or date.today()
    data["invoice_record_number"] = next_supplier_invoice_record_number(country_code, invoice_date.year)
    data["created_by"] = user
    invoice = SupplierInvoice.objects.create(**data)
    create_audit_log(
        user=user,
        action="Supplier Invoice Created",
        entity_type="SupplierInvoice",
        entity_id=invoice.id,
        new_value={"invoice_record_number": invoice.invoice_record_number, "status": invoice.status},
    )
    return invoice


def create_invoice_line(invoice: SupplierInvoice, data, user) -> SupplierInvoiceLine:
    ensure_invoice_editable(invoice)
    line = SupplierInvoiceLine.objects.create(supplier_invoice=invoice, **data)
    create_audit_log(
        user=user,
        action="Supplier Invoice Line Created",
        entity_type="SupplierInvoiceLine",
        entity_id=line.id,
        new_value={"ticket_number": line.ticket_number, "match_status": line.match_status},
        metadata={"supplier_invoice": invoice.id},
    )
    return line


def create_supplier_invoice_from_confirmed_extraction(
    extraction_job: DocumentExtractionJob,
    supplier: Supplier,
    overrides=None,
    user=None,
) -> SupplierInvoice:
    if extraction_job.status != ExtractionStatus.CONFIRMED:
        raise ValidationError({"extraction_job": "Extraction job must be confirmed before creating a SupplierInvoice."})
    if extraction_job.document_type != DocumentType.SUPPLIER_INVOICE:
        raise ValidationError({"extraction_job": "Extraction job document type must be SUPPLIER_INVOICE."})
    if not supplier:
        raise ValidationError({"supplier": "Supplier is required."})

    normalized_data = dict(extraction_job.normalized_data or {})
    data = {**normalized_data, **(overrides or {})}
    invoice_lines = data.get("lines") or []
    if not invoice_lines:
        raise ValidationError({"lines": "Confirmed extraction must include at least one invoice line."})

    invoice_date = _required_date(data, "invoice_date")
    received_date = _optional_date(data.get("received_date")) or date.today()
    country_code = str(data.get("country_code") or "LY").upper()

    with transaction.atomic():
        invoice = create_supplier_invoice(
            {
                "supplier": supplier,
                "supplier_invoice_number": _required_text(data, "supplier_invoice_number"),
                "invoice_date": invoice_date,
                "received_date": received_date,
                "currency": str(data.get("currency") or "USD").upper()[:3],
                "total_amount": _required_decimal(data, "total_amount"),
                "status": SupplierInvoiceStatus.AWAITING_MATCHING,
                "extracted_data_json": extraction_job.normalized_data or {},
                "ai_confidence_json": extraction_job.confidence_json or data.get("confidence") or {},
                "country_code": country_code,
            },
            user,
        )
        for line_data in invoice_lines:
            create_invoice_line(invoice, _invoice_line_data_from_extraction(line_data), user)
        create_audit_log(
            user=user,
            action="Supplier Invoice Created From Extraction",
            entity_type="SupplierInvoice",
            entity_id=invoice.id,
            new_value={"invoice_record_number": invoice.invoice_record_number, "status": invoice.status},
            metadata={"extraction_job": extraction_job.id, "line_count": len(invoice_lines)},
        )
    return invoice


def approve_supplier_invoice(invoice: SupplierInvoice, user) -> SupplierInvoice:
    ensure_invoice_editable(invoice)
    lines = list(invoice.lines.all())
    if not lines:
        raise ValidationError("Supplier invoice cannot be approved without invoice lines.")
    invalid_statuses = {MatchStatus.UNMATCHED, MatchStatus.DIFFERENCE, MatchStatus.EXCEPTION, MatchStatus.REJECTED}
    if any(line.match_status in invalid_statuses for line in lines):
        raise ValidationError("Supplier invoice can only be approved when all lines are matched or approved.")
    old_status = invoice.status
    invoice.status = SupplierInvoiceStatus.HR_APPROVED
    invoice.approved_by = user
    from django.utils import timezone

    invoice.approved_at = timezone.now()
    invoice.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
    create_audit_log(
        user=user,
        action="Supplier Invoice Approved",
        entity_type="SupplierInvoice",
        entity_id=invoice.id,
        old_value={"status": old_status},
        new_value={"status": invoice.status},
    )
    return invoice


def lock_supplier_invoice(invoice: SupplierInvoice, user=None) -> SupplierInvoice:
    with transaction.atomic():
        invoice = lock_instance(invoice)
        invoice.lines.update(is_locked=True)
    create_audit_log(
        user=user,
        action="Supplier Invoice Locked",
        entity_type="SupplierInvoice",
        entity_id=invoice.id,
        new_value={"is_locked": True, "lines_locked": invoice.lines.count()},
    )
    return invoice


def _invoice_line_data_from_extraction(data: dict) -> dict:
    return {
        "ticket_number": _required_text(data, "ticket_number"),
        "route_from": str(data.get("route_from") or "").strip(),
        "route_to": str(data.get("route_to") or "").strip(),
        "invoiced_amount": _required_decimal(data, "amount", fallback_keys=("invoiced_amount",)),
        "account_type": data.get("account_type") if data.get("account_type") in AccountType.values else AccountType.COMPANY,
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


def _required_decimal(data: dict, key: str, fallback_keys=()) -> Decimal:
    value = data.get(key)
    for fallback_key in fallback_keys:
        if value not in (None, ""):
            break
        value = data.get(fallback_key)
    if value in (None, ""):
        raise ValidationError({key: "This field is required in the confirmed extraction data."})
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError("Amount values must be valid decimals.") from exc
