"""Read-only copilot tools for supplier invoices and invoice matching."""
from __future__ import annotations

from django.db.models import Q

from apps.billing_confirmations.models import BillingConfirmationStatus
from apps.supplier_invoices.models import MatchStatus, SupplierInvoice, SupplierInvoiceLine, SupplierInvoiceStatus

from .common import CARD_INVOICE_LINE, CARD_SUPPLIER_INVOICE, card, money, tool_result

SEARCH_LIMIT = 8
EXCEPTION_LIMIT = 8
READY_LIMIT = 8

OPEN_EXCEPTION_STATUSES = [MatchStatus.UNMATCHED, MatchStatus.DIFFERENCE, MatchStatus.EXCEPTION]


def _matching_url(invoice_id) -> str:
    return f"/supplier-invoices/{invoice_id}/matching"


def _active_tbcn(invoice: SupplierInvoice):
    return (
        invoice.billing_confirmations.exclude(
            status__in=[BillingConfirmationStatus.CANCELLED, BillingConfirmationStatus.REVISED]
        )
        .order_by("-generated_at")
        .first()
    )


def _resolve_invoice(identifier: str) -> SupplierInvoice | None:
    identifier = str(identifier or "").strip()
    if not identifier:
        return None
    queryset = SupplierInvoice.objects.select_related("supplier").prefetch_related("lines", "billing_confirmations")
    invoice = queryset.filter(invoice_record_number__iexact=identifier).first()
    if invoice:
        return invoice
    invoice = queryset.filter(supplier_invoice_number__iexact=identifier).first()
    if invoice:
        return invoice
    if identifier.isdigit():
        invoice = queryset.filter(id=int(identifier)).first()
        if invoice:
            return invoice
    return queryset.filter(uid__iexact=identifier).first()


def next_action_for_invoice(invoice: SupplierInvoice) -> str:
    status = invoice.status
    if status in [SupplierInvoiceStatus.INVOICE_RECEIVED, SupplierInvoiceStatus.DATA_EXTRACTED, SupplierInvoiceStatus.AWAITING_MATCHING]:
        return "Run invoice matching on the invoice matching screen."
    if status == SupplierInvoiceStatus.EXCEPTION_FOUND:
        return "Resolve the invoice line exceptions on the invoice matching screen."
    if status == SupplierInvoiceStatus.MATCHED:
        return "Awaiting HR approval on the invoice matching screen."
    if status == SupplierInvoiceStatus.HR_APPROVED:
        if _active_tbcn(invoice):
            return "TBCN already exists. Continue from the TBCN screen."
        return "HR approved and ready for TBCN. Generate the TBCN on the invoice matching screen."
    if status == SupplierInvoiceStatus.TBCN_GENERATED:
        return "TBCN generated. Send it to finance from the TBCN screen."
    if status in [SupplierInvoiceStatus.SENT_TO_FINANCE, SupplierInvoiceStatus.FINANCE_ACCEPTED]:
        return "With finance. Track acceptance and payment on the finance screens."
    if status in [SupplierInvoiceStatus.PAID, SupplierInvoiceStatus.CLOSED]:
        return "No action required. The invoice is paid or closed."
    return "Continue the invoice workflow on the invoice matching screen."


def search_supplier_invoices(user, query: str = "", **_) -> dict:
    query = str(query or "").strip()
    queryset = SupplierInvoice.objects.select_related("supplier").order_by("-received_date", "-created_at")
    if query:
        queryset = queryset.filter(
            Q(invoice_record_number__icontains=query)
            | Q(supplier_invoice_number__icontains=query)
            | Q(supplier__name__icontains=query)
            | Q(supplier__code__icontains=query)
            | Q(status__icontains=query)
        )
    invoices = list(queryset[:SEARCH_LIMIT])
    if not invoices:
        suffix = f" matching '{query}'" if query else ""
        return tool_result("search_supplier_invoices", f"I found no supplier invoices{suffix}.")

    cards = [
        card(
            CARD_SUPPLIER_INVOICE,
            title=invoice.invoice_record_number,
            subtitle=f"{invoice.supplier.name} · {invoice.supplier_invoice_number} · {money(invoice.total_amount, invoice.currency)}",
            status=invoice.status,
            url=_matching_url(invoice.id),
        )
        for invoice in invoices
    ]
    summary = f"I found {len(invoices)} supplier invoice(s)" + (f" matching '{query}'." if query else ".")
    data = {
        "count": len(invoices),
        "invoices": [
            {
                "invoice_record_number": invoice.invoice_record_number,
                "supplier_invoice_number": invoice.supplier_invoice_number,
                "supplier": invoice.supplier.name,
                "status": invoice.status,
                "currency": invoice.currency,
                "total_amount": str(invoice.total_amount),
            }
            for invoice in invoices
        ],
    }
    return tool_result("search_supplier_invoices", summary, cards, data)


