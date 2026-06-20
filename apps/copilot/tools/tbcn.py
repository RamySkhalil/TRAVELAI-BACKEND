"""Read-only copilot tool for searching Travel Billing Confirmation Notes."""
from __future__ import annotations

from django.db.models import Q

from apps.billing_confirmations.models import TravelBillingConfirmationNote

from .common import CARD_TBCN, card, money, tool_result

SEARCH_LIMIT = 8


def _tbcn_url(tbcn: TravelBillingConfirmationNote) -> str:
    return f"/tbcn/{tbcn.id}"


def search_tbcn(user, query: str = "", **_) -> dict:
    query = str(query or "").strip()
    queryset = TravelBillingConfirmationNote.objects.select_related("supplier").order_by("-generated_at")
    if query:
        queryset = queryset.filter(
            Q(confirmation_no__icontains=query)
            | Q(supplier_invoice_number__icontains=query)
            | Q(supplier__name__icontains=query)
            | Q(supplier__code__icontains=query)
        )
    notes = list(queryset[:SEARCH_LIMIT])
    if not notes:
        suffix = f" matching '{query}'" if query else ""
        return tool_result("search_tbcn", f"I found no TBCNs{suffix}.")

    cards = [
        card(
            CARD_TBCN,
            title=tbcn.confirmation_no,
            subtitle=f"{tbcn.supplier.name} · {money(tbcn.total_amount, tbcn.currency)} · {'PDF ready' if tbcn.pdf_file else 'no PDF'}",
            status=f"{tbcn.status} / {tbcn.finance_status}",
            url=_tbcn_url(tbcn),
        )
        for tbcn in notes
    ]
    summary = f"I found {len(notes)} TBCN(s)" + (f" matching '{query}'." if query else ".")
    data = {
        "count": len(notes),
        "tbcns": [
            {
                "confirmation_no": tbcn.confirmation_no,
                "supplier": tbcn.supplier.name,
                "supplier_invoice_number": tbcn.supplier_invoice_number,
                "status": tbcn.status,
                "finance_status": tbcn.finance_status,
                "currency": tbcn.currency,
                "total_amount": str(tbcn.total_amount),
                "pdf_available": bool(tbcn.pdf_file),
            }
            for tbcn in notes
        ],
    }
    return tool_result("search_tbcn", summary, cards, data)
