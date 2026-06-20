from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.audit_logs.services import create_audit_log
from apps.supplier_invoices.models import MatchStatus, SupplierInvoiceStatus
from apps.ticket_versions.models import TicketVersion


def calculate_line_difference(booked_amount, invoiced_amount):
    return invoiced_amount - booked_amount


def match_invoice_line(line, user):
    old_value = {
        "ticket_version": line.ticket_version_id,
        "travel_case": line.travel_case_id,
        "booked_amount": str(line.booked_amount),
        "difference_amount": str(line.difference_amount),
        "currency": line.currency,
        "match_status": line.match_status,
        "exception_reason": line.exception_reason,
    }
    ticket_version = TicketVersion.objects.filter(ticket_number=line.ticket_number).select_related("travel_case").order_by("-created_at").first()
    metadata = {}
    if not ticket_version:
        line.match_status = MatchStatus.EXCEPTION
        line.exception_reason = "Ticket number not found"
    else:
        line.ticket_version = ticket_version
        line.travel_case = ticket_version.travel_case
        line.employee = ticket_version.travel_case.employee
        line.account_type = ticket_version.travel_case.account_type
        line.booked_amount = ticket_version.amount
        if ticket_version.currency != line.currency:
            line.difference_amount = 0
            line.match_status = MatchStatus.EXCEPTION
            line.exception_reason = f"Currency mismatch: ticket is {ticket_version.currency}, invoice line is {line.currency}"
            metadata["currency_mismatch"] = {
                "ticket_currency": ticket_version.currency,
                "invoice_line_currency": line.currency,
            }
        else:
            line.difference_amount = calculate_line_difference(line.booked_amount, line.invoiced_amount)
            if line.difference_amount == 0:
                line.match_status = MatchStatus.MATCHED
                line.exception_reason = ""
            else:
                line.match_status = MatchStatus.DIFFERENCE
                line.exception_reason = "Invoice amount differs from booked ticket amount"
        if ticket_version.travel_case.account_type == "PERSONAL":
            metadata["personal_account_flag"] = True
    line.save(
        update_fields=[
            "ticket_version",
            "travel_case",
            "employee",
            "account_type",
            "booked_amount",
            "difference_amount",
            "match_status",
            "exception_reason",
            "updated_at",
        ]
    )
    create_audit_log(
        user=user,
        action="Invoice Line Matched",
        entity_type="SupplierInvoiceLine",
        entity_id=line.id,
        old_value=old_value,
        new_value={"match_status": line.match_status, "difference_amount": str(line.difference_amount)},
        metadata=metadata,
    )
    if metadata.get("currency_mismatch"):
        create_audit_log(
            user=user,
            action="Invoice Line Currency Mismatch",
            entity_type="SupplierInvoiceLine",
            entity_id=line.id,
            old_value=old_value,
            new_value={
                "match_status": line.match_status,
                "exception_reason": line.exception_reason,
                "difference_amount": str(line.difference_amount),
            },
            metadata=metadata["currency_mismatch"],
        )
    return line


def match_invoice(invoice, user):
    lines = list(invoice.lines.all())
    if not lines:
        raise ValidationError("Supplier invoice has no lines to match.")
    with transaction.atomic():
        matched_lines = [match_invoice_line(line, user) for line in lines]
        old_status = invoice.status
        if any(line.match_status == MatchStatus.EXCEPTION for line in matched_lines):
            invoice.status = SupplierInvoiceStatus.EXCEPTION_FOUND
        else:
            invoice.status = SupplierInvoiceStatus.MATCHED
        invoice.save(update_fields=["status", "updated_at"])
    create_audit_log(
        user=user,
        action="Invoice Matching Run",
        entity_type="SupplierInvoice",
        entity_id=invoice.id,
        old_value={"status": old_status},
        new_value={"status": invoice.status},
        metadata={"line_count": len(matched_lines)},
    )
    return invoice


def resolve_invoice_line_exception(line, reason, user):
    if not reason:
        raise ValidationError("Exception resolution reason is required.")
    old_value = {"match_status": line.match_status, "exception_reason": line.exception_reason}
    line.exception_reason = reason
    line.match_status = MatchStatus.APPROVED
    line.save(update_fields=["exception_reason", "match_status", "updated_at"])
    create_audit_log(
        user=user,
        action="Invoice Line Exception Resolved",
        entity_type="SupplierInvoiceLine",
        entity_id=line.id,
        old_value=old_value,
        new_value={"match_status": line.match_status, "exception_reason": reason},
    )
    return line
