"""Read-only copilot tool for immutable ticket version history."""
from __future__ import annotations

from .common import CARD_TICKET, card, money, tool_result
from .travel_cases import _resolve_case

HISTORY_LIMIT = 12


def get_ticket_history(user, identifier: str = "", **_) -> dict:
    case = _resolve_case(identifier)
    if not case:
        return tool_result(
            "get_ticket_history",
            f"I could not find a travel case for '{identifier}'. Provide the travel case number to view its ticket history.",
        )

    versions = list(case.ticket_versions.select_related("supplier").order_by("version_number")[:HISTORY_LIMIT])
    if not versions:
        return tool_result(
            "get_ticket_history",
            f"{case.case_number} has no ticket versions yet.",
            data={"case_number": case.case_number, "versions": []},
        )

    cards = [
        card(
            CARD_TICKET,
            title=f"{version.version_number} · {version.ticket_number}",
            subtitle=f"{version.get_ticket_action_display()} · PNR {version.pnr} · {money(version.amount, version.currency)}",
            status=version.ticket_status,
            url=f"/travel-requests/{case.id}/tickets",
        )
        for version in versions
    ]
    summary = (
        f"{case.case_number} has {len(versions)} ticket version(s). "
        + "; ".join(
            f"{version.version_number} {version.ticket_action} {version.ticket_number} ({version.ticket_status}, {money(version.amount, version.currency)})"
            for version in versions
        )
        + ". Ticket history is immutable and never overwritten."
    )
    data = {
        "case_number": case.case_number,
        "versions": [
            {
                "version_number": version.version_number,
                "ticket_action": version.ticket_action,
                "ticket_number": version.ticket_number,
                "pnr": version.pnr,
                "ticket_status": version.ticket_status,
                "currency": version.currency,
                "amount": str(version.amount),
            }
            for version in versions
        ],
    }
    return tool_result("get_ticket_history", summary, cards, data)
