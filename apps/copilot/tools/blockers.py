"""Read-only copilot tool that explains why a record cannot move forward."""
from __future__ import annotations

from apps.billing_confirmations.models import BillingConfirmationStatus, FinanceStatus, TravelBillingConfirmationNote
from apps.permits.models import PermitStatus

from .common import CARD_TBCN, CARD_TRAVEL_CASE, card, tool_result
from .invoices import _matching_url, _resolve_invoice, next_action_for_invoice
from .travel_cases import _resolve_case, next_action_for_case

RECORD_TRAVEL_CASE = "TRAVEL_CASE"
RECORD_SUPPLIER_INVOICE = "SUPPLIER_INVOICE"
RECORD_TBCN = "TBCN"


def _explain_travel_case(identifier: str) -> dict:
    case = _resolve_case(identifier)
    if not case:
        return tool_result("explain_blocker", f"I could not find a travel case for '{identifier}'.")
    blockers = []
    pending_permits = [
        permit.get_permit_type_display()
        for permit in case.permits.all()
        if permit.status in [PermitStatus.PENDING, PermitStatus.EXPIRED]
    ]
    if pending_permits:
        blockers.append(f"Pending or expired permits: {', '.join(pending_permits)}.")
    next_action = next_action_for_case(case)
    blocker_text = " ".join(blockers) if blockers else "No hard blocker detected."
    summary = f"{case.case_number} is {case.current_status}. {blocker_text} Next action: {next_action}"
    cards = [card(CARD_TRAVEL_CASE, title=case.case_number, subtitle=blocker_text, status=case.current_status, url=f"/travel-requests/{case.id}")]
    return tool_result("explain_blocker", summary, cards, {"record_type": RECORD_TRAVEL_CASE, "status": case.current_status, "next_action": next_action})


def _explain_invoice(identifier: str) -> dict:
    invoice = _resolve_invoice(identifier)
    if not invoice:
        return tool_result("explain_blocker", f"I could not find a supplier invoice for '{identifier}'.")
    next_action = next_action_for_invoice(invoice)
    summary = f"{invoice.invoice_record_number} is {invoice.status}. Next action: {next_action}"
    cards = [
        card(
            "SUPPLIER_INVOICE",
            title=invoice.invoice_record_number,
            subtitle=invoice.supplier.name,
            status=invoice.status,
            url=_matching_url(invoice.id),
        )
    ]
    return tool_result("explain_blocker", summary, cards, {"record_type": RECORD_SUPPLIER_INVOICE, "status": invoice.status, "next_action": next_action})


def _explain_tbcn(identifier: str) -> dict:
    identifier = str(identifier or "").strip()
    queryset = TravelBillingConfirmationNote.objects.select_related("supplier")
    tbcn = queryset.filter(confirmation_no__iexact=identifier).first()
    if not tbcn and identifier.isdigit():
        tbcn = queryset.filter(id=int(identifier)).first()
    if not tbcn and identifier:
        tbcn = queryset.filter(uid__iexact=identifier).first()
    if not tbcn:
        return tool_result("explain_blocker", f"I could not find a TBCN for '{identifier}'.")

    if tbcn.finance_status == FinanceStatus.NOT_SENT and tbcn.status == BillingConfirmationStatus.GENERATED:
        next_action = "Send the TBCN to finance from the TBCN screen."
    elif tbcn.finance_status == FinanceStatus.SENT:
        next_action = "Awaiting finance acceptance."
    elif tbcn.finance_status == FinanceStatus.ACCEPTED:
        next_action = "Accepted by finance and awaiting payment."
    elif tbcn.finance_status == FinanceStatus.PAID:
        next_action = "Paid. No action required."
    else:
        next_action = "Review the TBCN status on the TBCN screen."
    summary = f"{tbcn.confirmation_no} is {tbcn.status} / finance {tbcn.finance_status}. Next action: {next_action}"
    cards = [card(CARD_TBCN, title=tbcn.confirmation_no, subtitle=tbcn.supplier.name, status=f"{tbcn.status} / {tbcn.finance_status}", url=f"/tbcn/{tbcn.id}")]
    return tool_result("explain_blocker", summary, cards, {"record_type": RECORD_TBCN, "status": tbcn.finance_status, "next_action": next_action})


def explain_blocker(user, record_type: str = "", record_id: str = "", **_) -> dict:
    record_type = str(record_type or "").strip().upper()
    record_id = str(record_id or "").strip()
    if not record_id:
        return tool_result(
            "explain_blocker",
            "Tell me which record to check, for example a travel case number, supplier invoice (SIR) number, or TBCN number.",
        )
    if record_type == RECORD_TRAVEL_CASE or record_id.upper().startswith("TRV"):
        return _explain_travel_case(record_id)
    if record_type == RECORD_TBCN or record_id.upper().startswith("TBCN"):
        return _explain_tbcn(record_id)
    if record_type == RECORD_SUPPLIER_INVOICE or record_id.upper().startswith("SIR"):
        return _explain_invoice(record_id)
    # Fall back to a best-effort lookup across record types.
    for resolver in (_explain_invoice, _explain_travel_case, _explain_tbcn):
        result = resolver(record_id)
        if "could not find" not in result["summary"]:
            return result
    return tool_result("explain_blocker", f"I could not find a travel case, supplier invoice, or TBCN for '{record_id}'.")
