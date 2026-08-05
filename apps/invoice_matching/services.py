from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.audit_logs.services import create_audit_log
from apps.supplier_invoices.models import MatchStatus, SupplierInvoiceStatus
from apps.ticket_versions.models import TicketVersion
from apps.ticket_versions.services import mark_ticket_invoiced
from apps.travel_cases.models import TravelCaseStatus
from apps.travel_cases.services import advance_travel_case_status


# A human reviewer's decision on an exception line. Re-running matching must not
# silently overwrite it, otherwise the written justification is lost.
HUMAN_RESOLVED_STATUSES = frozenset({MatchStatus.APPROVED})

# Invoice statuses whose header may still be recomputed from its lines. Once an
# invoice is HR-approved or further along, matching results no longer move it.
RECOMPUTABLE_INVOICE_STATUSES = frozenset(
    {
        SupplierInvoiceStatus.INVOICE_RECEIVED,
        SupplierInvoiceStatus.DATA_EXTRACTED,
        SupplierInvoiceStatus.AWAITING_MATCHING,
        SupplierInvoiceStatus.MATCHED,
        SupplierInvoiceStatus.EXCEPTION_FOUND,
    }
)


def calculate_line_difference(booked_amount, invoiced_amount):
    return invoiced_amount - booked_amount


def match_invoice_line(line, user):
    if line.match_status in HUMAN_RESOLVED_STATUSES:
        return line
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
        mark_ticket_invoiced(
            ticket_version,
            user,
            metadata={"supplier_invoice": line.supplier_invoice_id, "supplier_invoice_line": line.id},
        )
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


def _distinct_matched_cases(lines):
    """Travel cases whose ticket resolved on a non-exception line, de-duplicated."""
    cases = {}
    for line in lines:
        if line.travel_case_id and line.match_status != MatchStatus.EXCEPTION:
            cases[line.travel_case_id] = line.travel_case
    return list(cases.values())


def _invoice_status_from_lines(lines) -> str:
    if any(line.match_status == MatchStatus.EXCEPTION for line in lines):
        return SupplierInvoiceStatus.EXCEPTION_FOUND
    return SupplierInvoiceStatus.MATCHED


def refresh_invoice_match_status(invoice, user=None):
    """Recompute the invoice header from the current state of its lines.

    Resolving the last exception on an invoice has to clear `EXCEPTION_FOUND`,
    otherwise the invoice never reappears in the HR approval queue, which only
    looks for `MATCHED`. Invoices past HR approval are left untouched.
    """
    if invoice.status not in RECOMPUTABLE_INVOICE_STATUSES:
        return invoice
    lines = list(invoice.lines.all())
    if not lines:
        return invoice
    target_status = _invoice_status_from_lines(lines)
    if invoice.status == target_status:
        return invoice
    old_status = invoice.status
    invoice.status = target_status
    invoice.save(update_fields=["status", "updated_at"])
    create_audit_log(
        user=user,
        action="Supplier Invoice Match Status Refreshed",
        entity_type="SupplierInvoice",
        entity_id=invoice.id,
        old_value={"status": old_status},
        new_value={"status": invoice.status},
    )
    return invoice


def match_invoice(invoice, user):
    lines = list(invoice.lines.all())
    if not lines:
        raise ValidationError("Supplier invoice has no lines to match.")
    preserved_line_ids = [line.id for line in lines if line.match_status in HUMAN_RESOLVED_STATUSES]
    with transaction.atomic():
        matched_lines = [match_invoice_line(line, user) for line in lines]
        old_status = invoice.status
        invoice.status = _invoice_status_from_lines(matched_lines)
        invoice.save(update_fields=["status", "updated_at"])
        for travel_case in _distinct_matched_cases(matched_lines):
            advance_travel_case_status(
                travel_case,
                TravelCaseStatus.INVOICE_MATCHED,
                user,
                action="Travel Case Invoice Matched",
                metadata={"supplier_invoice": invoice.id},
            )
    create_audit_log(
        user=user,
        action="Invoice Matching Run",
        entity_type="SupplierInvoice",
        entity_id=invoice.id,
        old_value={"status": old_status},
        new_value={"status": invoice.status},
        metadata={
            "line_count": len(matched_lines),
            "preserved_resolved_lines": preserved_line_ids,
        },
    )
    return invoice


def resolve_invoice_line_exception(line, reason, user):
    if not reason:
        raise ValidationError("Exception resolution reason is required.")
    old_value = {"match_status": line.match_status, "exception_reason": line.exception_reason}
    with transaction.atomic():
        line.exception_reason = reason
        line.match_status = MatchStatus.APPROVED
        line.save(update_fields=["exception_reason", "match_status", "updated_at"])
        # A case whose only line was an exception was skipped during matching, so
        # its billing progress depends on this resolution.
        if line.travel_case_id:
            advance_travel_case_status(
                line.travel_case,
                TravelCaseStatus.INVOICE_MATCHED,
                user,
                action="Travel Case Invoice Matched",
                metadata={"supplier_invoice": line.supplier_invoice_id, "exception_resolved": True},
            )
        if line.ticket_version_id:
            mark_ticket_invoiced(
                line.ticket_version,
                user,
                metadata={"supplier_invoice": line.supplier_invoice_id, "supplier_invoice_line": line.id},
            )
        refresh_invoice_match_status(line.supplier_invoice, user)
    create_audit_log(
        user=user,
        action="Invoice Line Exception Resolved",
        entity_type="SupplierInvoiceLine",
        entity_id=line.id,
        old_value=old_value,
        new_value={"match_status": line.match_status, "exception_reason": reason},
    )
    return line
