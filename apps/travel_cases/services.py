from datetime import date

from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit_logs.models import AuditLog
from apps.audit_logs.services import create_audit_log
from apps.common.services.sequences import generate_sequence
from apps.travel_cases.models import TravelCase, TravelCaseStatus


def next_travel_case_number(country_code: str, year: int | None = None) -> str:
    target_year = year or date.today().year
    return generate_sequence(f"TRV-{country_code}", target_year, "{code}-{year}-{number:06d}")


def create_travel_case(data, user) -> TravelCase:
    employee = data["employee"]
    if not employee.is_active:
        raise ValidationError("Inactive employees cannot be used for new travel cases.")
    country = data["country"]
    requested_date = data["requested_travel_date"]
    data["case_number"] = next_travel_case_number(country.code, requested_date.year)
    data["badge_number"] = employee.badge_number
    data["employee_name"] = employee.full_name
    data["current_status"] = TravelCaseStatus.DRAFT
    data["created_by"] = user
    travel_case = TravelCase.objects.create(**data)
    create_audit_log(
        user=user,
        action="Travel Case Created",
        entity_type="TravelCase",
        entity_id=travel_case.id,
        new_value={"case_number": travel_case.case_number, "status": travel_case.current_status},
    )
    return travel_case


def submit_travel_case(travel_case: TravelCase, user) -> TravelCase:
    if travel_case.current_status != TravelCaseStatus.DRAFT:
        raise ValidationError("Only draft travel cases can be submitted.")
    old_status = travel_case.current_status
    travel_case.current_status = TravelCaseStatus.SUBMITTED_BY_HR
    travel_case.submitted_at = timezone.now()
    travel_case.save(update_fields=["current_status", "submitted_at", "updated_at"])
    create_audit_log(
        user=user,
        action="Travel Case Submitted",
        entity_type="TravelCase",
        entity_id=travel_case.id,
        old_value={"status": old_status},
        new_value={"status": travel_case.current_status},
    )
    return travel_case


# Forward-only rank for automatic lifecycle progression. Paused/branch states
# (CHANGE_REQUESTED, POSTPONED) sit just before TICKET_BOOKED so later billing
# milestones can still advance them, while re-runs never regress a case.
# CANCELLED and CLOSED are terminal and are never auto-advanced.
_WORKFLOW_RANK = {
    TravelCaseStatus.DRAFT: 0,
    TravelCaseStatus.SUBMITTED_BY_HR: 10,
    TravelCaseStatus.UNDER_BOOKING: 20,
    TravelCaseStatus.OPTIONS_RECEIVED: 30,
    TravelCaseStatus.APPROVED_FOR_BOOKING: 40,
    TravelCaseStatus.CHANGE_REQUESTED: 45,
    TravelCaseStatus.POSTPONED: 46,
    TravelCaseStatus.TICKET_BOOKED: 50,
    TravelCaseStatus.INVOICE_RECEIVED: 60,
    TravelCaseStatus.INVOICE_MATCHED: 70,
    TravelCaseStatus.SENT_TO_FINANCE: 80,
    TravelCaseStatus.PAID: 90,
    TravelCaseStatus.CLOSED: 100,
}

_TERMINAL_STATUSES = frozenset({TravelCaseStatus.CANCELLED, TravelCaseStatus.CLOSED})


def advance_travel_case_status(
    travel_case: TravelCase,
    target_status: str,
    user=None,
    *,
    action: str = "Travel Case Status Advanced",
    metadata=None,
) -> TravelCase:
    """Advance a case forward along the workflow: forward-only, idempotent, audited.

    The move is applied only when ``target_status`` ranks strictly higher than the
    current status, so re-runs and out-of-order events never regress the case or
    emit duplicate transitions. Terminal cases (``CANCELLED``, ``CLOSED``) are left
    untouched.
    """
    current = travel_case.current_status
    if current in _TERMINAL_STATUSES:
        return travel_case
    target_rank = _WORKFLOW_RANK.get(target_status)
    if target_rank is None:
        raise ValueError(f"{target_status} is not an auto-advanceable workflow status.")
    if target_rank <= _WORKFLOW_RANK.get(current, -1):
        return travel_case
    old_status = current
    travel_case.current_status = target_status
    travel_case.save(update_fields=["current_status", "updated_at"])
    create_audit_log(
        user=user,
        action=action,
        entity_type="TravelCase",
        entity_id=travel_case.id,
        old_value={"status": old_status},
        new_value={"status": travel_case.current_status},
        metadata=metadata or {},
    )
    return travel_case


def mark_case_ticket_booked(travel_case: TravelCase, user=None) -> TravelCase:
    """Advance a case to TICKET_BOOKED once a ticket document is attached.

    Thin wrapper over :func:`advance_travel_case_status` so ticket uploads, later
    versions, and re-uploads share the same forward-only, idempotent guard.
    """
    return advance_travel_case_status(
        travel_case,
        TravelCaseStatus.TICKET_BOOKED,
        user,
        action="Travel Case Ticket Booked",
    )


