from datetime import date

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit_logs.services import create_audit_log
from apps.common.services.locking import lock_instance
from apps.common.services.sequences import generate_sequence
from apps.supplier_invoices.models import SupplierInvoiceStatus

from .models import BillingConfirmationStatus, FinanceStatus, TravelBillingConfirmationNote


def next_tbcn_number(country_code: str, year: int | None = None) -> str:
    target_year = year or date.today().year
    return generate_sequence(f"TBCN-{country_code}", target_year, "{code}-{year}-{number:06d}")


def lock_records_after_tbcn(*records) -> None:
    for record in records:
        lock_instance(record)


def generate_tbcn(supplier_invoice, user) -> TravelBillingConfirmationNote:
    if supplier_invoice.status != SupplierInvoiceStatus.HR_APPROVED:
        raise ValidationError("TBCN can only be generated for HR approved supplier invoices.")
    if supplier_invoice.billing_confirmations.exclude(
        status__in=[BillingConfirmationStatus.CANCELLED, BillingConfirmationStatus.REVISED]
    ).exists():
        raise ValidationError("An active TBCN already exists for this supplier invoice.")
    lines = list(supplier_invoice.lines.select_related("travel_case", "ticket_version").all())
    if not lines:
        raise ValidationError("TBCN cannot be generated without invoice lines.")
    country_code = next((line.travel_case.country.code for line in lines if line.travel_case_id), "LY")
    year = supplier_invoice.invoice_date.year if supplier_invoice.invoice_date else date.today().year
    matched_amount = sum((line.invoiced_amount for line in lines), start=0)
    difference_amount = sum((line.difference_amount for line in lines), start=0)

    with transaction.atomic():
        tbcn = TravelBillingConfirmationNote.objects.create(
            confirmation_no=next_tbcn_number(country_code, year),
            supplier_invoice=supplier_invoice,
            supplier=supplier_invoice.supplier,
            supplier_invoice_number=supplier_invoice.supplier_invoice_number,
            total_amount=supplier_invoice.total_amount,
            matched_amount=matched_amount,
            difference_amount=difference_amount,
            currency=supplier_invoice.currency,
            status=BillingConfirmationStatus.GENERATED,
            finance_status=FinanceStatus.NOT_SENT,
            generated_by=user,
            generated_at=timezone.now(),
            is_locked=True,
        )
        lock_instance(supplier_invoice, save=False)
        supplier_invoice.status = SupplierInvoiceStatus.TBCN_GENERATED
        supplier_invoice.save(update_fields=["is_locked", "locked_at", "status", "updated_at"])
        for line in lines:
            line.is_locked = True
            line.save(update_fields=["is_locked", "updated_at"])
            if line.ticket_version_id:
                lock_instance(line.ticket_version)
    create_audit_log(
        user=user,
        action="TBCN Generated",
        entity_type="TravelBillingConfirmationNote",
        entity_id=tbcn.id,
        new_value={"confirmation_no": tbcn.confirmation_no, "status": tbcn.status, "finance_status": tbcn.finance_status},
        metadata={"supplier_invoice": supplier_invoice.id},
    )
    return tbcn


def _ensure_generated_tbcn(tbcn):
    if tbcn.status not in [
        BillingConfirmationStatus.GENERATED,
        BillingConfirmationStatus.REVIEWED,
        BillingConfirmationStatus.SENT_TO_FINANCE,
        BillingConfirmationStatus.FINANCE_ACCEPTED,
        BillingConfirmationStatus.PAID,
    ]:
        raise ValidationError("Finance actions require a generated TBCN.")


def send_tbcn_to_finance(tbcn, user):
    _ensure_generated_tbcn(tbcn)
    old_value = {"status": tbcn.status, "finance_status": tbcn.finance_status}
    tbcn.sent_to_finance_by = user
    tbcn.sent_to_finance_at = timezone.now()
    tbcn.finance_status = FinanceStatus.SENT
    tbcn.status = BillingConfirmationStatus.SENT_TO_FINANCE
    tbcn.save(update_fields=["sent_to_finance_by", "sent_to_finance_at", "finance_status", "status", "updated_at"])
    create_audit_log(
        user=user,
        action="TBCN Sent To Finance",
        entity_type="TravelBillingConfirmationNote",
        entity_id=tbcn.id,
        old_value=old_value,
        new_value={"status": tbcn.status, "finance_status": tbcn.finance_status},
    )
    return tbcn


def mark_finance_accepted(tbcn, user):
    _ensure_generated_tbcn(tbcn)
    if tbcn.finance_status == FinanceStatus.NOT_SENT:
        raise ValidationError("TBCN must be sent to finance before acceptance.")
    old_value = {"status": tbcn.status, "finance_status": tbcn.finance_status}
    tbcn.finance_status = FinanceStatus.ACCEPTED
    tbcn.status = BillingConfirmationStatus.FINANCE_ACCEPTED
    tbcn.reviewed_by = tbcn.reviewed_by or user
    tbcn.reviewed_at = tbcn.reviewed_at or timezone.now()
    tbcn.save(update_fields=["finance_status", "status", "reviewed_by", "reviewed_at", "updated_at"])
    create_audit_log(
        user=user,
        action="TBCN Finance Accepted",
        entity_type="TravelBillingConfirmationNote",
        entity_id=tbcn.id,
        old_value=old_value,
        new_value={"status": tbcn.status, "finance_status": tbcn.finance_status},
    )
    return tbcn


def mark_tbcn_paid(tbcn, user):
    _ensure_generated_tbcn(tbcn)
    if tbcn.finance_status != FinanceStatus.ACCEPTED:
        raise ValidationError("TBCN must be finance accepted before marking paid.")
    old_value = {"status": tbcn.status, "finance_status": tbcn.finance_status}
    tbcn.finance_status = FinanceStatus.PAID
    tbcn.status = BillingConfirmationStatus.PAID
    tbcn.save(update_fields=["finance_status", "status", "updated_at"])
    create_audit_log(
        user=user,
        action="TBCN Paid",
        entity_type="TravelBillingConfirmationNote",
        entity_id=tbcn.id,
        old_value=old_value,
        new_value={"status": tbcn.status, "finance_status": tbcn.finance_status},
    )
    return tbcn
