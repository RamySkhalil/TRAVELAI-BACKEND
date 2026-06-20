"""Read-only copilot tool for permit status (Egypt and Libya permits)."""
from __future__ import annotations

from apps.permits.models import PermitStatus, PermitType

from .common import CARD_PERMIT, card, tool_result
from .travel_cases import _resolve_case


def get_permit_status(user, identifier: str = "", **_) -> dict:
    case = _resolve_case(identifier)
    if not case:
        return tool_result(
            "get_permit_status",
            f"I could not find a travel case for '{identifier}'. Provide the travel case number to check its permits.",
        )

    permits = {permit.permit_type: permit for permit in case.permits.all()}
    lines = []
    data_permits = []
    for permit_type in (PermitType.EGYPT_PERMIT, PermitType.LIBYA_PERMIT):
        permit = permits.get(permit_type)
        label = dict(PermitType.choices)[permit_type]
        if not permit:
            lines.append(f"{label}: not recorded")
            data_permits.append({"type": permit_type, "status": "NOT_RECORDED", "expired": False, "pending": False})
            continue
        expired = permit.status == PermitStatus.EXPIRED
        pending = permit.status == PermitStatus.PENDING
        flags = []
        if expired:
            flags.append("expired")
        if pending:
            flags.append("pending")
        suffix = f" ({', '.join(flags)})" if flags else ""
        lines.append(f"{label}: {permit.status}{suffix}")
        data_permits.append(
            {
                "type": permit_type,
                "status": permit.status,
                "expired": expired,
                "pending": pending,
                "expiry_date": permit.expiry_date.isoformat() if permit.expiry_date else None,
            }
        )

    summary = f"Permits for {case.case_number}: " + "; ".join(lines) + "."
    cards = [
        card(
            CARD_PERMIT,
            title=f"{case.case_number} permits",
            subtitle="; ".join(lines),
            status=case.current_status,
            url=f"/travel-requests/{case.id}/permits",
        )
    ]
    return tool_result("get_permit_status", summary, cards, {"case_number": case.case_number, "permits": data_permits})
