"""Read-only copilot tools backed by existing dashboard analytics."""
from __future__ import annotations

from apps.dashboard.services import dashboard_summary, pending_actions_for_user

from .common import CARD_SUMMARY, amounts_by_currency_text, card, tool_result

PENDING_CARD_LIMIT = 8


def get_my_pending_actions(user, **_) -> dict:
    result = pending_actions_for_user(user)
    items = result["items"]
    if not items:
        return tool_result(
            "get_my_pending_actions",
            "You have no pending actions right now.",
            data={"count": 0, "groups": []},
        )

    cards = [
        card(item["type"], title=item["title"], subtitle=item["description"], status=item["status"], url=item["url"])
        for item in items[:PENDING_CARD_LIMIT]
    ]
    group_text = "; ".join(f"{group['title']} ({group['count']})" for group in result["groups"])
    summary = f"You have {len(items)} item(s) needing attention. " + (group_text + "." if group_text else "")
    data = {
        "count": len(items),
        "groups": [{"title": group["title"], "count": group["count"]} for group in result["groups"]],
    }
    return tool_result("get_my_pending_actions", summary.strip(), cards, data)


def get_dashboard_summary(user, **_) -> dict:
    summary = dashboard_summary()
    travel = summary["travel"]
    invoices = summary["supplier_invoices"]
    finance = summary["tbcn_finance"]
    unpaid_text = amounts_by_currency_text(finance["unpaid_by_currency"])

    text = (
        f"Travel cases: {travel['total']} total ({travel['submitted']} submitted, {travel['under_booking']} under booking). "
        f"Supplier invoices: {invoices['total']} total ({invoices['exception_found']} with exceptions, {invoices['hr_approved']} HR approved). "
        f"TBCN finance: {finance['total']} total, {finance['paid']} paid. "
        f"Unpaid by currency (never combined): {unpaid_text}."
    )
    cards = [
        card(CARD_SUMMARY, title="Travel cases", subtitle=f"{travel['total']} total", status=f"{travel['under_booking']} under booking", url="/dashboard"),
        card(CARD_SUMMARY, title="Invoice exceptions", subtitle=f"{invoices['exception_found']} invoice(s)", status="EXCEPTION", url="/dashboard"),
        card(CARD_SUMMARY, title="Unpaid TBCNs", subtitle=unpaid_text, status="UNPAID", url="/finance-control-report"),
    ]
    data = {
        "travel": travel,
        "supplier_invoices": invoices,
        "tbcn_finance": {
            "total": finance["total"],
            "paid": finance["paid"],
            "unpaid_by_currency": [{"currency": row["currency"], "amount": str(row["amount"])} for row in finance["unpaid_by_currency"]],
        },
    }
    return tool_result("get_dashboard_summary", text, cards, data)
