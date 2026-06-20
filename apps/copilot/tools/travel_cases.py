"""Read-only copilot tools for travel cases."""
from __future__ import annotations

from django.db.models import Q

from apps.permits.models import PermitStatus, PermitType
from apps.travel_cases.models import TravelCase, TravelCaseStatus

from .common import CARD_TRAVEL_CASE, card, tool_result

SEARCH_LIMIT = 8


def _case_url(case: TravelCase) -> str:
    return f"/travel-requests/{case.id}"


def _resolve_case(identifier: str) -> TravelCase | None:
    identifier = str(identifier or "").strip()
    if not identifier:
        return None
    queryset = TravelCase.objects.select_related("employee", "project", "department", "country")
    case = queryset.filter(case_number__iexact=identifier).first()
    if case:
        return case
    if identifier.isdigit():
        case = queryset.filter(id=int(identifier)).first()
        if case:
            return case
    return queryset.filter(uid__iexact=identifier).first()


def next_action_for_case(case: TravelCase) -> str:
    pending_permits = list(
        case.permits.filter(Q(status__in=[PermitStatus.PENDING, PermitStatus.EXPIRED])).values_list("permit_type", flat=True)
    )
    status = case.current_status
    if status == TravelCaseStatus.DRAFT:
        return "Submit the travel case from the travel request screen."
    if status == TravelCaseStatus.SUBMITTED_BY_HR:
        return "Awaiting a booking officer to take ownership on the Booking Desk."
    if status == TravelCaseStatus.UNDER_BOOKING:
        return "Booking in progress. Upload and confirm the ticket from the Booking Desk."
    if pending_permits:
        return "Resolve pending or expired permits before travel proceeds."
    if status in [TravelCaseStatus.CLOSED, TravelCaseStatus.CANCELLED]:
        return "No action required. The case is closed or cancelled."
    return "Continue the booking and invoicing workflow on the case screen."


def search_travel_cases(user, query: str = "", **_) -> dict:
    query = str(query or "").strip()
    queryset = TravelCase.objects.select_related("employee", "project").order_by("-created_at")
    if query:
        queryset = queryset.filter(
            Q(case_number__icontains=query)
            | Q(employee_name__icontains=query)
            | Q(badge_number__icontains=query)
            | Q(project__code__icontains=query)
            | Q(project__name__icontains=query)
            | Q(route_from__icontains=query)
            | Q(route_to__icontains=query)
        )
    cases = list(queryset[:SEARCH_LIMIT])
    if not cases:
        suffix = f" matching '{query}'" if query else ""
        return tool_result("search_travel_cases", f"I found no travel cases{suffix}.")

    cards = [
        card(
            CARD_TRAVEL_CASE,
            title=case.case_number,
            subtitle=f"{case.employee_name} · {case.route_from} → {case.route_to}",
            status=case.current_status,
            url=_case_url(case),
        )
        for case in cases
    ]
    summary = f"I found {len(cases)} travel case(s)" + (f" matching '{query}'." if query else ".")
    data = {
        "count": len(cases),
        "cases": [
            {
                "case_number": case.case_number,
                "employee_name": case.employee_name,
                "project": case.project.code,
                "route": f"{case.route_from} -> {case.route_to}",
                "status": case.current_status,
            }
            for case in cases
        ],
    }
    return tool_result("search_travel_cases", summary, cards, data)


def get_travel_case_status(user, identifier: str = "", **_) -> dict:
    case = _resolve_case(identifier)
    if not case:
        return tool_result(
            "get_travel_case_status",
            f"I could not find a travel case for '{identifier}'. Try the exact case number such as TRV-LY-2026-000001.",
        )

    permits = list(case.permits.all())
    permit_summary = (
        ", ".join(f"{permit.get_permit_type_display()}: {permit.status}" for permit in permits) or "no permits recorded"
    )
    tickets = list(case.ticket_versions.order_by("version_number"))
    active_ticket = next((ticket for ticket in tickets if ticket.ticket_status == "ACTIVE"), None)
    ticket_summary = f"{len(tickets)} ticket version(s)"
    if active_ticket:
        ticket_summary += f", active ticket {active_ticket.ticket_number}"
    next_action = next_action_for_case(case)

    summary = (
        f"{case.case_number} for {case.employee_name} ({case.route_from} → {case.route_to}, project {case.project.code}) "
        f"is {case.current_status}. Permits: {permit_summary}. Tickets: {ticket_summary}. Next action: {next_action}"
    )
    cards = [
        card(
            CARD_TRAVEL_CASE,
            title=case.case_number,
            subtitle=f"{case.employee_name} · {case.route_from} → {case.route_to}",
            status=case.current_status,
            url=_case_url(case),
        )
    ]
    data = {
        "case_number": case.case_number,
        "employee_name": case.employee_name,
        "project": case.project.code,
        "route": f"{case.route_from} -> {case.route_to}",
        "status": case.current_status,
        "permits": [{"type": permit.permit_type, "status": permit.status} for permit in permits],
        "ticket_count": len(tickets),
        "next_action": next_action,
    }
    return tool_result("get_travel_case_status", summary, cards, data)