def assign_travel_case(travel_case: TravelCase, assigned_to, user) -> TravelCase:
    if travel_case.current_status in [TravelCaseStatus.CANCELLED, TravelCaseStatus.CLOSED, TravelCaseStatus.PAID]:
        raise ValidationError("Closed, paid, or cancelled travel cases cannot be assigned.")
    old_value = {"assigned_to": travel_case.assigned_to_id, "status": travel_case.current_status}
    travel_case.assigned_to = assigned_to
    if travel_case.current_status in [TravelCaseStatus.SUBMITTED_BY_HR, TravelCaseStatus.DRAFT]:
        travel_case.current_status = TravelCaseStatus.UNDER_BOOKING
    travel_case.save(update_fields=["assigned_to", "current_status", "updated_at"])
    create_audit_log(
        user=user,
        action="Travel Case Assigned",
        entity_type="TravelCase",
        entity_id=travel_case.id,
        old_value=old_value,
        new_value={"assigned_to": assigned_to.id if assigned_to else None, "status": travel_case.current_status},
    )
    return travel_case


def close_travel_case(travel_case: TravelCase, user) -> TravelCase:
    valid_final_statuses = [TravelCaseStatus.PAID, TravelCaseStatus.SENT_TO_FINANCE, TravelCaseStatus.INVOICE_MATCHED]
    if travel_case.current_status == TravelCaseStatus.CANCELLED:
        raise ValidationError("Cancelled travel cases cannot be closed through this action.")
    if travel_case.current_status not in valid_final_statuses:
        raise ValidationError("Travel case must reach a valid final status before closing.")
    old_status = travel_case.current_status
    travel_case.current_status = TravelCaseStatus.CLOSED
    travel_case.closed_at = timezone.now()
    travel_case.save(update_fields=["current_status", "closed_at", "updated_at"])
    create_audit_log(
        user=user,
        action="Travel Case Closed",
        entity_type="TravelCase",
        entity_id=travel_case.id,
        old_value={"status": old_status},
        new_value={"status": travel_case.current_status},
    )
    return travel_case


# A case may be cancelled from any point before money has moved or it is already
# closed/cancelled. Paid cases are excluded because the spend is already settled.
CANCELLABLE_STATUSES = frozenset(
    set(TravelCaseStatus.values)
    - {TravelCaseStatus.CANCELLED, TravelCaseStatus.CLOSED, TravelCaseStatus.PAID}
)

# Postpone / change-request are operational (pre-finance) branch transitions.
POSTPONABLE_STATUSES = frozenset(
    {
        TravelCaseStatus.SUBMITTED_BY_HR,
        TravelCaseStatus.UNDER_BOOKING,
        TravelCaseStatus.OPTIONS_RECEIVED,
        TravelCaseStatus.APPROVED_FOR_BOOKING,
        TravelCaseStatus.TICKET_BOOKED,
        TravelCaseStatus.CHANGE_REQUESTED,
    }
)

CHANGE_REQUESTABLE_STATUSES = frozenset(
    {
        TravelCaseStatus.UNDER_BOOKING,
        TravelCaseStatus.OPTIONS_RECEIVED,
        TravelCaseStatus.APPROVED_FOR_BOOKING,
        TravelCaseStatus.TICKET_BOOKED,
        TravelCaseStatus.POSTPONED,
    }
)


def _apply_branch_transition(travel_case, target_status, reason, user, *, allowed, action, blocked_message):
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("A reason is required.")
    if travel_case.current_status not in allowed:
        raise ValidationError(blocked_message)
    old_status = travel_case.current_status
    travel_case.current_status = target_status
    travel_case.save(update_fields=["current_status", "updated_at"])
    create_audit_log(
        user=user,
        action=action,
        entity_type="TravelCase",
        entity_id=travel_case.id,
        old_value={"status": old_status},
        new_value={"status": travel_case.current_status, "reason": reason},
        metadata={"reason": reason},
    )
    return travel_case


def cancel_travel_case(travel_case: TravelCase, reason: str, user) -> TravelCase:
    return _apply_branch_transition(
        travel_case,
        TravelCaseStatus.CANCELLED,
        reason,
        user,
        allowed=CANCELLABLE_STATUSES,
        action="Travel Case Cancelled",
        blocked_message="Paid, closed, or already cancelled travel cases cannot be cancelled.",
    )


def postpone_travel_case(travel_case: TravelCase, reason: str, user) -> TravelCase:
    return _apply_branch_transition(
        travel_case,
        TravelCaseStatus.POSTPONED,
        reason,
        user,
        allowed=POSTPONABLE_STATUSES,
        action="Travel Case Postponed",
        blocked_message="This travel case cannot be postponed from its current status.",
    )


def request_travel_case_change(travel_case: TravelCase, reason: str, user) -> TravelCase:
    return _apply_branch_transition(
        travel_case,
        TravelCaseStatus.CHANGE_REQUESTED,
        reason,
        user,
        allowed=CHANGE_REQUESTABLE_STATUSES,
        action="Travel Case Change Requested",
        blocked_message="A change can only be requested for an active, booked, or postponed travel case.",
    )


def get_travel_case_timeline(travel_case: TravelCase):
    return [
        {
            "action": log.action,
            "old_value": log.old_value,
            "new_value": log.new_value,
            "metadata": log.metadata,
            "created_at": log.created_at,
            "user": log.user_id,
            "user_username": log.user.username if log.user else None,
        }
        for log in AuditLog.objects.filter(entity_type="TravelCase", entity_id=str(travel_case.id))
        .select_related("user")
        .order_by("created_at")
    ]
