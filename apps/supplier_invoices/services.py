from datetime import date

from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.audit_logs.services import create_audit_log
from apps.common.services.locking import ensure_unlocked, lock_instance
from apps.common.services.sequences import generate_sequence

from .models import MatchStatus, SupplierInvoice, SupplierInvoiceLine, SupplierInvoiceStatus


def next_supplier_invoice_record_number(country_code: str, year: int | None = None) -> str:
    target_year = year or date.today().year
    return generate_sequence(f"SIR-{country_code}", target_year, "{code}-{year}-{number:06d}")


def lock_supplier_invoice(invoice: SupplierInvoice) -> SupplierInvoice:
    return lock_instance(invoice)


def ensure_invoice_editable(invoice: SupplierInvoice) -> None:
    ensure_unlocked(invoice)


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