def explain_supplier_invoice_status(user, identifier: str = "", **_) -> dict:
    invoice = _resolve_invoice(identifier)
    if not invoice:
        return tool_result(
            "explain_supplier_invoice_status",
            f"I could not find a supplier invoice for '{identifier}'. Try the SIR number such as SIR-LY-2026-000001 or the supplier invoice number.",
        )

    lines = list(invoice.lines.all())
    exception_lines = [line for line in lines if line.match_status in OPEN_EXCEPTION_STATUSES]
    hr_approved = invoice.status in [
        SupplierInvoiceStatus.HR_APPROVED,
        SupplierInvoiceStatus.TBCN_GENERATED,
        SupplierInvoiceStatus.SENT_TO_FINANCE,
        SupplierInvoiceStatus.FINANCE_ACCEPTED,
        SupplierInvoiceStatus.PAID,
    ]
    active_tbcn = _active_tbcn(invoice)
    tbcn_ready = invoice.status == SupplierInvoiceStatus.HR_APPROVED and not active_tbcn
    next_action = next_action_for_invoice(invoice)

    parts = [
        f"{invoice.invoice_record_number} ({invoice.supplier.name}, {money(invoice.total_amount, invoice.currency)}) is {invoice.status}.",
        f"{len(lines)} line(s), {len(exception_lines)} with open exceptions.",
        "HR approved." if hr_approved else "Not yet HR approved.",
    ]
    if active_tbcn:
        parts.append(f"Active TBCN {active_tbcn.confirmation_no} ({active_tbcn.finance_status}).")
    elif tbcn_ready:
        parts.append("Ready for TBCN generation.")
    else:
        parts.append("No TBCN yet.")
    parts.append(f"Next action: {next_action}")
    summary = " ".join(parts)

    cards = [
        card(
            CARD_SUPPLIER_INVOICE,
            title=invoice.invoice_record_number,
            subtitle=f"{invoice.supplier.name} · {money(invoice.total_amount, invoice.currency)}",
            status=invoice.status,
            url=_matching_url(invoice.id),
        )
    ]
    data = {
        "invoice_record_number": invoice.invoice_record_number,
        "status": invoice.status,
        "line_count": len(lines),
        "exception_count": len(exception_lines),
        "hr_approved": hr_approved,
        "tbcn_ready": tbcn_ready,
        "active_tbcn": active_tbcn.confirmation_no if active_tbcn else None,
        "currency": invoice.currency,
        "total_amount": str(invoice.total_amount),
        "next_action": next_action,
    }
    return tool_result("explain_supplier_invoice_status", summary, cards, data)


def get_invoice_exceptions(user, **_) -> dict:
    lines = list(
        SupplierInvoiceLine.objects.filter(match_status__in=OPEN_EXCEPTION_STATUSES)
        .select_related("supplier_invoice", "supplier_invoice__supplier")
        .order_by("-updated_at")[:EXCEPTION_LIMIT]
    )
    if not lines:
        return tool_result("get_invoice_exceptions", "There are no invoice lines with open exceptions right now.")

    cards = [
        card(
            CARD_INVOICE_LINE,
            title=f"{line.supplier_invoice.invoice_record_number} · {line.ticket_number or 'no ticket #'}",
            subtitle=line.exception_reason or f"{line.match_status} ({money(line.invoiced_amount, line.currency)})",
            status=line.match_status,
            url=_matching_url(line.supplier_invoice_id),
        )
        for line in lines
    ]
    summary = f"There are {len(lines)} invoice line(s) with open exceptions needing review on the invoice matching screen."
    data = {
        "count": len(lines),
        "lines": [
            {
                "invoice_record_number": line.supplier_invoice.invoice_record_number,
                "ticket_number": line.ticket_number,
                "match_status": line.match_status,
                "exception_reason": line.exception_reason,
                "currency": line.currency,
            }
            for line in lines
        ],
    }
    return tool_result("get_invoice_exceptions", summary, cards, data)


def get_ready_for_tbcn(user, **_) -> dict:
    invoices = list(
        SupplierInvoice.objects.filter(status=SupplierInvoiceStatus.HR_APPROVED, billing_confirmations__isnull=True)
        .select_related("supplier")
        .order_by("-approved_at", "-updated_at")[:READY_LIMIT]
    )
    if not invoices:
        return tool_result("get_ready_for_tbcn", "No HR-approved invoices are currently waiting for a TBCN.")

    cards = [
        card(
            CARD_SUPPLIER_INVOICE,
            title=invoice.invoice_record_number,
            subtitle=f"{invoice.supplier.name} · {money(invoice.total_amount, invoice.currency)}",
            status=invoice.status,
            url=_matching_url(invoice.id),
        )
        for invoice in invoices
    ]
    summary = (
        f"{len(invoices)} HR-approved invoice(s) are ready for TBCN generation. "
        "Open the invoice matching screen to generate the TBCN; the copilot cannot generate it in this read-only phase."
    )
    data = {
        "count": len(invoices),
        "invoices": [
            {
                "invoice_record_number": invoice.invoice_record_number,
                "supplier": invoice.supplier.name,
                "currency": invoice.currency,
                "total_amount": str(invoice.total_amount),
            }
            for invoice in invoices
        ],
    }
    return tool_result("get_ready_for_tbcn", summary, cards, data)
