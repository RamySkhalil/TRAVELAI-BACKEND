"""Read-only copilot finance tools. Currencies (USD/EGP) are never combined."""
from __future__ import annotations

from apps.dashboard.services import cost_by_supplier as dashboard_cost_by_supplier
from apps.dashboard.services import dashboard_summary

from .common import CARD_TBCN, amounts_by_currency_text, card, money, tool_result

SUPPLIER_LIMIT = 10


def get_unpaid_tbcn_by_currency(user, **_) -> dict:
    summary = dashboard_summary()
    rows = summary["tbcn_finance"]["unpaid_by_currency"]
    if not rows:
        return tool_result("get_unpaid_tbcn_by_currency", "There are no unpaid TBCN finance items.")

    cards = [
        card(
            CARD_TBCN,
            title=f"Unpaid TBCNs · {row['currency']}",
            subtitle=money(row["amount"], row["currency"]),
            status="UNPAID",
            url="/finance-control-report",
        )
        for row in rows
    ]
    text = (
        "Unpaid TBCN finance totals, grouped by currency (USD and EGP are reported separately and never combined): "
        + amounts_by_currency_text(rows)
        + "."
    )
    data = {"unpaid_by_currency": [{"currency": row["currency"], "amount": str(row["amount"])} for row in rows]}
    return tool_result("get_unpaid_tbcn_by_currency", text, cards, data)


def get_cost_by_supplier(user, **_) -> dict:
    results = dashboard_cost_by_supplier()["results"][:SUPPLIER_LIMIT]
    if not results:
        return tool_result("get_cost_by_supplier", "There is no supplier cost data yet.")

    cards = [
        card(
            CARD_TBCN,
            title=row["supplier_name"],
            subtitle=f"{row['invoice_count']} invoice(s) · {money(row['total_amount'], row['currency'])}",
            status=row["currency"],
            url="/finance-control-report",
        )
        for row in results
    ]
    text = (
        "Cost by supplier, grouped by supplier and currency (USD and EGP are kept separate): "
        + "; ".join(f"{row['supplier_name']} {money(row['total_amount'], row['currency'])}" for row in results)
        + "."
    )
    data = {
        "rows": [
            {
                "supplier_name": row["supplier_name"],
                "currency": row["currency"],
                "invoice_count": row["invoice_count"],
                "total_amount": str(row["total_amount"]),
            }
            for row in results
        ]
    }
    return tool_result("get_cost_by_supplier", text, cards, data)
