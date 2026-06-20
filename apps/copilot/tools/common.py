"""Shared helpers for read-only copilot tools.

Every tool returns a ``ToolResult`` dict with this shape::

    {
        "tool": "<tool_name>",
        "summary": "<plain text answer fragment>",
        "cards": [<card>, ...],
        "data": {<compact, permission-filtered facts sent to the model>},
    }

Tools must never return secret values, full document text, or raw model
prompts. They only surface fields that are already exposed by the existing
authenticated read APIs.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

# Card types mirror the pending-actions/entity vocabulary already used by the
# dashboard and frontend so the UI can render and link them consistently.
CARD_TRAVEL_CASE = "TRAVEL_CASE"
CARD_TICKET = "TICKET"
CARD_PERMIT = "PERMIT"
CARD_SUPPLIER_INVOICE = "SUPPLIER_INVOICE"
CARD_INVOICE_LINE = "INVOICE_LINE"
CARD_TBCN = "TBCN"
CARD_SUMMARY = "SUMMARY"

MAX_CARDS = 8

PRIVILEGED_ROLES = {"Admin", "Auditor"}


def user_roles(user) -> set[str]:
    """Return the role group names assigned to the user."""
    if not user or not user.is_authenticated:
        return set()
    return set(user.groups.values_list("name", flat=True))


def is_privileged(user) -> bool:
    """Admin, Auditor, staff, and superusers may see the full read-only picture."""
    if not user or not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    return bool(user_roles(user) & PRIVILEGED_ROLES)


def card(card_type: str, title: str, subtitle: str = "", status: str = "", url: str = "") -> dict[str, str]:
    return {
        "type": card_type,
        "title": title or "",
        "subtitle": subtitle or "",
        "status": status or "",
        "url": url or "",
    }


def tool_result(tool: str, summary: str, cards: list[dict] | None = None, data: dict | None = None) -> dict[str, Any]:
    return {
        "tool": tool,
        "summary": summary,
        "cards": (cards or [])[:MAX_CARDS],
        "data": data or {},
    }


def money(amount, currency: str) -> str:
    """Format an amount with its currency without ever combining currencies."""
    value = amount if isinstance(amount, Decimal) else Decimal(str(amount or "0"))
    return f"{currency} {value:,.2f}"


def amounts_by_currency_text(rows: list[dict]) -> str:
    """Render currency-grouped amounts. USD and EGP are never summed together."""
    if not rows:
        return "none"
    return "; ".join(money(row.get("amount"), row.get("currency", "")) for row in rows)
